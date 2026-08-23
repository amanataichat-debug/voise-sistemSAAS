"""
User API endpoints for WellcomeAI application.
"""

"""
User API endpoints for WellcomeAI application.
"""

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.core.logging import get_logger  # Изменен импорт core
from backend.core.dependencies import get_current_user, hash_api_key  # Изменен импорт core
from backend.db.session import get_db  # Уже корректный импорт
from backend.models.user import User  # Изменен импорт models
from backend.schemas.user import UserUpdate, UserResponse, UserDetailResponse, UserPasswordUpdate  # Изменен импорт schemas
from backend.services.user_service import UserService  # Изменен импорт services
from backend.services.auth_service import AuthService  # Добавлен импорт AuthService

# Initialize logger
logger = get_logger(__name__)

# Create router
router = APIRouter()

@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get current user profile information.
    
    Args:
        current_user: Current authenticated user
        db: Database session dependency
    
    Returns:
        UserResponse with user profile information
    """
    try:
        return await UserService.get_user_profile(db, str(current_user.id))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in get_current_user_info: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve user information"
        )

@router.get("/me/details", response_model=UserDetailResponse)
async def get_user_details(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get detailed user information including usage statistics.
    
    Args:
        current_user: Current authenticated user
        db: Database session dependency
    
    Returns:
        UserDetailResponse with detailed user information
    """
    try:
        return await UserService.get_user_details(db, str(current_user.id))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in get_user_details: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve user details"
        )

@router.put("/me", response_model=UserResponse)
async def update_user(
    user_data: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Update current user information.
    
    Args:
        user_data: Updated user data
        current_user: Current authenticated user
        db: Database session dependency
    
    Returns:
        UserResponse with updated user information
    """
    try:
        return await UserService.update_user(db, str(current_user.id), user_data)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in update_user: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update user information"
        )

@router.put("/me/password", response_model=dict)
async def change_password(
    password_data: UserPasswordUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Change current user password.
    
    Args:
        password_data: Password change data
        current_user: Current authenticated user
        db: Database session dependency
    
    Returns:
        Confirmation message
    """
    try:
        await AuthService.change_password(
            db, 
            str(current_user.id), 
            password_data.current_password, 
            password_data.new_password
        )
        return {"success": True, "message": "Password changed successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in change_password: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to change password"
        )

@router.delete("/me", response_model=dict)
async def deactivate_account(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Deactivate current user account.
    
    Args:
        current_user: Current authenticated user
        db: Database session dependency
    
    Returns:
        Confirmation message
    """
    try:
        await UserService.deactivate_user(db, str(current_user.id))
        return {"success": True, "message": "Account deactivated successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in deactivate_account: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to deactivate account"
        )


# ============================================================================
# Персональный API-ключ Voksy AI (внешние интеграции: Claude Code и т.п.)
#
# Ключ формата vfy_<random> показывается полностью РОВНО ОДИН РАЗ — в ответе
# на regenerate. В БД хранится только SHA-256-хэш + префикс для отображения.
# Ключ даёт доступ к эндпоинтам агента: POST /api/agent/create, PUT /api/agent/,
# GET /api/agent/, GET /api/agent/list, GET /api/agent/orchestrator-models.
# ============================================================================


def _api_key_status(user: User) -> dict:
    """Статус ключа для настроек (без самого ключа)."""
    return {
        "has_key": bool(user.api_key_hash),
        "key_prefix": user.api_key_prefix,
        "created_at": user.api_key_created_at.isoformat() if user.api_key_created_at else None,
    }


@router.get("/api-key", response_model=dict)
async def get_api_key_status(
    current_user: User = Depends(get_current_user),
):
    """Статус персонального API-ключа (есть ли, префикс, дата создания)."""
    return _api_key_status(current_user)


@router.post("/api-key/regenerate", response_model=dict)
async def regenerate_api_key(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Сгенерировать (или перевыпустить) персональный API-ключ.
    Старый ключ мгновенно перестаёт работать.
    Полный ключ возвращается только в этом ответе — сохраните его.
    """
    api_key = f"vfy_{secrets.token_urlsafe(32)}"
    current_user.api_key_hash = hash_api_key(api_key)
    current_user.api_key_prefix = f"{api_key[:8]}…{api_key[-4:]}"
    current_user.api_key_created_at = datetime.now(timezone.utc)
    db.commit()
    logger.info(f"[API-KEY] Regenerated personal API key for user {current_user.id}")
    result = _api_key_status(current_user)
    result["api_key"] = api_key
    return result


@router.delete("/api-key", response_model=dict)
async def revoke_api_key(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Отозвать персональный API-ключ (доступ по нему прекращается сразу)."""
    current_user.api_key_hash = None
    current_user.api_key_prefix = None
    current_user.api_key_created_at = None
    db.commit()
    logger.info(f"[API-KEY] Revoked personal API key for user {current_user.id}")
    return _api_key_status(current_user)
