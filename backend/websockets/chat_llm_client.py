# backend/websockets/chat_llm_client.py
"""
Текстовый «мозг» Eleven-ассистента в режиме «ASR → текст»: OpenAI Chat Completions
со стримингом (по умолчанию gpt-5.6-luna, settings.ELEVEN_TEXT_LLM_MODEL).

Зачем не Realtime: в этом режиме звук распознаёт Scribe, модель получает и отдаёт
только текст. Обычная текстовая модель отвечает заметно быстрее gpt-realtime-2
(замер scripts/latency_bench.py 30.09: первый текст ~0,6 с против ~1,0 с, весь
ответ ~1,1 с против ~1,9 с, без выбросов на 4–5 с из-за рассуждений).

Интерфейс — как у FishLLMClient (от него же унаследованы запись сессии в
*_conversations, функции ассистента и session_id звонка), а события ответа
отдаются в формате Realtime API, поэтому FishVoiceSession и исполнитель функций
работают без изменений:
    response.created → response.output_text.delta… → response.output_text.done
    response.output_item.added (function_call) → response.function_call_arguments.done
    response.done {status: completed | cancelled | failed, usage}
    error

История диалога хранится здесь (Chat Completions без состояния). Вызов функции
сразу получает в истории ответ-заглушку «выполняется»: так история всегда
корректна для API (у каждого tool_call есть ответ), даже если клиент заговорил
раньше, чем функция вернулась. Результат функции подменяет заглушку, и модель
озвучивает его новым ответом.
"""

import asyncio
import json
import time
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.websockets.fish_llm_client import LANGUAGE_INSTRUCTIONS, FishLLMClient

logger = get_logger(__name__)

CHAT_URL = "https://api.openai.com/v1/chat/completions"
MAX_COMPLETION_TOKENS = 1000
PENDING_RESULT = {"status": "in_progress", "message": "Функция ещё выполняется, результат придёт позже."}

# Голосовой канал: ответ слушают, а не читают
VOICE_RULES = (
    "Это голосовой разговор (телефон или голосовой виджет). Отвечай коротко и разговорно: "
    "обычно 1–2 предложения, максимум 3. Без списков, нумерации, markdown, эмодзи и ссылок. "
    "Числа, время и суммы пиши так, как их произносят. Если не понял собеседника — "
    "переспроси одним коротким вопросом."
)


class ChatLLMClient(FishLLMClient):
    """OpenAI Chat Completions (стриминг) с интерфейсом FishLLMClient."""

    supports_audio_input = False

    def __init__(self, *args, model: Optional[str] = None, **kwargs) -> None:
        kwargs["text_input"] = True
        super().__init__(*args, **kwargs)
        self.model = model or settings.ELEVEN_TEXT_LLM_MODEL
        self.url = CHAT_URL
        self.ws = None  # совместимость: у этого клиента нет сокета
        self._http: Optional[httpx.AsyncClient] = None
        self._messages: List[Dict[str, Any]] = []
        self._tools: List[Dict[str, Any]] = []
        self._events: "asyncio.Queue[Optional[Dict[str, Any]]]" = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None
        self._followup = False           # результат функции пришёл, пока шёл другой ответ
        self._extra: Optional[Dict[str, Any]] = None  # параметры, которые принял API (reasoning_effort)
        self._pending_results: Dict[str, Dict[str, Any]] = {}  # call_id → сообщение-заглушка в истории

    # ------------------------------------------------------------------ соединение
    async def connect(self) -> bool:
        if not self.api_key:
            logger.error(f"[{self.label}] OPENAI_API_KEY is not configured")
            return False
        self._http = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=httpx.Timeout(60.0, connect=10.0),
        )
        # Проверка ключа и модели + прогрев TLS-соединения: первый ответ не ждёт рукопожатия
        try:
            r = await self._http.get(f"https://api.openai.com/v1/models/{self.model}")
            if r.status_code != 200:
                logger.error(f"[{self.label}] model {self.model} unavailable: HTTP {r.status_code} {r.text[:200]}")
                await self.close()
                return False
        except Exception as exc:
            logger.error(f"[{self.label}] connect failed: {exc}")
            await self.close()
            return False

        instructions = getattr(self.assistant_config, "system_prompt", None) or "Ты вежливый голосовой помощник."
        language = (getattr(self.assistant_config, "language", None) or "").strip().lower()[:2]
        if language in LANGUAGE_INSTRUCTIONS:
            instructions = f"{instructions}\n\n{LANGUAGE_INSTRUCTIONS[language]}"
        instructions = f"{instructions}\n\n{VOICE_RULES}"
        self._messages = [{"role": "system", "content": instructions}]
        # _build_tools отдаёт формат Realtime ({type,name,description,parameters})
        self._tools = [{"type": "function", "function": {k: t[k] for k in ("name", "description", "parameters")}}
                       for t in self._build_tools()]

        self.is_connected = True
        self._create_conversation_record()
        logger.info(f"[{self.label}] chat session ready: model={self.model} tools={self.enabled_functions} input=text (ASR)")
        return True

    async def close(self) -> None:
        self.is_connected = False
        if self._task and not self._task.done():
            self._task.cancel()
        http, self._http = self._http, None
        if http is not None:
            try:
                await http.aclose()
            except Exception:
                pass
        self._events.put_nowait(None)

    async def events(self) -> AsyncIterator[Dict[str, Any]]:
        """События ответа в формате Realtime API — их читает FishVoiceSession.handle_llm_events."""
        while True:
            event = await self._events.get()
            if event is None:
                return
            yield event

    def _emit(self, event: Dict[str, Any]) -> None:
        self._events.put_nowait(event)

    # ------------------------------------------------------------------ история
    async def add_assistant_message(self, text: str) -> bool:
        self._messages.append({"role": "assistant", "content": text})
        return True

    async def add_user_text(self, text: str) -> bool:
        self._messages.append({"role": "user", "content": text})
        return True

    async def send_function_result(self, function_call_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
        """Результат функции подменяет заглушку в истории; модель озвучивает его новым ответом."""
        if not self.is_connected:
            return {"success": False, "error": "not connected"}
        msg = self._pending_results.pop(function_call_id, None)
        content = json.dumps(result, ensure_ascii=False, default=str)
        if msg is not None:
            msg["content"] = content
        else:
            logger.warning(f"[{self.label}] result for unknown call {function_call_id}")
            return {"success": False, "error": "unknown call_id"}
        if self._pending_results:
            return {"success": True, "error": None}  # ждём остальные функции этого ответа
        if self._task and not self._task.done():
            self._followup = True  # идёт другой ответ — озвучим результат после него
        else:
            await self.create_response()
        return {"success": True, "error": None}

    # ------------------------------------------------------------------ ответы
    async def create_response(self) -> bool:
        if not self.is_connected or self._http is None:
            return False
        if self._task and not self._task.done():
            await self.cancel_response()
        self._followup = False
        self._task = asyncio.create_task(self._run_response())
        return True

    async def cancel_response(self) -> bool:
        task = self._task
        if task is None or task.done():
            return True
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        return True

    async def clear_audio_buffer(self) -> bool:
        return True  # звука здесь нет

    async def process_audio(self, audio_b64: str) -> bool:
        return False

    async def switch_to_audio_input(self) -> bool:
        """Запасной режим «звук в модель» есть только у Realtime-клиента."""
        return False

    def _request_variants(self) -> List[Dict[str, Any]]:
        if self._extra is not None:
            return [self._extra]
        # Для голоса рассуждения не нужны: пробуем выключить, принимаем то, что примет API
        return [{"reasoning_effort": "none"}, {"reasoning_effort": "minimal"}, {}]

    async def _run_response(self) -> None:
        response_id = f"resp_{uuid.uuid4().hex[:16]}"
        self._emit({"type": "response.created", "response": {"id": response_id}})
        text, calls, usage = "", {}, {}
        status = "completed"
        try:
            for extra in self._request_variants():
                body = {
                    "model": self.model,
                    "messages": self._messages,
                    "stream": True,
                    "stream_options": {"include_usage": True},
                    "max_completion_tokens": MAX_COMPLETION_TOKENS,
                    **extra,
                }
                if self._tools:
                    body["tools"] = self._tools
                    body["tool_choice"] = "auto"
                async with self._http.stream("POST", CHAT_URL, json=body) as resp:
                    if resp.status_code == 400 and self._extra is None and extra:
                        err = (await resp.aread()).decode("utf-8", "replace")
                        if "reasoning" in err:
                            continue  # параметр не поддерживается — следующий вариант
                        raise RuntimeError(f"HTTP 400: {err[:300]}")
                    if resp.status_code != 200:
                        err = (await resp.aread()).decode("utf-8", "replace")
                        raise RuntimeError(f"HTTP {resp.status_code}: {err[:300]}")
                    if self._extra is None:
                        self._extra = extra
                        logger.info(f"[{self.label}] {self.model}: request params {extra or '{}'}")
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except ValueError:
                            continue
                        if chunk.get("usage"):
                            usage = chunk["usage"]
                        for choice in chunk.get("choices") or []:
                            delta = choice.get("delta") or {}
                            if delta.get("content"):
                                text += delta["content"]
                                self._emit({"type": "response.output_text.delta", "delta": delta["content"]})
                            for tc in delta.get("tool_calls") or []:
                                slot = calls.setdefault(tc.get("index", 0), {"id": None, "name": "", "arguments": ""})
                                if tc.get("id"):
                                    slot["id"] = tc["id"]
                                fn = tc.get("function") or {}
                                if fn.get("name"):
                                    slot["name"] += fn["name"]
                                if fn.get("arguments"):
                                    slot["arguments"] += fn["arguments"]
                break
        except asyncio.CancelledError:
            status = "cancelled"
        except Exception as exc:
            status = "failed"
            logger.error(f"[{self.label}] response failed: {exc}")
            self._emit({"type": "error", "error": {"code": "llm_request_failed", "message": str(exc)[:300]}})

        # История: что модель успела сказать (при перебивании — начало фразы, как в Realtime)
        tool_calls = [c for c in (calls[i] for i in sorted(calls)) if c["id"] and c["name"]]
        if status == "completed" and tool_calls:
            self._messages.append({
                "role": "assistant", "content": text or None,
                "tool_calls": [{"id": c["id"], "type": "function",
                                "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
                               for c in tool_calls],
            })
            for c in tool_calls:
                placeholder = {"role": "tool", "tool_call_id": c["id"],
                               "content": json.dumps(PENDING_RESULT, ensure_ascii=False)}
                self._messages.append(placeholder)
                self._pending_results[c["id"]] = placeholder
        elif text:
            self._messages.append({"role": "assistant", "content": text})

        if text and status == "completed":
            self._emit({"type": "response.output_text.done", "text": text})
        if status == "completed":
            for c in tool_calls:
                item_id = f"item_{c['id']}"
                self._emit({"type": "response.output_item.added",
                            "item": {"type": "function_call", "id": item_id, "name": c["name"], "call_id": c["id"]}})
                self._emit({"type": "response.function_call_arguments.done", "item_id": item_id,
                            "call_id": c["id"], "name": c["name"], "arguments": c["arguments"] or "{}"})
        self._emit({"type": "response.done", "response": {
            "id": response_id, "status": status,
            "usage": {"input_tokens": usage.get("prompt_tokens") or 0,
                      "output_tokens": usage.get("completion_tokens") or 0},
            "output": [],
        }})
        # Результат функции пришёл, пока шёл этот ответ, — озвучить его отдельным ответом
        if status == "completed" and self._followup and not self._pending_results and self.is_connected:
            self._followup = False
            self._task = asyncio.create_task(self._run_response())
