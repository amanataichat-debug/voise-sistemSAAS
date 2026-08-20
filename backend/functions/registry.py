"""
Реестр функций для модульной системы.
"""
import importlib
import inspect
import os
import pkgutil
import re
from typing import Dict, Any, List, Type, Optional

from backend.core.logging import get_logger
from backend.functions.base import FunctionBase

logger = get_logger(__name__)

class FunctionRegistry:
    """Реестр функций для управления модульными функциями"""
    
    def __init__(self):
        self._functions: Dict[str, Type[FunctionBase]] = {}
        
    def register(self, function_class: Type[FunctionBase]) -> None:
        """Регистрирует функцию в реестре"""
        name = function_class.get_name()
        if name in self._functions:
            logger.warning(f"Функция '{name}' уже зарегистрирована и будет переопределена")
        
        self._functions[name] = function_class
        logger.info(f"Функция '{name}' зарегистрирована")
        
    def get_function(self, name: str) -> Optional[Type[FunctionBase]]:
        """Возвращает класс функции по имени"""
        # Пробуем найти точное соответствие
        if name in self._functions:
            return self._functions[name]
            
        # Если не нашли, пробуем нормализовать имя функции
        # (для поддержки camelCase/snake_case и разных форматов)
        normalized_name = self.normalize_function_name(name)
        return self._functions.get(normalized_name)
        
    def get_all_functions(self) -> Dict[str, Type[FunctionBase]]:
        """Возвращает все зарегистрированные функции"""
        return self._functions.copy()
        
    def get_definitions(self) -> List[Dict[str, Any]]:
        """Возвращает все определения функций для OpenAI API"""
        return [func.get_definition() for func in self._functions.values()]
        
    def get_enabled_functions(self, enabled_names: List[str]) -> List[Dict[str, Any]]:
        """Возвращает определения только для указанных функций"""
        result = []
        
        for name in enabled_names:
            # Нормализуем имя функции
            normalized_name = self.normalize_function_name(name)
            
            # Ищем функцию сначала по оригинальному имени, потом по нормализованному
            function_class = self._functions.get(name) or self._functions.get(normalized_name)
            
            if function_class:
                result.append(function_class.get_definition())
                
        return result
        
    async def execute_function(
        self, 
        name: str, 
        arguments: Dict[str, Any], 
        context: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """Выполняет функцию с заданными аргументами"""
        # Получаем функцию с нормализацией имени
        function_class = self.get_function(name)
        
        if not function_class:
            return {"error": f"Функция '{name}' не найдена в реестре"}
            
        try:
            return await function_class.execute(arguments, context)
        except Exception as e:
            logger.error(f"Ошибка выполнения функции '{name}': {e}")
            return {"error": str(e)}
            
    def discover_functions(self) -> None:
        """Обнаруживает и загружает все функции в пакете backend.functions"""
        package_name = "backend.functions"
        package_dir = os.path.dirname(os.path.abspath(__file__))
        
        for filename in os.listdir(package_dir):
            # Пропускаем файлы base.py, registry.py и __init__.py
            if filename in ["base.py", "registry.py", "__init__.py", "__pycache__"]:
                continue
                
            # Обрабатываем только Python файлы
            if not filename.endswith(".py"):
                continue
                
            module_name = filename[:-3]  # Удаляем расширение .py
            full_module_name = f"{package_name}.{module_name}"
            
            try:
                module = importlib.import_module(full_module_name)
                
                # Ищем классы функций в модуле
                for item_name, item in inspect.getmembers(module):
                    if (inspect.isclass(item) and 
                        issubclass(item, FunctionBase) and 
                        item is not FunctionBase):
                        self.register(item)
                        
            except Exception as e:
                logger.error(f"Ошибка при загрузке модуля {full_module_name}: {e}")
    
    @staticmethod
    def normalize_function_name(name: str) -> str:
        """
        Нормализует имя функции к стандартному формату snake_case.
        
        Например:
        - sendWebhook -> send_webhook
        - SendWebhook -> send_webhook
        - searchPinecone -> search_pinecone
        """
        if not name:
            return ""
            
        # Специальные случаи для обратной совместимости
        name_lower = name.lower()
        if name_lower == "sendwebhook" or name_lower == "webhook":
            return "send_webhook"
        elif name_lower == "searchpinecone":
            return "search_pinecone"
            
        # Общее правило преобразования camelCase в snake_case
        # Вставляем подчеркивание перед заглавными буквами и приводим к нижнему регистру
        s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
        return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()

# Создаем глобальный экземпляр реестра
registry = FunctionRegistry()

# Определяем функции для упрощения работы
def register_function(cls):
    """Декоратор для регистрации функций"""
    registry.register(cls)
    return cls
    
def normalize_function_name(name: str) -> str:
    """Нормализует имя функции"""
    return registry.normalize_function_name(name)
    
def get_function_definitions() -> List[Dict[str, Any]]:
    """Возвращает все определения функций"""
    return registry.get_definitions()
    
def get_enabled_functions(names: List[str]) -> List[Dict[str, Any]]:
    """Возвращает определения включенных функций"""
    return registry.get_enabled_functions(names)
    
async def execute_function(
    name: str, 
    arguments: Dict[str, Any], 
    context: Dict[str, Any] = None
) -> Dict[str, Any]:
    """Выполняет функцию с заданными аргументами"""
    return await registry.execute_function(name, arguments, context)
    
def discover_functions():
    """Обнаруживает и загружает все функции в пакете backend.functions"""
    registry.discover_functions()
