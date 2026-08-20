# backend/functions/__init__.py
"""
Модульная система функций для WellcomeAI.
"""

from backend.functions.base import FunctionBase
from backend.functions.registry import (
    register_function,
    normalize_function_name,
    get_function_definitions,
    get_enabled_functions,
    execute_function,
    discover_functions
)

# Автоматически загружаем все функции при импорте модуля
discover_functions()

# Алиасы для обратной совместимости
get_all_definitions = get_function_definitions
get_all_openai_definitions = get_function_definitions

__all__ = [
    "FunctionBase",
    "register_function", 
    "normalize_function_name",
    "get_function_definitions",
    "get_all_definitions",  # алиас
    "get_all_openai_definitions",  # алиас
    "get_enabled_functions",
    "execute_function",
    "discover_functions"
]
