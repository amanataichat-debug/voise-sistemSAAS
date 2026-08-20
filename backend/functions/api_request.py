# backend/functions/api_request.py
"""
Универсальная функция для запросов к внешним API.
Поддерживает GET, POST, PUT, DELETE, PATCH методы.
"""
import aiohttp
import asyncio
import json
import re
from typing import Dict, Any, Optional, List
from urllib.parse import urlencode

from backend.core.logging import get_logger
from backend.functions.base import FunctionBase
from backend.functions.registry import register_function

logger = get_logger(__name__)


def extract_api_config_from_prompt(prompt: str) -> Optional[Dict[str, Any]]:
    """
    Извлекает конфигурацию API из системного промпта.
    
    Примеры:
    - API URL: https://api.example.com
    - API Key: sk-abc123
    - API Header: Authorization: Bearer token123
    """
    if not prompt:
        return None
    
    config = {}
    
    # Паттерн для URL
    url_pattern = r'API\s+URL:\s*(https?://[^\s"\'<>]+)'
    url_match = re.search(url_pattern, prompt, re.IGNORECASE)
    if url_match:
        config['base_url'] = url_match.group(1)
    
    # Паттерн для API Key
    key_pattern = r'API\s+Key:\s*([^\s"\'<>]+)'
    key_match = re.search(key_pattern, prompt, re.IGNORECASE)
    if key_match:
        config['api_key'] = key_match.group(1)
    
    # Паттерн для Headers (может быть несколько)
    # API Header: Authorization: Bearer token
    header_pattern = r'API\s+Header:\s*([^:]+):\s*([^\n]+)'
    headers = {}
    for match in re.finditer(header_pattern, prompt, re.IGNORECASE):
        header_name = match.group(1).strip()
        header_value = match.group(2).strip()
        headers[header_name] = header_value
    
    if headers:
        config['headers'] = headers
    
    return config if config else None


@register_function
class ApiRequestFunction(FunctionBase):
    """Универсальная функция для HTTP запросов к внешним API"""
    
    @classmethod
    def get_name(cls) -> str:
        return "api_request"
    
    @classmethod
    def get_display_name(cls) -> str:
        return "HTTP запрос к внешнему API"
    
    @classmethod
    def get_description(cls) -> str:
        return "Отправляет HTTP запрос (GET/POST/PUT/DELETE/PATCH) к любому внешнему API"
    
    @classmethod
    def get_parameters(cls) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Полный URL для запроса (например: https://api.example.com/v1/users)"
                },
                "method": {
                    "type": "string",
                    "description": "HTTP метод",
                    "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"]
                },
                "headers": {
                    "type": "object",
                    "description": "HTTP заголовки в формате JSON объекта (например: {\"Authorization\": \"Bearer token\", \"Content-Type\": \"application/json\"})"
                },
                "query_params": {
                    "type": "object",
                    "description": "Query параметры URL в формате JSON объекта (например: {\"page\": \"1\", \"limit\": \"10\"})"
                },
                "body": {
                    "type": "object",
                    "description": "Тело запроса для POST/PUT/PATCH (будет отправлено как JSON)"
                },
                "timeout": {
                    "type": "integer",
                    "description": "Таймаут запроса в секундах (по умолчанию 30)"
                }
            },
            "required": ["url"]
        }
    
    @classmethod
    def get_example_prompt(cls) -> str:
        return """
<p>Используй функцию <code>api_request</code> для запросов к любым внешним API.</p>

<p><strong>Зачем это нужно?</strong></p>
<p>Универсальная интеграция с любыми сервисами: CRM, ERP, платежные системы, базы данных, микросервисы.</p>

<p><strong>Когда использовать:</strong></p>
<ul>
    <li>Нужно получить данные из внешней системы</li>
    <li>Требуется создать/обновить запись в стороннем сервисе</li>
    <li>Проверка статуса, наличия товара, цен</li>
    <li>Интеграция с REST API любого сервиса</li>
    <li>Получение курсов валют, погоды, геоданных</li>
</ul>

<p><strong>Настройка в промпте (опционально):</strong></p>
<pre>API URL: https://api.example.com/v1
API Key: sk-abc123xyz
API Header: Authorization: Bearer token123
API Header: X-Custom-Header: value</pre>

<p><strong>Параметры функции:</strong></p>
<ul>
    <li><code>url</code> — полный URL API endpoint (обязательно)</li>
    <li><code>method</code> — GET, POST, PUT, DELETE, PATCH (по умолчанию GET)</li>
    <li><code>headers</code> — объект с HTTP заголовками</li>
    <li><code>query_params</code> — параметры URL (?key=value)</li>
    <li><code>body</code> — тело запроса (для POST/PUT/PATCH)</li>
    <li><code>timeout</code> — таймаут в секундах (по умолчанию 30)</li>
</ul>

<p><strong>Пример 1 - GET запрос с параметрами:</strong></p>
<pre>{
  "url": "https://api.example.com/products",
  "method": "GET",
  "query_params": {
    "category": "electronics",
    "limit": "10"
  },
  "headers": {
    "Authorization": "Bearer sk-abc123"
  }
}</pre>

<p><strong>Пример 2 - POST запрос (создание записи):</strong></p>
<pre>{
  "url": "https://api.crm.com/contacts",
  "method": "POST",
  "headers": {
    "Authorization": "Bearer token123",
    "Content-Type": "application/json"
  },
  "body": {
    "name": "Иван Петров",
    "phone": "+79991234567",
    "email": "ivan@mail.com",
    "source": "voice_call"
  }
}</pre>

<p><strong>Пример 3 - PUT запрос (обновление):</strong></p>
<pre>{
  "url": "https://api.example.com/orders/12345",
  "method": "PUT",
  "headers": {
    "X-API-Key": "secret-key"
  },
  "body": {
    "status": "confirmed",
    "updated_at": "2024-12-01T10:00:00Z"
  }
}</pre>

<p><strong>Пример 4 - DELETE запрос:</strong></p>
<pre>{
  "url": "https://api.example.com/users/999",
  "method": "DELETE",
  "headers": {
    "Authorization": "Bearer admin-token"
  }
}</pre>

<p><strong>Что возвращается:</strong></p>
<pre>{
  "success": true,
  "status_code": 200,
  "data": {
    // ответ от API
  },
  "headers": {
    "content-type": "application/json",
    ...
  },
  "execution_time_ms": 245
}</pre>

<p><strong>Обработка ошибок:</strong></p>
<pre>{
  "success": false,
  "error": "Request failed",
  "status_code": 404,
  "details": "Not Found",
  "url": "https://api.example.com/..."
}</pre>

<p><strong>💡 Популярные сценарии:</strong></p>

<p><strong>1. Проверка статуса заказа:</strong></p>
<pre>GET https://shop.com/api/orders/12345
→ Узнать статус доставки</pre>

<p><strong>2. Создание лида в CRM:</strong></p>
<pre>POST https://crm.com/api/leads
Body: {"name": "...", "phone": "..."}
→ Сохранить клиента</pre>

<p><strong>3. Проверка наличия товара:</strong></p>
<pre>GET https://warehouse.com/api/stock?sku=ABC123
→ Сколько товара на складе</pre>

<p><strong>4. Получение курса валют:</strong></p>
<pre>GET https://api.exchangerate.com/latest?base=USD
→ Актуальные курсы</pre>

<p><strong>5. Отправка в Slack/Discord:</strong></p>
<pre>POST https://hooks.slack.com/services/XXX
Body: {"text": "Новая заявка!"}
→ Уведомление команде</pre>

<p><strong>⚙️ Продвинутые возможности:</strong></p>

<p><strong>Базовая авторизация (Basic Auth):</strong></p>
<pre>{
  "headers": {
    "Authorization": "Basic base64(user:password)"
  }
}</pre>

<p><strong>Bearer Token:</strong></p>
<pre>{
  "headers": {
    "Authorization": "Bearer eyJhbGc..."
  }
}</pre>

<p><strong>API Key в заголовке:</strong></p>
<pre>{
  "headers": {
    "X-API-Key": "your-api-key"
  }
}</pre>

<p><strong>API Key в query параметрах:</strong></p>
<pre>{
  "query_params": {
    "api_key": "your-api-key"
  }
}</pre>

<p><strong>⚠️ Безопасность:</strong></p>
<ul>
    <li>Никогда не логируй API ключи и токены</li>
    <li>Используй HTTPS для передачи чувствительных данных</li>
    <li>Храни ключи в системном промпте или переменных окружения</li>
    <li>Устанавливай разумные timeout'ы (30 сек по умолчанию)</li>
</ul>

<p><strong>🔧 Требования:</strong></p>
<ul>
    <li>URL должен начинаться с http:// или https://</li>
    <li>API должен быть доступен из вашей сети</li>
    <li>Соблюдай rate limits внешних API</li>
</ul>

<p><strong>💬 Примеры диалогов:</strong></p>

<p><em>Пользователь:</em> "Проверь статус моего заказа номер 12345"<br>
<em>Ассистент:</em> [вызывает api_request: GET /orders/12345] → "Ваш заказ в пути, прибудет завтра"</p>

<p><em>Пользователь:</em> "Запиши меня на консультацию завтра в 15:00"<br>
<em>Ассистент:</em> [вызывает api_request: POST /bookings с данными] → "Отлично! Вы записаны на завтра в 15:00"</p>
"""
    
    @staticmethod
    async def execute(arguments: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Выполняет HTTP запрос к внешнему API.
        """
        import time
        
        context = context or {}
        assistant_config = context.get("assistant_config")
        
        start_time = time.time()
        
        try:
            # Получаем параметры
            url = arguments.get("url")
            method = arguments.get("method", "GET").upper()
            headers = arguments.get("headers", {})
            query_params = arguments.get("query_params", {})
            body = arguments.get("body")
            timeout = arguments.get("timeout", 30)
            
            # Валидация
            if not url:
                return {"success": False, "error": "URL is required"}
            
            if not url.startswith(("http://", "https://")):
                return {"success": False, "error": "URL must start with http:// or https://"}
            
            if method not in ["GET", "POST", "PUT", "DELETE", "PATCH"]:
                return {"success": False, "error": f"Unsupported HTTP method: {method}"}
            
            # Извлекаем конфиг из промпта (если есть)
            api_config = None
            if assistant_config and hasattr(assistant_config, "system_prompt"):
                api_config = extract_api_config_from_prompt(assistant_config.system_prompt)
            
            # Мержим headers из промпта с переданными (приоритет у переданных)
            if api_config and "headers" in api_config:
                merged_headers = api_config["headers"].copy()
                merged_headers.update(headers)
                headers = merged_headers
            
            # Добавляем API Key из промпта (если есть и не передан в headers)
            if api_config and "api_key" in api_config:
                if "Authorization" not in headers and "X-API-Key" not in headers:
                    headers["Authorization"] = f"Bearer {api_config['api_key']}"
            
            # Строим URL с query параметрами
            if query_params:
                query_string = urlencode(query_params)
                url = f"{url}?{query_string}" if "?" not in url else f"{url}&{query_string}"
            
            # Логируем запрос (без чувствительных данных)
            safe_headers = {k: ("***" if k.lower() in ["authorization", "x-api-key"] else v) 
                           for k, v in headers.items()}
            
            logger.info(f"[API_REQUEST] ━━━━━━━━━━━━━━━━━━━━━━━━━")
            logger.info(f"[API_REQUEST] 🌐 {method} {url[:100]}...")
            logger.info(f"[API_REQUEST] 📋 Headers: {safe_headers}")
            if body:
                logger.info(f"[API_REQUEST] 📦 Body: {str(body)[:200]}...")
            
            # Выполняем запрос
            async with aiohttp.ClientSession() as session:
                request_kwargs = {
                    "headers": headers,
                    "timeout": aiohttp.ClientTimeout(total=timeout)
                }
                
                # Добавляем body для методов, которые его поддерживают
                if method in ["POST", "PUT", "PATCH"] and body is not None:
                    # Если headers не содержит Content-Type, добавляем JSON
                    if "Content-Type" not in headers and "content-type" not in headers:
                        headers["Content-Type"] = "application/json"
                    
                    request_kwargs["json"] = body
                
                async with session.request(method, url, **request_kwargs) as response:
                    status_code = response.status
                    response_headers = dict(response.headers)
                    
                    # Пытаемся распарсить ответ
                    try:
                        # Проверяем Content-Type
                        content_type = response_headers.get("Content-Type", "")
                        
                        if "application/json" in content_type:
                            response_data = await response.json()
                        else:
                            response_text = await response.text()
                            # Пытаемся распарсить как JSON, даже если Content-Type не указан
                            try:
                                response_data = json.loads(response_text)
                            except:
                                response_data = response_text
                    except Exception as e:
                        logger.warning(f"[API_REQUEST] Не удалось распарсить ответ: {e}")
                        response_data = await response.text()
                    
                    execution_time_ms = int((time.time() - start_time) * 1000)
                    
                    # Определяем успешность запроса
                    success = 200 <= status_code < 300
                    
                    logger.info(f"[API_REQUEST] ✅ Status: {status_code}")
                    logger.info(f"[API_REQUEST] ⏱️  Time: {execution_time_ms}ms")
                    logger.info(f"[API_REQUEST] 📤 Response: {str(response_data)[:200]}...")
                    logger.info(f"[API_REQUEST] ━━━━━━━━━━━━━━━━━━━━━━━━━")
                    
                    if success:
                        return {
                            "success": True,
                            "status_code": status_code,
                            "data": response_data,
                            "headers": response_headers,
                            "execution_time_ms": execution_time_ms,
                            "method": method,
                            "url": url.split("?")[0]  # URL без query параметров
                        }
                    else:
                        return {
                            "success": False,
                            "error": "Request failed",
                            "status_code": status_code,
                            "details": response_data,
                            "url": url.split("?")[0],
                            "execution_time_ms": execution_time_ms
                        }
        
        except asyncio.TimeoutError:
            execution_time_ms = int((time.time() - start_time) * 1000)
            logger.error(f"[API_REQUEST] ⏱️ Timeout после {execution_time_ms}ms")
            return {
                "success": False,
                "error": "Request timeout",
                "timeout_seconds": timeout,
                "execution_time_ms": execution_time_ms,
                "url": url
            }
        
        except aiohttp.ClientError as e:
            execution_time_ms = int((time.time() - start_time) * 1000)
            logger.error(f"[API_REQUEST] ❌ Client error: {str(e)}")
            return {
                "success": False,
                "error": "Network error",
                "details": str(e),
                "execution_time_ms": execution_time_ms,
                "url": url
            }
        
        except Exception as e:
            execution_time_ms = int((time.time() - start_time) * 1000)
            logger.error(f"[API_REQUEST] ❌ Unexpected error: {str(e)}")
            return {
                "success": False,
                "error": f"Request failed: {str(e)}",
                "execution_time_ms": execution_time_ms
            }
