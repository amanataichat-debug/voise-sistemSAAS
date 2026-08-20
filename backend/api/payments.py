# backend/api/payments.py

"""
Payment API endpoints for WellcomeAI application.
Handles Robokassa payment integration.

✅ ВЕРСИЯ 3.0 - Поддержка нескольких тарифов:
   - AI Voice: 1490₽/мес (3 ассистента)
   - Старт: 2990₽/мес (5 ассистентов)
   - Profi: 5990₽/мес (10 ассистентов)
   
   Скидки на длительные периоды:
   - 6 месяцев: 20%
   - 12 месяцев: 30%
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request, Form, Body
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from typing import Optional, Dict, Any, Literal
from datetime import datetime, timedelta, timezone
from pydantic import BaseModel
from decimal import Decimal, ROUND_HALF_UP

from backend.core.logging import get_logger
from backend.core.dependencies import get_current_user
from backend.core.config import settings
from backend.db.session import get_db
from backend.models.user import User
from backend.models.subscription import SubscriptionPlan, PaymentTransaction
from backend.services.payment_service import RobokassaService
from backend.services.subscription_service import SubscriptionService

logger = get_logger(__name__)

# =============================================================================
# ✅ КОНФИГУРАЦИЯ ТАРИФОВ И СКИДОК (v3.0)
# =============================================================================

# Скидки на длительные периоды (одинаковые для всех тарифов)
PERIOD_DISCOUNTS = {
    1: 0,    # 1 месяц — без скидки
    6: 20,   # 6 месяцев — 20% скидка
    12: 30   # 12 месяцев — 30% скидка
}

# Конфигурация тарифов (базовые месячные цены)
SUBSCRIPTION_PLANS_CONFIG = {
    "ai_voice": {
        "name": "AI Voice",
        "base_price": 1490.0,
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
        "base_price": 2990.0,
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
        "base_price": 5990.0,
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

def calculate_period_price(base_price: float, months: int) -> Dict[str, Any]:
    """
    Рассчитать цену за период с учетом скидки
    
    Args:
        base_price: Базовая месячная цена
        months: Количество месяцев (1, 6, 12)
        
    Returns:
        Словарь с информацией о цене
    """
    if months not in PERIOD_DISCOUNTS:
        raise ValueError(f"Неподдерживаемый период: {months}. Допустимые: {list(PERIOD_DISCOUNTS.keys())}")
    
    discount_percent = PERIOD_DISCOUNTS[months]
    
    # Полная цена без скидки
    full_price = base_price * months
    
    # Цена со скидкой (округляем до 10 рублей)
    if discount_percent > 0:
        discounted = full_price * (1 - discount_percent / 100)
        # Округляем до 10 рублей
        final_price = round(discounted / 10) * 10
    else:
        final_price = full_price
    
    # Экономия
    savings = full_price - final_price
    
    # Эффективная месячная цена
    monthly_effective = round(final_price / months)
    
    # Количество дней
    if months == 1:
        days = 30
    elif months == 6:
        days = 180
    elif months == 12:
        days = 365
    else:
        days = months * 30
    
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
    """
    Получить конфигурацию тарифа
    
    Args:
        plan_code: Код тарифа (ai_voice, start, profi)
        
    Returns:
        Конфигурация тарифа
        
    Raises:
        ValueError: Если тариф не найден
    """
    if plan_code not in SUBSCRIPTION_PLANS_CONFIG:
        raise ValueError(
            f"Неизвестный тариф: {plan_code}. "
            f"Допустимые: {ALLOWED_PLAN_CODES}"
        )
    return SUBSCRIPTION_PLANS_CONFIG[plan_code]


def get_plan_price_info(plan_code: str, months: int) -> Dict[str, Any]:
    """
    Получить полную информацию о цене тарифа за период
    
    Args:
        plan_code: Код тарифа
        months: Количество месяцев
        
    Returns:
        Полная информация о цене
    """
    plan_config = get_plan_config(plan_code)
    price_info = calculate_period_price(plan_config["base_price"], months)
    
    return {
        "plan_code": plan_code,
        "plan_name": plan_config["name"],
        "max_assistants": plan_config["max_assistants"],
        "description": plan_config["description"],
        "features": plan_config["features"],
        **price_info
    }


def get_all_plans_with_prices() -> Dict[str, Any]:
    """
    Получить все тарифы со всеми вариантами цен
    
    Returns:
        Словарь со всеми тарифами и ценами
    """
    plans = {}
    
    for plan_code, config in SUBSCRIPTION_PLANS_CONFIG.items():
        periods = {}
        for months in PERIOD_DISCOUNTS.keys():
            price_info = calculate_period_price(config["base_price"], months)
            periods[months] = {
                "months": months,
                "days": price_info["days"],
                "price": price_info["final_price"],
                "price_formatted": f"{int(price_info['final_price']):,}".replace(",", " ") + " ₽",
                "discount_percent": price_info["discount_percent"],
                "savings": price_info["savings"],
                "savings_formatted": f"{int(price_info['savings']):,}".replace(",", " ") + " ₽" if price_info["savings"] > 0 else None,
                "monthly_effective": price_info["monthly_effective"],
                "monthly_formatted": f"{int(price_info['monthly_effective']):,}".replace(",", " ") + " ₽/мес",
                "label": price_info["label"]
            }
        
        plans[plan_code] = {
            "code": plan_code,
            "name": config["name"],
            "base_price": config["base_price"],
            "base_price_formatted": f"{int(config['base_price']):,}".replace(",", " ") + " ₽/мес",
            "max_assistants": config["max_assistants"],
            "description": config["description"],
            "features": config["features"],
            "restricted_features": config.get("restricted_features", []),
            "periods": periods
        }
    
    return {
        "plans": plans,
        "discounts": PERIOD_DISCOUNTS,
        "currency": "RUB"
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
async def get_subscription_plans():
    """
    Получить информацию о всех доступных тарифах
    
    ✅ ВЕРСИЯ 3.0: Возвращает все тарифы со всеми вариантами цен
    
    Returns:
        Словарь с тарифами, ценами и скидками
    """
    try:
        return get_all_plans_with_prices()
    except Exception as e:
        logger.error(f"❌ Error in get_subscription_plans: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get subscription plans"
        )


@router.get("/plans/{plan_code}", response_model=Dict[str, Any])
async def get_plan_details(plan_code: str):
    """
    Получить детали конкретного тарифа
    
    Args:
        plan_code: Код тарифа (ai_voice, start, profi)
        
    Returns:
        Детали тарифа со всеми вариантами цен
    """
    try:
        if plan_code not in SUBSCRIPTION_PLANS_CONFIG:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Тариф '{plan_code}' не найден. Доступные: {ALLOWED_PLAN_CODES}"
            )
        
        all_plans = get_all_plans_with_prices()
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
    Создание платежа для подписки
    
    ✅ ВЕРСИЯ 3.0: Поддержка тарифов ai_voice, start, profi
    
    Args:
        request_data: Данные запроса (plan_code, duration_months)
        current_user: Текущий пользователь
        db: Сессия базы данных
        
    Returns:
        Данные для перенаправления на оплату
    """
    try:
        plan_code = request_data.plan_code
        duration_months = request_data.duration_months
        
        logger.info(f"🚀 Creating payment for user {current_user.id}")
        logger.info(f"   Plan: {plan_code}")
        logger.info(f"   Duration: {duration_months} months")
        
        # Проверяем план
        if plan_code not in SUBSCRIPTION_PLANS_CONFIG:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Неизвестный тариф: {plan_code}. Доступные: {ALLOWED_PLAN_CODES}"
            )
        
        # Получаем информацию о цене
        price_info = get_plan_price_info(plan_code, duration_months)
        
        subscription_price = price_info["final_price"]
        subscription_days = price_info["days"]
        plan_name = price_info["plan_name"]
        max_assistants = price_info["max_assistants"]
        discount_percent = price_info["discount_percent"]
        savings = price_info["savings"]
        period_label = price_info["label"]
        
        logger.info(f"📋 Payment settings:")
        logger.info(f"   Plan: {plan_name} ({plan_code})")
        logger.info(f"   Period: {period_label}")
        logger.info(f"   Price: {subscription_price} руб")
        logger.info(f"   Days: {subscription_days}")
        logger.info(f"   Max assistants: {max_assistants}")
        logger.info(f"   Discount: {discount_percent}%")
        logger.info(f"   Savings: {savings} руб")
        
        # Проверка настроек Robokassa
        if not settings.ROBOKASSA_MERCHANT_LOGIN:
            logger.error("❌ ROBOKASSA_MERCHANT_LOGIN is not configured")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Payment system not configured: missing merchant login. Contact administrator."
            )
            
        if not settings.ROBOKASSA_PASSWORD_1:
            logger.error("❌ ROBOKASSA_PASSWORD_1 is not configured")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Payment system not configured: missing password. Contact administrator."
            )
        
        # Получаем пользователя
        user = db.query(User).filter(User.id == current_user.id).first()
        if not user:
            logger.error(f"❌ User {current_user.id} not found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        # Получаем или создаем план подписки в БД
        plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.code == plan_code).first()
        if not plan:
            logger.info(f"📋 Creating subscription plan in DB: {plan_code}")
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
            logger.info(f"✅ Created subscription plan: {plan_code}")
        
        # Формируем параметры платежа
        out_sum = f"{subscription_price:.2f}"
        inv_id = f"{int(datetime.now().timestamp())}"
        
        # Формируем описание
        if duration_months == 1:
            description = f"{plan_name} на {subscription_days} дней за {subscription_price:.0f} руб"
        else:
            description = f"{plan_name} на {period_label} за {subscription_price:.0f} руб (скидка {discount_percent}%)"
        
        logger.info(f"💳 PAYMENT PARAMETERS:")
        logger.info(f"   out_sum: '{out_sum}'")
        logger.info(f"   inv_id: '{inv_id}'")
        logger.info(f"   description: '{description}'")
        
        # Создаем запись транзакции
        payment_details = (
            f"Plan: {plan_code} ({plan_name}), "
            f"Duration: {duration_months} months ({subscription_days} days), "
            f"Price: {subscription_price}, "
            f"Discount: {discount_percent}%, "
            f"Savings: {savings}, "
            f"Max assistants: {max_assistants}"
        )
        
        transaction = PaymentTransaction(
            user_id=user.id,
            plan_id=plan.id,
            external_payment_id=inv_id,
            payment_system="robokassa",
            amount=subscription_price,
            currency="RUB",
            status="pending",
            payment_details=payment_details
        )
        db.add(transaction)
        db.commit()
        db.refresh(transaction)
        
        logger.info(f"📋 Created payment transaction: {transaction.id}")
        
        # Дополнительные параметры для callback
        custom_params = None
        if not RobokassaService.DISABLE_SHP_PARAMS:
            custom_params = {
                "Shp_duration": str(duration_months),
                "Shp_plan_code": plan_code,
                "Shp_user_id": str(current_user.id)
            }
            logger.info(f"✅ Using Shp_ parameters: {custom_params}")
        else:
            logger.info(f"🔧 DIAGNOSTIC MODE: Shp_ parameters disabled")
        
        # Генерируем подпись
        logger.info(f"🔐 Generating signature with PASSWORD_1...")
        signature = RobokassaService.generate_signature(
            RobokassaService.MERCHANT_LOGIN,
            out_sum,
            inv_id,
            RobokassaService.PASSWORD_1,
            custom_params
        )
        
        # Базовые параметры формы
        form_params = {
            "MerchantLogin": RobokassaService.MERCHANT_LOGIN,
            "OutSum": out_sum,
            "InvId": inv_id,
            "Description": description,
            "SignatureValue": signature,
            "Culture": "ru",
            "Encoding": "utf-8"
        }
        
        # Добавляем URL'ы только для публичных доменов
        if RobokassaService.BASE_URL and not any(x in RobokassaService.BASE_URL for x in ["localhost", "127.0.0.1"]):
            form_params["ResultURL"] = RobokassaService.RESULT_URL
            form_params["SuccessURL"] = RobokassaService.SUCCESS_URL  
            form_params["FailURL"] = RobokassaService.FAIL_URL
            logger.info(f"✅ Added callback URLs")
        else:
            logger.warning(f"⚠️ Skipping callback URLs due to localhost")
        
        # Добавляем email пользователя
        if user.email:
            form_params["Email"] = user.email
        
        # Добавляем тестовый режим
        if RobokassaService.TEST_MODE:
            form_params["IsTest"] = "1"
            logger.info("🧪 Test mode enabled")
        
        # Добавляем пользовательские параметры
        if custom_params and not RobokassaService.DISABLE_SHP_PARAMS:
            for key, value in custom_params.items():
                form_params[key] = value
        
        logger.info(f"📋 FINAL FORM PARAMETERS:")
        for key, value in form_params.items():
            logger.info(f"   {key}: '{value}'")
        
        logger.info(f"✅ Payment created: {plan_name}, {subscription_price} руб for {subscription_days} days")
        
        # Логируем событие
        await SubscriptionService.log_subscription_event(
            db=db,
            user_id=str(current_user.id),
            action="payment_started",
            plan_id=str(plan.id),
            plan_code=plan_code,
            details=f"Payment initiated: {plan_name}, {period_label}, price={subscription_price}, days={subscription_days}, inv_id={inv_id}"
        )
        
        return {
            "payment_url": RobokassaService.PAYMENT_URL,
            "form_params": form_params,
            "inv_id": inv_id,
            "amount": out_sum,
            "transaction_id": str(transaction.id),
            # Полная информация о тарифе и периоде
            "plan_info": {
                "code": plan_code,
                "name": plan_name,
                "max_assistants": max_assistants
            },
            "period_info": {
                "months": duration_months,
                "days": subscription_days,
                "price": subscription_price,
                "discount_percent": discount_percent,
                "savings": savings,
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
async def get_subscription_periods(plan_code: str = "ai_voice"):
    """
    Получить информацию о доступных периодах подписки для конкретного тарифа
    
    ✅ ВЕРСИЯ 3.0: Принимает plan_code как параметр
    
    Args:
        plan_code: Код тарифа (по умолчанию ai_voice)
    
    Returns:
        Список периодов с ценами и скидками
    """
    try:
        if plan_code not in SUBSCRIPTION_PLANS_CONFIG:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Неизвестный тариф: {plan_code}. Доступные: {ALLOWED_PLAN_CODES}"
            )
        
        plan_config = SUBSCRIPTION_PLANS_CONFIG[plan_code]
        base_price = plan_config["base_price"]
        
        periods = []
        for months in PERIOD_DISCOUNTS.keys():
            price_info = calculate_period_price(base_price, months)
            periods.append({
                "months": months,
                "days": price_info["days"],
                "price": price_info["final_price"],
                "price_formatted": f"{int(price_info['final_price']):,}".replace(",", " ") + " ₽",
                "discount_percent": price_info["discount_percent"],
                "savings": price_info["savings"],
                "savings_formatted": f"{int(price_info['savings']):,}".replace(",", " ") + " ₽" if price_info["savings"] > 0 else None,
                "label": price_info["label"],
                "description": f"{'Со скидкой ' + str(price_info['discount_percent']) + '%' if price_info['discount_percent'] > 0 else 'Без скидки'}",
                "monthly_price": price_info["monthly_effective"],
                "monthly_price_formatted": f"{int(price_info['monthly_effective']):,}".replace(",", " ") + " ₽/мес"
            })
        
        return {
            "plan_code": plan_code,
            "plan_name": plan_config["name"],
            "base_monthly_price": base_price,
            "periods": periods,
            "currency": "RUB"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error in get_subscription_periods: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get subscription periods"
        )


@router.post("/robokassa-result")
async def robokassa_result(
    request: Request,
    db: Session = Depends(get_db),
    OutSum: str = Form(...),
    InvId: str = Form(...),
    SignatureValue: str = Form(...),
    Shp_user_id: Optional[str] = Form(None),
    Shp_plan_code: Optional[str] = Form(None),
    Shp_duration: Optional[str] = Form(None)
):
    """
    Обработка уведомления о результате платежа от Robokassa (ResultURL)
    
    ✅ ВЕРСИЯ 3.0: Поддержка разных тарифов через Shp_plan_code
    """
    try:
        # Собираем все данные формы
        form_data = await request.form()
        form_dict = dict(form_data)
        
        logger.info(f"📥 Received Robokassa result notification:")
        logger.info(f"   OutSum: {OutSum}")
        logger.info(f"   InvId: {InvId}")
        logger.info(f"   SignatureValue: {SignatureValue[:10]}...")
        logger.info(f"   Shp_user_id: {Shp_user_id}")
        logger.info(f"   Shp_plan_code: {Shp_plan_code}")
        logger.info(f"   Shp_duration: {Shp_duration}")
        logger.info(f"   All form data: {form_dict}")
        
        # Обрабатываем результат платежа
        result = await RobokassaService.process_payment_result(db, form_dict)
        
        logger.info(f"✅ Payment result processed: {result}")
        
        # Возвращаем ответ Robokassa
        return HTMLResponse(content=result, status_code=200)
        
    except Exception as e:
        logger.error(f"❌ Error in robokassa_result endpoint: {str(e)}", exc_info=True)
        return HTMLResponse(content="FAIL", status_code=200)


@router.get("/success", response_class=HTMLResponse)
@router.post("/success", response_class=HTMLResponse)
async def payment_success(
    request: Request,
    OutSum: Optional[str] = None,
    InvId: Optional[str] = None,
    SignatureValue: Optional[str] = None
):
    """
    Страница успешной оплаты (SuccessURL)
    """
    try:
        # Получаем параметры из GET или POST
        if request.method == "POST":
            try:
                form_data = await request.form()
                OutSum = form_data.get("OutSum", OutSum)
                InvId = form_data.get("InvId", InvId)
                SignatureValue = form_data.get("SignatureValue", SignatureValue)
            except Exception as form_error:
                logger.warning(f"⚠️ Could not parse form data: {form_error}")
        
        logger.info(f"🎉 User redirected to success page:")
        logger.info(f"   Method: {request.method}")
        logger.info(f"   InvId: {InvId}")
        logger.info(f"   OutSum: {OutSum}")
        
        # Получаем данные для отображения
        status_data = RobokassaService.get_payment_status_message(success=True)
        
        # Возвращаем HTML страницу с результатом
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


@router.get("/cancel", response_class=HTMLResponse)
@router.post("/cancel", response_class=HTMLResponse) 
async def payment_cancel(
    request: Request,
    OutSum: Optional[str] = None,
    InvId: Optional[str] = None
):
    """
    Страница отмены оплаты (FailURL)
    """
    try:
        # Получаем параметры из GET или POST
        if request.method == "POST":
            try:
                form_data = await request.form()
                OutSum = form_data.get("OutSum", OutSum)
                InvId = form_data.get("InvId", InvId)
            except Exception as form_error:
                logger.warning(f"⚠️ Could not parse form data in cancel: {form_error}")
        
        logger.info(f"❌ User redirected to cancel page:")
        logger.info(f"   Method: {request.method}")
        logger.info(f"   InvId: {InvId}")
        logger.info(f"   OutSum: {OutSum}")
        
        # Получаем данные для отображения
        status_data = RobokassaService.get_payment_status_message(success=False)
        
        # Возвращаем HTML страницу с результатом
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
                    background: linear-gradient(135deg, #ffeaa7 0%, #fab1a0 100%);
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
                    color: #f97316;
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
                    background: linear-gradient(135deg, #f97316 0%, #ea580c 100%);
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
                <div class="icon">⚠️</div>
                <h1 class="title">{status_data['title']}</h1>
                <p class="message">{status_data['message']}</p>
                <a href="{status_data['redirect_url']}" class="button">Вернуться в панель управления</a>
            </div>
            <script>
                // Автоматическое перенаправление через 10 секунд
                setTimeout(() => {{
                    window.location.href = "{status_data['redirect_url']}";
                }}, 10000);
            </script>
        </body>
        </html>
        """
        
        return HTMLResponse(content=html_content)
        
    except Exception as e:
        logger.error(f"❌ Error in payment_cancel endpoint: {str(e)}", exc_info=True)
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
# ДИАГНОСТИЧЕСКИЕ ЭНДПОИНТЫ
# =============================================================================

@router.get("/debug-prices")
async def debug_subscription_prices(db: Session = Depends(get_db)):
    """
    🔍 ДИАГНОСТИЧЕСКИЙ endpoint для проверки цен подписок
    """
    try:
        plans = db.query(SubscriptionPlan).all()
        
        result = {
            "config_plans": {},
            "database_plans": {},
            "calculated_prices": {}
        }
        
        # Конфигурация из кода
        for plan_code, config in SUBSCRIPTION_PLANS_CONFIG.items():
            result["config_plans"][plan_code] = {
                "name": config["name"],
                "base_price": config["base_price"],
                "max_assistants": config["max_assistants"]
            }
            
            # Рассчитанные цены
            result["calculated_prices"][plan_code] = {}
            for months in PERIOD_DISCOUNTS.keys():
                price_info = calculate_period_price(config["base_price"], months)
                result["calculated_prices"][plan_code][f"{months}_months"] = {
                    "price": price_info["final_price"],
                    "discount": price_info["discount_percent"],
                    "savings": price_info["savings"]
                }
        
        # Информация из БД
        for plan in plans:
            result["database_plans"][plan.code] = {
                "name": plan.name,
                "price": float(plan.price),
                "max_assistants": plan.max_assistants,
                "is_active": plan.is_active
            }
        
        logger.info(f"🔍 Debug prices requested")
        return result
        
    except Exception as e:
        logger.error(f"❌ Error in debug_subscription_prices: {str(e)}")
        return {"error": str(e)}


@router.get("/config-check")
async def check_robokassa_config():
    """
    🔍 ДИАГНОСТИЧЕСКИЙ endpoint для проверки конфигурации Robokassa
    """
    try:
        config_check = RobokassaService.validate_configuration()
        
        logger.info(f"🔍 Configuration check requested")
        
        return {
            "status": "ok" if config_check["valid"] else "error",
            "valid": config_check["valid"],
            "issues": config_check["issues"],
            "warnings": config_check["warnings"],
            "available_plans": ALLOWED_PLAN_CODES,
            "period_discounts": PERIOD_DISCOUNTS,
            "config": {
                "merchant_login": config_check["config"]["merchant_login"],
                "merchant_login_length": config_check["config"]["merchant_login_length"],
                "password1_length": config_check["config"]["password1_length"],
                "password2_length": config_check["config"]["password2_length"],
                "base_url": config_check["config"]["base_url"],
                "test_mode": config_check["config"]["test_mode"],
                "disable_shp_params": config_check["config"]["disable_shp_params"]
            }
        }
        
    except Exception as e:
        logger.error(f"❌ Error checking configuration: {str(e)}")
        return {"status": "error", "error": str(e)}


@router.post("/test-signature")
async def test_signature_generation(request: dict = Body(...)):
    """
    🔧 ДИАГНОСТИЧЕСКИЙ endpoint для тестирования генерации подписи
    """
    try:
        plan_code = request.get("plan_code", "ai_voice")
        duration_months = request.get("duration_months", 1)
        
        if plan_code not in SUBSCRIPTION_PLANS_CONFIG:
            return {"status": "error", "error": f"Unknown plan: {plan_code}"}
        
        price_info = get_plan_price_info(plan_code, duration_months)
        
        merchant_login = request.get("merchant_login", RobokassaService.MERCHANT_LOGIN)
        out_sum = request.get("out_sum", f"{price_info['final_price']:.2f}")
        inv_id = request.get("inv_id", "123456789")
        password = request.get("password", RobokassaService.PASSWORD_1)
        
        custom_params = {
            "Shp_duration": str(duration_months),
            "Shp_plan_code": plan_code,
            "Shp_user_id": "test"
        }
        
        signature = RobokassaService.generate_signature(
            merchant_login=merchant_login,
            out_sum=out_sum,
            inv_id=inv_id,
            password=password,
            custom_params=custom_params
        )
        
        sign_string = f"{merchant_login}:{out_sum}:{inv_id}:{password}"
        if custom_params and not RobokassaService.DISABLE_SHP_PARAMS:
            sorted_params = sorted(custom_params.items())
            for key, value in sorted_params:
                sign_string += f":{key}={value}"
        
        return {
            "status": "ok",
            "signature": signature,
            "sign_string": sign_string,
            "plan_info": price_info,
            "parameters": {
                "merchant_login": merchant_login,
                "out_sum": out_sum,
                "inv_id": inv_id,
                "plan_code": plan_code,
                "duration_months": duration_months,
                "custom_params": custom_params
            }
        }
        
    except Exception as e:
        logger.error(f"❌ Error testing signature: {str(e)}")
        return {"status": "error", "error": str(e)}


@router.post("/enable-diagnostic-mode")
async def enable_diagnostic_mode():
    """
    🔧 Включение диагностического режима (без Shp_ параметров)
    """
    try:
        RobokassaService.DISABLE_SHP_PARAMS = True
        logger.info(f"🔧 Diagnostic mode enabled")
        return {"status": "ok", "message": "Diagnostic mode enabled", "disable_shp_params": True}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.post("/disable-diagnostic-mode")
async def disable_diagnostic_mode():
    """
    🔧 Выключение диагностического режима
    """
    try:
        RobokassaService.DISABLE_SHP_PARAMS = False
        logger.info(f"🔧 Diagnostic mode disabled")
        return {"status": "ok", "message": "Diagnostic mode disabled", "disable_shp_params": False}
    except Exception as e:
        return {"status": "error", "error": str(e)}
