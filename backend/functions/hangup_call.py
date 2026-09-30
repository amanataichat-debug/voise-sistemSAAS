# backend/functions/hangup_call.py
"""
Функция завершения звонка для отображения в UI.
Фактическое выполнение происходит в VoxEngine скрипте.
"""

from typing import Dict, Any
from backend.functions.base import FunctionBase
from backend.functions.registry import register_function
from backend.core.logging import get_logger

logger = get_logger(__name__)

@register_function
class HangupCallFunction(FunctionBase):
    """
    Функция для завершения текущего звонка.
    
    ВАЖНО: Эта функция создана только для отображения в UI.
    Реальное выполнение происходит локально в VoxEngine скрипте.
    """
    
    @classmethod
    def get_name(cls) -> str:
        return "hangup_call"
    
    @classmethod
    def get_display_name(cls) -> str:
        return "Завершение звонка (только Voximplant)"
    
    @classmethod
    def get_description(cls) -> str:
        return """
        Завершить текущий звонок когда разговор естественно завершен или по просьбе пользователя.
        
        Используйте эту функцию в следующих случаях:
        - Пользователь поблагодарил и завершил разговор
        - Пользователь прямо попросил завершить звонок
        - Разговор достиг естественного завершения
        - Превышено время разговора
        - Возникли технические проблемы
        
        Функция может включать прощальное сообщение перед завершением.
        """
    
    @classmethod
    def get_parameters(cls) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Причина завершения звонка",
                    "enum": [
                        "conversation_completed",  # Разговор естественно завершен
                        "user_request",           # По просьбе пользователя
                        "time_limit_reached",     # Превышен лимит времени
                        "technical_issue",        # Техническая проблема
                        "emergency"               # Экстренное завершение
                    ]
                },
                "farewell_message": {
                    "type": "string",
                    "description": "Прощальное сообщение, которое будет произнесено перед завершением звонка (опционально)",
                    "maxLength": 200,
                    "examples": [
                        "До свидания! Хорошего дня!",
                        "Спасибо за обращение! До встречи!",
                        "Всего доброго!",
                        "Обращайтесь еще!"
                    ]
                }
            },
            "required": ["reason"]
        }
    
    @classmethod
    def get_example_prompt(cls) -> str:
        return """
<p><strong>⚠️ ВАЖНО:</strong> Эта функция работает <strong>ТОЛЬКО</strong> при телефонных звонках через Voximplant!</p>

<p>Используй функцию <code>hangup_call</code> для корректного завершения телефонного разговора.</p>

<p><strong>Когда завершать звонок:</strong></p>
<ul>
    <li>Пользователь поблагодарил и попрощался ("спасибо, до свидания")</li>
    <li>Пользователь прямо попросил завершить звонок</li>
    <li>Разговор достиг естественного завершения (все вопросы решены)</li>
    <li>Превышено максимальное время разговора</li>
    <li>Возникли технические проблемы (плохая связь, ошибки)</li>
</ul>

<p><strong>⚠️ НЕ завершай звонок если:</strong></p>
<ul>
    <li>Пользователь задает дополнительные вопросы</li>
    <li>Разговор все еще продуктивен</li>
    <li>Пользователь явно хочет продолжить общение</li>
    <li>Есть незавершенные задачи (бронирование, запись данных)</li>
</ul>

<p><strong>Параметры функции:</strong></p>
<ul>
    <li><code>reason</code> — причина завершения (обязательно)</li>
    <li><code>farewell_message</code> — прощальная фраза (опционально, макс 200 символов)</li>
</ul>

<p><strong>Причины завершения (reason):</strong></p>
<ul>
    <li><code>conversation_completed</code> — разговор естественно завершен ✅</li>
    <li><code>user_request</code> — пользователь попросил закончить 👋</li>
    <li><code>time_limit_reached</code> — превышен лимит времени ⏰</li>
    <li><code>technical_issue</code> — техническая проблема ⚠️</li>
    <li><code>emergency</code> — экстренное завершение 🚨</li>
</ul>

<p><strong>Примеры вызова:</strong></p>

<p>Пример 1 - Нормальное завершение:</p>
<pre>{
  "reason": "conversation_completed",
  "farewell_message": "До свидания! Хорошего дня!"
}</pre>

<p>Пример 2 - По просьбе пользователя:</p>
<pre>{
  "reason": "user_request",
  "farewell_message": "Спасибо за обращение! Обращайтесь еще!"
}</pre>

<p>Пример 3 - Превышен лимит времени:</p>
<pre>{
  "reason": "time_limit_reached",
  "farewell_message": "Извините, время разговора истекло. Всего доброго!"
}</pre>

<p><strong>💡 Примеры прощальных фраз:</strong></p>
<ul>
    <li>"До свидания! Хорошего дня!" (универсальная)</li>
    <li>"Спасибо за обращение! До встречи!" (для клиентов)</li>
    <li>"Всего доброго! Обращайтесь еще!" (дружелюбная)</li>
    <li>"До свидания!" (короткая)</li>
    <li>"Приятного дня! До новых встреч!" (позитивная)</li>
</ul>

<p><strong>🔧 Техническая информация:</strong></p>
<ul>
    <li>Функция выполняется локально в VoxEngine скрипте</li>
    <li>Backend метод <code>execute()</code> не вызывается при работе через Voximplant</li>
    <li>Прощальное сообщение будет озвучено перед завершением звонка</li>
    <li>После выполнения функции звонок немедленно завершится</li>
</ul>

<p><strong>Сценарии использования:</strong></p>

<p><em>Пользователь:</em> "Спасибо, это все! До свидания!"<br>
<em>Ассистент:</em> [вызывает hangup_call с reason="user_request", farewell="Спасибо за обращение! Хорошего дня!"]</p>

<p><em>Пользователь:</em> "Закончили, спасибо"<br>
<em>Ассистент:</em> [вызывает hangup_call с reason="conversation_completed", farewell="Всего доброго!"]</p>
"""
    
    @staticmethod
    async def execute(arguments: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Подтверждение для модели. Звонок кладёт не эта функция, а SIP-адаптер
        (HandlerSocket в sip_media_adapter.py) по событию function_call.executing:
        дожидается конца прощальной фразы и просит мост положить трубку.
        В виджете звонка нет — результат просто сообщает модели, что разговор окончен.
        """
        reason = arguments.get("reason", "unknown")
        farewell_message = arguments.get("farewell_message", "")
        logger.info(f"[HANGUP] reason={reason}, farewell={farewell_message[:80]}")
        return {
            "success": True,
            "status": "call_ending",
            "message": "Скажи прощальную фразу (farewell_message) и больше ничего: звонок завершится сразу после неё.",
            "reason": reason,
            "farewell_message": farewell_message,
        }
