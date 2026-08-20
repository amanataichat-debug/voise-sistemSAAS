"""
Query Orchestrator function for JARVIS AI Agent Mode.
Registered in the function registry, intercepted in browser_handler_gemini.py.
"""
from typing import Dict, Any
from backend.functions.base import FunctionBase
from backend.functions.registry import register_function


@register_function
class QueryOrchestratorFunction(FunctionBase):
    """Agent orchestrator function - intercepted before execute() is called."""

    @classmethod
    def get_name(cls) -> str:
        return "query_orchestrator"

    @classmethod
    def get_display_name(cls) -> str:
        return "Agent Orchestrator"

    @classmethod
    def get_description(cls) -> str:
        return (
            "Запускает агентский оркестратор для сложных многошаговых задач. "
            "Используй когда задача требует планирования, поиска информации "
            "или нескольких последовательных действий."
        )

    @classmethod
    def get_parameters(cls) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Задача для агента. Опиши максимально подробно."
                }
            },
            "required": ["task"]
        }

    @classmethod
    def get_example_prompt(cls) -> str:
        return """
        <p>Используй эту функцию когда:</p>
        <ul>
            <li>Задача требует нескольких шагов для выполнения</li>
            <li>Нужно спланировать и выполнить сложное действие</li>
            <li>Требуется поиск информации и её обработка</li>
        </ul>
        <p>Пример вызова:</p>
        <pre>{"task": "Найди информацию о клиенте и создай для него отчёт"}</pre>
        """

    @staticmethod
    async def execute(arguments: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
        # This method is NOT called directly — the function is intercepted
        # in browser_handler_gemini.py before execute() is called
        return {"success": True, "message": "Задача передана оркестратору"}
