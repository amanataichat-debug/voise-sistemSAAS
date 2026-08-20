"""
🚀 LLM Streaming Service v2.1 (OPTIMIZED)
==========================================

Сервис для стриминга ответов от OpenAI Chat API.
Используется для вывода развёрнутых текстовых ответов на экран
при голосовом взаимодействии через Gemini.

🔧 v2.1 OPTIMIZATION:
✅ Buffered delta sending (batches instead of per-token)
✅ Reduced WebSocket message frequency
✅ Prevents audio distortion during streaming

АРХИТЕКТУРА:
- Gemini Live API — голосовой ввод/вывод
- OpenAI Chat API (stream=True) — генерация текстовых ответов
- WebSocket — доставка текста на фронтенд в реальном времени

СОБЫТИЯ WebSocket:
- llm.stream.start   — начало генерации
- llm.stream.delta   — chunk текста (BUFFERED)
- llm.stream.done    — завершение генерации
- llm.stream.error   — ошибка

ИСПОЛЬЗОВАНИЕ:
    from backend.services.browser_agent_service import get_browser_agent_service
    
    llm_service = get_browser_agent_service()
    result = await llm_service.stream_response(
        query="Что такое машинное обучение?",
        websocket=websocket
    )
    # result = {"success": True, "phrase": "Готово, информация на экране", ...}
"""

import asyncio
import json
import uuid
import time
import random
import os
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
import aiohttp

from backend.core.logging import get_logger
from backend.core.config import settings

logger = get_logger(__name__)


# ============================================================================
# CONFIGURATION
# ============================================================================

class LLMConfig:
    """Конфигурация LLM Streaming Service"""
    
    # Модель OpenAI для стриминга
    MODEL = "gpt-4o-mini"
    
    # Максимальное количество токенов в ответе
    MAX_TOKENS = 4096
    
    # Температура (креативность)
    TEMPERATURE = 0.1
    
    # Таймауты
    REQUEST_TIMEOUT = 60.0  # Общий таймаут запроса
    CONNECT_TIMEOUT = 10.0  # Таймаут подключения
    
    # OpenAI API
    OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
    
    # 🆕 v2.1: Buffering settings
    # Минимальный размер буфера перед отправкой (символы)
    BUFFER_MIN_CHARS = 50
    # Максимальное время ожидания буфера (секунды)
    BUFFER_MAX_WAIT = 0.3
    # Принудительная отправка после точки/абзаца
    FLUSH_ON_SENTENCE = True


# ============================================================================
# SYSTEM PROMPT
# ============================================================================

SYSTEM_PROMPT = """Ты — умный и полезный ассистент. Отвечай подробно, структурированно и по существу.

Правила форматирования:
- Используй markdown для структурирования ответа
- Заголовки: ## для основных разделов, ### для подразделов
- Списки: - или 1. 2. 3. для перечислений
- Код: ```язык для блоков кода, `код` для inline
- Выделяй **важные термины** жирным
- Разделяй логические блоки пустыми строками

Правила ответов:
- Отвечай на языке вопроса (русский/английский)
- Будь конкретным и информативным
- Приводи примеры где уместно
- Избегай воды и общих фраз
- Если вопрос неясен — уточни

Ты помогаешь пользователю, который общается голосом. Твой текстовый ответ будет показан на экране."""


# ============================================================================
# RESPONSE PHRASES (для Gemini озвучки)
# ============================================================================

RESPONSE_PHRASES = [
    "Готово, информация на экране",
    "Ответ выведен на экран",
    "Смотри на экран, там всё подробно",
    "Вывел развёрнутый ответ",
    "Информация перед тобой",
    "Готово, можешь изучить на экране",
    "Показал подробный ответ",
    "Ответ на экране",
    "Вот что я нашёл, смотри на экран",
    "Подготовил информацию, она на экране",
]

ERROR_PHRASES = [
    "Произошла ошибка, попробуй ещё раз",
    "Не удалось получить ответ, повтори вопрос",
    "Что-то пошло не так, попробуй снова",
    "Ошибка при обработке, попробуй переформулировать",
]


# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class StreamResult:
    """Результат стриминга"""
    success: bool
    phrase: str  # Фраза для озвучки Gemini
    full_content: str = ""
    error: Optional[str] = None
    tokens_used: int = 0
    duration_ms: int = 0
    model: str = LLMConfig.MODEL


# ============================================================================
# LLM STREAMING SERVICE
# ============================================================================

class BrowserAgentService:
    """
    LLM Streaming Service v2.1 (OPTIMIZED)
    
    Стримит ответы от OpenAI Chat API на фронтенд через WebSocket.
    🆕 v2.1: Буферизация дельт для предотвращения искажения аудио.
    """
    
    def __init__(self):
        """Инициализация сервиса"""
        self.api_key = os.environ.get('OPENAI_API_KEY')
        
        if not self.api_key:
            logger.warning("[LLM-STREAM] ⚠️ OPENAI_API_KEY not found in environment")
        
        logger.info(f"[LLM-STREAM] ✅ Service initialized (v2.1 OPTIMIZED)")
        logger.info(f"[LLM-STREAM]    Model: {LLMConfig.MODEL}")
        logger.info(f"[LLM-STREAM]    Max tokens: {LLMConfig.MAX_TOKENS}")
        logger.info(f"[LLM-STREAM]    Buffer min chars: {LLMConfig.BUFFER_MIN_CHARS}")
        logger.info(f"[LLM-STREAM]    Buffer max wait: {LLMConfig.BUFFER_MAX_WAIT}s")
    
    # ========================================================================
    # MAIN METHOD: Stream Response (OPTIMIZED v2.1)
    # ========================================================================
    
    async def stream_response(
        self,
        query: str,
        websocket: Any,
        model: str = None,
        system_prompt: str = None,
        max_tokens: int = None,
        temperature: float = None
    ) -> Dict[str, Any]:
        """
        Главный метод — стримит ответ от OpenAI на фронтенд.
        
        🆕 v2.1: Буферизация для уменьшения нагрузки на WebSocket.
        
        Args:
            query: Вопрос пользователя
            websocket: WebSocket соединение для отправки событий
            model: Модель OpenAI (опционально, по умолчанию gpt-4o-mini)
            system_prompt: Системный промпт (опционально)
            max_tokens: Максимум токенов (опционально)
            temperature: Температура (опционально)
        
        Returns:
            Dict с результатом
        """
        request_id = f"req_{uuid.uuid4().hex[:12]}"
        start_time = time.time()
        full_content = ""
        tokens_used = 0
        
        # Параметры
        _model = model or LLMConfig.MODEL
        _system_prompt = system_prompt or SYSTEM_PROMPT
        _max_tokens = max_tokens or LLMConfig.MAX_TOKENS
        _temperature = temperature or LLMConfig.TEMPERATURE
        
        logger.info(f"[LLM-STREAM] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        logger.info(f"[LLM-STREAM] 🚀 STREAM START (v2.1 BUFFERED)")
        logger.info(f"[LLM-STREAM]    Request ID: {request_id}")
        logger.info(f"[LLM-STREAM]    Query: {query[:100]}{'...' if len(query) > 100 else ''}")
        logger.info(f"[LLM-STREAM]    Model: {_model}")
        logger.info(f"[LLM-STREAM] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        
        # 🆕 Buffer state
        buffer = ""
        last_flush_time = time.time()
        messages_sent = 0
        
        try:
            # Проверяем API ключ
            if not self.api_key:
                raise ValueError("OpenAI API key not configured")
            
            # 1. Отправляем событие начала
            await self._send_event(websocket, {
                "type": "llm.stream.start",
                "request_id": request_id,
                "query": query,
                "model": _model
            })
            
            # 2. Стримим ответ от OpenAI с буферизацией
            async for chunk_data in self._call_openai_stream(
                query=query,
                model=_model,
                system_prompt=_system_prompt,
                max_tokens=_max_tokens,
                temperature=_temperature
            ):
                if chunk_data.get("type") == "content":
                    content = chunk_data.get("content", "")
                    full_content += content
                    buffer += content
                    
                    # 🆕 v2.1: Проверяем условия для отправки буфера
                    current_time = time.time()
                    time_since_flush = current_time - last_flush_time
                    
                    should_flush = False
                    
                    # Условие 1: Буфер достаточно большой
                    if len(buffer) >= LLMConfig.BUFFER_MIN_CHARS:
                        should_flush = True
                    
                    # Условие 2: Прошло достаточно времени
                    if time_since_flush >= LLMConfig.BUFFER_MAX_WAIT and len(buffer) > 0:
                        should_flush = True
                    
                    # Условие 3: Конец предложения (точка, ?, !, перенос строки)
                    if LLMConfig.FLUSH_ON_SENTENCE and len(buffer) > 10:
                        if buffer.rstrip().endswith(('.', '!', '?', '\n', '。', '！', '？')):
                            should_flush = True
                    
                    if should_flush:
                        await self._send_event(websocket, {
                            "type": "llm.stream.delta",
                            "request_id": request_id,
                            "content": buffer
                        })
                        messages_sent += 1
                        buffer = ""
                        last_flush_time = current_time
                        
                        # 🆕 Небольшая пауза чтобы не перегружать браузер
                        await asyncio.sleep(0.01)
                
                elif chunk_data.get("type") == "usage":
                    tokens_used = chunk_data.get("total_tokens", 0)
            
            # 3. Отправляем остаток буфера
            if buffer:
                await self._send_event(websocket, {
                    "type": "llm.stream.delta",
                    "request_id": request_id,
                    "content": buffer
                })
                messages_sent += 1
            
            # 4. Отправляем событие завершения
            duration_ms = int((time.time() - start_time) * 1000)
            
            await self._send_event(websocket, {
                "type": "llm.stream.done",
                "request_id": request_id,
                "full_content": full_content,
                "model": _model,
                "usage": {
                    "total_tokens": tokens_used
                },
                "duration_ms": duration_ms
            })
            
            # Выбираем рандомную фразу для Gemini
            phrase = self.get_random_phrase()
            
            logger.info(f"[LLM-STREAM] ✅ STREAM COMPLETE (v2.1)")
            logger.info(f"[LLM-STREAM]    Duration: {duration_ms}ms")
            logger.info(f"[LLM-STREAM]    Content length: {len(full_content)} chars")
            logger.info(f"[LLM-STREAM]    Tokens: {tokens_used}")
            logger.info(f"[LLM-STREAM]    Messages sent: {messages_sent} (buffered)")
            logger.info(f"[LLM-STREAM]    Phrase: {phrase}")
            
            return {
                "success": True,
                "phrase": phrase,
                "full_content": full_content,
                "tokens_used": tokens_used,
                "duration_ms": duration_ms,
                "model": _model,
                "request_id": request_id,
                "messages_sent": messages_sent
            }
            
        except asyncio.TimeoutError:
            error_msg = "Превышено время ожидания ответа"
            logger.error(f"[LLM-STREAM] ❌ TIMEOUT: {error_msg}")
            
            await self._send_error(websocket, request_id, "timeout", error_msg)
            
            return {
                "success": False,
                "phrase": self.get_error_phrase(),
                "error": error_msg,
                "full_content": full_content,
                "duration_ms": int((time.time() - start_time) * 1000),
                "model": _model,
                "request_id": request_id
            }
            
        except aiohttp.ClientError as e:
            error_msg = f"Ошибка подключения к OpenAI: {str(e)}"
            logger.error(f"[LLM-STREAM] ❌ CONNECTION ERROR: {error_msg}")
            
            await self._send_error(websocket, request_id, "connection_error", error_msg)
            
            return {
                "success": False,
                "phrase": self.get_error_phrase(),
                "error": error_msg,
                "full_content": full_content,
                "duration_ms": int((time.time() - start_time) * 1000),
                "model": _model,
                "request_id": request_id
            }
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"[LLM-STREAM] ❌ ERROR: {error_msg}")
            
            await self._send_error(websocket, request_id, "internal_error", error_msg)
            
            return {
                "success": False,
                "phrase": self.get_error_phrase(),
                "error": error_msg,
                "full_content": full_content,
                "duration_ms": int((time.time() - start_time) * 1000),
                "model": _model,
                "request_id": request_id
            }
    
    # ========================================================================
    # OpenAI Streaming Call
    # ========================================================================
    
    async def _call_openai_stream(
        self,
        query: str,
        model: str,
        system_prompt: str,
        max_tokens: int,
        temperature: float
    ):
        """
        Вызов OpenAI Chat Completions API со стримингом.
        
        Yields:
            Dict с типом события:
            - {"type": "content", "content": "текст"}
            - {"type": "usage", "total_tokens": 123}
        """
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": query}
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True}  # Для получения usage в конце
        }
        
        timeout = aiohttp.ClientTimeout(
            total=LLMConfig.REQUEST_TIMEOUT,
            connect=LLMConfig.CONNECT_TIMEOUT
        )
        
        logger.info(f"[LLM-STREAM] 📤 Calling OpenAI API...")
        logger.info(f"[LLM-STREAM]    URL: {LLMConfig.OPENAI_API_URL}")
        logger.info(f"[LLM-STREAM]    Model: {model}")
        
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                LLMConfig.OPENAI_API_URL,
                headers=headers,
                json=payload
            ) as response:
                
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"[LLM-STREAM] ❌ OpenAI API error: {response.status}")
                    logger.error(f"[LLM-STREAM]    Response: {error_text[:500]}")
                    
                    # Парсим ошибку OpenAI
                    try:
                        error_data = json.loads(error_text)
                        error_message = error_data.get("error", {}).get("message", error_text)
                    except:
                        error_message = error_text
                    
                    raise Exception(f"OpenAI API error ({response.status}): {error_message}")
                
                logger.info(f"[LLM-STREAM] 📥 Streaming response...")
                
                # Читаем SSE поток
                async for line in response.content:
                    line = line.decode('utf-8').strip()
                    
                    if not line:
                        continue
                    
                    if line.startswith("data: "):
                        data_str = line[6:]  # Убираем "data: "
                        
                        if data_str == "[DONE]":
                            logger.info(f"[LLM-STREAM] 📥 Stream finished")
                            break
                        
                        try:
                            data = json.loads(data_str)
                            
                            # Получаем content из delta
                            choices = data.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                content = delta.get("content")
                                
                                if content:
                                    yield {"type": "content", "content": content}
                            
                            # Получаем usage (приходит в последнем chunk)
                            usage = data.get("usage")
                            if usage:
                                yield {
                                    "type": "usage",
                                    "prompt_tokens": usage.get("prompt_tokens", 0),
                                    "completion_tokens": usage.get("completion_tokens", 0),
                                    "total_tokens": usage.get("total_tokens", 0)
                                }
                                
                        except json.JSONDecodeError as e:
                            logger.warning(f"[LLM-STREAM] ⚠️ Failed to parse chunk: {data_str[:100]}")
                            continue
    
    # ========================================================================
    # Helper Methods
    # ========================================================================
    
    async def _send_event(self, websocket: Any, event: Dict) -> bool:
        """Отправить событие через WebSocket"""
        try:
            await websocket.send_json(event)
            return True
        except Exception as e:
            logger.error(f"[LLM-STREAM] ❌ Failed to send event: {e}")
            return False
    
    async def _send_error(
        self,
        websocket: Any,
        request_id: str,
        error_code: str,
        message: str
    ) -> bool:
        """Отправить событие ошибки"""
        return await self._send_event(websocket, {
            "type": "llm.stream.error",
            "request_id": request_id,
            "error_code": error_code,
            "message": message
        })
    
    def get_random_phrase(self) -> str:
        """Получить рандомную фразу для озвучки Gemini"""
        return random.choice(RESPONSE_PHRASES)
    
    def get_error_phrase(self) -> str:
        """Получить рандомную фразу ошибки для озвучки Gemini"""
        return random.choice(ERROR_PHRASES)


# ============================================================================
# FACTORY FUNCTIONS (для обратной совместимости)
# ============================================================================

# Singleton instance
_service_instance: Optional[BrowserAgentService] = None


def get_browser_agent_service(api_key: str = None) -> BrowserAgentService:
    """
    Получить экземпляр LLM Streaming Service.
    
    Args:
        api_key: Опционально, игнорируется (для совместимости)
    
    Returns:
        BrowserAgentService instance
    """
    global _service_instance
    
    if _service_instance is None:
        _service_instance = BrowserAgentService()
    
    return _service_instance


def create_browser_agent_service() -> BrowserAgentService:
    """Создать новый экземпляр сервиса"""
    return BrowserAgentService()


# Для обратной совместимости
browser_agent_service: Optional[BrowserAgentService] = None
