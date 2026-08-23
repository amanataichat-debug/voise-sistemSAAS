"""
FastAPI dependencies for WellcomeAI application.
Contains reusable dependency functions that can be used across API endpoints.
✅ FIXED: UUID conversion for database queries
✅ UPDATED: Added special assistant limits for specific users
"""

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
import hashlib
import uuid
from typing import Optional
from datetime import datetime, timezone

from backend.core.security import get_current_user_id, decode_jwt_token
from backend.core.logging import get_logger
from backend.models.user import User
from backend.models.assistant import AssistantConfig
from backend.db.session import get_db
from backend.core.config import settings

# Initialize logger
logger = get_logger(__name__)

# ✅ НОВОЕ: Специальные лимиты ассистентов для отдельных пользователей
# Эти пользователи имеют увеличенный лимит, но подписка всё равно проверяется
SPECIAL_ASSISTANT_LIMITS = {
    "v83839370@gmail.com": 25,
}

# Пользователи без лимита ассистентов (помимо is_admin)
PRIVILEGED_UNLIMITED_EMAILS = {
    "amanat.aichat@gmail.com",
    "stas@gmail.com",
}


async def get_current_user(
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db)
) -> User:
    """
    Get the current user from the database
    ✅ FIXED: Properly converts string user_id to UUID
    
    Args:
        user_id: User ID from token (string)
        db: Database session
        
    Returns:
        User object
        
    Raises:
        HTTPException: If user not found or invalid ID format
    """
    try:
        # ✅ ИСПРАВЛЕНИЕ: Конвертируем строку в UUID
        user_uuid = uuid.UUID(user_id)
        user = db.query(User).filter(User.id == user_uuid).first()
        
        if not user:
            logger.warning(f"User not found: {user_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        logger.debug(f"✅ User retrieved: {user.email} (ID: {user_id})")
        return user
        
    except ValueError as e:
        logger.error(f"Invalid user ID format: {user_id} - {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user ID format"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in get_current_user: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve user information"
        )


def hash_api_key(api_key: str) -> str:
    """SHA-256-хэш персонального API-ключа (в БД храним только хэш)."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


async def get_current_user_flexible(
    request: Request,
    db: Session = Depends(get_db)
) -> User:
    """
    Авторизация для эндпоинтов, открытых во внешний API (Claude Code и т.п.):
    принимает ЛИБО персональный API-ключ (заголовок X-Api-Key: vfy_...),
    ЛИБО обычный JWT (Authorization: Bearer <jwt>) — как в кабинете.

    Используется ТОЛЬКО на выбранных эндпоинтах агента (create/update/get/list
    и справочнике моделей). Остальной API остаётся строго JWT-only.
    """
    # 1) Персональный API-ключ
    api_key = request.headers.get("X-Api-Key")
    if api_key:
        api_key = api_key.strip()
        user = db.query(User).filter(
            User.api_key_hash == hash_api_key(api_key)
        ).first()
        if not user:
            logger.warning("❌ Invalid API key presented to flexible auth endpoint")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid_api_key"
            )
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="user_inactive"
            )
        return user

    # 2) JWT из кабинета
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        token_data = decode_jwt_token(token)  # кидает 401 на невалидном токене
        return await get_current_user(user_id=token_data["sub"], db=db)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="No authentication credentials provided"
    )


async def get_assistant_by_id(
    assistant_id: str,
    db: Session = Depends(get_db)
) -> AssistantConfig:
    """
    Get an assistant by ID
    ✅ FIXED: Properly converts string assistant_id to UUID
    
    Args:
        assistant_id: Assistant ID (string)
        db: Database session
        
    Returns:
        AssistantConfig object
        
    Raises:
        HTTPException: If assistant not found or invalid ID format
    """
    try:
        # ✅ ИСПРАВЛЕНИЕ: Конвертируем строку в UUID
        assistant_uuid = uuid.UUID(assistant_id)
        assistant = db.query(AssistantConfig).filter(
            AssistantConfig.id == assistant_uuid
        ).first()
        
        if not assistant:
            logger.warning(f"Assistant not found: {assistant_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Assistant not found"
            )
        
        return assistant
        
    except ValueError as e:
        logger.error(f"Invalid assistant ID format: {assistant_id} - {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid assistant ID format"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in get_assistant_by_id: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve assistant"
        )


async def check_admin_access(
    current_user: User = Depends(get_current_user)
) -> User:
    """
    Check if the current user has admin access
    
    Args:
        current_user: Current authenticated user
        
    Returns:
        Current user if they have admin access
        
    Raises:
        HTTPException: If user doesn't have admin access
    """
    if not current_user.is_admin:
        logger.warning(f"Admin access denied for user: {current_user.email}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return current_user


async def check_subscription_active(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> User:
    """
    Check if the current user has an active subscription
    
    Args:
        db: Database session
        current_user: Current authenticated user
        
    Returns:
        Current user if they have an active subscription
        
    Raises:
        HTTPException: If user doesn't have an active subscription
    """
    from backend.services.user_service import UserService
    
    # Администраторы и привилегированные пользователи всегда имеют доступ
    if current_user.is_admin or current_user.email == "amanat.aichat@gmail.com" or current_user.email == "stas@gmail.com":
        return current_user
    
    # Check subscription status
    subscription_status = await UserService.check_subscription_status(db, str(current_user.id))
    
    if not subscription_status["active"]:
        logger.warning(f"User {current_user.id} attempted to access protected resource with inactive subscription")
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "error": "subscription_expired",
                "message": "Your trial period has expired. Please upgrade your subscription to continue using this service.",
                "code": "TRIAL_EXPIRED",
                "subscription_status": subscription_status
            }
        )
    
    return current_user


async def check_subscription_active_for_assistants(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> User:
    """
    Специальная СТРОГАЯ проверка для работы с ассистентами
    Блокирует доступ если подписка неактивна
    
    Args:
        db: Database session
        current_user: Current authenticated user
        
    Returns:
        Current user if they have an active subscription
        
    Raises:
        HTTPException: If user doesn't have an active subscription
    """
    from backend.services.user_service import UserService
    
    # Администраторы и привилегированные пользователи всегда имеют доступ
    if current_user.is_admin or current_user.email == "amanat.aichat@gmail.com" or current_user.email == "stas@gmail.com":
        return current_user
    
    # Проверяем статус подписки
    subscription_status = await UserService.check_subscription_status(db, str(current_user.id))
    
    if not subscription_status["active"]:
        logger.warning(f"User {current_user.id} blocked from using assistants - subscription expired")
        
        # Определяем тип блокировки для детального сообщения
        if subscription_status.get("is_trial", False):
            error_code = "TRIAL_EXPIRED"
            error_message = "Ваш пробный период истек. Пожалуйста, оплатите подписку для продолжения использования ассистентов."
        else:
            error_code = "SUBSCRIPTION_EXPIRED" 
            error_message = "Ваша подписка истекла. Пожалуйста, продлите подписку для продолжения использования ассистентов."
        
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "error": "subscription_required",
                "message": error_message,
                "code": error_code,
                "subscription_status": subscription_status,
                "requires_payment": True
            }
        )
    
    return current_user


async def enforce_assistant_limit(db: Session, current_user: User) -> User:
    """
    Проверить, что пользователь может создать ещё одного ассистента.

    Общая реализация для двух зависимостей: `check_assistant_limit` (JWT из
    кабинета) и `check_assistant_limit_flexible` (JWT или персональный
    API-ключ интеграции). Правила у них обязаны совпадать.

    Args:
        db: Database session
        current_user: Current authenticated user

    Returns:
        Current user if they haven't reached their assistant limit

    Raises:
        HTTPException: If user has reached their assistant limit or subscription expired
    """
    from backend.services.assistant_limit_service import count_user_assistants
    from backend.services.user_service import UserService

    # Admin и привилегированные пользователи имеют неограниченное количество ассистентов
    if current_user.is_admin or current_user.email in PRIVILEGED_UNLIMITED_EMAILS:
        return current_user

    # Get subscription status
    subscription_status = await UserService.check_subscription_status(db, str(current_user.id))
    
    # Сначала проверяем активность подписки - СТРОГАЯ ПРОВЕРКА
    if not subscription_status["active"]:
        logger.warning(f"User {current_user.id} blocked from creating assistants - subscription expired")
        
        # Определяем тип ошибки
        if subscription_status.get("is_trial", False):
            error_code = "TRIAL_EXPIRED"
            error_message = "Ваш пробный период истек. Пожалуйста, оплатите подписку для создания ассистентов."
        else:
            error_code = "SUBSCRIPTION_EXPIRED"
            error_message = "Ваша подписка истекла. Пожалуйста, продлите подписку для создания ассистентов."
        
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "error": "subscription_required", 
                "message": error_message,
                "code": error_code,
                "subscription_status": subscription_status,
                "requires_payment": True
            }
        )
    
    # Count user's assistants across every provider (OpenAI, Gemini, Grok,
    # Cascade, Cartesia, Yandex, Translate). Голосовые ассистенты мастера
    # Voksy AI Agent в лимит не входят.
    assistant_count = count_user_assistants(db, current_user.id)
    
    # ✅ НОВОЕ: Проверяем специальные лимиты для отдельных пользователей
    if current_user.email in SPECIAL_ASSISTANT_LIMITS:
        max_assistants = SPECIAL_ASSISTANT_LIMITS[current_user.email]
        logger.info(f"User {current_user.email} has special assistant limit: {max_assistants}")
    else:
        max_assistants = subscription_status.get("max_assistants", 0)
    
    # Check if limit reached
    if assistant_count >= max_assistants:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "assistant_limit_reached",
                "message": f"Вы достигли лимита в {max_assistants} ассистентов. Пожалуйста, обновите подписку для создания большего количества ассистентов.",
                "current_count": assistant_count,
                "max_assistants": max_assistants
            }
        )

    return current_user


async def check_assistant_limit(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> User:
    """Лимит ассистентов для эндпоинтов кабинета (авторизация только по JWT)."""
    return await enforce_assistant_limit(db, current_user)


async def check_assistant_limit_flexible(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_flexible)
) -> User:
    """
    Лимит ассистентов для эндпоинтов, открытых во внешний API: авторизация
    по персональному API-ключу (`X-Api-Key`) ИЛИ по JWT кабинета.
    """
    return await enforce_assistant_limit(db, current_user)


async def check_subscription_or_show_popup(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> User:
    """
    Проверка подписки для функций, которые должны показывать поп-ап
    Вместо блокировки возвращает пользователя, но фронтенд сам решает показывать ли поп-ап
    
    Args:
        db: Database session
        current_user: Current authenticated user
        
    Returns:
        Current user (всегда возвращает пользователя)
    """
    from backend.services.user_service import UserService
    
    # Администраторы и привилегированные пользователи всегда имеют доступ
    if current_user.is_admin or current_user.email == "amanat.aichat@gmail.com" or current_user.email == "stas@gmail.com":
        return current_user
    
    # Для других пользователей просто возвращаем - проверку делает фронтенд
    return current_user
