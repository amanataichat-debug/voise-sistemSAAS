# backend/functions/base.py
"""
Базовый класс для функций в модульной системе.
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List

class FunctionBase(ABC):
    """Базовый класс для всех функций"""
    
    @classmethod
    @abstractmethod
    def get_name(cls) -> str:
        """Возвращает имя функции"""
        pass
        
    @classmethod
    @abstractmethod
    def get_description(cls) -> str:
        """Возвращает описание функции"""
        pass
        
    @classmethod
    @abstractmethod
    def get_parameters(cls) -> Dict[str, Any]:
        """Возвращает параметры функции"""
        pass
    
    @classmethod
    def get_display_name(cls) -> str:
        """
        Возвращает читаемое имя функции для UI.
        
        По умолчанию преобразует snake_case в Title Case.
        Например: 'send_webhook' -> 'Send Webhook'
        
        Может быть переопределено в дочернем классе для кастомного отображения.
        """
        return cls.get_name().replace('_', ' ').title()
    
    @classmethod
    def get_example_prompt(cls) -> str:
        """
        Возвращает пример использования функции в промпте (HTML разметка).
        
        По умолчанию возвращает пустую строку.
        Переопределите в дочернем классе для отображения подробных инструкций в UI.
        
        Пример возвращаемого значения:
        '''
        <p>Используй эту функцию когда:</p>
        <ul>
            <li>Пользователь просит отправить данные</li>
            <li>Нужно записать информацию</li>
        </ul>
        <p>Пример вызова:</p>
        <pre>{"param1": "value1"}</pre>
        '''
        """
        return ""
        
    @classmethod
    def get_definition(cls) -> Dict[str, Any]:
        """
        Возвращает полное определение функции для OpenAI/Gemini API.
        
        Включает базовые поля (name, description, parameters) и метаданные для UI.
        """
        return {
            "name": cls.get_name(),
            "description": cls.get_description(),
            "parameters": cls.get_parameters(),
            # Метаданные для UI (не влияют на работу LLM)
            "display_name": cls.get_display_name(),
            "example_prompt": cls.get_example_prompt()
        }
        
    @staticmethod
    @abstractmethod
    async def execute(arguments: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
        """Выполняет функцию с заданными аргументами"""
        pass
