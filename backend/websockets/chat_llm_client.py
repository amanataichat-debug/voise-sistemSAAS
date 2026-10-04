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
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_COMPLETION_TOKENS = 1000
# Ждать первый байт ответа не дольше READ — иначе соединение считаем мёртвым и повторяем запрос
STREAM_TIMEOUT = httpx.Timeout(30.0, connect=5.0, read=6.0, pool=5.0)
KEEPALIVE_IDLE_SEC = 15      # простой, после которого пингуем OpenAI, чтобы не остывало соединение
SLOW_HEADERS_SEC = 1.5       # медленнее — предупреждение в журнале звонка
SLOW_FIRST_SEC = 2.0
PENDING_RESULT = {"status": "in_progress", "message": "Функция ещё выполняется, результат придёт позже."}

# Голосовой канал: ответ слушают, а не читают
VOICE_RULES = (
    "Это голосовой разговор (телефон или голосовой виджет). Отвечай коротко и разговорно: "
    "обычно 1–2 предложения, максимум 3. Без списков, нумерации, markdown, эмодзи и ссылок. "
    "Числа, время и суммы пиши так, как их произносят. Если не понял собеседника — "
    "переспроси одним коротким вопросом. Никогда не завершай звонок (hangup_call) из-за того, что "
    "не понял собеседника или плохо слышно, — переспроси. Завершай звонок, только когда разговор "
    "окончен или собеседник прощается. "
    "Реплики собеседника — автоматическое распознавание телефонной речи: в них бывают искажённые "
    "слова, смесь кыргызского и русского, неверно услышанные названия (название компании и продуктов "
    "часто искажается до похожих слов). Угадывай смысл по контексту разговора и отвечай по сути. "
    "Не говори про плохую связь и не проси повторить, если смысл можно угадать; если не уверен — "
    "назови свою догадку и уточни её одним вопросом («Вы спрашиваете про …?»). "
    "Не говори, что уже что-то сделал (записал, передал специалисту, отправил), если не вызвал "
    "для этого функцию в этом разговоре: без функции говори в будущем времени («передам», «с вами свяжутся»)."
)


class ChatLLMClient(FishLLMClient):
    """OpenAI Chat Completions (стриминг) с интерфейсом FishLLMClient."""

    supports_audio_input = False

    def __init__(self, *args, model: Optional[str] = None, provider: str = "openai",
                 route: Optional[List[str]] = None, **kwargs) -> None:
        kwargs["text_input"] = True
        super().__init__(*args, **kwargs)
        self.model = model or settings.ELEVEN_TEXT_LLM_MODEL
        # openai — api.openai.com; openrouter — OpenRouter (route = провайдеры по порядку, напр. ["together"])
        self.provider = provider
        self.route = [r for r in (route or []) if r]
        self.url = OPENROUTER_URL if provider == "openrouter" else CHAT_URL
        self.served_by: Optional[str] = None
        self.ws = None  # совместимость: у этого клиента нет сокета
        self._http: Optional[httpx.AsyncClient] = None
        self._messages: List[Dict[str, Any]] = []
        self._tools: List[Dict[str, Any]] = []
        self._events: "asyncio.Queue[Optional[Dict[str, Any]]]" = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None
        self._followup = False           # результат функции пришёл, пока шёл другой ответ
        self._extra: Optional[Dict[str, Any]] = None  # параметры, которые принял API (reasoning_effort)
        self._pending_results: Dict[str, Dict[str, Any]] = {}  # call_id → сообщение-заглушка в истории
        self._last_request_at = time.monotonic()
        self._keepalive_task: Optional[asyncio.Task] = None

        # Промпт и функции — сразу, без сети: приветствие можно класть в историю до connect()
        instructions = getattr(self.assistant_config, "system_prompt", None) or "Ты вежливый голосовой помощник."
        language = (getattr(self.assistant_config, "language", None) or "").strip().lower()[:2]
        if language in LANGUAGE_INSTRUCTIONS:
            instructions = f"{instructions}\n\n{LANGUAGE_INSTRUCTIONS[language]}"
        instructions = f"{instructions}\n\n{VOICE_RULES}"
        self._messages = [{"role": "system", "content": instructions}]
        # _build_tools отдаёт формат Realtime ({type,name,description,parameters})
        self._tools = [{"type": "function", "function": {k: t[k] for k in ("name", "description", "parameters")}}
                       for t in self._build_tools()]

    # ------------------------------------------------------------------ соединение
    async def connect(self) -> bool:
        if not self.api_key:
            logger.error(f"[{self.label}] OPENAI_API_KEY is not configured")
            return False
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if self.provider == "openrouter":
            headers.update({"HTTP-Referer": "https://voksyai.online", "X-Title": "VoksiAI voice"})
        self._http = httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(60.0, connect=10.0),
            # по умолчанию httpx закрывает простаивающее соединение через 5 с — а паузы в разговоре длиннее
            limits=httpx.Limits(max_keepalive_connections=4, keepalive_expiry=300.0),
        )
        # Проверка ключа и модели + прогрев TLS-соединения: первый ответ не ждёт рукопожатия
        try:
            r = await self._http.get(self._check_url())
            if r.status_code != 200:
                logger.error(f"[{self.label}] model {self.model} unavailable: HTTP {r.status_code} {r.text[:200]}")
                await self.close()
                return False
        except Exception as exc:
            logger.error(f"[{self.label}] connect failed: {exc}")
            await self.close()
            return False

        self.is_connected = True
        self._last_request_at = time.monotonic()
        self._keepalive_task = asyncio.create_task(self._keepalive())
        # запись сессии в БД — синхронная; в отдельном потоке, чтобы не останавливать звонок
        await asyncio.to_thread(self._create_conversation_record)
        logger.info(f"[{self.label}] chat session ready: model={self.model} tools={self.enabled_functions} input=text (ASR)")
        return True

    def _check_url(self) -> str:
        """Лёгкий запрос: проверка ключа/модели при подключении и пинг, чтобы соединение не остывало."""
        if self.provider == "openrouter":
            return "https://openrouter.ai/api/v1/key"
        return f"https://api.openai.com/v1/models/{self.model}"

    async def close(self) -> None:
        self.is_connected = False
        if self._task and not self._task.done():
            self._task.cancel()
        if self._keepalive_task and not self._keepalive_task.done():
            self._keepalive_task.cancel()
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
        if self.provider == "openrouter":
            return [{"reasoning": {"enabled": False}}, {"reasoning": {"effort": "minimal"}}, {}]
        return [{"reasoning_effort": "none"}, {"reasoning_effort": "minimal"}, {}]

    async def _stream_response(self, timing: Dict[str, Any], t0: float):
        """Один запрос со стримингом. Возвращает (text, calls, usage); события уходят в очередь по ходу."""
        text, calls, usage = "", {}, {}
        for extra in self._request_variants():
            body = {"model": self.model, "messages": self._messages, "stream": True, **extra}
            if self.provider == "openrouter":
                body["max_tokens"] = MAX_COMPLETION_TOKENS
                body["usage"] = {"include": True}
                if self.route:
                    body["provider"] = {"order": self.route, "allow_fallbacks": True}
            else:
                body["max_completion_tokens"] = MAX_COMPLETION_TOKENS
                body["stream_options"] = {"include_usage": True}
            if self._tools:
                body["tools"] = self._tools
                body["tool_choice"] = "auto"
            self._last_request_at = time.monotonic()
            async with self._http.stream("POST", self.url, json=body, timeout=STREAM_TIMEOUT) as resp:
                timing.setdefault("headers", time.monotonic() - t0)
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
                    if chunk.get("provider") and not self.served_by:
                        self.served_by = chunk["provider"]
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            timing.setdefault("first", time.monotonic() - t0)
                            text += delta["content"]
                            self._emit({"type": "response.output_text.delta", "delta": delta["content"]})
                        for tc in delta.get("tool_calls") or []:
                            timing.setdefault("first", time.monotonic() - t0)
                            slot = calls.setdefault(tc.get("index", 0), {"id": None, "name": "", "arguments": ""})
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["name"] += fn["name"]
                            if fn.get("arguments"):
                                slot["arguments"] += fn["arguments"]
            return text, calls, usage
        return text, calls, usage

    def _log_timing(self, timing: Dict[str, Any], t0: float, status: str) -> None:
        """Время запроса — в журнал звонка: ответ сервера, первый текст, всего."""
        from backend.websockets.call_log import CallLogRecorder
        rec = CallLogRecorder.current()
        if rec is None:
            return
        ms = lambda v: f"{int(v * 1000)} мс" if v is not None else "—"
        headers, first = timing.get("headers"), timing.get("first")
        slow = (headers or 0) > SLOW_HEADERS_SEC or (first or 0) > SLOW_FIRST_SEC
        rec.add(
            "llm",
            f"Запрос к {self.model}{(' (' + self.served_by + ')') if self.served_by else ''}: ответ сервера через {ms(headers)}, первый текст через {ms(first)}, "
            f"всего {ms(time.monotonic() - t0)}"
            + (", с повтором" if timing.get("retried") else "")
            + ("" if status == "completed" else f" ({'отменён' if status == 'cancelled' else 'ошибка'})"),
            level="warning" if slow and status == "completed" else "info",
        )

    async def _keepalive(self) -> None:
        """Держим HTTPS-соединение с OpenAI тёплым: без этого каждая реплика после паузы > 5 с
        открывала новое соединение (в звонке 30.09 ответ сервера ждали 5–6 с вместо 0,6)."""
        try:
            while self.is_connected and self._http is not None:
                await asyncio.sleep(5)
                if time.monotonic() - self._last_request_at < KEEPALIVE_IDLE_SEC:
                    continue
                self._last_request_at = time.monotonic()
                try:
                    await self._http.get(self._check_url(), timeout=10)
                except Exception as exc:
                    logger.info(f"[{self.label}] keepalive ping failed: {exc}")
        except asyncio.CancelledError:
            pass

    async def _run_response(self) -> None:
        response_id = f"resp_{uuid.uuid4().hex[:16]}"
        self._emit({"type": "response.created", "response": {"id": response_id}})
        text, calls, usage = "", {}, {}
        status = "completed"
        timing: Dict[str, Any] = {"retried": False}
        t0 = time.monotonic()
        try:
            for attempt in range(2):
                try:
                    text, calls, usage = await self._stream_response(timing, t0)
                    break
                except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                    # Сервер не ответил / соединение умерло, а текста ещё не было — один повтор
                    if attempt == 0 and not timing.get("first"):
                        timing["retried"] = True
                        logger.warning(f"[{self.label}] no response in time ({type(exc).__name__}), retrying")
                        continue
                    raise
        except asyncio.CancelledError:
            status = "cancelled"
        except Exception as exc:
            status = "failed"
            logger.error(f"[{self.label}] response failed: {type(exc).__name__}: {exc}")
            self._emit({"type": "error", "error": {"code": "llm_request_failed", "message": str(exc)[:300]}})
        self._log_timing(timing, t0, status)

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
