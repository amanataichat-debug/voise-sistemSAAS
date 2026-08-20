# backend/functions/get_current_time.py
"""
Функция для получения текущего времени и даты.
"""
from datetime import datetime
from typing import Dict, Any
import pytz

from backend.core.logging import get_logger
from backend.functions.base import FunctionBase
from backend.functions.registry import register_function

logger = get_logger(__name__)

@register_function
class GetCurrentTimeFunction(FunctionBase):
    """Функция для получения текущего времени и даты"""
    
    @classmethod
    def get_name(cls) -> str:
        return "get_current_time"
    
    @classmethod
    def get_display_name(cls) -> str:
        return "Получение текущего времени"
    
    @classmethod
    def get_description(cls) -> str:
        return "Возвращает текущее время и дату в указанном часовом поясе"
    
    @classmethod
    def get_parameters(cls) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": "Часовой пояс (например: 'Europe/Moscow', 'America/New_York', 'UTC')",
                    "default": "UTC"
                },
                "format": {
                    "type": "string",
                    "description": "Формат вывода: 'full' (полная дата и время), 'time' (только время), 'date' (только дата)",
                    "enum": ["full", "time", "date"],
                    "default": "full"
                }
            }
        }
    
    @classmethod
    def get_example_prompt(cls) -> str:
        return """
<p>Используй функцию <code>get_current_time</code> когда пользователь спрашивает про время или дату.</p>

<p><strong>Когда использовать:</strong></p>
<ul>
    <li>"Который час?", "Какое сейчас время?"</li>
    <li>"Какое сегодня число?", "Какая дата?"</li>
    <li>"Сколько времени в Москве/Нью-Йорке?"</li>
    <li>Для записи времени звонка, бронирования</li>
</ul>

<p><strong>Параметры:</strong></p>
<ul>
    <li><code>timezone</code> — часовой пояс (по умолчанию UTC)</li>
    <li><code>format</code> — формат: 'full', 'time', 'date'</li>
</ul>

<p><strong>Примеры:</strong></p>
<pre>{"timezone": "Europe/Moscow", "format": "full"}
{"timezone": "America/New_York", "format": "time"}
{"format": "date"}</pre>

<p><strong>Популярные часовые пояса:</strong></p>
<ul>
    <li>Europe/Moscow — Москва (MSK)</li>
    <li>Europe/London — Лондон (GMT)</li>
    <li>America/New_York — Нью-Йорк (EST)</li>
    <li>Asia/Tokyo — Токио (JST)</li>
    <li>UTC — универсальное время</li>
</ul>
"""
    
    @staticmethod
    async def execute(arguments: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Возвращает текущее время и дату.
        """
        try:
            timezone_str = arguments.get("timezone", "UTC")
            format_type = arguments.get("format", "full")
            
            # Получаем текущее время в указанном часовом поясе
            try:
                tz = pytz.timezone(timezone_str)
            except pytz.exceptions.UnknownTimeZoneError:
                return {
                    "error": f"Неизвестный часовой пояс: {timezone_str}",
                    "suggestion": "Используйте формат 'Europe/Moscow', 'America/New_York', 'UTC'"
                }
            
            now = datetime.now(tz)
            
            # Форматируем вывод
            if format_type == "time":
                formatted = now.strftime("%H:%M:%S")
                readable = now.strftime("%H:%M")
            elif format_type == "date":
                formatted = now.strftime("%Y-%m-%d")
                readable = now.strftime("%d.%m.%Y")
            else:  # full
                formatted = now.strftime("%Y-%m-%d %H:%M:%S")
                readable = now.strftime("%d.%m.%Y %H:%M")
            
            logger.info(f"[GET_TIME] Timezone: {timezone_str}, Format: {format_type}, Time: {formatted}")
            
            return {
                "success": True,
                "timezone": timezone_str,
                "timestamp": now.timestamp(),
                "formatted": formatted,
                "readable": readable,
                "iso": now.isoformat(),
                "weekday": now.strftime("%A"),
                "weekday_ru": ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"][now.weekday()]
            }
            
        except Exception as e:
            logger.error(f"[GET_TIME] Ошибка: {str(e)}")
            return {
                "error": f"Не удалось получить время: {str(e)}"
            }
