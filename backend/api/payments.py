# backend/api/payments.py

"""
Payment API endpoints for WellcomeAI application.
Платёжный провайдер — Finik (finik.kg), QR-эквайринг, валюта KGS (сом).

Поток оплаты:
  1. Фронт → POST /api/payments/create-payment
  2. Бэкенд создаёт pending-транзакцию и платёж в Finik (302 → Location)
  3. Фронт получает payment_url и делает window.location = payment_url
  4. После оплаты Finik шлёт webhook → POST /api/payments/finik-webhook
     (единственный источник истины о факте оплаты)
  5. RedirectUrl (/api/payments/success) — только UX-страница, ничего не зачисляет.

ЦЕНЫ ТАРИФОВ:
  Источник истины — БД, таблица subscription_plans (колонка price, в сомах).
  Значения ниже в SUBSCRIPTION_PLANS_CONFIG — fallback, если строки тарифа
  в БД ещё нет (она создаётся автоматически при первом платеже).
  ⚠️ Сейчас выставлены МИНИМАЛЬНЫЕ ТЕСТОВЫЕ ЦЕНЫ для проверки эквайринга.
  Смена цены: UPDATE subscription_plans SET price = <сом> WHERE code = '<код>';
  Пакеты кредитов: UPDATE credit_packages SET price_rub = <сом> WHERE code = '<код>';
"""

import json
import uuid
from typing import Optional, Dict, Any, Literal
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.core.logging import get_logger
from backend.core.dependencies import get_current_user
from backend.core.config import settings
from backend.db.session import get_db
from backend.models.user import User
from backend.models.subscription import SubscriptionPlan, PaymentTransaction
from backend.services.finik_service import FinikService, FinikError, is_success_status
from backend.services.payment_service import (
    FinikPaymentService,
    get_subscription_days_by_duration,
)
from backend.services.subscription_service import SubscriptionService

logger = get_logger(__name__)

# =============================================================================
# КОНФИГУРАЦИЯ ТАРИФОВ И СКИДОК (валюта — KGS, сом)
# =============================================================================

CURRENCY_CODE = "KGS"
CURRENCY_LABEL = "сом"

# Скидки на длительные периоды (одинаковые для всех тарифов)
PERIOD_DISCOUNTS = {
    1: 0,    # 1 месяц — без скидки
    6: 20,   # 6 месяцев — 20% скидка
    12: 30   # 12 месяцев — 30% скидка
}

# ⚠️ ВРЕМЕННЫЕ ТЕСТОВЫЕ ЦЕНЫ (сомы). Реальные цены задаются в БД
# (subscription_plans.price) и имеют приоритет над этими значениями.
SUBSCRIPTION_PLANS_CONFIG = {
    "ai_voice": {
        "name": "AI Voice",
        "base_price": 10.0,   # ТЕСТ: сом/мес
        "max_assistants": 3,
        "description": "Голосовые ассистенты без телефонии",
        "features": [
            "До 3 голосовых ассистентов",
            "OpenAI и Gemini агенты",
            "База знаний",
            "Безлимитные диалоги",
            "API доступ"
        ],
        "restricted_features": ["telephony", "outbound_calls", "crm"]
    },
    "start": {
        "name": "Тариф Старт",
        "base_price": 20.0,   # ТЕСТ: сом/мес
        "max_assistants": 5,
        "description": "Полный доступ со всеми функциями",
        "features": [
            "До 5 голосовых ассистентов",
            "OpenAI и Gemini агенты",
            "База знаний",
            "Телефония и исходящие звонки",
            "CRM система",
            "Безлимитные диалоги",
            "Приоритетная поддержка",
            "API доступ"
        ],
        "restricted_features": []
    },
    "profi": {
        "name": "Profi",
        "base_price": 30.0,   # ТЕСТ: сом/мес
        "max_assistants": 10,
        "description": "Максимальные возможности для бизнеса",
        "features": [
            "До 10 голосовых ассистентов",
            "OpenAI и Gemini агенты",
            "База знаний",
            "Телефония и исходящие звонки",
            "CRM система",
            "Безлимитные диалоги",
            "Приоритетная поддержка 24/7",
            "API доступ",
            "Персональный менеджер"
        ],
        "restricted_features": []
    }
}

# Допустимые коды тарифов для оплаты
ALLOWED_PLAN_CODES = list(SUBSCRIPTION_PLANS_CONFIG.keys())

# Create router
router = APIRouter()


# =============================================================================
# Вспомогательные функции
# =============================================================================

def format_som(amount: float) -> str:
    """1490 -> '1 490 сом'"""
    return f"{int(round(amount)):,}".replace(",", " ") + f" {CURRENCY_LABEL}"


def get_effective_base_price(db: Optional[Session], plan_code: str) -> float:
    """
    Базовая месячная цена тарифа в сомах.
    Источник истины — БД (subscription_plans.price); fallback — конфиг выше.
    """
    config_price = SUBSCRIPTION_PLANS_CONFIG[plan_code]["base_price"]
    if db is None:
        return config_price
    try:
        plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.code == plan_code).first()
        if plan is not None and plan.price is not None and float(plan.price) > 0:
            return float(plan.price)
    except Exception as e:
        logger.warning(f"⚠️ Could not read plan price from DB for {plan_code}: {e}")
    return config_price


def calculate_period_price(base_price: float, months: int) -> Dict[str, Any]:
    """Рассчитать цену за период с учетом скидки (суммы в сомах)."""
    if months not in PERIOD_DISCOUNTS:
        raise ValueError(f"Неподдерживаемый период: {months}. Допустимые: {list(PERIOD_DISCOUNTS.keys())}")

    discount_percent = PERIOD_DISCOUNTS[months]

    # Полная цена без скидки
    full_price = base_price * months

    # Цена со скидкой (округляем до целых сомов)
    if discount_percent > 0:
        final_price = round(full_price * (1 - discount_percent / 100))
    else:
        final_price = round(full_price)

    savings = full_price - final_price
    monthly_effective = round(final_price / months)
    days = get_subscription_days_by_duration(months)

    return {
        "months": months,
        "days": days,
        "base_price": base_price,
        "full_price": full_price,
        "discount_percent": discount_percent,
        "final_price": final_price,
        "savings": savings,
        "monthly_effective": monthly_effective,
        "label": _get_period_label(months)
    }


def _get_period_label(months: int) -> str:
    """Получить человекочитаемую метку периода"""
    labels = {
        1: "1 месяц",
        6: "6 месяцев",
        12: "1 год"
    }
    return labels.get(months, f"{months} месяцев")


def get_plan_config(plan_code: str) -> Dict[str, Any]:
    """Получить конфигурацию тарифа."""
    if plan_code not in SUBSCRIPTION_PLANS_CONFIG:
        raise ValueError(
            f"Неизвестный тариф: {plan_code}. "
            f"Допустимые: {ALLOWED_PLAN_CODES}"
        )
    return SUBSCRIPTION_PLANS_CONFIG[plan_code]


def get_plan_price_info(plan_code: str, months: int, db: Optional[Session] = None) -> Dict[str, Any]:
    """Полная информация о цене тарифа за период (цена — из БД при наличии)."""
    plan_config = get_plan_config(plan_code)
    base_price = get_effective_base_price(db, plan_code)
    price_info = calculate_period_price(base_price, months)

    return {
        "plan_code": plan_code,
        "plan_name": plan_config["name"],
        "max_assistants": plan_config["max_assistants"],
        "description": plan_config["description"],
        "features": plan_config["features"],
        **price_info
    }


def get_all_plans_with_prices(db: Optional[Session] = None) -> Dict[str, Any]:
    """Все тарифы со всеми вариантами цен (в сомах)."""
    plans = {}

    for plan_code, config in SUBSCRIPTION_PLANS_CONFIG.items():
        base_price = get_effective_base_price(db, plan_code)
        periods = {}
        for months in PERIOD_DISCOUNTS.keys():
            price_info = calculate_period_price(base_price, months)
            periods[months] = {
                "months": months,
                "days": price_info["days"],
                "price": price_info["final_price"],
                "price_formatted": format_som(price_info["final_price"]),
                "discount_percent": price_info["discount_percent"],
                "savings": price_info["savings"],
                "savings_formatted": format_som(price_info["savings"]) if price_info["savings"] > 0 else None,
                "monthly_effective": price_info["monthly_effective"],
                "monthly_formatted": format_som(price_info["monthly_effective"]) + "/мес",
                "label": price_info["label"]
            }

        plans[plan_code] = {
            "code": plan_code,
            "name": config["name"],
            "base_price": base_price,
            "base_price_formatted": format_som(base_price) + "/мес",
            "max_assistants": config["max_assistants"],
            "description": config["description"],
            "features": config["features"],
            "restricted_features": config.get("restricted_features", []),
            "periods": periods
        }

    # Цена тарифа agent (для отображения на дашборде) — из БД
    agent_price = None
    if db is not None:
        try:
            agent_plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.code == "agent").first()
            if agent_plan is not None and agent_plan.price is not None:
                agent_price = float(agent_plan.price)
        except Exception:
            pass

    return {
        "plans": plans,
        "discounts": PERIOD_DISCOUNTS,
        "currency": CURRENCY_CODE,
        "currency_label": CURRENCY_LABEL,
        "agent_price": agent_price
    }


# =============================================================================
# Pydantic модели для запросов
# =============================================================================

class CreatePaymentRequest(BaseModel):
    """Модель запроса на создание платежа"""
    plan_code: Literal["ai_voice", "start", "profi"] = "ai_voice"
    duration_months: Literal[1, 6, 12] = 1


# =============================================================================
# API Endpoints
# =============================================================================

@router.get("/plans", response_model=Dict[str, Any])
async def get_subscription_plans(db: Session = Depends(get_db)):
    """Все доступные тарифы с ценами (в сомах, из БД)."""
    try:
        return get_all_plans_with_prices(db)
    except Exception as e:
        logger.error(f"❌ Error in get_subscription_plans: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get subscription plans"
        )


@router.get("/plans/{plan_code}", response_model=Dict[str, Any])
async def get_plan_details(plan_code: str, db: Session = Depends(get_db)):
    """Детали конкретного тарифа со всеми вариантами цен."""
    try:
        if plan_code not in SUBSCRIPTION_PLANS_CONFIG:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Тариф '{plan_code}' не найден. Доступные: {ALLOWED_PLAN_CODES}"
            )

        all_plans = get_all_plans_with_prices(db)
        return all_plans["plans"][plan_code]

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error in get_plan_details: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get plan details"
        )


@router.post("/create-payment", response_model=Dict[str, Any])
async def create_payment(
    request_data: CreatePaymentRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Создание платежа Finik для подписки.
    Возвращает payment_url — фронт делает window.location = payment_url.
    """
    try:
        plan_code = request_data.plan_code
        duration_months = request_data.duration_months

        logger.info(f"🚀 Creating Finik payment for user {current_user.id}")
        logger.info(f"   Plan: {plan_code}, Duration: {duration_months} months")

        if plan_code not in SUBSCRIPTION_PLANS_CONFIG:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Неизвестный тариф: {plan_code}. Доступные: {ALLOWED_PLAN_CODES}"
            )

        if not FinikService.is_configured():
            logger.error("❌ Finik is not configured (FINIK_API_KEY / FINIK_PRIVATE_PEM / FINIK_ACCOUNT_ID)")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Payment system not configured. Contact administrator."
            )

        user = db.query(User).filter(User.id == current_user.id).first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )

        # Получаем или создаем план подписки в БД
        plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.code == plan_code).first()
        if not plan:
            plan_config = SUBSCRIPTION_PLANS_CONFIG[plan_code]
            plan = SubscriptionPlan(
                code=plan_code,
                name=plan_config["name"],
                price=plan_config["base_price"],
                max_assistants=plan_config["max_assistants"],
                description=plan_config["description"],
                is_active=True
            )
            db.add(plan)
            db.flush()
            logger.info(f"✅ Created subscription plan in DB: {plan_code}")

        # Информация о цене (цена — из БД)
        price_info = get_plan_price_info(plan_code, duration_months, db)
        subscription_price = price_info["final_price"]
        subscription_days = price_info["days"]
        plan_name = price_info["plan_name"]
        discount_percent = price_info["discount_percent"]
        period_label = price_info["label"]

        # Описание платежа
        if duration_months == 1:
            description = f"{plan_name} на {subscription_days} дней за {subscription_price:.0f} сом"
        else:
            description = f"{plan_name} на {period_label} за {subscription_price:.0f} сом (скидка {discount_percent}%)"

        # Уникальный PaymentId — по нему сопоставляем webhook
        payment_id = str(uuid.uuid4())

        payment_details = json.dumps({
            "type": "subscription",
            "plan_code": plan_code,
            "duration_months": duration_months,
            "days": subscription_days,
        }, ensure_ascii=False)

        transaction = PaymentTransaction(
            user_id=user.id,
            plan_id=plan.id,
            external_payment_id=payment_id,
            payment_system="finik",
            amount=subscription_price,
            currency=CURRENCY_CODE,
            status="pending",
            payment_details=payment_details
        )
        db.add(transaction)
        db.commit()
        db.refresh(transaction)

        logger.info(f"📋 Created payment transaction {transaction.id}, PaymentId={payment_id}")

        # Создаём платёж в Finik (302 → Location)
        try:
            payment_url = await FinikService.create_payment(
                amount=subscription_price,
                payment_id=payment_id,
                description=description,
                name_en="Voicyfy",
                lang="ru",
            )
        except FinikError as e:
            transaction.status = "failed"
            transaction.error_message = str(e)[:500]
            db.commit()
            logger.error(f"❌ Finik payment creation failed: {e}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Не удалось создать платёж. Попробуйте позже или обратитесь в поддержку."
            )

        transaction.payment_url = payment_url
        db.commit()

        # Логируем событие
        await SubscriptionService.log_subscription_event(
            db=db,
            user_id=str(current_user.id),
            action="payment_started",
            plan_id=str(plan.id),
            plan_code=plan_code,
            details=(
                f"Finik payment initiated: {plan_name}, {period_label}, "
                f"price={subscription_price} KGS, days={subscription_days}, payment_id={payment_id}"
            )
        )

        return {
            "payment_url": payment_url,
            "payment_id": payment_id,
            "transaction_id": str(transaction.id),
            "amount": subscription_price,
            "currency": CURRENCY_CODE,
            "plan_info": {
                "code": plan_code,
                "name": plan_name,
                "max_assistants": price_info["max_assistants"]
            },
            "period_info": {
                "months": duration_months,
                "days": subscription_days,
                "price": subscription_price,
                "discount_percent": discount_percent,
                "savings": price_info["savings"],
                "label": period_label
            }
        }

    except HTTPException as he:
        logger.error(f"❌ HTTP Exception in create_payment: {he.detail}")
        raise
    except Exception as e:
        logger.error(f"❌ Unexpected error in create_payment: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create payment: {str(e)}"
        )


@router.get("/subscription-periods", response_model=Dict[str, Any])
async def get_subscription_periods(plan_code: str = "ai_voice", db: Session = Depends(get_db)):
    """Доступные периоды подписки для тарифа (цены в сомах)."""
    try:
        if plan_code not in SUBSCRIPTION_PLANS_CONFIG:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Неизвестный тариф: {plan_code}. Доступные: {ALLOWED_PLAN_CODES}"
            )

        plan_config = SUBSCRIPTION_PLANS_CONFIG[plan_code]
        base_price = get_effective_base_price(db, plan_code)

        periods = []
        for months in PERIOD_DISCOUNTS.keys():
            price_info = calculate_period_price(base_price, months)
            periods.append({
                "months": months,
                "days": price_info["days"],
                "price": price_info["final_price"],
                "price_formatted": format_som(price_info["final_price"]),
                "discount_percent": price_info["discount_percent"],
                "savings": price_info["savings"],
                "savings_formatted": format_som(price_info["savings"]) if price_info["savings"] > 0 else None,
                "label": price_info["label"],
                "description": f"{'Со скидкой ' + str(price_info['discount_percent']) + '%' if price_info['discount_percent'] > 0 else 'Без скидки'}",
                "monthly_price": price_info["monthly_effective"],
                "monthly_price_formatted": format_som(price_info["monthly_effective"]) + "/мес"
            })

        return {
            "plan_code": plan_code,
            "plan_name": plan_config["name"],
            "base_monthly_price": base_price,
            "periods": periods,
            "currency": CURRENCY_CODE
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error in get_subscription_periods: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get subscription periods"
        )


# =============================================================================
# WEBHOOK FINIK — единственный источник истины о факте оплаты
# =============================================================================

@router.post("/finik-webhook")
async def finik_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Обработка webhook'а Finik. Приходит ТОЛЬКО при успешной оплате
    (неуспех webhook'ом не сообщается).

    Гарантии:
      - проверка подписи (заголовок `signature`, публичный ключ Finik);
      - идемпотентность по transactionId (уникальный индекс в БД);
      - сверка суммы с суммой заказа;
      - быстрый ответ 200 (ретраи Finik: 5 × 30 сек, далее экспоненциально).
    """
    try:
        raw_body = await request.body()
        try:
            payload = json.loads(raw_body)
        except (ValueError, TypeError):
            logger.error(f"❌ [FINIK-WEBHOOK] Invalid JSON body: {raw_body[:300]!r}")
            return JSONResponse(status_code=400, content={"status": "invalid_json"})

        logger.info(f"📥 [FINIK-WEBHOOK] Received: {json.dumps(payload, ensure_ascii=False)[:1000]}")

        # 1. Проверка подписи входящего запроса
        signature = request.headers.get("signature")
        is_valid = FinikService.verify_webhook_signature(
            http_method=request.method,
            path=request.url.path,
            headers=dict(request.headers),
            query_params=dict(request.query_params),
            body=payload,
            signature_b64=signature,
        )
        if not is_valid:
            logger.error("❌ [FINIK-WEBHOOK] Signature verification failed → 401")
            return JSONResponse(status_code=401, content={"status": "invalid_signature"})

        # 2. Извлекаем данные
        finik_transaction_id = payload.get("transactionId") or payload.get("id")
        payment_status = payload.get("status")
        fields = payload.get("fields") or {}
        payment_id = fields.get("paymentId") or payload.get("paymentId")
        webhook_amount = payload.get("amount", fields.get("amount"))

        # 3. Статус — регистронезависимо (webhook и так приходит только при успехе)
        if not is_success_status(payment_status):
            logger.warning(f"⚠️ [FINIK-WEBHOOK] Non-success status '{payment_status}' — ignored")
            return {"status": "ignored"}

        if not payment_id:
            logger.error(f"❌ [FINIK-WEBHOOK] Missing fields.paymentId in payload")
            return {"status": "missing_payment_id"}

        # 4. Идемпотентность: этот transactionId уже обработан?
        if finik_transaction_id:
            existing = db.query(PaymentTransaction).filter(
                PaymentTransaction.finik_transaction_id == str(finik_transaction_id)
            ).first()
            if existing:
                logger.info(f"ℹ️ [FINIK-WEBHOOK] Duplicate delivery for transactionId={finik_transaction_id} — OK, no-op")
                return {"status": "already_processed"}

        # 5. Сопоставление с заказом — по нашему PaymentId
        transaction = db.query(PaymentTransaction).filter(
            PaymentTransaction.external_payment_id == str(payment_id)
        ).first()
        if not transaction:
            logger.error(f"❌ [FINIK-WEBHOOK] No transaction found for paymentId={payment_id}")
            return {"status": "unknown_payment"}

        if transaction.is_processed:
            # Уже зачислено (например, повтор без transactionId в прошлый раз)
            if finik_transaction_id and not transaction.finik_transaction_id:
                transaction.finik_transaction_id = str(finik_transaction_id)
                db.commit()
            logger.info(f"ℹ️ [FINIK-WEBHOOK] Payment {payment_id} already processed — OK, no-op")
            return {"status": "already_processed"}

        # 6. Сверка суммы
        try:
            if webhook_amount is None or abs(float(webhook_amount) - float(transaction.amount)) > 0.01:
                logger.error(
                    f"❌ [FINIK-WEBHOOK] AMOUNT MISMATCH for paymentId={payment_id}: "
                    f"webhook={webhook_amount}, expected={transaction.amount}. NOT crediting automatically!"
                )
                transaction.error_message = (
                    f"Amount mismatch: webhook={webhook_amount}, expected={transaction.amount}"
                )[:500]
                db.commit()
                return {"status": "amount_mismatch"}
        except (ValueError, TypeError):
            logger.error(f"❌ [FINIK-WEBHOOK] Invalid amount '{webhook_amount}' for paymentId={payment_id}")
            return {"status": "invalid_amount"}

        # 7. Фиксируем transactionId (уникальный индекс защищает от гонки
        #    двух параллельных доставок одного webhook'а)
        if finik_transaction_id:
            transaction.finik_transaction_id = str(finik_transaction_id)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                logger.info(f"ℹ️ [FINIK-WEBHOOK] Race on transactionId={finik_transaction_id} — processed by parallel delivery")
                return {"status": "already_processed"}

        # 8. Зачисление
        ok = await FinikPaymentService.apply_successful_payment(db, transaction)
        if not ok:
            # Данные заказа некорректны — ошибку уже залогировали; 200, чтобы
            # Finik не ретраил бесконечно (ручной разбор по логам)
            logger.error(f"❌ [FINIK-WEBHOOK] Failed to apply payment {payment_id} — needs manual review")
            return {"status": "processing_failed"}

        logger.info(f"✅ [FINIK-WEBHOOK] Payment {payment_id} processed successfully")
        return {"status": "ok"}

    except Exception as e:
        logger.error(f"❌ [FINIK-WEBHOOK] Unexpected error: {e}", exc_info=True)
        # 500 → Finik повторит доставку позже
        return JSONResponse(status_code=500, content={"status": "error"})


# =============================================================================
# Страница возврата (RedirectUrl) — только UX, ничего не зачисляет
# =============================================================================

@router.get("/success", response_class=HTMLResponse)
@router.post("/success", response_class=HTMLResponse)
async def payment_success(request: Request):
    """
    Страница, на которую Finik возвращает пользователя после оплаты.
    Факт оплаты подтверждается ТОЛЬКО webhook'ом — здесь ничего не зачисляем.
    """
    try:
        status_data = FinikPaymentService.get_payment_status_message(success=True)

        html_content = f"""
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>{status_data['title']}</title>
            <style>
                body {{
                    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    margin: 0;
                    padding: 20px;
                    min-height: 100vh;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                }}
                .container {{
                    background: white;
                    border-radius: 20px;
                    padding: 40px;
                    max-width: 500px;
                    text-align: center;
                    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.1);
                }}
                .icon {{
                    font-size: 4rem;
                    color: #10b981;
                    margin-bottom: 20px;
                }}
                .title {{
                    font-size: 1.8rem;
                    font-weight: 600;
                    color: #1f2937;
                    margin-bottom: 10px;
                }}
                .message {{
                    color: #6b7280;
                    margin-bottom: 30px;
                    line-height: 1.6;
                }}
                .button {{
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                    padding: 12px 30px;
                    border: none;
                    border-radius: 10px;
                    font-weight: 500;
                    text-decoration: none;
                    display: inline-block;
                    transition: transform 0.2s;
                }}
                .button:hover {{
                    transform: translateY(-2px);
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="icon">✅</div>
                <h1 class="title">{status_data['title']}</h1>
                <p class="message">{status_data['message']}</p>
                <a href="{status_data['redirect_url']}?payment_result=success&payment_status=success" class="button">Перейти в панель управления</a>
            </div>
            <script>
                // Автоматическое перенаправление через 5 секунд
                setTimeout(() => {{
                    window.location.href = "{status_data['redirect_url']}?payment_result=success&payment_status=success";
                }}, 5000);
            </script>
        </body>
        </html>
        """

        return HTMLResponse(content=html_content)

    except Exception as e:
        logger.error(f"❌ Error in payment_success endpoint: {str(e)}", exc_info=True)
        return HTMLResponse(content="<h1>Произошла ошибка</h1>", status_code=500)


@router.get("/status/{user_id}")
async def get_payment_status(
    user_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Получение статуса платежа/подписки пользователя
    """
    try:
        # Проверяем права доступа
        if str(current_user.id) != user_id and not current_user.is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )

        # Получаем информацию о подписке
        from backend.api.subscriptions import get_my_subscription

        if str(current_user.id) == user_id:
            return await get_my_subscription(current_user, db)
        else:
            # Для админа - получаем информацию о другом пользователе
            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="User not found"
                )
            return await get_my_subscription(user, db)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error in get_payment_status endpoint: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get payment status"
        )


# =============================================================================
# ДИАГНОСТИКА
# =============================================================================

@router.get("/config-check")
async def check_finik_config():
    """🔍 Проверка конфигурации Finik (без секретов)."""
    try:
        return {
            "status": "ok" if FinikService.is_configured() else "error",
            "configured": FinikService.is_configured(),
            "api_url": settings.FINIK_API_URL,
            "account_id_set": bool(settings.FINIK_ACCOUNT_ID),
            "api_key_set": bool(settings.FINIK_API_KEY),
            "private_key_set": bool(settings.FINIK_PRIVATE_PEM),
            "public_key_set": bool(settings.FINIK_PUBLIC_KEY),
            "verify_webhook_signature": settings.FINIK_VERIFY_WEBHOOK_SIGNATURE,
            "webhook_url": FinikService.webhook_url(),
            "redirect_url": FinikService.redirect_url(),
            "available_plans": ALLOWED_PLAN_CODES,
            "period_discounts": PERIOD_DISCOUNTS,
            "currency": CURRENCY_CODE,
        }
    except Exception as e:
        logger.error(f"❌ Error checking configuration: {str(e)}")
        return {"status": "error", "error": str(e)}
