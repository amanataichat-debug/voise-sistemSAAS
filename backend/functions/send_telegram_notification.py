# backend/functions/send_telegram_notification.py
"""
Функция для отправки уведомлений через Telegram Bot API.
Поддерживает отправку в личные чаты, группы и каналы.
"""
import re
import aiohttp
import asyncio
from typing import Dict, Any, Optional

from backend.core.logging import get_logger
from backend.functions.base import FunctionBase
from backend.functions.registry import register_function

logger = get_logger(__name__)


def extract_bot_token_from_prompt(prompt: str) -> Optional[str]:
    """
    Извлекает токен Telegram бота из системного промпта.
    
    Примеры:
    - Telegram Bot Token: 123456:ABC-DEF...
    - Bot Token: 123456:ABC-DEF...
    - Token: 123456:ABC-DEF...
    """
    if not prompt:
        return None
    
    # Паттерны для поиска токена
    patterns = [
        r'Telegram\s+Bot\s+Token:\s*([0-9]+:[A-Za-z0-9_-]+)',
        r'Bot\s+Token:\s*([0-9]+:[A-Za-z0-9_-]+)',
        r'Token:\s*([0-9]+:[A-Za-z0-9_-]+)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, prompt, re.IGNORECASE)
        if match:
            return match.group(1)
    
    return None


@register_function
class SendTelegramNotificationFunction(FunctionBase):
    """Функция для отправки уведомлений через Telegram Bot API"""
    
    @classmethod
    def get_name(cls) -> str:
        return "send_telegram_notification"
    
    @classmethod
    def get_display_name(cls) -> str:
        return "Отправка уведомления в Telegram"
    
    @classmethod
    def get_description(cls) -> str:
        return "Отправляет текстовое уведомление через Telegram бота в личный чат, группу или канал"
    
    @classmethod
    def get_parameters(cls) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "bot_token": {
                    "type": "string",
                    "description": "Токен Telegram бота (формат: 123456:ABC-DEF...)"
                },
                "chat_id": {
                    "type": "string",
                    "description": "ID чата, группы, канала или @username (например: '123456789', '-1001234567890', '@my_channel')"
                },
                "message": {
                    "type": "string",
                    "description": "Текст сообщения для отправки"
                },
                "parse_mode": {
                    "type": "string",
                    "description": "Режим форматирования текста",
                    "enum": ["HTML", "Markdown", "MarkdownV2"],
                    "default": "HTML"
                },
                "disable_notification": {
                    "type": "boolean",
                    "description": "Отправить тихое уведомление (без звука)",
                    "default": False
                }
            },
            "required": ["chat_id", "message"]
        }
    
    @classmethod
    def get_example_prompt(cls) -> str:
        return """
<p>Используй функцию <code>send_telegram_notification</code> для отправки уведомлений через Telegram бота.</p>

<p><strong>Когда использовать:</strong></p>
<ul>
    <li>Пользователь просит отправить уведомление в Telegram</li>
    <li>Нужно оповестить команду или клиента</li>
    <li>Требуется записать важную информацию в чат</li>
    <li>Логирование событий в Telegram канал</li>
    <li>Отправка алертов, напоминаний, уведомлений</li>
</ul>

<p><strong>Настройка в промпте:</strong></p>
<p>Укажи в системном промпте токен бота (опционально):</p>
<pre>Telegram Bot Token: 123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11</pre>

<p><strong>Параметры функции:</strong></p>
<ul>
    <li><code>bot_token</code> — токен бота (можно указать в промпте или передать напрямую)</li>
    <li><code>chat_id</code> — ID получателя (обязательно)</li>
    <li><code>message</code> — текст сообщения (обязательно)</li>
    <li><code>parse_mode</code> — форматирование: HTML, Markdown, MarkdownV2 (по умолчанию HTML)</li>
    <li><code>disable_notification</code> — тихое уведомление без звука (по умолчанию false)</li>
</ul>

<p><strong>Типы получателей (chat_id):</strong></p>
<ul>
    <li><strong>Личный чат:</strong> <code>"123456789"</code> — ID пользователя</li>
    <li><strong>Группа:</strong> <code>"-123456789"</code> — ID группы (отрицательное число)</li>
    <li><strong>Супергруппа/канал:</strong> <code>"-1001234567890"</code> — длинный отрицательный ID</li>
    <li><strong>Публичный канал:</strong> <code>"@my_channel"</code> — username канала</li>
    <li><strong>Публичная группа:</strong> <code>"@my_group"</code> — username группы</li>
</ul>

<p><strong>Пример 1 - Простое уведомление:</strong></p>
<pre>{
  "bot_token": "123456:ABC-DEF...",
  "chat_id": "123456789",
  "message": "Новая заявка от клиента!"
}</pre>

<p><strong>Пример 2 - Форматированное сообщение:</strong></p>
<pre>{
  "bot_token": "123456:ABC-DEF...",
  "chat_id": "@my_channel",
  "message": "&lt;b&gt;Важное уведомление!&lt;/b&gt;\n\n&lt;i&gt;Клиент:&lt;/i&gt; Иван Петров\n&lt;i&gt;Телефон:&lt;/i&gt; +79991234567",
  "parse_mode": "HTML"
}</pre>

<p><strong>Пример 3 - Тихое уведомление в группу:</strong></p>
<pre>{
  "chat_id": "-1001234567890",
  "message": "Статистика за день обновлена",
  "disable_notification": true
}</pre>

<p><strong>Результат успешной отправки:</strong></p>
<pre>{
  "success": true,
  "message": "Сообщение успешно отправлено",
  "message_id": 12345,
  "chat_id": "123456789",
  "message_length": 42
}</pre>

<p><strong>HTML форматирование (parse_mode: "HTML"):</strong></p>
<ul>
    <li><code>&lt;b&gt;жирный&lt;/b&gt;</code> → <strong>жирный</strong></li>
    <li><code>&lt;i&gt;курсив&lt;/i&gt;</code> → <em>курсив</em></li>
    <li><code>&lt;u&gt;подчеркнутый&lt;/u&gt;</code> → <u>подчеркнутый</u></li>
    <li><code>&lt;s&gt;зачеркнутый&lt;/s&gt;</code> → <s>зачеркнутый</s></li>
    <li><code>&lt;code&gt;код&lt;/code&gt;</code> → <code>код</code></li>
    <li><code>&lt;pre&gt;блок кода&lt;/pre&gt;</code> → блок кода</li>
    <li><code>&lt;a href="url"&gt;ссылка&lt;/a&gt;</code> → ссылка</li>
</ul>

<p><strong>Markdown форматирование (parse_mode: "Markdown"):</strong></p>
<ul>
    <li><code>*жирный*</code> → <strong>жирный</strong></li>
    <li><code>_курсив_</code> → <em>курсив</em></li>
    <li><code>`код`</code> → <code>код</code></li>
    <li><code>[ссылка](url)</code> → ссылка</li>
</ul>

<p><strong>💡 Как узнать chat_id:</strong></p>

<p><strong>Для личного чата:</strong></p>
<ol>
    <li>Пользователь отправляет боту команду <code>/start</code></li>
    <li>Бот видит <code>message.chat.id</code> в webhook</li>
    <li>Или используй <code>https://api.telegram.org/bot&lt;TOKEN&gt;/getUpdates</code></li>
</ol>

<p><strong>Для группы/канала:</strong></p>
<ol>
    <li>Добавь бота в группу/канал (сделай администратором для каналов)</li>
    <li>Напиши что-то в группу</li>
    <li>Используй ботов: @userinfobot, @getidsbot или @raw_data_bot</li>
    <li>Или смотри в <code>getUpdates</code></li>
</ol>

<p><strong>⚠️ Важно:</strong></p>
<ul>
    <li>Бот должен быть добавлен в группу/канал до отправки сообщений</li>
    <li>Для каналов бот должен быть администратором с правом публикации</li>
    <li>Токен бота храни в безопасности (не показывай пользователям)</li>
    <li>Используй HTML форматирование для красивых сообщений</li>
</ul>

<p><strong>Обработка ошибок:</strong></p>
<ul>
    <li><strong>400 Bad Request</strong> → неверный chat_id или формат сообщения</li>
    <li><strong>401 Unauthorized</strong> → неверный bot_token</li>
    <li><strong>403 Forbidden</strong> → бот заблокирован пользователем или не имеет прав</li>
    <li><strong>404 Not Found</strong> → чат не найден или бот не добавлен</li>
</ul>

<p><strong>🎯 Примеры использования:</strong></p>

<p><em>Пользователь:</em> "Отправь уведомление в наш рабочий чат, что клиент оставил заявку"<br>
<em>Ассистент:</em> [вызывает send_telegram_notification с данными клиента] → "Уведомление отправлено в Telegram!"</p>

<p><em>Пользователь:</em> "Запиши мои контакты в Telegram канал"<br>
<em>Ассистент:</em> [вызывает send_telegram_notification с контактами] → "Контакты сохранены в канал!"</p>
"""
    
    @staticmethod
    async def execute(arguments: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Отправляет сообщение через Telegram Bot API.
        
        Args:
            arguments: Параметры функции
            context: Контекст выполнения
            
        Returns:
            Результат отправки сообщения
        """
        try:
            context = context or {}
            assistant_config = context.get("assistant_config")
            
            # Получаем параметры
            bot_token = arguments.get("bot_token")
            chat_id = arguments.get("chat_id")
            message = arguments.get("message")
            parse_mode = arguments.get("parse_mode", "HTML")
            disable_notification = arguments.get("disable_notification", False)
            
            # Валидация обязательных параметров
            if not message:
                return {"success": False, "error": "Message is required"}
            
            if not chat_id:
                return {"success": False, "error": "chat_id is required"}
            
            # Если токен не передан, пытаемся извлечь из промпта
            if not bot_token and assistant_config:
                if hasattr(assistant_config, "system_prompt") and assistant_config.system_prompt:
                    bot_token = extract_bot_token_from_prompt(assistant_config.system_prompt)
                    if bot_token:
                        logger.info("[TELEGRAM] Токен бота извлечен из промпта")
            
            if not bot_token:
                return {
                    "success": False,
                    "error": "bot_token is required (укажите в параметрах или в системном промпте)"
                }
            
            # Валидация формата токена
            if not re.match(r'^\d+:[A-Za-z0-9_-]+$', bot_token):
                return {
                    "success": False,
                    "error": "Неверный формат токена. Должен быть: 123456:ABC-DEF..."
                }
            
            logger.info(f"[TELEGRAM] ━━━━━━━━━━━━━━━━━━━━━━━━━")
            logger.info(f"[TELEGRAM] 📤 Отправка сообщения")
            logger.info(f"[TELEGRAM] 💬 Chat ID: {chat_id}")
            logger.info(f"[TELEGRAM] 📝 Message: {message[:100]}...")
            logger.info(f"[TELEGRAM] 🎨 Parse mode: {parse_mode}")
            
            # Формируем URL для Telegram Bot API
            telegram_api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            
            # Подготавливаем данные для отправки
            payload = {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": parse_mode,
                "disable_notification": disable_notification
            }
            
            # Отправляем запрос к Telegram API
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    telegram_api_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    
                    response_data = await response.json()
                    
                    if response.status == 200 and response_data.get("ok"):
                        # Успешная отправка
                        result = response_data.get("result", {})
                        message_id = result.get("message_id")
                        
                        logger.info(f"[TELEGRAM] ✅ Сообщение отправлено успешно!")
                        logger.info(f"[TELEGRAM] 🆔 Message ID: {message_id}")
                        logger.info(f"[TELEGRAM] ━━━━━━━━━━━━━━━━━━━━━━━━━")
                        
                        return {
                            "success": True,
                            "message": "Сообщение успешно отправлено",
                            "message_id": message_id,
                            "chat_id": chat_id,
                            "message_length": len(message)
                        }
                    
                    else:
                        # Ошибка при отправке
                        error_description = response_data.get("description", "Unknown error")
                        error_code = response_data.get("error_code", response.status)
                        
                        logger.error(f"[TELEGRAM] ❌ Ошибка: {error_description}")
                        logger.error(f"[TELEGRAM] 🔢 Error code: {error_code}")
                        
                        # Понятные сообщения об ошибках
                        if error_code == 400:
                            return {
                                "success": False,
                                "error": "Неверный запрос",
                                "details": error_description,
                                "suggestion": "Проверьте chat_id и формат сообщения"
                            }
                        elif error_code == 401:
                            return {
                                "success": False,
                                "error": "Неверный токен бота",
                                "details": error_description,
                                "suggestion": "Проверьте правильность bot_token"
                            }
                        elif error_code == 403:
                            return {
                                "success": False,
                                "error": "Доступ запрещен",
                                "details": error_description,
                                "suggestion": "Бот заблокирован пользователем или не имеет прав в группе/канале"
                            }
                        elif error_code == 404:
                            return {
                                "success": False,
                                "error": "Чат не найден",
                                "details": error_description,
                                "suggestion": "Проверьте chat_id или добавьте бота в группу/канал"
                            }
                        else:
                            return {
                                "success": False,
                                "error": f"Ошибка Telegram API: {error_description}",
                                "error_code": error_code
                            }
        
        except asyncio.TimeoutError:
            logger.error("[TELEGRAM] ⏱️ Таймаут при отправке запроса")
            return {
                "success": False,
                "error": "Timeout: превышено время ожидания ответа от Telegram API"
            }
        
        except aiohttp.ClientError as e:
            logger.error(f"[TELEGRAM] 🌐 Сетевая ошибка: {str(e)}")
            return {
                "success": False,
                "error": f"Сетевая ошибка: {str(e)}"
            }
        
        except Exception as e:
            logger.error(f"[TELEGRAM] ❌ Критическая ошибка: {str(e)}")
            return {
                "success": False,
                "error": f"Критическая ошибка: {str(e)}"
            }
