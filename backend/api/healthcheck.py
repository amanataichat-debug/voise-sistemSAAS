"""
Health check and status endpoints for WellcomeAI application.
"""

"""
Health check and status endpoints for WellcomeAI application.
"""
from fastapi import APIRouter, Depends, status
from datetime import datetime

from backend.core.logging import get_logger  # Изменен импорт core
from backend.core.config import settings  # Изменен импорт core
from backend.db.session import get_db  # Уже корректный импорт

# Initialize logger
logger = get_logger(__name__)

# Create router
router = APIRouter()

# Остальной код остается без изменений

# Initialize logger
logger = get_logger(__name__)

# Create router
router = APIRouter()

@router.get("/healthcheck", tags=["Health"])
async def healthcheck():
    """
    Health check endpoint to verify the API is running.
    
    Returns:
        Status information about the application
    """
    return {
        "status": "ok",
        "version": settings.VERSION,
        "timestamp": datetime.now().isoformat(),
        "environment": "development" if settings.DEBUG else "production"
    }

@router.get("/api/health/smtp", tags=["Health"])
async def smtp_diagnose():
    """
    ✅ DEBUG: Диагностика SMTP — DNS, TCP-соединение и логин по шагам.
    Показывает, какие EMAIL_* переменные реально подхватились и на каком
    шаге падает отправка почты. Секреты не раскрывает.
    После завершения отладки endpoint можно удалить.
    """
    import asyncio
    from backend.services.email_service import EmailService

    logger.info("🩺 SMTP diagnose requested")
    result = await asyncio.to_thread(EmailService.diagnose_smtp)
    logger.info(f"🩺 SMTP diagnose result: ok={result.get('ok')}, steps={result.get('steps')}")
    return result


@router.get("/status", tags=["Health"])
async def status(db = Depends(get_db)):
    """
    Extended status endpoint that checks database connectivity.
    
    Args:
        db: Database session dependency
    
    Returns:
        Detailed status information including database connectivity
    """
    # Check database connection
    db_status = "ok"
    try:
        # Execute a simple query to check DB connection
        db.execute("SELECT 1").fetchone()
    except Exception as e:
        db_status = f"error: {str(e)}"
        logger.error(f"Database connection error: {str(e)}")
    
    return {
        "status": "ok",
        "version": settings.VERSION,
        "timestamp": datetime.now().isoformat(),
        "environment": "development" if settings.DEBUG else "production",
        "database": db_status
    }
