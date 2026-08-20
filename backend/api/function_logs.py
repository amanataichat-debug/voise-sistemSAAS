from fastapi import APIRouter, Depends, HTTPException, Query, Path
from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional

from backend.core.logging import get_logger
from backend.core.dependencies import get_current_user, check_admin_access
from backend.db.session import get_db
from backend.models.user import User
from backend.models.function_log import FunctionLog
from backend.services.function_log_service import FunctionLogService

logger = get_logger(__name__)

router = APIRouter()

@router.get("/stats", response_model=Dict[str, Any])
async def get_function_statistics(
    function_name: Optional[str] = Query(None, description="Фильтр по имени функции"),
    days: int = Query(7, description="Период в днях"),
    current_user: User = Depends(check_admin_access),
    db: Session = Depends(get_db)
):
    """
    Получение статистики по вызовам функций (только для администраторов)
    """
    return await FunctionLogService.get_function_statistics(
        db=db,
        function_name=function_name,
        time_period_days=days
    )

@router.get("/user/stats", response_model=Dict[str, Any])
async def get_user_function_statistics(
    days: int = Query(7, description="Период в днях"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Получение статистики по вызовам функций текущего пользователя
    """
    return await FunctionLogService.get_function_statistics(
        db=db,
        user_id=str(current_user.id),
        time_period_days=days
    )

@router.get("/logs", response_model=List[Dict[str, Any]])
async def get_function_logs(
    function_name: Optional[str] = Query(None, description="Фильтр по имени функции"),
    assistant_id: Optional[str] = Query(None, description="Фильтр по ID ассистента"),
    limit: int = Query(50, ge=1, le=500, description="Лимит записей"),
    offset: int = Query(0, ge=0, description="Смещение"),
    current_user: User = Depends(check_admin_access),
    db: Session = Depends(get_db)
):
    """
    Получение логов вызовов функций (только для администраторов)
    """
    # Реализация...
