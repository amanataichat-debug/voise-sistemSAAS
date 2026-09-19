# backend/websockets/handler_live.py
"""
Хендлер OpenAI-ассистентов на GPT-Live (gpt-live-1) — основной голосовой
транспорт OpenAI. Заменил handler_realtime_new (Realtime API, легаси).

Говорит с клиентом на протоколе виджета — том же, что handler_gemini и
handler_fish, поэтому обслуживает и браузерный виджет (/ws/{assistant_id},
/ws/demo), и телефонные звонки через SIP-шлюз (HandlerSocket в sip_media_adapter):

    клиент → {"type":"input_audio_buffer.append","audio":<b64 PCM16 24 кГц>}
    клиент ← {"type":"response.audio.delta","delta":<b64 PCM16 24 кГц>}
             assistant.speech.started / assistant.speech.ended (по потоку аудио)
             transcript.delta {role, delta}   — фрагменты стенограммы Live
             function_call.started / .executing / .completed / .error
             usage {seconds, context_usage_ratio}
             connection_status {..., full_duplex: true}

Чем отличается от Realtime-хендлера:
- Модель full-duplex: слушает и говорит одновременно, сама решает, когда
  говорить и когда замолчать. Событий VAD (speech.started, conversation.interrupted)
  нет — клиент должен стримить микрофон непрерывно, и во время речи ассистента
  тоже (виджет делает это в режиме full_duplex, телефония — всегда).
- Аудио от модели идёт в реальном темпе, а не быстрее реального времени:
  клиенту нужен небольшой буфер (виджет — 200 мс, SIP-адаптер — OUTBOUND_CUSHION_MS).
- Функции исполняет бэкенд-модель делегирования (delegation.responses) на том же
  ключе; вызовы приходят через клиент как live.function_call, исполняются общим
  execute_and_send_function_result (function_calls.py), результат уходит назад
  через client.send_function_result. hangup_call ловит SIP-адаптер по
  function_call.executing.
- Транскрипт приходит фрагментами без границ реплик; в conversations пишем
  пары (user, assistant) по окончании сессии (TranscriptCollector).
- Приветствие — session.instructions.append сразу после session.started.
- Vision (screen.context) недоступен: у gpt-live-1 нет входа изображений.
"""

import asyncio
import json
import time
import traceback
import uuid
from typing import Any, Dict, List, Optional

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from websockets.exceptions import ConnectionClosed

from backend.core.logging import get_logger
from backend.functions import normalize_function_name
from backend.models.assistant import AssistantConfig
from backend.models.conversation import Conversation
from backend.models.user import User
from backend.services.conversation_service import ConversationService
from backend.websockets.function_calls import (
    async_save_to_google_sheets,
    execute_and_send_function_result,
)
from backend.websockets.live_client import LIVE_MODEL, LIVE_VOICES, OpenAILiveClient

logger = get_logger(__name__)

LIVE_AUDIO_RATE = 24000
DEFAULT_GREETING = "Здравствуйте! Чем я могу вам помочь?"
# Пауза в потоке output-аудио, после которой считаем, что ассистент договорил
# (у Live нет события «аудио закончилось»; паузы между фразами короче).
OUTPUT_IDLE_SEC = 1.0
# Ошибки Live, которые не надо показывать клиенту (ответы на наши служебные события)
_QUIET_ERROR_PREFIXES = ("instr_", "think_", "comm_", "mute_")

# Активные соединения по ассистенту (для статистики /ws/status)
active_live_connections: Dict[str, List[Any]] = {}


def _log(message: str, level: str = "INFO") -> None:
    if level == "ERROR":
        logger.error(f"[LIVE] {message}")
    elif level == "WARNING":
        logger.warning(f"[LIVE] {message}")
    else:
        logger.info(f"[LIVE] {message}")


# ----------------------------------------------------------------------
# Транскрипт: фрагменты → реплики → пары для conversations
# ----------------------------------------------------------------------
class TranscriptCollector:
    """
    GPT-Live отдаёт транскрипт кусками с start_ms/end_ms и без границ реплик;
    пользователь и ассистент могут говорить одновременно. Копим фрагменты по
    ролям, а в конце склеиваем в реплики по порядку времени: новая реплика
    начинается при смене говорящего.
    """

    def __init__(self):
        self.fragments: List[Dict[str, Any]] = []

    def add(self, role: str, text: str, start_ms: Optional[int], end_ms: Optional[int]):
        if not text:
            return
        self.fragments.append({
            "role": role, "text": text,
            "start_ms": start_ms if isinstance(start_ms, (int, float)) else len(self.fragments),
            "end_ms": end_ms,
        })

    def turns(self) -> List[Dict[str, Any]]:
        ordered = sorted(self.fragments, key=lambda f: (f["start_ms"], 0 if f["role"] == "user" else 1))
        turns: List[Dict[str, Any]] = []
        for f in ordered:
            if turns and turns[-1]["role"] == f["role"]:
                # Фрагменты Live режутся посреди слов и сами несут пробелы там,
                # где они нужны («Отвеч» + «аю», «ИИ» + «.») — клеим как есть.
                turns[-1]["text"] += f["text"]
            else:
                turns.append({"role": f["role"], "text": f["text"], "start_ms": f["start_ms"]})
        for t in turns:
            t["text"] = " ".join(t["text"].split())
        return [t for t in turns if t["text"]]

    def pairs(self) -> List[Dict[str, str]]:
        """Пары (user, assistant) для conversations: как у остальных хендлеров."""
        pairs: List[Dict[str, str]] = []
        current_user = ""
        for t in self.turns():
            if t["role"] == "user":
                if current_user:
                    pairs.append({"user": current_user, "assistant": ""})
                current_user = t["text"]
            else:
                if pairs and not pairs[-1]["assistant"] and not current_user:
                    pairs[-1]["assistant"] = t["text"]
                else:
                    pairs.append({"user": current_user, "assistant": t["text"]})
                    current_user = ""
        if current_user:
            pairs.append({"user": current_user, "assistant": ""})
        return pairs


async def _save_dialog(assistant_id: str, session_id: str, pairs: List[Dict[str, str]],
                       duration: Optional[float], record_id: Optional[str], transport: str) -> int:
    """
    Записать диалог отдельной сессией БД. Первая пара заполняет пустую запись,
    созданную клиентом на старте (к ней привязаны function_logs), остальные —
    новые записи. Возвращает число сохранённых пар.
    """
    from backend.db.session import SessionLocal

    if not pairs:
        return 0
    saved = 0
    db = SessionLocal()
    try:
        rest = pairs
        if record_id:
            try:
                conv = db.get(Conversation, uuid.UUID(record_id))
            except Exception:
                conv = None
            if conv is not None and not (conv.user_message or conv.assistant_message):
                conv.user_message = pairs[0]["user"] or ""
                conv.assistant_message = pairs[0]["assistant"] or ""
                conv.audio_duration = float(duration) if duration is not None else None
                db.commit()
                saved += 1
                rest = pairs[1:]
        for p in rest:
            result = await ConversationService.save_conversation(
                db=db, assistant_id=assistant_id,
                user_message=p["user"] or "", assistant_message=p["assistant"] or "",
                session_id=session_id, caller_number=None,
                client_info={"transport": transport},
                audio_duration=float(duration) if duration is not None else None,
                tokens_used=0,
            )
            if result is not None:
                saved += 1
    except Exception as exc:
        _log(f"save dialog failed: {exc}\n{traceback.format_exc()}", "ERROR")
    finally:
        db.close()
    return saved


# ----------------------------------------------------------------------
# Сессия
# ----------------------------------------------------------------------
class LiveVoiceSession:
    """Один диалог: сокет клиента + сессия GPT-Live."""

    def __init__(self, websocket: WebSocket, assistant: AssistantConfig, client: OpenAILiveClient,
                 db: Optional[Session], client_id: str, telephony: bool = False) -> None:
        self.ws = websocket
        self.assistant = assistant
        self.client = client
        self.db = db
        self.client_id = client_id
        self.telephony = telephony

        self.transcript = TranscriptCollector()
        self.started_at = time.time()
        self.stop_event = asyncio.Event()
        self.closed = False
        self.assistant_speaking = False
        self._last_audio_at = 0.0
        self._first_audio_at: Optional[float] = None
        self.audio_bytes_out = 0
        self.audio_deltas_out = 0
        self.audio_chunks_in = 0
        # Буфер стенограммы для лога: фрагменты склеиваются в реплику и пишутся при смене говорящего
        self._log_buf: Dict[str, str] = {"user": "", "assistant": ""}
        self._log_role: Optional[str] = None
        self.function_calls = 0
        self.delegations = 0
        self.errors = 0
        self.backend_text = ""
        self._tasks: list = []

    # ------------------------------------------------------------------ helpers
    async def emit(self, data: Dict[str, Any]) -> None:
        if self.closed:
            return
        try:
            await self.ws.send_json(data)
        except Exception as exc:
            _log(f"send to client failed ({data.get('type')}): {exc}", "WARNING")

    def _track(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.append(task)
        task.add_done_callback(lambda t: self._tasks.remove(t) if t in self._tasks else None)

    # ------------------------------------------------------------------ greeting
    async def greet(self) -> None:
        greeting = (getattr(self.assistant, "greeting_message", None) or DEFAULT_GREETING).strip()
        if not greeting:
            return
        if await self.client.say_greeting(greeting):
            _log(f"greeting requested: {greeting[:60]}")

    # ------------------------------------------------------------------ speech state
    async def _on_audio_delta(self, delta_b64: str) -> None:
        if not delta_b64:
            return
        self._last_audio_at = time.time()
        self.audio_bytes_out += len(delta_b64) * 3 // 4
        self.audio_deltas_out += 1
        if self._first_audio_at is None:
            self._first_audio_at = time.time()
            _log(f"first audio from model {self._first_audio_at - self.started_at:.2f}s after session start")
        if not self.assistant_speaking:
            self.assistant_speaking = True
            await self.emit({"type": "assistant.speech.started", "response_id": self.client.session_id,
                             "timestamp": time.time()})
        await self.emit({"type": "response.audio.delta", "delta": delta_b64})

    async def _speech_watch(self) -> None:
        """Ассистент «договорил», если аудио не приходило OUTPUT_IDLE_SEC."""
        try:
            while not self.stop_event.is_set():
                await asyncio.sleep(0.1)
                if self.assistant_speaking and time.time() - self._last_audio_at > OUTPUT_IDLE_SEC:
                    self.assistant_speaking = False
                    self._flush_log("assistant")
                    await self.emit({"type": "assistant.speech.ended", "timestamp": time.time()})
        except asyncio.CancelledError:
            pass

    def _log_transcript(self, role: str, delta: str) -> None:
        """Копим фрагменты по ролям, пишем реплику в лог при смене говорящего."""
        if self._log_role and self._log_role != role:
            self._flush_log(self._log_role)
        self._log_role = role
        self._log_buf[role] += delta

    def _flush_log(self, role: str) -> None:
        text = " ".join(self._log_buf.get(role, "").split())
        if text:
            _log(f"{role}: {text[:300]}")
        self._log_buf[role] = ""

    # ------------------------------------------------------------------ Live events
    async def handle_live_events(self) -> None:
        try:
            async for event in self.client.receive_events():
                await self._on_live_event(event)
                if self.stop_event.is_set():
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log(f"live event loop error: {exc}\n{traceback.format_exc()}", "ERROR")
        finally:
            self.stop_event.set()

    async def _on_live_event(self, event: Dict[str, Any]) -> None:
        etype = event.get("type", "")

        if etype == "session.output_audio.delta":
            await self._on_audio_delta(event.get("delta") or "")
            return

        if etype in ("session.input_transcript.delta", "session.output_transcript.delta"):
            role = "user" if etype.startswith("session.input") else "assistant"
            delta = event.get("delta") or event.get("text") or ""
            self.transcript.add(role, delta, event.get("start_ms"), event.get("end_ms"))
            self._log_transcript(role, delta)
            await self.emit({"type": "transcript.delta", "role": role, "delta": delta,
                             "start_ms": event.get("start_ms"), "end_ms": event.get("end_ms")})
            return

        if etype == "live.function_call":
            await self._on_function_call(event)
            return

        if etype == "session.delegation.created":
            self.delegations += 1
            d = event.get("delegation") or {}
            _log(f"delegation #{self.delegations} created: id={d.get('id')} target={d.get('target')}")
            return

        if etype == "response.event":
            inner = event.get("event") or {}
            if inner.get("type") == "response.output_text.delta":
                self.backend_text += inner.get("delta") or ""
            elif inner.get("type") in ("response.completed", "response.done"):
                if self.backend_text:
                    _log(f"backend: {self.backend_text[:200]}")
                    self.backend_text = ""
            return

        if etype == "session.usage.updated":
            usage = event.get("usage") or {}
            ctx = event.get("context_window") or {}
            await self.emit({"type": "usage", "seconds": usage.get("seconds"),
                             "context_usage_ratio": ctx.get("usage_ratio")})
            return

        if etype == "session.closed":
            _log(f"session.closed reason={event.get('reason')} usage={event.get('usage')}")
            self.stop_event.set()
            return

        if etype == "error":
            self.errors += 1
            err = event.get("error") or {}
            client_event_id = str(err.get("client_event_id") or event.get("client_event_id") or "")
            if client_event_id.startswith(_QUIET_ERROR_PREFIXES):
                return  # уже в логе клиента; пользователю показывать нечего
            await self.emit({"type": "error", "error": {
                "code": err.get("code") or "live_error",
                "message": err.get("message") or json.dumps(event, ensure_ascii=False)[:300],
            }})
            return
        # session.started / *.appended / input_audio.muted и прочее клиенту не нужны

    async def _on_function_call(self, event: Dict[str, Any]) -> None:
        name = event.get("name") or ""
        call_id = event.get("call_id")
        normalized = normalize_function_name(name) or name
        if not normalized or not call_id:
            _log(f"function call without name/call_id: {event}", "ERROR")
            return

        if normalized not in self.client.enabled_functions:
            _log(f"unauthorized function {normalized}", "WARNING")
            await self.emit({"type": "function_call.error", "function": normalized,
                             "error": f"Function {normalized} not activated"})
            await self.client.send_function_result(call_id, {"error": f"Function {normalized} not allowed",
                                                             "status": "error"})
            return

        # SIP-адаптер по этому событию (function == hangup_call) запускает прощание → hangup
        await self.emit({"type": "function_call.started", "function": normalized, "function_call_id": call_id})

        arguments = event.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError as exc:
                await self.emit({"type": "error", "error": {"code": "function_args_error", "message": str(exc)}})
                await self.client.send_function_result(call_id, {"error": f"bad arguments: {exc}", "status": "error"})
                return

        self.function_calls += 1
        await self.emit({"type": "function_call.executing", "function": normalized, "function_call_id": call_id,
                         "arguments": arguments, "async_execution": True})

        user_text = ""
        for t in reversed(self.transcript.turns()):
            if t["role"] == "user":
                user_text = t["text"]
                break

        self._track(execute_and_send_function_result(
            openai_client=self.client,
            websocket=self.ws,
            function_call_id=call_id,
            function_name=normalized,
            arguments=arguments,
            context={
                "assistant_config": self.assistant,
                "client_id": self.client_id,
                "db_session": self.db,
                "websocket": self.ws,
                "provider": "openai",
                "live_session_id": self.client.session_id,
            },
            user_transcript=user_text,
        ))

    # ------------------------------------------------------------------ client loop
    async def handle_client_messages(self) -> None:
        while not self.stop_event.is_set():
            message = await self.ws.receive()
            if message.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect(code=1000)
            if "bytes" in message and message["bytes"] is not None:
                await self.emit({"type": "binary.ack"})
                continue
            text = message.get("text")
            if not text:
                continue
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            mtype = data.get("type", "")

            if mtype == "input_audio_buffer.append":
                audio = data.get("audio")
                if audio and self.client.is_connected:
                    if await self.client.send_audio_b64(audio):
                        self.audio_chunks_in += 1
                continue
            if mtype == "ping":
                await self.emit({"type": "pong"})
            elif mtype == "session.update":
                await self.emit({"type": "session.update.ack", "event_id": data.get("event_id")})
            elif mtype in ("input_audio_buffer.commit", "input_audio_buffer.clear", "response.cancel",
                           "interruption.manual"):
                # Модель full-duplex сама управляет ходом реплик; клиенту отвечаем, чтобы он не ждал
                await self.emit({"type": f"{mtype}.ack", "event_id": data.get("event_id"), "note": "full_duplex"})
            elif mtype in ("audio_playback.stopped", "speech.user_started", "speech.user_stopped"):
                pass
            elif mtype == "session.close":
                _log("client requested session close")
                self.stop_event.set()
                return
            elif mtype == "screen.context":
                _log("screen.context ignored: gpt-live-1 has no image input", "WARNING")
            elif mtype == "input_text":
                text_in = (data.get("text") or "").strip()
                if text_in:
                    # start_ms у Live отсчитывается от начала сессии — кладём в ту же шкалу
                    self.transcript.add("user", text_in, int((time.time() - self.started_at) * 1000), None)
                    await self.client.append_instructions(
                        f"Собеседник написал текстом: «{text_in[:1500]}». Ответь на это вслух.")

    # ------------------------------------------------------------------ run
    async def run(self) -> None:
        started = time.time()
        live_task = asyncio.create_task(self.handle_live_events())
        watch_task = asyncio.create_task(self._speech_watch())
        client_task = asyncio.create_task(self.handle_client_messages())
        stop_task = asyncio.create_task(self.stop_event.wait())
        reason = "unknown"
        try:
            await self.greet()
            done, _ = await asyncio.wait({live_task, client_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
            if client_task in done:
                exc = client_task.exception() if not client_task.cancelled() else None
                if isinstance(exc, (WebSocketDisconnect, ConnectionClosed)) or exc is None:
                    reason = "client_disconnected"
                    _log(f"client disconnected: {self.client_id}")
                else:
                    reason = "client_error"
                    _log(f"client loop error: {exc}", "ERROR")
            elif live_task in done:
                reason = "live_closed"
            else:
                reason = "stopped"
        except Exception as exc:
            reason = "error"
            _log(f"session error: {exc}\n{traceback.format_exc()}", "ERROR")
        finally:
            self.stop_event.set()
            if self.assistant_speaking:
                self.assistant_speaking = False
                if reason != "client_disconnected":
                    await self.emit({"type": "assistant.speech.ended", "timestamp": time.time()})
            self.closed = True  # дальше клиенту ничего не шлём
            for role in ("user", "assistant"):
                self._flush_log(role)
            # 1. Закрыть сессию у OpenAI (дождаться session.closed с итоговым usage)
            closed = None
            try:
                closed = await self.client.close()
            except Exception as exc:
                _log(f"client.close error: {exc}", "WARNING")
            for task in (live_task, watch_task, client_task, stop_task, *self._tasks):
                if not task.done():
                    task.cancel()
            await asyncio.gather(live_task, watch_task, client_task, stop_task, *self._tasks, return_exceptions=True)

            usage_seconds = None
            if closed and isinstance(closed.get("usage"), dict):
                usage_seconds = closed["usage"].get("seconds")
            elif self.client.last_usage:
                usage_seconds = self.client.last_usage.get("seconds")
            elapsed = time.time() - started

            # 2. Диалог в conversations (ждём запись: SIP-роутер тегирует звонок сразу после хендлера)
            pairs = self.transcript.pairs()
            saved = 0
            if pairs:
                saved = await _save_dialog(str(self.assistant.id), self.client.session_id or self.client_id, pairs,
                                           usage_seconds if usage_seconds is not None else elapsed,
                                           self.client.conversation_record_id, LIVE_MODEL)
                sheet_id = getattr(self.assistant, "google_sheet_id", None)
                if sheet_id:
                    for p in pairs:
                        if p["user"] or p["assistant"]:
                            asyncio.create_task(async_save_to_google_sheets(
                                sheet_id=sheet_id, user_message=p["user"], assistant_message=p["assistant"],
                                function_result=None, conversation_id=self.client.conversation_record_id,
                                context="Live dialog",
                            ))
            _log(
                f"session {self.client_id} finished ({reason}): {elapsed:.1f}s, live_usage={usage_seconds}s, "
                f"close_reason={(closed or {}).get('reason')}, audio_in={self.audio_chunks_in} chunks, "
                f"audio_out={self.audio_bytes_out / (LIVE_AUDIO_RATE * 2):.1f}s in {self.audio_deltas_out} deltas "
                f"(first after {'-' if self._first_audio_at is None else f'{self._first_audio_at - self.started_at:.2f}s'}), "
                f"delegations={self.delegations}, "
                f"functions={self.function_calls}, errors={self.errors}, dialog_pairs={len(pairs)} saved={saved}"
            )
            await self._send_webhook()

    async def _send_webhook(self) -> None:
        """Итоговый webhook conversation.completed владельцу ассистента (как у Realtime-хендлера)."""
        if not getattr(self.assistant, "user_id", None):
            return
        try:
            from backend.db.session import SessionLocal
            from backend.services.webhook_notification import send_webhook_safe

            db = SessionLocal()
            try:
                user = db.query(User).get(self.assistant.user_id)
                if user and user.has_webhook_config():
                    await send_webhook_safe(
                        db=db,
                        webhook_url=user.webhook_url,
                        webhook_enabled=user.webhook_enabled,
                        source="sip_call" if self.telephony else "web_chat",
                        session_id=self.client.session_id or self.client_id,
                        assistant_id=str(self.assistant.id),
                        assistant_name=self.assistant.name,
                        assistant_type="openai",
                        caller_number=None,
                        call_direction=None,
                        duration_seconds=None,
                        call_cost=None,
                        record_url=None,
                    )
            finally:
                db.close()
        except Exception as exc:
            _log(f"webhook error: {exc}", "ERROR")


# ----------------------------------------------------------------------
# Точка входа
# ----------------------------------------------------------------------
async def handle_live_websocket_connection(websocket: WebSocket, assistant_id: str, db: Session) -> None:
    """Точка входа для /ws/{assistant_id}, /ws/demo и SIP-адаптера (SIP_HANDLERS["openai"])."""
    client_id = str(uuid.uuid4())
    registered_key: Optional[str] = None

    async def fail(code: str, message: str, ws_code: int = 1008, extra: Optional[Dict[str, Any]] = None) -> None:
        _log(f"{code}: {message} (assistant {assistant_id})", "WARNING")
        try:
            await websocket.send_json({"type": "error", "error": {"code": code, "message": message, **(extra or {})}})
            await websocket.close(code=ws_code)
        except Exception:
            pass

    try:
        await websocket.accept()

        if assistant_id == "demo":
            assistant = db.query(AssistantConfig).filter(AssistantConfig.is_public.is_(True)).first()
            if not assistant:
                assistant = db.query(AssistantConfig).first()
        else:
            try:
                assistant = db.query(AssistantConfig).get(uuid.UUID(assistant_id))
            except ValueError:
                assistant = db.query(AssistantConfig).filter(AssistantConfig.id.cast(str) == assistant_id).first()
        if not assistant:
            await fail("assistant_not_found", "Assistant not found")
            return

        registered_key = str(assistant.id)
        active_live_connections.setdefault(registered_key, []).append(websocket)

        # Владелец: подписка и ключ OpenAI (GPT-Live и бэкенд делегирования работают на ключе владельца)
        api_key = None
        if assistant.user_id:
            user = db.query(User).get(assistant.user_id)
            if user:
                if not user.is_admin and user.email != "amanat.aichat@gmail.com":
                    from backend.services.user_service import UserService
                    sub = await UserService.check_subscription_status(db, str(user.id))
                    if not sub.get("active"):
                        code = "TRIAL_EXPIRED" if sub.get("is_trial") else "SUBSCRIPTION_EXPIRED"
                        msg = "Ваш пробный период истек" if sub.get("is_trial") else "Ваша подписка истекла"
                        await fail(code, msg, extra={"subscription_status": sub, "requires_payment": True})
                        return
                api_key = user.openai_api_key
        if not api_key:
            await fail("no_api_key", "OpenAI API key required")
            return

        telephony = bool(getattr(assistant, "telephony_mode", False))
        voice_override = None
        try:
            voice_override = websocket.query_params.get("voice")
        except Exception:
            pass

        client = OpenAILiveClient(api_key, assistant, client_id, db, audio_rate=LIVE_AUDIO_RATE,
                                  voice_override=voice_override, telephony=telephony)
        connect_started = time.time()
        if not await client.connect():
            await fail("openai_connection_failed", "Failed to open gpt-live-1 session", 1011)
            return
        _log(f"session {client_id} started in {time.time() - connect_started:.2f}s: assistant={assistant.id} "
             f"'{assistant.name}' voice={client.voice} telephony={telephony} functions={client.enabled_functions} "
             f"conversation_record={client.conversation_record_id}")

        await websocket.send_json({
            "type": "connection_status",
            "status": "connected",
            "provider": "openai",
            "transport": LIVE_MODEL,
            "model": LIVE_MODEL,
            "backend_model": client.delegation_model,
            "message": f"Connected to OpenAI {LIVE_MODEL} (full-duplex)",
            "full_duplex": True,
            "audio_rate": LIVE_AUDIO_RATE,
            "voice": client.voice,
            "voices": LIVE_VOICES,
            "session_id": client.session_id,
            "client_id": client_id,
            "functions_enabled": len(client.enabled_functions),
            "google_sheets": bool(getattr(assistant, "google_sheet_id", None)),
            "telephony": telephony,
            "enable_vision": False,
            "greeting_message": assistant.greeting_message or DEFAULT_GREETING,
        })

        session = LiveVoiceSession(websocket, assistant, client, db, client_id, telephony=telephony)
        await session.run()

    except WebSocketDisconnect:
        _log(f"client disconnected before start: {client_id}")
    except Exception as exc:
        _log(f"fatal: {exc}\n{traceback.format_exc()}", "ERROR")
        try:
            await websocket.send_json({"type": "error", "error": {"code": "server_error", "message": "Internal server error"}})
        except Exception:
            pass
    finally:
        if registered_key:
            conns = active_live_connections.get(registered_key, [])
            if websocket in conns:
                conns.remove(websocket)
        try:
            await websocket.close()
        except Exception:
            pass
