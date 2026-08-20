"""
Credits API — баланс кредитов оркестратора, пакеты докупки, история транзакций,
покупка пакетов и оформление/продление тарифа `agent` через Robokassa.

Префикс: /api/credits
"""

from datetime import datetime
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
from backend.services.payment_service import RobokassaService

logger = get_logger(__name__)

router = APIRouter(prefix="/api/credits", tags=["Credits"])

AGENT_PLAN_PRICE = 5490.0


# ============================================================================
# SCHEMAS
# ============================================================================

class PurchaseRequest(BaseModel):
    package_code: str


# ============================================================================
# HELPERS
# ============================================================================

def _build_robokassa_payment(
    *,
    user: User,
    amount: float,
    description: str,
    extra_shp: Dict[str, str],
) -> Dict[str, Any]:
    """
    Создать параметры Robokassa-платежа (по аналогии с /api/payments/create-payment).
    Возвращает dict с payment_url, form_params, inv_id, amount.
    """
    if not settings.ROBOKASSA_MERCHANT_LOGIN or not settings.ROBOKASSA_PASSWORD_1:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Payment system not configured",
        )

    out_sum = f"{amount:.2f}"
    inv_id = f"{int(datetime.now().timestamp())}"

    custom_params = None
    if not RobokassaService.DISABLE_SHP_PARAMS:
        custom_params = {"Shp_user_id": str(user.id), **extra_shp}

    signature = RobokassaService.generate_signature(
        RobokassaService.MERCHANT_LOGIN,
        out_sum,
        inv_id,
        RobokassaService.PASSWORD_1,
        custom_params,
    )

    form_params: Dict[str, Any] = {
        "MerchantLogin": RobokassaService.MERCHANT_LOGIN,
        "OutSum": out_sum,
        "InvId": inv_id,
        "Description": description,
        "SignatureValue": signature,
        "Culture": "ru",
        "Encoding": "utf-8",
    }

    if RobokassaService.BASE_URL and not any(
        x in RobokassaService.BASE_URL for x in ["localhost", "127.0.0.1"]
    ):
        form_params["ResultURL"] = RobokassaService.RESULT_URL
        form_params["SuccessURL"] = RobokassaService.SUCCESS_URL
        form_params["FailURL"] = RobokassaService.FAIL_URL

    if user.email:
        form_params["Email"] = user.email
    if RobokassaService.TEST_MODE:
        form_params["IsTest"] = "1"

    if custom_params and not RobokassaService.DISABLE_SHP_PARAMS:
        for key, value in custom_params.items():
            form_params[key] = value

    return {
        "payment_url": RobokassaService.PAYMENT_URL,
        "form_params": form_params,
        "inv_id": inv_id,
        "amount": out_sum,
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
    """Создать Robokassa-платёж для покупки пакета кредитов."""
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

    payment = _build_robokassa_payment(
        user=user,
        amount=float(package.price_rub),
        description=f"Пакет кредитов {package.name} ({package.credits} кредитов)",
        extra_shp={"Shp_credits_package": package.code},
    )

    # Сохраняем pending-транзакцию
    transaction = PaymentTransaction(
        user_id=user.id,
        plan_id=plan.id if plan else None,
        external_payment_id=payment["inv_id"],
        payment_system="robokassa",
        amount=float(package.price_rub),
        currency="RUB",
        status="pending",
        payment_details=f"Shp_credits_package={package.code}; credits={package.credits}",
    )
    db.add(transaction)
    db.commit()
    db.refresh(transaction)

    logger.info(f"[CREDITS] Purchase payment created for user {user.id}, package {package.code}, inv {payment['inv_id']}")

    return {
        **payment,
        "transaction_id": str(transaction.id),
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

    payment = _build_robokassa_payment(
        user=user,
        amount=amount,
        description=f"Подписка Voicyfy Agent на 30 дней за {int(amount)} ₽",
        extra_shp={"Shp_plan_code": AGENT_PLAN_CODE},
    )

    transaction = PaymentTransaction(
        user_id=user.id,
        plan_id=plan.id,
        external_payment_id=payment["inv_id"],
        payment_system="robokassa",
        amount=amount,
        currency="RUB",
        status="pending",
        payment_details=f"Shp_plan_code={AGENT_PLAN_CODE}",
    )
    db.add(transaction)
    db.commit()
    db.refresh(transaction)

    logger.info(f"[CREDITS] Agent subscription payment created for user {user.id}, inv {payment['inv_id']}")

    return {
        **payment,
        "trial_activated": False,
        "transaction_id": str(transaction.id),
    }
