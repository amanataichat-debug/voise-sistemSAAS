# backend/websockets/handler_gemini_live.py
"""
Карточка ElevenLabs с движком «Google Gemini Live» (voice_engine="gemini_live").

Тот же протокол виджета и телефона, что у GPT-Live и ElevenLabs, но говорит Gemini Live
(gemini-3.8-live, речь в речь, full-duplex): сервер Google сам ловит речь и перебивания, мы
стримим микрофон непрерывно. Вход в сессию — handler_eleven.handle_eleven_websocket_connection
(выбор движка по карточке), поэтому у движка общие с остальными: промпт, приветствие, функции,
база знаний агента, номера и звонки, «Диалоги» (eleven_conversations), журнал звонка, кошелёк.

Переиспользовано без копирования:
- GeminiLiveClient (gemini_client.py) — соединение, setup, VAD-профиль, функции NON_BLOCKING;
- handle_gemini_messages (handler_gemini.py) — разбор событий Gemini, транскрипты, перебивания,
  вызовы функций; реплики он отдаёт в gemini_client.turn_saver, мы копим пары (клиент, агент)
  и пишем диалог в конце сессии, как LiveVoiceSession у GPT-Live.

Ключ — серверный GEMINI_API_KEY, иначе gemini_api_key владельца карточки.
Голос — имя из GEMINI_LIVE_VOICES в колонке live_voice (регистр восстанавливаем).
Вход: по телефону PCM16 16 кГц (адаптер, профиль "gemini"), в виджете PCM16 24 кГц — сжимаем до 16.
Выход Gemini — PCM16 24 кГц (виджет играет как есть, адаптер сам понижает до 8 кГц).
"""

import asyncio
import audioop
import base64
import json
import time
import traceback
import uuid
from typing import Any, Dict, List, Optional

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from websockets.exceptions import ConnectionClosed

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.models.eleven_assistant import (
    DEFAULT_ELEVEN_GREETING,
    GEMINI_LIVE_DEFAULT_VOICE,
    GEMINI_LIVE_VOICES,
    ElevenConversation,
)
from backend.models.user import User
from backend.websockets.call_log import CallLogRecorder
from backend.websockets.gemini_client import GEMINI_LIVE_MODEL, GeminiLiveClient
from backend.websockets.handler_gemini import handle_gemini_messages
from backend.websockets.handler_live import _save_dialog, async_save_to_google_sheets
from backend.websockets.wallet_meter import WalletMeter

logger = get_logger(__name__)

GEMINI_IN_RATE = 16000        # Gemini Live принимает PCM16 16 кГц
WIDGET_IN_RATE = 24000        # микрофон виджета в full-duplex (как у GPT-Live)
GEMINI_OUT_RATE = 24000
_PLACEHOLDERS = {"[no user input]", "[no response]", "[interrupted]", "[incomplete - disconnected]"}


def _log(message: str, level: str = "INFO") -> None:
    if level == "ERROR":
        logger.error(f"[GEMINI-LIVE] {message}")
    elif level == "WARNING":
        logger.warning(f"[GEMINI-LIVE] {message}")
    else:
        logger.info(f"[GEMINI-LIVE] {message}")


def resolve_voice(live_voice: Optional[str]) -> str:
    """Имя голоса Gemini с правильным регистром (в колонке оно в нижнем); чужой голос → по умолчанию."""
    wanted = (live_voice or "").strip().lower()
    for name in GEMINI_LIVE_VOICES:
        if name.lower() == wanted:
            return name
    return GEMINI_LIVE_DEFAULT_VOICE


def resolve_api_key(db: Session, assistant) -> Optional[str]:
    """Серверный ключ, иначе ключ владельца карточки."""
    if settings.GEMINI_API_KEY:
        return settings.GEMINI_API_KEY
    if getattr(assistant, "user_id", None):
        try:
            user = db.query(User).filter(User.id == assistant.user_id).first()
            if user is not None and user.gemini_api_key:
                return user.gemini_api_key
        except Exception as exc:
            _log(f"owner key lookup failed: {exc}", "WARNING")
    return None


class GeminiLiveVoiceSession:
    """Один разговор: клиент (виджет / SIP-адаптер) ↔ Gemini Live."""

    def __init__(self, websocket: WebSocket, assistant, client: GeminiLiveClient, db: Session,
                 client_id: str, telephony: bool, call_log: Optional[CallLogRecorder]) -> None:
        self.websocket = websocket
        self.assistant = assistant
        self.client = client
        self.db = db
        self.client_id = client_id
        self.telephony = telephony
        self.call_log = call_log
        self._owns_call_log = False
        self.in_rate = GEMINI_IN_RATE if telephony else WIDGET_IN_RATE
        self._resample_state = None
        self.pairs: List[Dict[str, str]] = []
        self.audio_chunks_in = 0
        self.started_at = time.time()
        self.on_session_end = None
        self.interruption_state: Dict[str, Any] = {
            "is_user_speaking": False,
            "is_assistant_speaking": False,
            "last_speech_start": 0,
            "last_speech_stop": 0,
            "interruption_count": 0,
            "last_interruption_time": 0,
            "assistant_audio_ever": False,
            "greeting_retries": 0,
        }

    @property
    def dialog_session_id(self) -> str:
        # у звонка диалог пишется под id звонка (SIP-роут связывает по нему звонок и реплики)
        return getattr(self.assistant, "sip_session_id", None) or self.client.session_id

    # ------------------------------------------------------------------ реплики
    async def save_turn(self, user: str, assistant: str) -> bool:
        """gemini_client.turn_saver: реплика закончилась — копим пару и пишем в журнал звонка."""
        user = "" if (user or "").strip() in _PLACEHOLDERS else (user or "").strip()
        assistant = "" if (assistant or "").strip() in _PLACEHOLDERS else (assistant or "").strip()
        if not (user or assistant):
            return True
        self.pairs.append({"user": user, "assistant": assistant})
        if self.call_log:
            if user:
                self.call_log.add("user", f"Клиент: «{user}»")
            if assistant:
                self.call_log.add("assistant", f"Агент: «{assistant}»")
        return True

    # ------------------------------------------------------------------ вход от клиента
    def _to_gemini(self, pcm: bytes) -> bytes:
        if self.in_rate == GEMINI_IN_RATE:
            return pcm
        out, self._resample_state = audioop.ratecv(pcm, 2, 1, self.in_rate, GEMINI_IN_RATE, self._resample_state)
        return out

    async def handle_client_messages(self) -> None:
        """Цикл чтения от виджета / адаптера: звук в Gemini, служебные сообщения — как в handler_gemini."""
        while True:
            message = await self.websocket.receive()
            if message.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect()
            if message.get("text") is not None:
                try:
                    data = json.loads(message["text"])
                except json.JSONDecodeError:
                    continue
                msg_type = data.get("type", "")
                if msg_type == "ping":
                    await self.websocket.send_json({"type": "pong"})
                elif msg_type == "session.update":
                    await self.websocket.send_json({
                        "type": "session.update.ack",
                        "event_id": data.get("event_id", f"ack_{int(time.time() * 1000)}"),
                    })
                elif msg_type == "input_audio_buffer.append":
                    try:
                        pcm = base64.b64decode(data.get("audio") or "")
                    except Exception:
                        continue
                    self.audio_chunks_in += 1
                    if pcm and self.client.is_connected:
                        await self.client.process_audio(self._to_gemini(pcm))
                    await self.websocket.send_json({"type": "input_audio_buffer.append.ack",
                                                    "event_id": data.get("event_id")})
                elif msg_type == "response.cancel":
                    await self.websocket.send_json({"type": "response.cancel.ack", "event_id": data.get("event_id")})
                elif msg_type == "audio_playback.stopped":
                    self.client.set_assistant_speaking(False)
                    self.interruption_state["is_assistant_speaking"] = False
                elif msg_type == "screen.context":
                    pass  # Vision на этом пути не используется
                # speech.user_started/stopped: сервер Google сам определяет речь и перебивания
            elif message.get("bytes") is not None:
                self.audio_chunks_in += 1
                if self.client.is_connected:
                    await self.client.process_audio(self._to_gemini(message["bytes"]))

    # ------------------------------------------------------------------ жизненный цикл
    async def run(self) -> None:
        self.client.turn_saver = self.save_turn
        gemini_task = asyncio.create_task(
            handle_gemini_messages(self.client, self.websocket, self.interruption_state))
        client_task = asyncio.create_task(self.handle_client_messages())
        reason = "unknown"
        try:
            done, _ = await asyncio.wait({gemini_task, client_task}, return_when=asyncio.FIRST_COMPLETED)
            if client_task in done:
                exc = client_task.exception() if not client_task.cancelled() else None
                if exc is None or isinstance(exc, (WebSocketDisconnect, ConnectionClosed)):
                    reason = "client_disconnected"
                else:
                    reason = "client_error"
                    _log(f"client loop error: {exc}", "ERROR")
            else:
                reason = "gemini_closed"
        except Exception as exc:
            reason = "error"
            _log(f"session error: {exc}\n{traceback.format_exc()}", "ERROR")
        finally:
            if self.on_session_end is not None:
                try:
                    await self.on_session_end()
                except Exception as exc:
                    _log(f"on_session_end failed: {exc}", "ERROR")
            for task in (gemini_task, client_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(gemini_task, client_task, return_exceptions=True)
            try:
                await self.client.close()
            except Exception as exc:
                _log(f"client.close error: {exc}", "WARNING")
            await self._finish(reason)

    async def _finish(self, reason: str) -> None:
        elapsed = time.time() - self.started_at
        # Реплика, которую Gemini не успел закрыть turnComplete, уже ушла в turn_saver через финальное сохранение
        saved = 0
        if self.pairs:
            saved = await _save_dialog(
                str(self.assistant.id), self.dialog_session_id, self.pairs, elapsed,
                self.client.conversation_record_id, GEMINI_LIVE_MODEL, conversation_model=ElevenConversation)
            sheet_id = getattr(self.assistant, "google_sheet_id", None)
            if sheet_id:
                for p in self.pairs:
                    if p["user"] or p["assistant"]:
                        asyncio.create_task(async_save_to_google_sheets(
                            sheet_id=sheet_id, user_message=p["user"], assistant_message=p["assistant"],
                            function_result=None, conversation_id=self.client.conversation_record_id,
                            context="Gemini Live dialog"))
        _log(f"session {self.client_id} finished ({reason}): {elapsed:.1f}s, audio_in={self.audio_chunks_in} chunks, "
             f"interruptions={self.interruption_state['interruption_count']}, "
             f"dialog_pairs={len(self.pairs)} saved={saved}")
        if self.call_log:
            self.call_log.add(
                "session",
                f"Сессия завершена ({reason}): {elapsed:.1f} с, прерываний {self.interruption_state['interruption_count']}, "
                f"реплик в диалоге {len(self.pairs)}"
                + ("" if self.interruption_state["assistant_audio_ever"] else
                   ". Gemini не прислал ни одного звука — смотрите предупреждения выше"),
                level="info" if self.interruption_state["assistant_audio_ever"] else "warning",
            )
            if self._owns_call_log:
                await self.call_log.save()
        await self._send_webhook()

    async def _send_webhook(self) -> None:
        """Итоговый webhook conversation.completed владельцу карточки (как у GPT-Live)."""
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
                        db=db, webhook_url=user.webhook_url, webhook_enabled=user.webhook_enabled,
                        source="sip_call" if self.telephony else "web_chat",
                        session_id=self.dialog_session_id, assistant_id=str(self.assistant.id),
                        assistant_name=self.assistant.name, assistant_type="eleven",
                        caller_number=None, call_direction=None, duration_seconds=None,
                        call_cost=None, record_url=None,
                    )
            finally:
                db.close()
        except Exception as exc:
            _log(f"webhook error: {exc}", "ERROR")


def _create_conversation_record(db: Session, assistant, session_id: str) -> Optional[str]:
    """Пустая запись диалога: к ней привязываются function_logs этой сессии (как у GPT-Live)."""
    try:
        conv = ElevenConversation(assistant_id=assistant.id, session_id=session_id,
                                  user_message="", assistant_message="")
        db.add(conv)
        db.commit()
        db.refresh(conv)
        return str(conv.id)
    except Exception as exc:
        _log(f"conversation record failed: {exc}", "ERROR")
        try:
            db.rollback()
        except Exception:
            pass
        return None


async def run_gemini_live_session(websocket: WebSocket, assistant, db: Session, client_id: str, fail,
                                  meter: Optional[WalletMeter] = None) -> None:
    """
    Карточка ElevenLabs с движком Gemini Live. Сокет уже принят, кошелёк проверен, журнал звонка
    заведён (виджет) или унаследован от SIP-роута — см. handler_eleven.handle_eleven_websocket_connection.
    """
    from sqlalchemy.orm.attributes import set_committed_value
    from backend.websockets.handler_eleven import _check_subscription

    api_key = resolve_api_key(db, assistant)
    if not api_key:
        await fail("gemini_not_configured",
                   "Ключ Google Gemini не задан: добавьте GEMINI_API_KEY на сервере или ключ в профиле владельца", 1011)
        return
    telephony = bool(getattr(assistant, "telephony_mode", False))
    voice = resolve_voice(assistant.live_voice)
    greeting = assistant.greeting_message or DEFAULT_ELEVEN_GREETING
    if not assistant.greeting_message:
        set_committed_value(assistant, "greeting_message", greeting)  # только в памяти
    user_agent = ""
    try:
        user_agent = websocket.headers.get("user-agent", "")
    except Exception:
        pass
    t_start = time.monotonic()

    sub_task = asyncio.create_task(_check_subscription(assistant))
    client = GeminiLiveClient(api_key, assistant, client_id, db, user_agent,
                              voice_override=voice, record_conversation=False, realtime_text=True)
    connect_task = asyncio.create_task(client.connect())

    sub = await sub_task
    if sub is not None and not sub.get("active"):
        connect_task.cancel()
        await asyncio.gather(connect_task, return_exceptions=True)
        try:
            await client.close()
        except Exception:
            pass
        code = "TRIAL_EXPIRED" if sub.get("is_trial") else "SUBSCRIPTION_EXPIRED"
        msg = "Ваш пробный период истек" if sub.get("is_trial") else "Ваша подписка истекла"
        try:
            await websocket.send_json({"type": "error", "error": {
                "code": code, "message": msg, "subscription_status": sub, "requires_payment": True}})
            await websocket.close(code=1008)
        except Exception:
            pass
        return
    if not await connect_task:
        await fail("gemini_connection_failed", f"Не удалось открыть сессию Gemini Live ({GEMINI_LIVE_MODEL})", 1011)
        return
    connect_ms = int((time.monotonic() - t_start) * 1000)

    session_id = getattr(assistant, "sip_session_id", None) or client.session_id
    client.conversation_record_id = await asyncio.to_thread(_create_conversation_record, db, assistant, session_id)

    call_log = CallLogRecorder.current()
    if call_log is not None:
        call_log.session_id = session_id
        call_log.assistant_type = "eleven"
        call_log.assistant_id = str(assistant.id)
        call_log.meta.update({
            "assistant_name": assistant.name,
            "engine": "gemini_live",
            "llm_model": GEMINI_LIVE_MODEL,
            "voice": voice,
            "language": assistant.language,
            "telephony_profile": telephony,
        })
        call_log.add("session", f"Сессия начата: Google {GEMINI_LIVE_MODEL} (речь в речь), голос {voice}; "
                                f"подключение {connect_ms} мс",
                     functions=", ".join(client.enabled_functions) or None)

    await websocket.send_json({
        "type": "connection_status",
        "status": "connected",
        "provider": "eleven",
        "engine": "gemini_live",
        "transport": GEMINI_LIVE_MODEL,
        "model": GEMINI_LIVE_MODEL,
        "message": f"Connected to Google {GEMINI_LIVE_MODEL} (full-duplex)",
        "full_duplex": True,
        "audio_rate": GEMINI_OUT_RATE,
        "voice": voice,
        "voices": GEMINI_LIVE_VOICES,
        "session_id": client.session_id,
        "client_id": client_id,
        "functions_enabled": len(client.enabled_functions),
        "telephony": telephony,
        "enable_vision": False,
        "greeting_message": greeting,
    })
    _log(f"session {client_id} started on Gemini Live in {connect_ms}ms: assistant={assistant.id} '{assistant.name}' "
         f"voice={voice} telephony={telephony} functions={client.enabled_functions}")

    session = GeminiLiveVoiceSession(websocket, assistant, client, db, client_id, telephony, call_log)
    if meter is not None:
        meter.start(websocket)
        session.on_session_end = lambda: meter.finish(session_id)
    await session.run()
    if meter is not None:
        await meter.finish(session_id)
