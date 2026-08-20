from backend.functions.base import FunctionBase
from typing import Dict, Any


class ShowImageFunction(FunctionBase):
    
    @classmethod
    def get_name(cls) -> str:
        return "show_image"
    
    @classmethod
    def get_description(cls) -> str:
        return "Показывает изображение по URL на экране пользователя во время разговора"
    
    @classmethod
    def get_display_name(cls) -> str:
        return "Показ изображения"
    
    @classmethod
    def get_parameters(cls) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Публичный URL изображения для отображения"
                },
                "description": {
                    "type": "string",
                    "description": "Краткое описание изображения (опционально)"
                }
            },
            "required": ["url"]
        }
    
    @classmethod
    def get_example_prompt(cls) -> str:
        return """
        <p>Добавьте в системный промпт описание когда показывать картинки:</p>
        <pre>Когда нужно показать изображение — вызови функцию show_image.
Примеры URL для показа: https://site.com/image.jpg</pre>
        <p>Пользователь увидит изображение на весь экран во время разговора.</p>
        """
    
    @staticmethod
    async def execute(arguments: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
        url = arguments.get("url", "")
        description = arguments.get("description", "")
        
        if not url:
            return {"success": False, "error": "URL не указан"}
        
        # Функция просто возвращает данные — виджет обработает их сам
        return {
            "success": True,
            "url": url,
            "description": description,
            "action": "show_image"
        }
