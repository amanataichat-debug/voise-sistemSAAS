"""
Subscription status API endpoints for checking subscription and blocking features.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Dict, Any
from datetime import datetime, timezone

from backend.core.logging import get_logger
from backend.core.dependencies import get_current_user
from backend.db.session import get_db
from backend.models.user import User
from backend.services.user_service import UserService

logger = get_logger(__name__)

router = APIRouter()

@router.get("/check-access", response_model=Dict[str, Any])
async def check_user_access(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Проверка доступа пользователя к функциям платформы.
    Возвращает информацию о том, нужно ли показывать поп-ап блокировки.
    
    Returns:
        Dict с информацией о доступе и подписке
    """
    try:
        # Админ всегда имеет доступ
        if current_user.is_admin or current_user.email == "well96well@gmail.com":
            return {
                "access_granted": True,
                "subscription_active": True,
                "show_payment_popup": False,
                "is_admin": True,
                "user_type": "admin",
                "message": "Администратор - полный доступ"
            }
        
        # Проверяем статус подписки
        subscription_status = await UserService.check_subscription_status(db, str(current_user.id))
        
        # Если подписка активна - доступ разрешен
        if subscription_status["active"]:
            return {
                "access_granted": True,
                "subscription_active": True,
                "show_payment_popup": False,
                "is_admin": False,
                "user_type": "trial" if subscription_status["is_trial"] else "paid",
                "days_left": subscription_status.get("days_left", 0),
                "max_assistants": subscription_status.get("max_assistants", 1),
                "message": "Подписка активна"
            }
        
        # Подписка неактивна - нужно показать поп-ап блокировки
        return {
            "access_granted": False,
            "subscription_active": False,
            "show_payment_popup": True,
            "is_admin": False,
            "user_type": "expired",
            "days_left": 0,
            "max_assistants": 0,
            "message": "Подписка истекла - требуется оплата",
            "subscription_details": subscription_status
        }
        
    except Exception as e:
        logger.error(f"Error checking user access: {str(e)}")
        # В случае ошибки - блокируем доступ для безопасности
        return {
            "access_granted": False,
            "subscription_active": False,
            "show_payment_popup": True,
            "is_admin": False,
            "user_type": "error",
            "message": "Ошибка проверки подписки - доступ заблокирован",
            "error": str(e)
        }

@router.get("/force-check", response_model=Dict[str, Any])
async def force_subscription_check(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Принудительная проверка подписки (для вызова после оплаты).
    
    Returns:
        Dict с обновленной информацией о подписке
    """
    try:
        # Обновляем данные пользователя из БД
        db.refresh(current_user)
        
        # Возвращаем результат обычной проверки
        return await check_user_access(current_user, db)
        
    except Exception as e:
        logger.error(f"Error in force subscription check: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка при проверке подписки"
        )
