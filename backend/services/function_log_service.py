import time
import uuid
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session

from backend.models.function_log import FunctionLog
from backend.core.logging import get_logger

logger = get_logger(__name__)

class FunctionLogService:
    """Сервис для работы с логами функций"""
    
    @staticmethod
    async def log_function_call(
        db: Session,
        function_name: str,
        arguments: Dict[str, Any],
        result: Dict[str, Any],
        status: str,
        execution_time_ms: float,
        user_id: Optional[str] = None,
        assistant_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        error_message: Optional[str] = None,
        function_version: Optional[str] = None
    ) -> FunctionLog:
        """
        Записывает вызов функции в лог.
        
        Args:
            db: Сессия базы данных
            function_name: Имя функции
            arguments: Аргументы вызова
            result: Результат выполнения
            status: Статус выполнения (success, error, timeout)
            execution_time_ms: Время выполнения в миллисекундах
            user_id: ID пользователя (опционально)
            assistant_id: ID ассистента (опционально)
            conversation_id: ID диалога (опционально)
            error_message: Сообщение об ошибке (опционально)
            function_version: Версия функции (опционально)
            
        Returns:
            FunctionLog: Созданная запись лога
        """
        try:
            # Обработка UUID
            user_uuid = uuid.UUID(user_id) if user_id else None
            assistant_uuid = uuid.UUID(assistant_id) if assistant_id else None
            conversation_uuid = uuid.UUID(conversation_id) if conversation_id else None
            
            # Создаем запись в логе
            log_entry = FunctionLog(
                user_id=user_uuid,
                assistant_id=assistant_uuid,
                conversation_id=conversation_uuid,
                function_name=function_name,
                function_version=function_version,
                arguments=arguments,
                result=result,
                execution_time_ms=execution_time_ms,
                status=status,
                error_message=error_message
            )
            
            db.add(log_entry)
            db.commit()
            db.refresh(log_entry)
            
            logger.info(
                f"Вызов функции {function_name} "
                f"пользователем {user_id or 'unknown'} "
                f"статус: {status}, "
                f"время: {execution_time_ms}ms"
            )
            
            return log_entry
            
        except Exception as e:
            db.rollback()
            logger.error(f"Ошибка при записи лога функции: {str(e)}")
            # Возвращаем None в случае ошибки, но не прерываем основной процесс
            return None
    
    @staticmethod
    async def get_function_statistics(
        db: Session,
        function_name: Optional[str] = None,
        user_id: Optional[str] = None,
        assistant_id: Optional[str] = None,
        time_period_days: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Получает статистику по вызовам функций.
        
        Args:
            db: Сессия базы данных
            function_name: Фильтр по имени функции (опционально)
            user_id: Фильтр по ID пользователя (опционально)
            assistant_id: Фильтр по ID ассистента (опционально)
            time_period_days: Период в днях (опционально)
            
        Returns:
            Dict[str, Any]: Статистика по вызовам
        """
        # Реализация статистики...
        pass
