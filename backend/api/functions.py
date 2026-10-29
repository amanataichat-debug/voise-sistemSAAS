from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Dict, Any

from backend.core.dependencies import get_current_user, check_subscription_active
from backend.db.session import get_db
from backend.models.user import User
from backend.functions import get_all_definitions, get_all_openai_definitions
from backend.core.logging import get_logger

logger = get_logger(__name__)

# Функции, у которых нет работающего транспорта (Voximplant выключен): в каталоге не показываем,
# чтобы владелец не включал то, что не сработает. У уже настроенных ассистентов они остаются.
HIDDEN_FUNCTIONS = {"send_sms"}


def _visible(definitions):
    return [d for d in definitions if d.get("name") not in HIDDEN_FUNCTIONS]


router = APIRouter()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🌐 Публичный эндпоинт (без авторизации)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.get("/public/catalog", response_model=List[Dict[str, Any]])
async def get_public_functions_catalog():
    """
    Публичный каталог доступных функций (без авторизации).
    
    Возвращает список функций с именами, описаниями и параметрами.
    Не требует токен аутентификации.
    
    GET /api/functions/public/catalog
    """
    try:
        definitions = _visible(get_all_definitions())
        return [
            {
                "name": d.get("name"),
                "display_name": d.get("display_name", d.get("name", "").replace("_", " ").title()),
                "description": d.get("description"),
                "parameters": d.get("parameters", {})
            }
            for d in definitions
        ]
    except Exception as e:
        logger.error(f"Error getting public functions catalog: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve functions catalog"
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔒 Приватные эндпоинты (с авторизацией)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.get("/", response_model=List[Dict[str, Any]])
async def get_functions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Получить список доступных функций (требует авторизацию).
    
    Returns:
        List[Dict[str, Any]]: Список определений функций
    """
    try:
        return _visible(get_all_definitions())
    except Exception as e:
        logger.error(f"Error getting functions: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve functions"
        )

@router.get("/openai-format", response_model=List[Dict[str, Any]])
async def get_functions_openai_format(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Получить список функций в формате для OpenAI API (требует авторизацию).
    
    Returns:
        List[Dict[str, Any]]: Список определений функций для OpenAI
    """
    try:
        return get_all_openai_definitions()
    except Exception as e:
        logger.error(f"Error getting OpenAI functions: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve OpenAI functions"
        )
