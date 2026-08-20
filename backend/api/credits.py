"""
Credits API — баланс кредитов оркестратора, пакеты докупки, история транзакций,
покупка пакетов и оформление/продление тарифа `agent` через Finik (KGS).

Префикс: /api/credits
"""

import json
import uuid
from typing import Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.core.logging import get_logger
from backend.core.config import settings
from backend.core.dependencies import get_current_user
from backend.db.session import get_db
from backend.models.user import User
from backend.models.subscription import SubscriptionPlan, PaymentTransaction
from backend.models.credit_package import CreditPackage
from backend.services.credit_service import (
    CreditService,
    activate_agent_trial,
    AGENT_PLAN_CODE,
)
from backend.services.finik_service import FinikService, FinikError

logger = get_logger(__name__)

router = APIRouter(prefix="/api/credits", tags=["Credits"])

# Fallback-цена тарифа agent (сом/мес), если строки нет в БД.
# Источник истины — БД: UPDATE subscription_plans SET price=<сом> WHERE code='agent';
# ⚠️ Сейчас минимальная ТЕСТОВАЯ цена для проверки эквайринга Finik.
AGENT_PLAN_PRICE = 50.0


# ============================================================================
# SCHEMAS
# ============================================================================

class PurchaseRequest(BaseModel):
    package_code: str


# ============================================================================
# HELPERS
# ============================================================================

async def create_finik_payment_transaction(
    *,
    db: Session,
    user: User,
    amount: float,
    description: str,
    details: Dict[str, Any],
    plan_id=None,
) -> Dict[str, Any]:
    """
    Создать pending-транзакцию и платёж в Finik.
    Возвращает dict с payment_url (фронт делает window.location = payment_url),
    payment_id, amount, transaction_id. Используется также для cascade-пакетов
    (backend/api/grok_assistants.py).
    """
    if not FinikService.is_configured():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Payment system not configured",
        )

    payment_id = str(uuid.uuid4())

    transaction = PaymentTransaction(
        user_id=user.id,
        plan_id=plan_id,
        external_payment_id=payment_id,
        payment_system="finik",
        amount=amount,
        currency="KGS",
        status="pending",
        payment_details=json.dumps(details, ensure_ascii=False),
    )
    db.add(transaction)
    db.commit()
    db.refresh(transaction)

    try:
        payment_url = await FinikService.create_payment(
            amount=amount,
            payment_id=payment_id,
            description=description,
            name_en="Voicyfy",
            lang="ru",
        )
    except FinikError as e:
        transaction.status = "failed"
        transaction.error_message = str(e)[:500]
        db.commit()
        logger.error(f"❌ [CREDITS] Finik payment creation failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Не удалось создать платёж. Попробуйте позже или обратитесь в поддержку.",
        )

    transaction.payment_url = payment_url
    db.commit()

    return {
        "payment_url": payment_url,
        "payment_id": payment_id,
        "amount": amount,
        "currency": "KGS",
        "transaction_id": str(transaction.id),
    }


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.get("/balance")
async def get_balance(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Баланс кредитов + статус подписки agent."""
    user = db.query(User).filter(User.id == current_user.id).first()
    if not user:
        raise HTTPException(status_code=404, detail="user_not_found")

    return {
        "credits_balance": user.credits_balance or 0,
        "subscription_status": user.agent_subscription_status(),
        "subscription_end_date": user.subscription_end_date.isoformat() if user.subscription_end_date else None,
        "is_trial": user.agent_subscription_status() == "trial",
        "days_remaining": user.agent_days_remaining(),
        "is_blocked": bool(user.agent_subscription_blocked),
        "trial_available": not bool(user.agent_trial_used),
        "has_agent_access": user.has_agent_access(),
        "is_profi_plan": user.is_profi_plan(),
    }


@router.get("/packages")
async def get_packages(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Список активных пакетов докупки, отсортированных по sort_order."""
    packages = (
        db.query(CreditPackage)
        .filter(CreditPackage.is_active == True,
                CreditPackage.product == "orchestrator")
        .order_by(CreditPackage.sort_order.asc())
        .all()
    )
    return {"packages": [p.to_dict() for p in packages]}


@router.get("/transactions")
async def get_transactions(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    type_filter: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Пагинированная история транзакций кредитов."""
    rows, total = CreditService.get_transactions(
        db, current_user.id, limit=limit, offset=offset, type_filter=type_filter
    )
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "transactions": [t.to_dict() for t in rows],
    }


@router.post("/purchase")
async def purchase_package(
    body: PurchaseRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Создать Finik-платёж для покупки пакета кредитов."""
    user = db.query(User).filter(User.id == current_user.id).first()

    package = db.query(CreditPackage).filter(
        CreditPackage.code == body.package_code,
        CreditPackage.is_active == True,
        CreditPackage.product == "orchestrator",
    ).first()
    if not package:
        raise HTTPException(status_code=404, detail="package_not_found")

    # Пакеты доступны только при активной подписке agent (включая trial)
    if not user.has_active_agent_subscription():
        raise HTTPException(status_code=403, detail="subscription_required")

    plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.code == AGENT_PLAN_CODE).first()

    payment = await create_finik_payment_transaction(
        db=db,
        user=user,
        amount=float(package.price_rub),
        description=f"Пакет кредитов {package.name} ({package.credits} кредитов)",
        details={"type": "credits_package", "package_code": package.code, "credits": package.credits},
        plan_id=plan.id if plan else None,
    )

    logger.info(f"[CREDITS] Purchase payment created for user {user.id}, package {package.code}, payment {payment['payment_id']}")

    return {
        **payment,
        "package": package.to_dict(),
    }


@router.post("/subscribe")
async def subscribe_agent(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Оформить/продлить тариф agent (5 490 ₽).
    Если trial ещё не использован — активирует бесплатный trial без оплаты.
    """
    user = db.query(User).filter(User.id == current_user.id).first()

    # Trial доступен — активируем бесплатно
    if not user.agent_trial_used:
        activated = activate_agent_trial(db, user)
        if activated:
            db.refresh(user)
            logger.info(f"[CREDITS] Trial activated for user {user.id} via /subscribe")
            from datetime import timedelta
            trial_until = None
            if user.agent_trial_started_at:
                trial_until = (user.agent_trial_started_at + timedelta(days=User.AGENT_TRIAL_DAYS)).isoformat()
            return {
                "trial_activated": True,
                "trial_until": trial_until,
                "credits_balance": user.credits_balance or 0,
            }

    # Иначе — обычный платёж за тариф agent
    plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.code == AGENT_PLAN_CODE).first()
    if not plan:
        raise HTTPException(status_code=500, detail="agent_plan_not_found")

    amount = float(plan.price) if plan.price else AGENT_PLAN_PRICE

    payment = await create_finik_payment_transaction(
        db=db,
        user=user,
        amount=amount,
        description=f"Подписка Voicyfy Agent на 30 дней за {int(amount)} сом",
        details={"type": "agent_subscription"},
        plan_id=plan.id,
    )

    logger.info(f"[CREDITS] Agent subscription payment created for user {user.id}, payment {payment['payment_id']}")

    return {
        **payment,
        "trial_activated": False,
    }
