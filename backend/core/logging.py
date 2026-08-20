"""
Logging configuration for WellcomeAI application.
Sets up structured logging with consistent format.
"""

import logging
import sys
import time
import os
from typing import Dict, Any, Optional
import traceback
import json

from backend.core.config import settings

# Constants
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
JSON_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"

# ✅ ИСПРАВЛЕНО: Принудительный DEBUG режим через env
FORCE_DEBUG = os.getenv('FORCE_DEBUG', 'False').lower() == 'true'
LOG_LEVEL = logging.DEBUG if (settings.DEBUG or FORCE_DEBUG) else logging.INFO

LOG_DIR = os.path.join(os.getcwd(), "logs")

# Ensure log directory exists
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

class JsonFormatter(logging.Formatter):
    """
    Custom formatter for JSON structured logging
    """
    def format(self, record):
        log_data = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
        }
        
        # Add exception info if available
        if record.exc_info:
            log_data["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
                "traceback": traceback.format_exception(*record.exc_info)
            }
            
        # Add extra fields if available
        if hasattr(record, "extra") and record.extra:
            log_data.update(record.extra)
            
        return json.dumps(log_data)

def setup_logging(force_debug: bool = False):
    """
    Configure the logging system for the application
    
    Args:
        force_debug: Force DEBUG level regardless of settings
    """
    # ✅ ИСПРАВЛЕНО: Правильная проверка с определенной переменной
    effective_log_level = logging.DEBUG if (force_debug or FORCE_DEBUG or settings.DEBUG) else logging.INFO
    
    # Reset root logger
    root_logger = logging.getLogger()
    root_logger.handlers = []
    root_logger.setLevel(effective_log_level)
    
    # Console handler (human-readable format)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(effective_log_level)
    console_formatter = logging.Formatter(LOG_FORMAT)
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)
    
    # File handler (JSON format for processing)
    log_file = os.path.join(LOG_DIR, f"wellcomeai_{time.strftime('%Y%m%d')}.log")
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(effective_log_level)
    file_handler.setFormatter(JsonFormatter())
    root_logger.addHandler(file_handler)
    
    # Disable propagation for some noisy loggers
    for logger_name in ["uvicorn", "uvicorn.access", "uvicorn.error"]:
        for handler in logging.getLogger(logger_name).handlers:
            logging.getLogger(logger_name).removeHandler(handler)
        logging.getLogger(logger_name).propagate = False
    
    # Log startup message
    level_name = logging.getLevelName(effective_log_level)
    root_logger.info(
        f"Logging system initialized. Application: {settings.APP_NAME}, "
        f"Version: {settings.VERSION}, Level: {level_name}, "
        f"Environment: {'Development' if settings.DEBUG else 'Production'}"
    )
    
    # ✅ Явное сообщение если DEBUG режим включен
    if effective_log_level == logging.DEBUG:
        root_logger.info("🔍 DEBUG MODE ENABLED - All debug logs will be visible")

def get_logger(name: str) -> logging.Logger:
    """
    Get a logger with the given name
    
    Args:
        name: The name for the logger
        
    Returns:
        Logger instance
    """
    return logging.getLogger(name)

class LoggerAdapter(logging.LoggerAdapter):
    """
    Logger adapter for adding context information to logs
    """
    def process(self, msg, kwargs):
        # Add extra fields
        kwargs.setdefault("extra", {}).update(self.extra)
        return msg, kwargs

def get_context_logger(name: str, context: Dict[str, Any]) -> LoggerAdapter:
    """
    Get a logger with context information
    
    Args:
        name: The name for the logger
        context: Dictionary of context information to include in logs
        
    Returns:
        LoggerAdapter instance
    """
    logger = get_logger(name)
    return LoggerAdapter(logger, {"extra": context})
