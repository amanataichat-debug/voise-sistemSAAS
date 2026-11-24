"""
Logging configuration for WellcomeAI application.
Sets up structured logging with consistent format.
"""

import atexit
import logging
import logging.handlers
import queue
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

# ── Неблокирующая запись логов (23.11.2026) ──────────────────────────────────────────────────────────────
# Раньше stdout и файл писались прямо в потоке, который логирует, — то есть в общем цикле событий, где идёт звук
# звонков: когда Render медленно забирал stdout, запись строки ждала, и сторож цикла видел замирания звука.
# Теперь корневой логгер кладёт запись в очередь (это мгновенно), а печатает её отдельный поток QueueListener.
# Обработчик журнала звонка (call_log._ContextHandler) по-прежнему синхронный — ему нужен contextvar звонка.
# Поток не переживает fork (gunicorn preload_app), поэтому в дочернем процессе очередь и поток создаются заново.
# LOG_QUEUE=0 — прежняя синхронная запись.
LOG_QUEUE = os.getenv("LOG_QUEUE", "1").lower() not in ("0", "false", "no")
_listener: Optional[logging.handlers.QueueListener] = None
_queue_handler: Optional[logging.Handler] = None
_out_handlers: list = []
_fork_hook_registered = False


def _stop_listener() -> None:
    global _listener
    if _listener is not None:
        try:
            _listener.stop()  # дописывает всё, что осталось в очереди
        except Exception:
            pass
        _listener = None


def _start_listener(root_logger: logging.Logger) -> None:
    global _listener, _queue_handler
    q = queue.SimpleQueue()
    qh = logging.handlers.QueueHandler(q)
    qh.setLevel(logging.NOTSET)
    if _queue_handler is not None and _queue_handler in root_logger.handlers:
        root_logger.removeHandler(_queue_handler)
    root_logger.handlers.insert(0, qh)
    _queue_handler = qh
    _listener = logging.handlers.QueueListener(q, *_out_handlers, respect_handler_level=True)
    _listener.start()


def _after_fork_in_child() -> None:
    global _listener
    if not LOG_QUEUE or not _out_handlers:
        return
    _listener = None  # поток родителя в дочернем процессе не существует
    root = logging.getLogger()
    if _queue_handler is None or _queue_handler not in root.handlers:
        return  # корневой логгер перенастроили — очередь не наша
    _start_listener(root)


def _install_handlers(root_logger: logging.Logger, handlers: list) -> None:
    global _out_handlers, _fork_hook_registered
    if not LOG_QUEUE:
        for h in handlers:
            root_logger.addHandler(h)
        return
    _stop_listener()
    _out_handlers = list(handlers)
    _start_listener(root_logger)
    if not _fork_hook_registered:
        if hasattr(os, "register_at_fork"):
            os.register_at_fork(after_in_child=_after_fork_in_child)
        atexit.register(_stop_listener)
        _fork_hook_registered = True


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

    # File handler (JSON format for processing)
    log_file = os.path.join(LOG_DIR, f"wellcomeai_{time.strftime('%Y%m%d')}.log")
    out = [console_handler]
    # Файл JSON — только по LOG_FILE=1: на проде его и так стирали basicConfig(force=True) в 4 модулях, Render
    # читает stdout, а файл на диске сервиса только рос (23.11.2026).
    if os.getenv("LOG_FILE", "0").lower() in ("1", "true", "yes"):
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(effective_log_level)
        file_handler.setFormatter(JsonFormatter())
        out.append(file_handler)

    _install_handlers(root_logger, out)
    
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
