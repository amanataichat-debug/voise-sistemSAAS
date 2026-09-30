# backend/websockets/handler_fish.py
"""
Хендлер Fish-ассистента: OpenAI Realtime (текст) + Fish Audio (озвучка).

Говорит с клиентом на протоколе виджета — том же, что handler_live и
handler_gemini, поэтому обслуживает и браузерный виджет (/ws/fish/{id}), и
телефонные звонки через SIP-шлюз (HandlerSocket в sip_media_adapter):

    клиент → {"type":"input_audio_buffer.append","audio":<b64 PCM16 24 кГц>}
    клиент ← {"type":"response.audio.delta","delta":<b64 PCM16 24 кГц>}
             speech.started / speech.stopped / conversation.interrupted
             assistant.speech.started / assistant.speech.ended
             response.text.delta / response.text.done
             function_call.executing / function_call.completed / ...
             input.transcription (стенограмма реплики абонента)

Ход реплики:
    аудио абонента → OpenAI (server VAD, транскрипция) → текстовые дельты
    → нарезка по предложениям → Fish (text+flush) → PCM → клиенту.
Приветствие уходит в Fish напрямую, без раунда к модели, и кладётся в контекст
OpenAI как реплика ассистента.

Перебивание: input_audio_buffer.speech_started при говорящем ассистенте →
response.cancel в OpenAI, сброс Fish (переподключение), клиенту speech.started
+ conversation.interrupted (виджет останавливает воспроизведение, SIP-адаптер
шлёт мосту clear).

Функции: тот же реестр backend/functions и тот же асинхронный исполнитель
execute_and_send_function_result (function_calls.py), что у OpenAI-хендлера
(логи функций с conversation_id в fish_conversations). hangup_call обрабатывает
адаптер SIP по событию function_call.executing.

Ключи серверные: settings.OPENAI_API_KEY и settings.FISH_API_KEY.

Режим «ASR → текст» (только Eleven, handler_eleven.py): к сессии подключён
ScribeSTTClient (attach_stt). Звук абонента идёт в Scribe, а не в OpenAI;
промежуточный текст Scribe = speech.started (+ перебивание), окончательный =
speech.stopped + input.transcription + conversation.item.create (текст) +
response.create. VAD и whisper OpenAI в этом режиме выключены. Если Scribe
отвалился и не переподключился, сессия возвращается к прежней схеме
(llm.switch_to_audio_input()).
"""

import asyncio
import base64
import contextvars
import json
import time
import traceback
import uuid
from typing import Any, Dict, Optional

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from websockets.exceptions import ConnectionClosed

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.functions import normalize_function_name
from backend.models.fish_assistant import FishAssistantConfig
from backend.models.user import User
from backend.services.conversation_service import ConversationService
from backend.websockets.call_log import CallLogRecorder
from backend.websockets.fish_llm_client import FishLLMClient
from backend.websockets.fish_tts_client import FishTTSClient
from backend.websockets.function_calls import (
    execute_and_send_function_result,
    async_save_to_google_sheets,
)
from backend.websockets.sentence_detector import StreamingSentenceDetector

logger = get_logger(__name__)

TTS_RATE = 24000               # частота выхода всех браузерных хендлеров
FIRST_SENTENCE_MIN_CHARS = 25  # ранняя отправка первого предложения — быстрее первый звук
TRANSCRIPT_WAIT_SEC = 1.5      # сколько ждать стенограмму абонента перед сохранением хода
CANCEL_WAIT_SEC = 2.0          # ASR: сколько ждать, пока OpenAI закроет отменённый ответ, перед новой репликой
DEFAULT_GREETING = "Здравствуйте! Чем я могу вам помочь?"

# Ошибки OpenAI, которые не надо показывать клиенту: отмена без активного ответа
# штатно случается при перебивании между ответами.
QUIET_OPENAI_ERRORS = {"response_cancel_not_active", "cancel_not_active"}


# Префикс логов: FishVoiceSession переиспользует Eleven-хендлер (handler_eleven.py),
# который выставляет свой тег в контексте задачи — все вложенные задачи его наследуют.
LOG_TAG: contextvars.ContextVar = contextvars.ContextVar("voice_session_log_tag", default="FISH")


def _log(message: str, level: str = "INFO") -> None:
    tag = LOG_TAG.get()
    if level == "ERROR":
        logger.error(f"[{tag}] {message}")
    elif level == "WARNING":
        logger.warning(f"[{tag}] {message}")
    else:
        logger.info(f"[{tag}] {message}")


async def _save_dialog(assistant_id: str, user_message: str, assistant_message: str,
                       session_id: str) -> None:
    """Запись хода диалога в fish_conversations отдельной сессией БД (как у OpenAI-хендлера)."""
    from backend.db.session import SessionLocal
    db = None
    try:
        db = SessionLocal()
        await ConversationService.save_conversation(
            db=db,
            assistant_id=assistant_id,
            user_message=user_message,
            assistant_message=assistant_message,
            session_id=session_id,
            caller_number=None,
            tokens_used=0,
        )
    except Exception as exc:
        _log(f"dialog save failed: {exc}", "ERROR")
    finally:
        if db:
            db.close()


class _EventTap:
    """Прокси сокета клиента для исполнителя функций: результат функции попадает и в журнал звонка."""

    def __init__(self, ws, on_event) -> None:
        self._ws = ws
        self._on_event = on_event

    async def send_json(self, data: Dict[str, Any]) -> None:
        try:
            self._on_event(data)
        except Exception:
            pass
        await self._ws.send_json(data)

    def __getattr__(self, name):
        return getattr(self._ws, name)


class FishVoiceSession:
    """
    Один диалог: сокет клиента + OpenAI (текст) + синтез (Fish или ElevenLabs).

    Хендлер создаёт сессию после проверок и вызывает run(); всё состояние хода
    (стенограммы, активный ответ, ожидающие вызовы функций) живёт здесь.
    tts — любой клиент с интерфейсом FishTTSClient (say / end_of_response / clear /
    close, speaking, audio_bytes, chunks); provider — тип ассистента для контекста функций.
    """

    def __init__(
        self,
        websocket: WebSocket,
        assistant,
        llm: FishLLMClient,
        tts,
        db: Optional[Session],
        client_id: str,
        provider: str = "fish",
    ) -> None:
        self.ws = websocket
        self.assistant = assistant
        self.llm = llm
        self.tts = tts
        self.db = db
        self.client_id = client_id
        self.provider = provider

        self.response_active = False
        self.response_id: Optional[str] = None
        self.response_text = ""
        self.response_had_function_call = False
        self.detector = self._new_detector()
        self.user_transcript = ""
        self.last_user_transcript = ""
        self.assistant_speaking = False
        self.interruptions = 0
        self.function_calls = 0
        self.pending_calls: Dict[str, Dict[str, Any]] = {}  # item_id / call_id → name, call_id
        self.closed = False
        self.tokens_in = 0
        self.tokens_out = 0
        self._tasks: list = []
        # Режим «ASR → текст»: распознавание на стороне (ScribeSTTClient), OpenAI получает текст
        self.stt = None
        self.asr_fallback = False
        self.user_speaking = False
        self.asr_turns = 0
        self._llm_idle = asyncio.Event()  # у OpenAI нет активного ответа (по событиям сервера)
        self._llm_idle.set()
        self._turn_lock = asyncio.Lock()
        # Журнал звонка: у звонка его заводит SIP-роут, у виджета — сессия (и сама же сохраняет)
        self.call_log = CallLogRecorder.current()
        if self.call_log is None:
            self.call_log = CallLogRecorder(channel="widget")
            self.call_log.activate()
        self._owns_call_log = self.call_log.channel != "phone"  # журнал звонка сохраняет SIP-роут
        self.call_log.session_id = llm.session_id
        self.call_log.assistant_type = provider
        self.call_log.assistant_id = str(getattr(assistant, "id", "") or "") or None
        self.call_log.meta.update({
            "assistant_name": getattr(assistant, "name", None),
            "llm_model": getattr(llm, "model", None),
            "language": getattr(assistant, "language", None),
        })
        self._first_text_logged = False

    # ------------------------------------------------------------------ helpers
    def _new_detector(self) -> StreamingSentenceDetector:
        language = (getattr(self.assistant, "language", None) or "ru")[:2].lower()
        if language not in ("ru", "en"):
            language = "ru"
        return StreamingSentenceDetector(language=language, min_chunk_length=FIRST_SENTENCE_MIN_CHARS)

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

    # ------------------------------------------------------------------ TTS callbacks
    async def on_tts_audio(self, pcm: bytes) -> None:
        if not self.assistant_speaking:
            self.assistant_speaking = True
        await self.emit({"type": "response.audio.delta", "delta": base64.b64encode(pcm).decode("ascii")})

    async def on_tts_speech_started(self) -> None:
        self.assistant_speaking = True
        latency = self.call_log.reply_audio_started()
        if latency is not None:
            self.call_log.add("latency", f"Первый звук ответа через {latency} мс после конца фразы клиента",
                              level="warning" if latency >= 2500 else "info", ms=latency)
        else:
            self.call_log.add("tts", "Ассистент начал говорить")
        await self.emit({"type": "assistant.speech.started", "response_id": self.response_id, "timestamp": time.time()})

    async def on_tts_speech_ended(self) -> None:
        self.assistant_speaking = False
        self.call_log.add("tts", "Ассистент закончил говорить")
        await self.emit({"type": "assistant.speech.ended", "timestamp": time.time()})

    # ------------------------------------------------------------------ ASR (режим «ASR → текст»)
    def attach_stt(self, stt) -> None:
        """Подключить распознавание: звук абонента пойдёт в stt, реплики — в OpenAI текстом."""
        self.stt = stt
        stt.on_partial = self.on_asr_partial
        stt.on_committed = self.on_asr_committed
        stt.on_closed = self.on_asr_closed

    async def on_asr_partial(self, text: str) -> None:
        """Первые распознанные слова реплики: абонент заговорил."""
        if self.user_speaking:
            return
        self.user_speaking = True
        self.call_log.add("asr", f"Клиент заговорил: «{text}»")
        await self.emit({"type": "speech.started", "timestamp": time.time()})
        if self.assistant_speaking or self.response_active or self.tts.speaking:
            await self.barge_in("asr_partial")

    async def on_asr_committed(self, text: str) -> None:
        """Фраза закончена (пауза ELEVEN_ASR_SILENCE_MS): отправить её модели текстом."""
        self.user_speaking = False
        await self.emit({"type": "speech.stopped", "timestamp": time.time()})
        if not text:
            self.call_log.add("asr", "Фраза закончена, но текст пустой (шум?)")
            return
        _log(f"user (asr): {text}")
        self.call_log.user_turns += 1
        self.call_log.user_done()
        self.call_log.add("user", f"Клиент: «{text}»")
        await self.emit({"type": "input.transcription", "transcript": text})
        self._track(self._send_user_turn(text))

    async def on_asr_closed(self, fatal: bool) -> None:
        """Scribe отвалился посреди разговора: одна попытка переподключения, иначе прежняя схема."""
        if self.closed or self.stt is None:
            return
        self.user_speaking = False
        if not fatal:
            _log("ASR connection lost, reconnecting", "WARNING")
            if await self.stt.connect():
                self.call_log.add("asr", "Scribe переподключён")
                return
        _log(f"ASR unavailable ({self.stt.fatal_error or 'connection lost'}): "
             f"falling back to audio input in OpenAI", "WARNING")
        self.stt = None
        self.asr_fallback = True
        self.call_log.meta["asr"] = "openai audio (fallback)"
        await self.llm.switch_to_audio_input()

    async def _send_user_turn(self, text: str) -> None:
        """Реплика абонента текстом → новый ответ модели. Реплики идут строго по одной."""
        async with self._turn_lock:
            if self.closed:
                return
            # Реплика пришла без промежуточного текста, а ассистент ещё говорит — перебиваем здесь.
            if self.assistant_speaking or self.response_active or self.tts.speaking:
                await self.barge_in("asr_commit")
            # Ответ, отменённый при перебивании, должен закрыться на стороне OpenAI,
            # иначе response.create вернёт conversation_already_has_active_response.
            if not self._llm_idle.is_set():
                await self.llm.cancel_response()
                try:
                    await asyncio.wait_for(self._llm_idle.wait(), timeout=CANCEL_WAIT_SEC)
                except asyncio.TimeoutError:
                    _log("previous response did not finish in time", "WARNING")
            # Если прошлую фразу перебили до ответа, в сохранение хода идут обе.
            self.user_transcript = f"{self.user_transcript} {text}".strip() if self.user_transcript else text
            self.asr_turns += 1
            await self.llm.add_user_text(text)
            await self.llm.create_response()
            self.call_log.add("llm", "Фраза отправлена модели, запрошен ответ")

    # ------------------------------------------------------------------ greeting
    async def greet(self) -> None:
        greeting = (getattr(self.assistant, "greeting_message", None) or DEFAULT_GREETING).strip()
        if not greeting:
            return
        self.call_log.add("assistant", f"Приветствие: «{greeting}»")
        await self.llm.add_assistant_message(greeting)
        await self.tts.say(greeting)
        self.tts.end_of_response()
        _log(f"greeting sent to Fish: {greeting[:60]}")

    # ------------------------------------------------------------------ barge-in
    async def barge_in(self, reason: str) -> None:
        """Абонент заговорил поверх ассистента: остановить модель и синтез, сообщить клиенту."""
        self.interruptions += 1
        _log(f"barge-in #{self.interruptions} ({reason}); response_active={self.response_active}")
        self.call_log.interruptions += 1
        self.call_log.add("barge_in", f"Перебивание #{self.interruptions}", reason=reason)
        if self.response_active:
            await self.llm.cancel_response()
        await self.tts.clear()
        self.assistant_speaking = False
        self.response_active = False
        self.detector = self._new_detector()
        await self.emit({"type": "conversation.interrupted", "timestamp": time.time(),
                         "interruption_count": self.interruptions})

    # ------------------------------------------------------------------ OpenAI events
    async def handle_llm_events(self) -> None:
        try:
            while self.llm.is_connected and self.llm.ws is not None:
                raw = await self.llm.ws.recv()
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await self._on_llm_event(event)
        except ConnectionClosed:
            _log("OpenAI connection closed")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log(f"OpenAI event loop error: {exc}\n{traceback.format_exc()}", "ERROR")
        finally:
            self.llm.is_connected = False

    async def _on_llm_event(self, event: Dict[str, Any]) -> None:
        etype = event.get("type", "")

        if etype == "input_audio_buffer.speech_started":
            self.call_log.add("vad", "Клиент заговорил (VAD OpenAI)")
            await self.emit({"type": "speech.started", "timestamp": time.time()})
            if self.assistant_speaking or self.response_active or self.tts.speaking:
                await self.barge_in("speech_started")
            return

        if etype == "input_audio_buffer.speech_stopped":
            self.call_log.user_turns += 1
            self.call_log.user_done()
            self.call_log.add("vad", "Клиент замолчал (VAD OpenAI)")
            await self.emit({"type": "speech.stopped", "timestamp": time.time()})
            return

        if etype == "conversation.item.input_audio_transcription.completed":
            transcript = (event.get("transcript") or "").strip()
            if transcript:
                self.user_transcript = transcript
                _log(f"user: {transcript}")
                self.call_log.add("user", f"Клиент (whisper): «{transcript}»")
                await self.emit({"type": "input.transcription", "transcript": transcript})
            return

        if etype == "response.created":
            self._llm_idle.clear()
            self.response_active = True
            self.response_id = (event.get("response") or {}).get("id")
            self.response_text = ""
            self.response_had_function_call = False
            self.detector = self._new_detector()
            self._first_text_logged = False
            return

        if etype == "response.output_text.delta":
            delta = event.get("delta") or ""
            if not delta or not self.response_active:
                return
            self.response_text += delta
            if not self._first_text_logged:
                self._first_text_logged = True
                since = self.call_log.since_user_done_ms()
                self.call_log.add("llm", "Первый текст ответа модели" + (f" через {since} мс" if since is not None else ""))
            await self.emit({"type": "response.text.delta", "delta": delta})
            for sentence in self.detector.add_chunk(delta):
                await self.tts.say(sentence)
            return

        if etype == "response.output_text.done":
            text = event.get("text") or self.response_text
            # Дельт могло не быть или прийти не все — озвучиваем хвост, которого детектор не видел.
            if text.startswith(self.response_text) and len(text) > len(self.response_text):
                tail = text[len(self.response_text):]
                await self.emit({"type": "response.text.delta", "delta": tail})
                for sentence in self.detector.add_chunk(tail):
                    await self.tts.say(sentence)
            self.response_text = text
            rest = self.detector.flush()
            if rest:
                await self.tts.say(rest)
            await self.emit({"type": "response.text.done", "text": text})
            return

        if etype == "response.output_item.added":
            item = event.get("item") or {}
            if item.get("type") == "function_call":
                info = {"name": item.get("name"), "call_id": item.get("call_id")}
                if item.get("id"):
                    self.pending_calls[item["id"]] = info
                if item.get("call_id"):
                    self.pending_calls[item["call_id"]] = info
                await self.emit({"type": "function_call.started", "function": normalize_function_name(item.get("name") or ""),
                                 "call_id": item.get("call_id")})
            return

        if etype == "response.function_call_arguments.done":
            await self._on_function_call(event)
            return

        if etype == "response.done":
            self._llm_idle.set()
            await self._on_response_done(event)
            return

        if etype == "error":
            err = event.get("error") or {}
            if err.get("code") in QUIET_OPENAI_ERRORS:
                return
            _log(f"OpenAI error: {json.dumps(event, ensure_ascii=False)[:400]}", "ERROR")
            await self.emit({"type": "error", "error": err or event})
            return

    async def _on_function_call(self, event: Dict[str, Any]) -> None:
        call_id = event.get("call_id")
        info = self.pending_calls.get(event.get("item_id") or "") or self.pending_calls.get(call_id or "") or {}
        name = event.get("name") or info.get("name")
        call_id = call_id or info.get("call_id")
        arguments_str = event.get("arguments") or "{}"

        if not name or not call_id:
            _log(f"function call without name/call_id: {event}", "ERROR")
            await self.emit({"type": "function_call.error", "error": "Cannot determine function", "call_id": call_id})
            return

        normalized = normalize_function_name(name) or name
        if normalized not in self.llm.enabled_functions:
            _log(f"unauthorized function {normalized}", "WARNING")
            await self.emit({"type": "function_call.error", "function": normalized, "error": "Function not activated"})
            await self.llm.send_function_result(call_id, {"error": f"Function {normalized} not allowed", "status": "error"})
            return

        try:
            arguments = json.loads(arguments_str) if arguments_str else {}
        except json.JSONDecodeError as exc:
            await self.emit({"type": "error", "error": {"code": "function_args_error", "message": str(exc)}})
            return

        self.function_calls += 1
        self.response_had_function_call = True
        await self.emit({"type": "function_call.executing", "function": normalized, "function_call_id": call_id,
                         "arguments": arguments, "async_execution": True})
        _log(f"function {normalized}({json.dumps(arguments, ensure_ascii=False)[:200]})")
        self.call_log.functions += 1
        self.call_log.add("function", f"Вызов функции {normalized}",
                          arguments=json.dumps(arguments, ensure_ascii=False)[:500])

        self._track(execute_and_send_function_result(
            openai_client=self.llm,
            websocket=_EventTap(self.ws, self._on_function_event),
            function_call_id=call_id,
            function_name=normalized,
            arguments=arguments,
            context={
                "assistant_config": self.assistant,
                "client_id": self.client_id,
                "db_session": self.db,
                "websocket": self.ws,
                "provider": self.provider,
            },
            user_transcript=self.user_transcript or self.last_user_transcript,
        ))

    def _on_function_event(self, data: Dict[str, Any]) -> None:
        mtype = data.get("type")
        name = data.get("function")
        if mtype == "function_call.completed":
            result = data.get("result")
            text = json.dumps(result, ensure_ascii=False, default=str) if result is not None else ""
            failed = isinstance(result, dict) and bool(result.get("error"))
            if failed:
                self.call_log.function_errors += 1
            secs = data.get("execution_time")
            self.call_log.add("function", f"Функция {name} {'вернула ошибку' if failed else 'выполнена'}"
                              + (f" за {secs:.2f} с" if isinstance(secs, (int, float)) else ""),
                              level="warning" if failed else "info", result=text[:500] or None)
        elif mtype in ("function_call.error", "function_call.delivery_error"):
            self.call_log.function_errors += 1
            self.call_log.add("function", f"Функция {name or ''} — ошибка: {data.get('error')}", level="error")

    async def _on_response_done(self, event: Dict[str, Any]) -> None:
        response = event.get("response") or {}
        status = response.get("status")
        usage = response.get("usage") or {}
        self.tokens_in += int(usage.get("input_tokens") or 0)
        self.tokens_out += int(usage.get("output_tokens") or 0)
        self.response_active = False

        if status == "cancelled":
            self.call_log.add("llm", "Ответ модели отменён (перебивание)")
            self.response_text = ""
            return
        if status and status not in ("completed", "cancelled"):
            details = response.get("status_details") or {}
            self.call_log.add("llm", f"Ответ модели завершился со статусом {status}",
                              level="warning", details=json.dumps(details, ensure_ascii=False)[:300])

        # Текст ответа мог прийти только в response.done (без дельт) — дошлём в Fish.
        if not self.response_text:
            for item in response.get("output") or []:
                for part in item.get("content") or []:
                    if part.get("type") in ("output_text", "text") and part.get("text"):
                        self.response_text += part["text"]
            if self.response_text:
                await self.emit({"type": "response.text.delta", "delta": self.response_text})
                await self.tts.say(self.response_text)

        if self.response_text:
            self.tts.end_of_response()
            _log(f"assistant: {self.response_text[:120]}")
            self.call_log.assistant_turns += 1
            self.call_log.add("assistant", f"Ассистент: «{self.response_text}»",
                              tokens_in=usage.get("input_tokens"), tokens_out=usage.get("output_tokens"))

        assistant_text = self.response_text
        if assistant_text:
            self._track(self._save_turn(assistant_text))
        self.response_text = ""

    async def _save_turn(self, assistant_text: str) -> None:
        """Сохранить ход: подождать стенограмму абонента (она приходит асинхронно)."""
        waited = 0.0
        while not self.user_transcript and waited < TRANSCRIPT_WAIT_SEC:
            await asyncio.sleep(0.1)
            waited += 0.1
        user_text = self.user_transcript or self.last_user_transcript
        if self.user_transcript:
            self.last_user_transcript = self.user_transcript
            self.user_transcript = ""
        if not user_text:
            return  # приветствие / ответ без реплики абонента — как у OpenAI-хендлера, не пишем
        await _save_dialog(str(self.assistant.id), user_text, assistant_text, self.llm.session_id)
        sheet_id = getattr(self.assistant, "google_sheet_id", None)
        if sheet_id:
            self._track(async_save_to_google_sheets(
                sheet_id=sheet_id, user_message=user_text, assistant_message=assistant_text,
                function_result=None, conversation_id=self.llm.conversation_record_id, context="Fish dialog",
            ))

    # ------------------------------------------------------------------ client loop
    async def handle_client_messages(self) -> None:
        while True:
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
                if not audio:
                    continue
                if self.stt is not None:
                    # ASR-режим; пока Scribe переподключается, звук теряется (доли секунды)
                    if self.stt.is_connected:
                        await self.stt.send_audio(audio)
                elif self.llm.is_connected:
                    await self.llm.process_audio(audio)
                continue
            if mtype == "ping":
                await self.emit({"type": "pong"})
            elif mtype == "session.update":
                await self.emit({"type": "session.update.ack", "event_id": data.get("event_id")})
            elif mtype == "input_audio_buffer.commit":
                await self.emit({"type": "input_audio_buffer.commit.ack", "event_id": data.get("event_id"),
                                 "note": "server_vad_active"})
            elif mtype == "input_audio_buffer.clear":
                await self.llm.clear_audio_buffer()
                await self.emit({"type": "input_audio_buffer.clear.ack", "event_id": data.get("event_id")})
            elif mtype in ("response.cancel", "interruption.manual"):
                await self.barge_in(mtype)
                await self.emit({"type": f"{mtype}.ack", "event_id": data.get("event_id")})
            elif mtype == "audio_playback.stopped":
                self.assistant_speaking = False
            elif mtype == "speech.user_started":
                if self.assistant_speaking or self.tts.speaking:
                    await self.barge_in("client_speech")
            elif mtype == "input_text":
                text_in = (data.get("text") or "").strip()
                if text_in:
                    if self.llm.text_input:
                        self._track(self._send_user_turn(text_in))
                    else:
                        self.user_transcript = text_in
                        await self.llm.add_user_text(text_in)
                        await self.llm.create_response()

    # ------------------------------------------------------------------ run
    async def run(self) -> None:
        started = time.time()
        llm_task = asyncio.create_task(self.handle_llm_events())
        try:
            await self.greet()
            await self.handle_client_messages()
        except (WebSocketDisconnect, ConnectionClosed):
            _log(f"client disconnected: {self.client_id}")
            self.call_log.add("session", "Клиент отключился")
        except Exception as exc:
            _log(f"client loop error: {exc}\n{traceback.format_exc()}", "ERROR")
        finally:
            self.closed = True
            llm_task.cancel()
            # Сохранение последней реплики (и прочие фоновые задачи) успевают закончиться:
            # раньше их отменяли сразу, и при сбросе звонка последний ход терялся.
            pending = list(self._tasks)
            if pending:
                _, still = await asyncio.wait(pending, timeout=TRANSCRIPT_WAIT_SEC + 1.5)
                for task in still:
                    task.cancel()
            await asyncio.gather(llm_task, *self._tasks, return_exceptions=True)
            stt, self.stt = self.stt, None
            if stt is not None:
                await stt.close()
                _log(f"ASR: {stt.audio_seconds:.1f}s audio, {stt.commits} phrases, {self.asr_turns} turns")
            elif self.asr_fallback:
                _log("ASR: fell back to audio input during the session")
            await self.tts.close()
            await self.llm.close()
            _log(
                f"session {self.client_id} finished: {time.time() - started:.1f}s, "
                f"interruptions={self.interruptions} functions={self.function_calls} "
                f"tokens_in={self.tokens_in} tokens_out={self.tokens_out} "
                f"tts_audio={self.tts.audio_bytes / (TTS_RATE * 2):.1f}s in {self.tts.chunks} chunks"
            )
            self.call_log.add(
                "session",
                f"Сессия завершена: {time.time() - started:.1f} с, озвучено {self.tts.audio_bytes / (TTS_RATE * 2):.1f} с, "
                f"токенов вход/выход {self.tokens_in}/{self.tokens_out}",
            )
            if self._owns_call_log:
                await self.call_log.save()


_tables_ready = False


def _ensure_tables() -> None:
    """
    Создать fish_conversations, если её нет. Страховка, как в api/sip_gateway.py:
    startup-воркер Gunicorn на Render иногда убивается по таймауту до create_all.
    """
    global _tables_ready
    if _tables_ready:
        return
    try:
        from backend.models.base import Base, engine
        from backend.models.fish_assistant import FishConversation
        Base.metadata.create_all(engine, tables=[FishConversation.__table__], checkfirst=True)
        _tables_ready = True
    except Exception as exc:
        _log(f"ensure tables failed: {exc}", "ERROR")


async def handle_fish_websocket_connection(websocket: WebSocket, assistant_id: str, db: Session) -> None:
    """Точка входа для /ws/fish/{assistant_id} и для SIP-адаптера."""
    client_id = str(uuid.uuid4())
    await websocket.accept()
    _ensure_tables()

    async def fail(code: str, message: str, ws_code: int = 1008) -> None:
        _log(f"{code}: {message} (assistant {assistant_id})", "WARNING")
        try:
            await websocket.send_json({"type": "error", "error": {"code": code, "message": message}})
            await websocket.close(code=ws_code)
        except Exception:
            pass

    try:
        try:
            assistant_uuid = uuid.UUID(str(assistant_id))
        except ValueError:
            await fail("assistant_not_found", "Assistant not found")
            return
        assistant = db.query(FishAssistantConfig).filter(FishAssistantConfig.id == assistant_uuid).first()
        if not assistant:
            await fail("assistant_not_found", "Assistant not found")
            return
        if not assistant.is_active:
            await fail("assistant_inactive", "Assistant is inactive")
            return

        user = db.query(User).filter(User.id == assistant.user_id).first() if assistant.user_id else None
        if user and not user.is_admin and user.email != "amanat.aichat@gmail.com":
            from backend.services.user_service import UserService
            sub = await UserService.check_subscription_status(db, str(user.id))
            if not sub.get("active"):
                code = "TRIAL_EXPIRED" if sub.get("is_trial") else "SUBSCRIPTION_EXPIRED"
                msg = "Ваш пробный период истек" if sub.get("is_trial") else "Ваша подписка истекла"
                try:
                    await websocket.send_json({"type": "error", "error": {
                        "code": code, "message": msg, "subscription_status": sub, "requires_payment": True}})
                    await websocket.close(code=1008)
                except Exception:
                    pass
                return

        if not settings.OPENAI_API_KEY:
            await fail("openai_not_configured", "OPENAI_API_KEY is not configured on the server", 1011)
            return
        if not settings.FISH_API_KEY:
            await fail("fish_not_configured", "FISH_API_KEY is not configured on the server", 1011)
            return

        telephony = bool(getattr(assistant, "telephony_mode", False))
        user_agent = ""
        try:
            user_agent = websocket.headers.get("user-agent", "")
        except Exception:
            pass

        llm = FishLLMClient(settings.OPENAI_API_KEY, assistant, client_id, db, user_agent, telephony=telephony)
        if not await llm.connect():
            await fail("openai_connection_failed", "Failed to connect to OpenAI", 1011)
            return

        session = FishVoiceSession(websocket, assistant, llm, None, db, client_id)
        tts = FishTTSClient(
            settings.FISH_API_KEY, assistant, TTS_RATE,
            on_audio=session.on_tts_audio,
            on_speech_started=session.on_tts_speech_started,
            on_speech_ended=session.on_tts_speech_ended,
            label=client_id[:8],
        )
        session.tts = tts
        try:
            await tts.connect()
        except Exception as exc:
            _log(f"Fish connect failed: {exc}", "ERROR")
            await llm.close()
            await fail("fish_connection_failed", "Failed to connect to Fish Audio", 1011)
            return

        await websocket.send_json({
            "type": "connection_status",
            "status": "connected",
            "provider": "fish",
            "message": f"Connected: OpenAI {llm.model} (text) + Fish Audio",
            "model": llm.model,
            "fish_model": assistant.fish_model,
            "functions_enabled": len(llm.enabled_functions),
            "client_id": client_id,
            "telephony": telephony,
            "greeting_message": assistant.greeting_message or DEFAULT_GREETING,
        })
        _log(f"session {client_id} started: assistant={assistant.id} '{assistant.name}' telephony={telephony}")

        await session.run()

    except WebSocketDisconnect:
        _log(f"client disconnected before start: {client_id}")
    except Exception as exc:
        _log(f"fatal: {exc}\n{traceback.format_exc()}", "ERROR")
        try:
            await websocket.send_json({"type": "error", "error": {"code": "server_error", "message": "Internal server error"}})
        except Exception:
            pass
