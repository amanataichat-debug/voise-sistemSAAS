"""
Admin API endpoints for WellcomeAI application.
✅ v2.0: Добавлена поддержка Gemini и Grok ассистентов
✅ v2.1: Добавлена статистика доходов и звонков
✅ v2.2: Исправлен эндпоинт смены тарифа (Body вместо Query)
"""

from fastapi import APIRouter, Depends, HTTPException, status, Path, Query
from sqlalchemy.orm import Session
from sqlalchemy import func as sql_func, case
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
import uuid
from datetime import datetime, timedelta, timezone
from calendar import monthrange

from backend.core.logging import get_logger
from backend.core.dependencies import get_current_user, check_admin_access
from backend.db.session import get_db
from backend.models.user import User
from backend.models.assistant import AssistantConfig
from backend.models.gemini_assistant import GeminiAssistantConfig
from backend.models.grok_assistant import GrokAssistantConfig
from backend.models.conversation import Conversation
from backend.models.subscription import PaymentTransaction
from backend.models.credit_transaction import CreditTransaction, CreditTransactionType
from backend.models.agent_config import AgentConfig
from backend.models.agent_contact import AgentContact
from backend.models.agent_call import AgentCall
from backend.services.user_service import UserService
from backend.services.subscription_service import SubscriptionService

# Московская таймзона для границ "дня" в аналитике кредитов (UTC+3, без DST)
MSK = timezone(timedelta(hours=3))


def _msk_day_start_utc(now: Optional[datetime] = None) -> datetime:
    """Начало текущего московского дня в UTC (aware)."""
    now_msk = (now or datetime.now(timezone.utc)).astimezone(MSK)
    return now_msk.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)

# Initialize logger
logger = get_logger(__name__)

# Create router
router = APIRouter()


# ============================================================================
# ТАРИФНАЯ СЕТКА ДЛЯ АДМИНКИ
# ============================================================================
# Платные тарифы берём из SUBSCRIPTION_PLANS_CONFIG (backend/api/payments.py) —
# это единственный источник цен и лимитов, по которому реально выставляются
# счета. Здесь добавляем то, чего в оплате нет: бесплатные тарифы и legacy
# `agent`. Так админка не разъезжается с прайсом на лендинге.

# Бесплатные и legacy-тарифы — их нельзя купить, но админ может назначить.
NON_PAYABLE_PLANS = {
    "free": {
        "name": "Бесплатный",
        "price": 0.0,
        "max_assistants": 1,
        "description": "Базовый доступ: 1 ассистент, без телефонии и CRM",
        "is_trial_default": True,
    },
    "referral_trial": {
        "name": "Реферальный триал",
        "price": 0.0,
        "max_assistants": 3,
        "description": "Расширенный пробный период для приглашённых пользователей",
        "is_trial_default": True,
    },
    "agent": {
        "name": "Агент (legacy)",
        "price": 4990.0,
        "max_assistants": 10,
        "description": "Старый тариф автономного агента, оставлен для совместимости",
        "is_trial_default": False,
    },
}

# Тарифы, дающие доступ к агенту-оркестратору.
# Зеркало User.is_profi_plan() / User._legacy_agent_plan_active() — меняешь там,
# поправь и здесь, иначе админка будет обещать доступ, которого нет.
AGENT_ACCESS_PLAN_CODES = {"profi", "agent"}

# Порядок отображения в выпадающем списке админки (от младшего к старшему).
PLAN_DISPLAY_ORDER = ["free", "referral_trial", "ai_voice", "start", "profi", "agent"]


def get_admin_plan_grid() -> Dict[str, Dict[str, Any]]:
    """Полная тарифная сетка, доступная админу для назначения."""
    from backend.api.payments import SUBSCRIPTION_PLANS_CONFIG

    grid: Dict[str, Dict[str, Any]] = {}

    for code, cfg in SUBSCRIPTION_PLANS_CONFIG.items():
        grid[code] = {
            "code": code,
            "name": cfg["name"],
            "price": float(cfg["base_price"]),
            "max_assistants": cfg["max_assistants"],
            "description": cfg["description"],
            "is_trial_default": False,
            "payable": True,
        }

    for code, cfg in NON_PAYABLE_PLANS.items():
        grid[code] = {
            "code": code,
            "name": cfg["name"],
            "price": cfg["price"],
            "max_assistants": cfg["max_assistants"],
            "description": cfg["description"],
            "is_trial_default": cfg["is_trial_default"],
            "payable": False,
        }

    for code, entry in grid.items():
        entry["grants_agent_access"] = code in AGENT_ACCESS_PLAN_CODES

    return grid


def _sync_plan_row(db: Session, code: str, entry: Dict[str, Any]):
    """
    Найти план в subscription_plans, создав или починив его по тарифной сетке.

    Лимит ассистентов пользователя берётся из этой строки БД
    (UserService.check_subscription_status), поэтому расхождение с сеткой —
    это не косметика: назначив «Старт», пользователь получил бы старый лимит.
    """
    from backend.models.subscription import SubscriptionPlan

    plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.code == code).first()

    if not plan:
        plan = SubscriptionPlan(
            code=code,
            name=entry["name"],
            price=entry["price"],
            max_assistants=entry["max_assistants"],
            description=entry["description"],
            is_active=True,
        )
        db.add(plan)
        db.flush()
        logger.info(f"[ADMIN] Created subscription plan '{code}' from tariff grid")
        return plan

    changes = []
    if plan.max_assistants != entry["max_assistants"]:
        changes.append(f"max_assistants {plan.max_assistants} -> {entry['max_assistants']}")
        plan.max_assistants = entry["max_assistants"]
    if float(plan.price or 0) != entry["price"]:
        changes.append(f"price {plan.price} -> {entry['price']}")
        plan.price = entry["price"]
    if plan.name != entry["name"]:
        changes.append(f"name '{plan.name}' -> '{entry['name']}'")
        plan.name = entry["name"]
    if changes:
        db.flush()
        logger.info(f"[ADMIN] Synced plan '{code}' with tariff grid: {', '.join(changes)}")

    return plan


# ✅ v2.2: Pydantic модель для обновления подписки
class SubscriptionUpdateRequest(BaseModel):
    """Модель запроса на обновление подписки"""
    plan_code: str
    duration_days: int = 30
    # None — взять значение по умолчанию для выбранного тарифа
    # (бесплатные считаются пробными, платные — нет).
    is_trial: Optional[bool] = None
    # extend — продлить от текущей даты окончания (если подписка ещё активна),
    # reset — отсчитывать срок с сегодняшнего дня.
    mode: str = "extend"


class AdminPasswordResetRequest(BaseModel):
    new_password: str


@router.get("/users", response_model=List[Dict[str, Any]])
async def get_all_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    search: Optional[str] = None,
    subscription_status: Optional[str] = None,
    current_user: User = Depends(check_admin_access),
    db: Session = Depends(get_db)
):
    """
    Get all users with pagination and filtering options.
    Admin only endpoint.
    
    ✅ v2.0: Теперь подсчитывает все типы ассистентов (OpenAI + Gemini + Grok)
    """
    try:
        query = db.query(User)
        
        # Apply search filter
        if search:
            search_term = f"%{search}%"
            query = query.filter(
                (User.email.ilike(search_term)) |
                (User.first_name.ilike(search_term)) |
                (User.last_name.ilike(search_term)) |
                (User.company_name.ilike(search_term))
            )
        
        # Apply subscription status filter
        if subscription_status:
            now = datetime.now(timezone.utc)
            if subscription_status == "active":
                query = query.filter(User.subscription_end_date > now)
            elif subscription_status == "expired":
                query = query.filter(
                    (User.subscription_end_date < now) | 
                    (User.subscription_end_date.is_(None))
                )
            elif subscription_status == "trial":
                query = query.filter(User.is_trial.is_(True))
        
        # Get total count before pagination
        total_count = query.count()
                
        # Apply pagination
        users = query.order_by(User.created_at.desc()).offset(skip).limit(limit).all()
        
        # Prepare result with subscription info
        result = []
        for user in users:
            # ✅ v2.0: Подсчёт всех типов ассистентов
            openai_count = db.query(AssistantConfig).filter(
                AssistantConfig.user_id == user.id
            ).count()
            
            gemini_count = db.query(GeminiAssistantConfig).filter(
                GeminiAssistantConfig.user_id == user.id
            ).count()
            
            grok_count = db.query(GrokAssistantConfig).filter(
                GrokAssistantConfig.user_id == user.id
            ).count()
            
            total_assistants = openai_count + gemini_count + grok_count
            
            # Check subscription status
            subscription_status_info = await UserService.check_subscription_status(db, str(user.id))
            
            # Format user data
            user_data = {
                "id": str(user.id),
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "company_name": user.company_name,
                "created_at": user.created_at,
                "last_login": user.last_login,
                "is_active": user.is_active,
                "is_admin": user.is_admin,
                "is_trial": user.is_trial,
                "subscription_plan": user.subscription_plan,
                "subscription_start_date": user.subscription_start_date,
                "subscription_end_date": user.subscription_end_date,
                "subscription_active": subscription_status_info["active"],
                "days_left": subscription_status_info.get("days_left", 0),
                # ✅ v2.0: Детализация по типам ассистентов
                "assistant_count": total_assistants,
                "openai_assistants": openai_count,
                "gemini_assistants": gemini_count,
                "grok_assistants": grok_count
            }
            
            result.append(user_data)
        
        return result
    except Exception as e:
        logger.error(f"Error in get_all_users: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve users"
        )


@router.get("/users/{user_id}", response_model=Dict[str, Any])
async def get_user_details(
    user_id: str = Path(..., description="User ID"),
    current_user: User = Depends(check_admin_access),
    db: Session = Depends(get_db)
):
    """
    Get detailed information about a specific user.
    Admin only endpoint.
    
    ✅ v2.0: Теперь возвращает все типы ассистентов (OpenAI + Gemini + Grok)
    """
    try:
        # Get user with validation
        user = await UserService.get_user_by_id(db, user_id)
        
        # Get subscription details
        subscription_status = await UserService.check_subscription_status(db, user_id)
        
        # ✅ v2.0: Получаем ВСЕ типы ассистентов
        
        # OpenAI ассистенты
        openai_assistants = db.query(AssistantConfig).filter(
            AssistantConfig.user_id == user.id
        ).all()
        
        openai_data = []
        for assistant in openai_assistants:
            openai_data.append({
                "id": str(assistant.id),
                "name": assistant.name,
                "description": assistant.description,
                "type": "openai",
                "voice": assistant.voice,
                "created_at": assistant.created_at,
                "total_conversations": assistant.total_conversations
            })
        
        # Gemini ассистенты
        gemini_assistants = db.query(GeminiAssistantConfig).filter(
            GeminiAssistantConfig.user_id == user.id
        ).all()
        
        gemini_data = []
        for assistant in gemini_assistants:
            gemini_data.append({
                "id": str(assistant.id),
                "name": assistant.name,
                "description": assistant.description,
                "type": "gemini",
                "voice": assistant.voice,
                "created_at": assistant.created_at,
                "total_conversations": assistant.total_conversations
            })
        
        # Grok ассистенты
        grok_assistants = db.query(GrokAssistantConfig).filter(
            GrokAssistantConfig.user_id == user.id
        ).all()
        
        grok_data = []
        for assistant in grok_assistants:
            grok_data.append({
                "id": str(assistant.id),
                "name": assistant.name,
                "description": assistant.description,
                "type": "grok",
                "voice": assistant.voice,
                "created_at": assistant.created_at,
                "total_conversations": assistant.total_conversations
            })
        
        # Объединяем все ассистенты
        all_assistants = openai_data + gemini_data + grok_data
        # Сортируем по дате создания (новые первыми)
        all_assistants.sort(key=lambda x: x["created_at"] or datetime.min, reverse=True)
        
        # Subscription logs
        from backend.models.subscription import SubscriptionLog
        logs = db.query(SubscriptionLog).filter(
            SubscriptionLog.user_id == user.id
        ).order_by(SubscriptionLog.created_at.desc()).limit(20).all()
        
        subscription_logs = []
        for log in logs:
            subscription_logs.append({
                "id": str(log.id),
                "action": log.action,
                "plan_code": log.plan_code,
                "details": log.details,
                "created_at": log.created_at
            })
        
        # Return combined user details
        return {
            "user": {
                "id": str(user.id),
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "company_name": user.company_name,
                "created_at": user.created_at,
                "last_login": user.last_login,
                "is_active": user.is_active,
                "is_admin": user.is_admin,
                "is_trial": user.is_trial,
                "usage_tokens": user.usage_tokens,
                "subscription_plan": user.subscription_plan
            },
            "subscription": {
                "plan": user.subscription_plan,
                "plan_id": str(user.subscription_plan_id) if user.subscription_plan_id else None,
                "start_date": user.subscription_start_date,
                "end_date": user.subscription_end_date,
                "is_active": subscription_status["active"],
                "max_assistants": subscription_status.get("max_assistants", 1),
                "days_left": subscription_status.get("days_left", 0)
            },
            "assistants": {
                "count": len(all_assistants),
                "openai_count": len(openai_data),
                "gemini_count": len(gemini_data),
                "grok_count": len(grok_data),
                "list": all_assistants
            },
            "subscription_logs": subscription_logs
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in get_user_details: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve user details"
        )


@router.get("/plans", response_model=List[Dict[str, Any]])
async def get_admin_plans(
    current_user: User = Depends(check_admin_access),
    db: Session = Depends(get_db)
):
    """
    Тарифная сетка для админки: что именно можно назначить пользователю.

    Отдаёт цену, лимит ассистентов и признак доступа к агенту-оркестратору по
    каждому тарифу, плюс `in_db_mismatch` — расходится ли строка в
    subscription_plans с сеткой (при назначении она будет починена).
    """
    from backend.models.subscription import SubscriptionPlan

    grid = get_admin_plan_grid()
    db_plans = {p.code: p for p in db.query(SubscriptionPlan).all()}

    result = []
    for code in PLAN_DISPLAY_ORDER:
        entry = grid.get(code)
        if not entry:
            continue
        row = db_plans.get(code)
        result.append({
            **entry,
            "in_db": row is not None,
            "in_db_mismatch": bool(
                row is not None and (
                    row.max_assistants != entry["max_assistants"]
                    or float(row.price or 0) != entry["price"]
                )
            ),
        })

    # Тарифы, которых нет в сетке, но которые уже проставлены кому-то в БД
    # (например мусорный `pro` из старой админки) — показываем, чтобы админ их
    # видел и мог переназначить человека на нормальный тариф.
    for code, row in db_plans.items():
        if code in grid:
            continue
        result.append({
            "code": code,
            "name": f"{row.name} (нет в сетке)",
            "price": float(row.price or 0),
            "max_assistants": row.max_assistants,
            "description": "Устаревший тариф из БД — назначать не следует",
            "is_trial_default": False,
            "payable": False,
            "grants_agent_access": False,
            "in_db": True,
            "in_db_mismatch": False,
            "deprecated": True,
        })

    return result


@router.post("/users/{user_id}/subscription", response_model=Dict[str, Any])
async def update_user_subscription(
    user_id: str = Path(..., description="User ID"),
    request: SubscriptionUpdateRequest = ...,  # ✅ v2.2: Body вместо Query
    current_user: User = Depends(check_admin_access),
    db: Session = Depends(get_db)
):
    """
    Сменить тариф пользователя. Только для админа.

    ✅ v2.2: принимает JSON body вместо query параметров
    ✅ v2.3: plan_code проверяется по тарифной сетке, строка в subscription_plans
             синхронизируется с ней (иначе лимит ассистентов был бы взят из
             устаревшей записи), добавлен режим extend/reset
    """
    try:
        # Get user with validation
        user = await UserService.get_user_by_id(db, user_id)

        grid = get_admin_plan_grid()
        entry = grid.get(request.plan_code)
        if not entry:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Неизвестный тариф '{request.plan_code}'. "
                    f"Допустимые: {', '.join(PLAN_DISPLAY_ORDER)}"
                ),
            )

        if not 1 <= request.duration_days <= 3650:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="duration_days должен быть от 1 до 3650",
            )

        if request.mode not in ("extend", "reset"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="mode должен быть 'extend' или 'reset'",
            )

        # План в БД создаём или чиним по сетке — из него берётся лимит ассистентов.
        plan = _sync_plan_row(db, request.plan_code, entry)

        # extend — продлеваем от текущей даты окончания, reset — считаем с сегодня.
        now = datetime.now(timezone.utc)
        start_date = now
        if request.mode == "extend":
            current_end = user.subscription_end_date
            if current_end and current_end.tzinfo is None:
                current_end = current_end.replace(tzinfo=timezone.utc)
            if current_end and current_end > now:
                start_date = current_end

        is_trial = (
            entry["is_trial_default"] if request.is_trial is None else request.is_trial
        )

        previous_plan = user.subscription_plan or "—"

        # Update user subscription
        user.subscription_plan = request.plan_code  # ✅ Обновляем и текстовое поле
        user.subscription_plan_id = plan.id
        user.subscription_start_date = start_date
        user.subscription_end_date = start_date + timedelta(days=request.duration_days)
        user.is_trial = is_trial

        db.commit()

        # Log subscription event
        await SubscriptionService.log_subscription_event(
            db=db,
            user_id=str(user.id),
            action="admin_update",
            plan_id=str(plan.id),
            plan_code=request.plan_code,
            details=(
                f"Admin {current_user.email} changed plan {previous_plan} -> "
                f"{request.plan_code} ({request.mode}) for {request.duration_days} days "
                f"until {user.subscription_end_date.strftime('%Y-%m-%d')}, "
                f"trial={is_trial}"
            )
        )

        # Return updated subscription info
        db.refresh(user)
        subscription_status = await UserService.check_subscription_status(db, user_id)

        return {
            "success": True,
            "message": f"Тариф пользователя {user.email} изменён на «{entry['name']}»",
            "subscription": {
                "plan": request.plan_code,
                "plan_name": entry["name"],
                "plan_id": str(plan.id),
                "previous_plan": previous_plan,
                "start_date": user.subscription_start_date,
                "end_date": user.subscription_end_date,
                "is_trial": user.is_trial,
                "is_active": subscription_status["active"],
                "days_left": subscription_status.get("days_left", 0),
                "max_assistants": subscription_status.get("max_assistants"),
                # Тариф даёт доступ к агенту, но ручная блокировка его перекрывает —
                # поэтому отдаём и фактический доступ, чтобы админ не гадал.
                "grants_agent_access": entry["grants_agent_access"],
                "agent_access_effective": user.has_agent_access(),
                "agent_subscription_blocked": bool(user.agent_subscription_blocked),
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error in update_user_subscription: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update user subscription: {str(e)}"
        )


@router.get("/stats", response_model=Dict[str, Any])
async def get_admin_statistics(
    current_user: User = Depends(check_admin_access),
    db: Session = Depends(get_db)
):
    """
    Get system-wide statistics for admin dashboard.
    Admin only endpoint.
    
    ✅ v2.0: Добавлена статистика по всем типам ассистентов
    ✅ v2.1: Добавлена статистика доходов и звонков
    """
    try:
        now = datetime.now(timezone.utc)
        
        # ========== ПОЛЬЗОВАТЕЛИ ==========
        total_users = db.query(User).count()
        active_users = db.query(User).filter(User.is_active.is_(True)).count()
        
        users_with_active_subscription = db.query(User).filter(
            User.subscription_end_date > now
        ).count()
        
        trial_users = db.query(User).filter(User.is_trial.is_(True)).count()
        
        # Недавние пользователи
        recent_users = db.query(User).order_by(
            User.created_at.desc()
        ).limit(5).all()
        
        recent_user_data = []
        for user in recent_users:
            recent_user_data.append({
                "id": str(user.id),
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "created_at": user.created_at,
                "is_trial": user.is_trial
            })
        
        # ========== АССИСТЕНТЫ (все типы) ==========
        openai_assistants = db.query(AssistantConfig).count()
        gemini_assistants = db.query(GeminiAssistantConfig).count()
        grok_assistants = db.query(GrokAssistantConfig).count()
        total_assistants = openai_assistants + gemini_assistants + grok_assistants
        
        # Статистика ассистентов по пользователям
        # OpenAI
        openai_stats = db.query(
            AssistantConfig.user_id,
            sql_func.count(AssistantConfig.id).label("count")
        ).group_by(AssistantConfig.user_id).all()
        
        # Gemini
        gemini_stats = db.query(
            GeminiAssistantConfig.user_id,
            sql_func.count(GeminiAssistantConfig.id).label("count")
        ).group_by(GeminiAssistantConfig.user_id).all()
        
        # Grok
        grok_stats = db.query(
            GrokAssistantConfig.user_id,
            sql_func.count(GrokAssistantConfig.id).label("count")
        ).group_by(GrokAssistantConfig.user_id).all()
        
        # Максимум ассистентов у одного пользователя
        user_assistant_counts = {}
        for stat in openai_stats:
            user_assistant_counts[str(stat[0])] = user_assistant_counts.get(str(stat[0]), 0) + stat[1]
        for stat in gemini_stats:
            user_assistant_counts[str(stat[0])] = user_assistant_counts.get(str(stat[0]), 0) + stat[1]
        for stat in grok_stats:
            user_assistant_counts[str(stat[0])] = user_assistant_counts.get(str(stat[0]), 0) + stat[1]
        
        max_assistants = max(user_assistant_counts.values()) if user_assistant_counts else 0
        avg_assistants = total_assistants / total_users if total_users > 0 else 0
        
        # ========== ДОХОДЫ ==========
        # Начало текущего месяца
        current_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        
        # Начало прошлого месяца
        if now.month == 1:
            prev_month_start = now.replace(year=now.year - 1, month=12, day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            prev_month_start = now.replace(month=now.month - 1, day=1, hour=0, minute=0, second=0, microsecond=0)
        
        # Доход за текущий месяц (только успешные платежи)
        current_month_revenue = db.query(
            sql_func.coalesce(sql_func.sum(PaymentTransaction.amount), 0)
        ).filter(
            PaymentTransaction.status == "success",
            PaymentTransaction.paid_at >= current_month_start
        ).scalar() or 0
        
        # Доход за прошлый месяц
        prev_month_revenue = db.query(
            sql_func.coalesce(sql_func.sum(PaymentTransaction.amount), 0)
        ).filter(
            PaymentTransaction.status == "success",
            PaymentTransaction.paid_at >= prev_month_start,
            PaymentTransaction.paid_at < current_month_start
        ).scalar() or 0
        
        # Общий доход за всё время
        total_revenue = db.query(
            sql_func.coalesce(sql_func.sum(PaymentTransaction.amount), 0)
        ).filter(
            PaymentTransaction.status == "success"
        ).scalar() or 0
        
        # Количество успешных платежей
        total_payments = db.query(PaymentTransaction).filter(
            PaymentTransaction.status == "success"
        ).count()
        
        # ========== СТАТИСТИКА ЗВОНКОВ ==========
        # Входящие звонки
        inbound_calls = db.query(Conversation).filter(
            Conversation.call_direction == "inbound"
        ).count()
        
        # Исходящие звонки
        outbound_calls = db.query(Conversation).filter(
            Conversation.call_direction == "outbound"
        ).count()
        
        # Общая длительность звонков (в секундах)
        total_call_duration = db.query(
            sql_func.coalesce(sql_func.sum(Conversation.duration_seconds), 0)
        ).filter(
            Conversation.duration_seconds.isnot(None)
        ).scalar() or 0
        
        # Общая стоимость звонков
        total_call_cost = db.query(
            sql_func.coalesce(sql_func.sum(Conversation.call_cost), 0)
        ).filter(
            Conversation.call_cost.isnot(None)
        ).scalar() or 0
        
        # Звонки за текущий месяц
        calls_this_month = db.query(Conversation).filter(
            Conversation.created_at >= current_month_start,
            Conversation.call_direction.isnot(None)
        ).count()

        # Стоимость звонков за текущий месяц
        call_cost_this_month = db.query(
            sql_func.coalesce(sql_func.sum(Conversation.call_cost), 0)
        ).filter(
            Conversation.created_at >= current_month_start,
            Conversation.call_cost.isnot(None)
        ).scalar() or 0

        # ========== ОРКЕСТРАТОР (Voksy AI Agent) ==========
        orchestrator_stats = _build_orchestrator_stats(db, now)

        return {
            "users": {
                "total": total_users,
                "active": active_users,
                "with_subscription": users_with_active_subscription,
                "trial": trial_users,
                "recent": recent_user_data
            },
            "assistants": {
                "total": total_assistants,
                "openai": openai_assistants,
                "gemini": gemini_assistants,
                "grok": grok_assistants,
                "max_per_user": max_assistants,
                "avg_per_user": round(avg_assistants, 2)
            },
            # ✅ v2.1: Статистика доходов
            "revenue": {
                "current_month": float(current_month_revenue),
                "previous_month": float(prev_month_revenue),
                "total": float(total_revenue),
                "total_payments": total_payments,
                "currency": "RUB"
            },
            # ✅ v2.1: Статистика звонков
            "calls": {
                "inbound": inbound_calls,
                "outbound": outbound_calls,
                "total": inbound_calls + outbound_calls,
                "this_month": calls_this_month,
                "total_duration_seconds": float(total_call_duration),
                "total_duration_minutes": round(float(total_call_duration) / 60, 1),
                "total_cost": float(total_call_cost),
                "cost_this_month": float(call_cost_this_month)
            },
            # ✅ v2.3: Статистика оркестратора (Voksy AI Agent)
            "orchestrator": orchestrator_stats,
            "timestamp": now
        }
    except Exception as e:
        logger.error(f"Error in get_admin_statistics: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve admin statistics"
        )


# ============================================================================
# АНАЛИТИКА ОРКЕСТРАТОРА (Voksy AI Agent) — кредиты, агенты, активность
# ============================================================================

SPEND_TYPES = (CreditTransactionType.SPEND.value, CreditTransactionType.REFUND.value)


def _spend_case(period_start):
    """SUM(-amount) по транзакциям начиная с period_start (расход минус возвраты)."""
    return sql_func.coalesce(
        sql_func.sum(case(
            (CreditTransaction.created_at >= period_start, -CreditTransaction.amount),
            else_=0
        )), 0
    )


def _build_orchestrator_stats(db: Session, now: datetime) -> Dict[str, Any]:
    """Сводка по оркестратору для /admin/stats."""
    today_start = _msk_day_start_utc(now)
    d7_start = now - timedelta(days=7)
    d30_start = now - timedelta(days=30)
    # AgentCall.created_at хранится наивным UTC
    today_start_naive = today_start.replace(tzinfo=None)
    d7_naive = d7_start.replace(tzinfo=None)
    d30_naive = d30_start.replace(tzinfo=None)

    # --- Расход кредитов (spend минус refund) за периоды одним запросом ---
    spend_row = db.query(
        _spend_case(today_start).label("today"),
        _spend_case(d7_start).label("d7"),
        _spend_case(d30_start).label("d30"),
    ).filter(
        CreditTransaction.type.in_(SPEND_TYPES),
        CreditTransaction.created_at >= d30_start,
    ).one()

    spend_all_time = db.query(
        sql_func.coalesce(sql_func.sum(-CreditTransaction.amount), 0)
    ).filter(CreditTransaction.type.in_(SPEND_TYPES)).scalar() or 0

    # --- Активные пользователи (уникальные user_id со списаниями) ---
    def _active_users(since) -> int:
        return db.query(
            sql_func.count(sql_func.distinct(CreditTransaction.user_id))
        ).filter(
            CreditTransaction.type == CreditTransactionType.SPEND.value,
            CreditTransaction.created_at >= since,
        ).scalar() or 0

    # --- Выдано / куплено кредитов за 30 дней ---
    granted_30d = db.query(
        sql_func.coalesce(sql_func.sum(CreditTransaction.amount), 0)
    ).filter(
        CreditTransaction.type.in_((
            CreditTransactionType.TRIAL_GRANT.value,
            CreditTransactionType.SUBSCRIPTION_GRANT.value,
        )),
        CreditTransaction.created_at >= d30_start,
    ).scalar() or 0

    purchased_30d = db.query(
        sql_func.coalesce(sql_func.sum(CreditTransaction.amount), 0)
    ).filter(
        CreditTransaction.type == CreditTransactionType.PURCHASE.value,
        CreditTransaction.created_at >= d30_start,
    ).scalar() or 0

    # --- Разбивка расхода за 30 дней по типу операции ---
    by_ref_type = db.query(
        CreditTransaction.ref_type,
        sql_func.coalesce(sql_func.sum(-CreditTransaction.amount), 0).label("credits"),
        sql_func.count(CreditTransaction.id).label("count"),
    ).filter(
        CreditTransaction.type == CreditTransactionType.SPEND.value,
        CreditTransaction.created_at >= d30_start,
    ).group_by(CreditTransaction.ref_type).order_by(sql_func.sum(-CreditTransaction.amount).desc()).all()

    # --- Разбивка расхода за 30 дней по моделям ---
    by_model = db.query(
        CreditTransaction.model_slug,
        sql_func.coalesce(sql_func.sum(-CreditTransaction.amount), 0).label("credits"),
        sql_func.coalesce(sql_func.sum(CreditTransaction.prompt_tokens), 0).label("prompt_tokens"),
        sql_func.coalesce(sql_func.sum(CreditTransaction.completion_tokens), 0).label("completion_tokens"),
        sql_func.count(CreditTransaction.id).label("count"),
    ).filter(
        CreditTransaction.type == CreditTransactionType.SPEND.value,
        CreditTransaction.model_slug.isnot(None),
        CreditTransaction.created_at >= d30_start,
    ).group_by(CreditTransaction.model_slug).order_by(sql_func.sum(-CreditTransaction.amount).desc()).all()

    # --- Топ-10 пользователей по расходу за 30 дней ---
    top_rows = db.query(
        CreditTransaction.user_id,
        sql_func.coalesce(sql_func.sum(-CreditTransaction.amount), 0).label("credits"),
    ).filter(
        CreditTransaction.type.in_(SPEND_TYPES),
        CreditTransaction.created_at >= d30_start,
    ).group_by(CreditTransaction.user_id).order_by(
        sql_func.sum(-CreditTransaction.amount).desc()
    ).limit(10).all()

    top_user_ids = [r[0] for r in top_rows]
    top_emails = {}
    if top_user_ids:
        for uid, email in db.query(User.id, User.email).filter(User.id.in_(top_user_ids)).all():
            top_emails[uid] = email

    # --- Агенты и их звонки ---
    agents_total = db.query(AgentConfig).count()
    agents_active = db.query(AgentConfig).filter(AgentConfig.is_active.is_(True)).count()

    calls_row = db.query(
        sql_func.coalesce(sql_func.sum(case((AgentCall.created_at >= today_start_naive, 1), else_=0)), 0).label("today"),
        sql_func.coalesce(sql_func.sum(case((AgentCall.created_at >= d7_naive, 1), else_=0)), 0).label("d7"),
        sql_func.count(AgentCall.id).label("d30"),
    ).filter(AgentCall.created_at >= d30_naive).one()

    # Средняя стоимость одного события (precall+postcall) за 30 дней
    call_spend_30d = db.query(
        sql_func.coalesce(sql_func.sum(-CreditTransaction.amount), 0)
    ).filter(
        CreditTransaction.type == CreditTransactionType.SPEND.value,
        CreditTransaction.ref_type.in_(("precall", "postcall")),
        CreditTransaction.created_at >= d30_start,
    ).scalar() or 0
    avg_credits_per_call = round(call_spend_30d / calls_row.d30, 1) if calls_row.d30 else 0

    # --- Trial-воронка ---
    trial_users = db.query(User).filter(User.agent_trial_used.is_(True)).count()
    spent_users = db.query(
        sql_func.count(sql_func.distinct(CreditTransaction.user_id))
    ).filter(CreditTransaction.type == CreditTransactionType.SPEND.value).scalar() or 0
    paying_users = db.query(
        sql_func.count(sql_func.distinct(CreditTransaction.user_id))
    ).filter(
        CreditTransaction.type.in_((
            CreditTransactionType.PURCHASE.value,
            CreditTransactionType.SUBSCRIPTION_GRANT.value,
        ))
    ).scalar() or 0

    return {
        "spend": {
            "today": int(spend_row.today),
            "d7": int(spend_row.d7),
            "d30": int(spend_row.d30),
            "all_time": int(spend_all_time),
        },
        "active_users": {
            "today": _active_users(today_start),
            "d7": _active_users(d7_start),
            "d30": _active_users(d30_start),
        },
        "credits_flow_30d": {
            "granted": int(granted_30d),
            "purchased": int(purchased_30d),
            "spent": int(spend_row.d30),
        },
        "agents": {"total": agents_total, "active": agents_active},
        "agent_calls": {
            "today": int(calls_row.today),
            "d7": int(calls_row.d7),
            "d30": int(calls_row.d30),
            "avg_credits_per_call_30d": avg_credits_per_call,
        },
        "trial_funnel": {
            "trial_users": trial_users,
            "spent_users": spent_users,
            "paying_users": paying_users,
        },
        "by_ref_type_30d": [
            {"ref_type": r.ref_type or "other", "credits": int(r.credits), "count": int(r.count)}
            for r in by_ref_type
        ],
        "by_model_30d": [
            {
                "model_slug": r.model_slug,
                "credits": int(r.credits),
                "prompt_tokens": int(r.prompt_tokens),
                "completion_tokens": int(r.completion_tokens),
                "count": int(r.count),
            }
            for r in by_model
        ],
        "top_users_30d": [
            {"user_id": str(r.user_id), "email": top_emails.get(r.user_id, "—"), "credits": int(r.credits)}
            for r in top_rows
        ],
    }


@router.get("/agent-usage", response_model=Dict[str, Any])
async def get_agent_usage(
    search: Optional[str] = Query(None, description="Поиск по email/имени/компании"),
    activity_filter: Optional[str] = Query(
        None, description="active_7d | inactive_7d | trial | low_balance"
    ),
    sort: str = Query("spend_7d", description="spend_today | spend_7d | spend_30d | spend_total | last_activity | balance"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    current_user: User = Depends(check_admin_access),
    db: Session = Depends(get_db)
):
    """
    Использование оркестратора по пользователям: расход кредитов за
    сегодня (МСК) / 7 / 30 дней, баланс, агенты, контакты, звонки,
    последняя активность. Admin only.
    """
    try:
        now = datetime.now(timezone.utc)
        today_start = _msk_day_start_utc(now)
        d7_start = now - timedelta(days=7)
        d30_start = now - timedelta(days=30)
        d7_naive = d7_start.replace(tzinfo=None)
        d30_naive = d30_start.replace(tzinfo=None)

        # --- Расход по периодам + последняя активность (один GROUP BY) ---
        spend_rows = db.query(
            CreditTransaction.user_id,
            sql_func.coalesce(sql_func.sum(
                case((
                    (CreditTransaction.created_at >= today_start) &
                    CreditTransaction.type.in_(SPEND_TYPES),
                    -CreditTransaction.amount
                ), else_=0)
            ), 0).label("spend_today"),
            sql_func.coalesce(sql_func.sum(
                case((
                    (CreditTransaction.created_at >= d7_start) &
                    CreditTransaction.type.in_(SPEND_TYPES),
                    -CreditTransaction.amount
                ), else_=0)
            ), 0).label("spend_7d"),
            sql_func.coalesce(sql_func.sum(
                case((
                    (CreditTransaction.created_at >= d30_start) &
                    CreditTransaction.type.in_(SPEND_TYPES),
                    -CreditTransaction.amount
                ), else_=0)
            ), 0).label("spend_30d"),
            sql_func.coalesce(sql_func.sum(
                case((CreditTransaction.type.in_(SPEND_TYPES), -CreditTransaction.amount), else_=0)
            ), 0).label("spend_total"),
            sql_func.max(
                case((
                    CreditTransaction.type == CreditTransactionType.SPEND.value,
                    CreditTransaction.created_at
                ))
            ).label("last_activity"),
        ).group_by(CreditTransaction.user_id).all()

        spend_map = {r.user_id: r for r in spend_rows}

        # --- Агенты по пользователям ---
        agent_rows = db.query(
            AgentConfig.user_id,
            sql_func.count(AgentConfig.id).label("total"),
            sql_func.coalesce(sql_func.sum(case((AgentConfig.is_active.is_(True), 1), else_=0)), 0).label("active"),
        ).group_by(AgentConfig.user_id).all()
        agents_map = {r.user_id: r for r in agent_rows}

        # --- Контакты по пользователям ---
        contact_rows = db.query(
            AgentContact.user_id,
            sql_func.count(AgentContact.id).label("total"),
        ).group_by(AgentContact.user_id).all()
        contacts_map = {r.user_id: int(r.total) for r in contact_rows}

        # --- Звонки/события агента по пользователям ---
        call_rows = db.query(
            AgentCall.user_id,
            sql_func.count(AgentCall.id).label("total"),
            sql_func.coalesce(sql_func.sum(case((AgentCall.created_at >= d7_naive, 1), else_=0)), 0).label("d7"),
            sql_func.coalesce(sql_func.sum(case((AgentCall.created_at >= d30_naive, 1), else_=0)), 0).label("d30"),
            sql_func.coalesce(sql_func.sum(case((
                (AgentCall.created_at >= d30_naive) & (AgentCall.post_call_decision == "SUCCESS"), 1
            ), else_=0)), 0).label("success_30d"),
        ).filter(AgentCall.user_id.isnot(None)).group_by(AgentCall.user_id).all()
        calls_map = {r.user_id: r for r in call_rows}

        # --- Сводка по всей платформе (до фильтров/пагинации) ---
        summary = {
            "spend_today": int(sum(r.spend_today for r in spend_rows)),
            "spend_7d": int(sum(r.spend_7d for r in spend_rows)),
            "spend_30d": int(sum(r.spend_30d for r in spend_rows)),
            "active_7d": sum(1 for r in spend_rows if r.spend_7d > 0),
            "active_30d": sum(1 for r in spend_rows if r.spend_30d > 0),
        }

        # --- Кого показываем: у кого есть агент ИЛИ транзакции кредитов ---
        user_ids = set(spend_map.keys()) | set(agents_map.keys())
        if not user_ids:
            return {"summary": summary, "users": [], "total": 0}

        users_q = db.query(User).filter(User.id.in_(user_ids))
        if search:
            term = f"%{search}%"
            users_q = users_q.filter(
                (User.email.ilike(term)) |
                (User.first_name.ilike(term)) |
                (User.last_name.ilike(term)) |
                (User.company_name.ilike(term))
            )
        users = users_q.all()

        result = []
        for u in users:
            sp = spend_map.get(u.id)
            ag = agents_map.get(u.id)
            cl = calls_map.get(u.id)
            last_activity = sp.last_activity if sp is not None else None

            item = {
                "user_id": str(u.id),
                "email": u.email,
                "name": (f"{u.first_name or ''} {u.last_name or ''}".strip()
                         or u.company_name or ""),
                "credits_balance": int(u.credits_balance or 0),
                "spend_today": int(sp.spend_today) if sp else 0,
                "spend_7d": int(sp.spend_7d) if sp else 0,
                "spend_30d": int(sp.spend_30d) if sp else 0,
                "spend_total": int(sp.spend_total) if sp else 0,
                "last_activity": last_activity,
                "agents_count": int(ag.total) if ag else 0,
                "agents_active": int(ag.active) if ag else 0,
                "contacts_count": contacts_map.get(u.id, 0),
                "calls_total": int(cl.total) if cl else 0,
                "calls_7d": int(cl.d7) if cl else 0,
                "calls_30d": int(cl.d30) if cl else 0,
                "success_calls_30d": int(cl.success_30d) if cl else 0,
                "agent_status": u.agent_subscription_status(),
                "subscription_plan": u.subscription_plan,
            }
            result.append(item)

        # --- Фильтры активности ---
        if activity_filter == "active_7d":
            result = [r for r in result if r["spend_7d"] > 0]
        elif activity_filter == "inactive_7d":
            result = [r for r in result if r["spend_7d"] == 0]
        elif activity_filter == "trial":
            result = [r for r in result if r["agent_status"] == "trial"]
        elif activity_filter == "low_balance":
            result = [r for r in result if r["credits_balance"] < 100]

        # --- Сортировка ---
        def _epoch(dt):
            if not dt:
                return 0
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()

        sort_keys = {
            "spend_today": lambda r: r["spend_today"],
            "spend_7d": lambda r: r["spend_7d"],
            "spend_30d": lambda r: r["spend_30d"],
            "spend_total": lambda r: r["spend_total"],
            "last_activity": lambda r: _epoch(r["last_activity"]),
            "balance": lambda r: r["credits_balance"],
        }
        key_fn = sort_keys.get(sort, sort_keys["spend_7d"])
        result.sort(key=lambda r: (key_fn(r), _epoch(r["last_activity"])), reverse=True)

        total = len(result)
        return {"summary": summary, "users": result[skip:skip + limit], "total": total}
    except Exception as e:
        logger.error(f"Error in get_agent_usage: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve agent usage"
        )


@router.get("/users/{user_id}/agent-usage", response_model=Dict[str, Any])
async def get_user_agent_usage(
    user_id: str = Path(..., description="User ID"),
    current_user: User = Depends(check_admin_access),
    db: Session = Depends(get_db)
):
    """
    Детальная аналитика оркестратора по одному пользователю: дневная серия
    расхода за 30 дней (МСК), разбивка по типам операций и моделям,
    статистика по каждому агенту, воронка контактов, последние транзакции.
    Admin only.
    """
    try:
        user = await UserService.get_user_by_id(db, user_id)

        now = datetime.now(timezone.utc)
        today_start = _msk_day_start_utc(now)
        d7_start = now - timedelta(days=7)
        # Дневная серия: 30 полных МСК-дней, включая сегодняшний
        series_start = today_start - timedelta(days=29)
        series_start_naive = series_start.replace(tzinfo=None)

        # --- Транзакции spend/refund за окно серии ---
        txs = db.query(
            CreditTransaction.created_at,
            CreditTransaction.amount,
            CreditTransaction.type,
            CreditTransaction.ref_type,
            CreditTransaction.model_slug,
            CreditTransaction.prompt_tokens,
            CreditTransaction.completion_tokens,
        ).filter(
            CreditTransaction.user_id == user.id,
            CreditTransaction.type.in_(SPEND_TYPES),
            CreditTransaction.created_at >= series_start,
        ).all()

        daily = {}
        for i in range(30):
            day = (series_start + timedelta(days=i)).astimezone(MSK).date()
            daily[day.isoformat()] = {"date": day.isoformat(), "credits": 0, "calls": 0}

        by_ref: Dict[str, Dict[str, int]] = {}
        by_model: Dict[str, Dict[str, int]] = {}
        spend_today = spend_7d = spend_30d = 0

        for tx in txs:
            created = tx.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            spent = -(tx.amount or 0)  # spend: положительный расход, refund: отрицательный

            day_key = created.astimezone(MSK).date().isoformat()
            if day_key in daily:
                daily[day_key]["credits"] += spent

            spend_30d += spent
            if created >= d7_start:
                spend_7d += spent
            if created >= today_start:
                spend_today += spent

            if tx.type == CreditTransactionType.SPEND.value:
                ref = tx.ref_type or "other"
                bucket = by_ref.setdefault(ref, {"credits": 0, "count": 0})
                bucket["credits"] += spent
                bucket["count"] += 1

                if tx.model_slug:
                    mb = by_model.setdefault(tx.model_slug, {
                        "credits": 0, "count": 0, "prompt_tokens": 0, "completion_tokens": 0
                    })
                    mb["credits"] += spent
                    mb["count"] += 1
                    mb["prompt_tokens"] += tx.prompt_tokens or 0
                    mb["completion_tokens"] += tx.completion_tokens or 0

        spend_total = db.query(
            sql_func.coalesce(sql_func.sum(-CreditTransaction.amount), 0)
        ).filter(
            CreditTransaction.user_id == user.id,
            CreditTransaction.type.in_(SPEND_TYPES),
        ).scalar() or 0

        granted_total = db.query(
            sql_func.coalesce(sql_func.sum(CreditTransaction.amount), 0)
        ).filter(
            CreditTransaction.user_id == user.id,
            CreditTransaction.type.in_((
                CreditTransactionType.TRIAL_GRANT.value,
                CreditTransactionType.SUBSCRIPTION_GRANT.value,
                CreditTransactionType.MANUAL_ADJUST.value,
            )),
        ).scalar() or 0

        purchased_total = db.query(
            sql_func.coalesce(sql_func.sum(CreditTransaction.amount), 0)
        ).filter(
            CreditTransaction.user_id == user.id,
            CreditTransaction.type == CreditTransactionType.PURCHASE.value,
        ).scalar() or 0

        # --- Дневная серия звонков (AgentCall.created_at — наивный UTC) ---
        call_dates = db.query(AgentCall.created_at).filter(
            AgentCall.user_id == user.id,
            AgentCall.created_at >= series_start_naive,
        ).all()
        for (created,) in call_dates:
            day_key = created.replace(tzinfo=timezone.utc).astimezone(MSK).date().isoformat()
            if day_key in daily:
                daily[day_key]["calls"] += 1

        # --- Статистика по каждому агенту ---
        agents = db.query(AgentConfig).filter(AgentConfig.user_id == user.id).all()
        agent_ids = [a.id for a in agents]

        agent_contacts = {}
        agent_calls = {}
        if agent_ids:
            for row in db.query(
                AgentContact.agent_config_id,
                sql_func.count(AgentContact.id),
            ).filter(AgentContact.agent_config_id.in_(agent_ids)).group_by(
                AgentContact.agent_config_id
            ).all():
                agent_contacts[row[0]] = int(row[1])

            for row in db.query(
                AgentCall.agent_config_id,
                sql_func.count(AgentCall.id).label("total"),
                sql_func.coalesce(sql_func.sum(
                    case((AgentCall.post_call_decision == "SUCCESS", 1), else_=0)
                ), 0).label("success"),
                sql_func.coalesce(sql_func.sum(AgentCall.duration_seconds), 0).label("duration"),
                sql_func.max(AgentCall.created_at).label("last_call"),
            ).filter(AgentCall.agent_config_id.in_(agent_ids)).group_by(
                AgentCall.agent_config_id
            ).all():
                agent_calls[row.agent_config_id] = row

        agents_data = []
        for a in agents:
            ac = agent_calls.get(a.id)
            agents_data.append({
                "id": str(a.id),
                "name": a.name,
                "orchestrator_model": a.orchestrator_model,
                "is_active": a.is_active,
                "telegram_enabled": a.telegram_enabled,
                "kb_char_count": a.kb_char_count or 0,
                "contacts_count": agent_contacts.get(a.id, 0),
                "calls_total": int(ac.total) if ac else 0,
                "success_calls": int(ac.success) if ac else 0,
                "duration_seconds": int(ac.duration) if ac else 0,
                "last_call_at": ac.last_call.isoformat() if ac and ac.last_call else None,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            })

        # --- Воронка контактов по стадиям ---
        funnel = [
            {"status": row[0], "count": int(row[1])}
            for row in db.query(
                AgentContact.status, sql_func.count(AgentContact.id)
            ).filter(AgentContact.user_id == user.id).group_by(
                AgentContact.status
            ).order_by(sql_func.count(AgentContact.id).desc()).all()
        ]

        # --- Последние транзакции кредитов ---
        recent_txs = db.query(CreditTransaction).filter(
            CreditTransaction.user_id == user.id
        ).order_by(CreditTransaction.created_at.desc()).limit(20).all()

        return {
            "user": {
                "id": str(user.id),
                "email": user.email,
                "name": (f"{user.first_name or ''} {user.last_name or ''}".strip()
                         or user.company_name or ""),
                "credits_balance": int(user.credits_balance or 0),
                "agent_status": user.agent_subscription_status(),
                "agent_trial_used": user.agent_trial_used,
                "agent_trial_started_at": (
                    user.agent_trial_started_at.isoformat()
                    if user.agent_trial_started_at else None
                ),
                "subscription_plan": user.subscription_plan,
            },
            "summary": {
                "spend_today": int(spend_today),
                "spend_7d": int(spend_7d),
                "spend_30d": int(spend_30d),
                "spend_total": int(spend_total),
                "granted_total": int(granted_total),
                "purchased_total": int(purchased_total),
            },
            "daily": list(daily.values()),
            "by_ref_type": sorted(
                [{"ref_type": k, **v} for k, v in by_ref.items()],
                key=lambda x: x["credits"], reverse=True
            ),
            "by_model": sorted(
                [{"model_slug": k, **v} for k, v in by_model.items()],
                key=lambda x: x["credits"], reverse=True
            ),
            "agents": agents_data,
            "funnel": funnel,
            "transactions": [tx.to_dict() for tx in recent_txs],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in get_user_agent_usage: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve user agent usage"
        )


@router.post("/users/{user_id}/reset-password", response_model=Dict[str, Any])
async def admin_reset_password(
    user_id: str = Path(..., description="User ID"),
    request: AdminPasswordResetRequest = ...,
    current_user: User = Depends(check_admin_access),
    db: Session = Depends(get_db)
):
    """
    Reset password for a specific user.
    Admin only endpoint.
    Uses SHA-256 hashing (same as backend/core/security.py hash_password).
    """
    import hashlib

    try:
        user = await UserService.get_user_by_id(db, user_id)

        if len(request.new_password) < 6:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password must be at least 6 characters"
            )

        user.password_hash = hashlib.sha256(request.new_password.encode()).hexdigest()
        db.commit()

        logger.info(f"Admin {current_user.email} reset password for user {user.email}")

        return {
            "success": True,
            "message": f"Password successfully reset for {user.email}"
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error resetting password: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to reset password"
        )
