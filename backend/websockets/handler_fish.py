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
import re
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
# Фраза-заполнитель: функция выполняется дольше FILLER_DELAY_SEC, а ассистент молчит —
# коротко говорим «секунду», чтобы абонент не слушал тишину. Не для hangup_call (там прощание).
FILLER_DELAY_SEC = 0.7
NO_FILLER_FUNCTIONS = {"hangup_call"}
FILLER_PHRASES = {
    "ky": "Бир секунд, азыр карап көрөйүн.",
    "ru": "Одну секунду, сейчас проверю.",
    "kk": "Бір сәт, қазір қарап көрейін.",
    "uz": "Bir soniya, hozir tekshirib ko'raman.",
    "en": "One moment, let me check.",
    "tr": "Bir saniye, hemen bakıyorum.",
}
# Перебивание по распознаванию (ASR-режим).
# Эхо: по телефону голос ассистента возвращается в линию, и Scribe распознаёт его как речь
# клиента. Черновик без букв («.») или повторяющий то, что ассистент сейчас говорит, — не перебивание.
ECHO_WORD_SHARE = 0.6          # доля слов черновика, найденных в речи ассистента, начиная с которой это эхо
ECHO_TAIL_SEC = 1.5            # сколько после конца речи ассистента ещё ждать его эхо
GREETING_MIN_WORDS = 2         # приветствие перебивает только фраза от 2 слов (не «алло», «да»)
REGREET_AFTER_SEC = 2.5        # приветствие оборвали, а клиент молчит — повторить его
# hangup_call с такой причиной не выполняем: модель кладёт трубку, «не поняв» собеседника
HANGUP_REJECT_REASONS = {"technical_issue"}
HANGUP_REJECT_RESULT = {
    "status": "rejected",
    "message": ("Звонок НЕ завершён. Не клади трубку из-за того, что не понял собеседника или плохо слышно: "
                "извинись и попроси повторить или сказать иначе. Завершай звонок, только когда разговор "
                "окончен или собеседник прощается."),
}
LOOP_LAG_WARN_MS = 400         # event loop был занят дольше — предупреждение в журнал звонка
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


_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _words(text: str) -> list:
    return [w.lower() for w in _WORD_RE.findall(text or "")]


def _has_letters(text: str) -> bool:
    return bool(_WORD_RE.search(text or ""))


def _word_count(text: str) -> int:
    """Слова из 2+ букв: «А-а-а, мо» — одно слово, а не четыре."""
    return sum(1 for w in _words(text) if len(w) > 1)


async def _save_dialog(assistant_id: str, user_message: str, assistant_message: str,
                       session_id: str) -> None:
    """
    Запись хода диалога отдельной сессией БД — в отдельном потоке. ConversationService
    работает с БД синхронно: прямо в event loop это останавливало звонок на ~1,2 с ровно
    между «текст ответа готов» и «первый звук» (журнал звонка 30.09).
    """
    await asyncio.to_thread(asyncio.run, _save_dialog_sync(assistant_id, user_message, assistant_message, session_id))


async def _save_dialog_sync(assistant_id: str, user_message: str, assistant_message: str,
                            session_id: str) -> None:
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
        # Фраза-заполнитель на время долгой функции
        self._pending_function_calls: set = set()
        self._filler_response_id: Optional[str] = None
        self._filler_next_speech = False
        self._hangup_requested = False
        # Эхо и приветствие
        self._speaking_text = ""        # что ассистент говорит сейчас (для фильтра эха)
        self._speech_ended_at = 0.0
        self._greeting_playing = False
        self._greeting_interrupted = False
        self._regreeted = False
        self._deferred_turn: Optional[str] = None  # «алло» во время приветствия — отправим после него
        # Реальное воспроизведение: синтез быстрее реального времени, и «синтез закончен» наступает
        # раньше, чем клиент дослушал. _play_until — когда доиграет всё отданное клиенту аудио.
        self._play_until = 0.0
        self._greeting_until = 0.0
        # Приветствие: готовая запись из кэша (handler_eleven) и сбор аудио для кэша
        self.greeting_started = False
        self.on_greeting_audio = None      # колбэк(pcm) — синтезированное приветствие целиком
        self._greeting_capture: Optional[bytearray] = None
        self._stt_resampler = None         # 24 кГц → частота распознавания (телефон: 8 кГц)

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
    def _audible(self) -> bool:
        """Клиент ещё слышит ассистента (отданное аудио не доиграло)."""
        return time.monotonic() < self._play_until

    def _greeting_active(self) -> bool:
        return self._greeting_playing or time.monotonic() < self._greeting_until

    async def on_tts_audio(self, pcm: bytes) -> None:
        now = time.monotonic()
        self._play_until = max(self._play_until, now) + len(pcm) / (TTS_RATE * 2)
        if self._greeting_capture is not None:
            self._greeting_capture.extend(pcm)
        if not self.assistant_speaking:
            self.assistant_speaking = True
        await self.emit({"type": "response.audio.delta", "delta": base64.b64encode(pcm).decode("ascii")})

    async def on_tts_speech_started(self) -> None:
        self.assistant_speaking = True
        filler, self._filler_next_speech = self._filler_next_speech, False
        pause_ms = self.call_log.pause_ms
        latency = self.call_log.reply_audio_started()
        if latency is not None:
            self.call_log.add(
                "latency",
                f"Первый звук {'(заполнитель) ' if filler else 'ответа '}через {latency} мс после конца речи клиента"
                + (f" (из них {pause_ms} мс — пауза, по которой распознавание определяет конец фразы)" if pause_ms else ""),
                level="warning" if latency >= 3000 else "info", ms=latency,
            )
        else:
            self.call_log.add("tts", "Ассистент начал говорить")
        await self.emit({"type": "assistant.speech.started", "response_id": self.response_id, "timestamp": time.time()})

    async def on_tts_speech_ended(self) -> None:
        self.assistant_speaking = False
        self._speech_ended_at = time.monotonic()
        greeting_done = self._greeting_playing
        self._greeting_playing = False
        tail = max(0.0, self._play_until - time.monotonic())
        self.call_log.add("tts", "Синтез реплики закончен" + (f", клиент дослушает через {tail:.1f} с" if tail > 0.3 else ""))
        if greeting_done:
            self._greeting_until = self._play_until
            capture, self._greeting_capture = self._greeting_capture, None
            if capture and self.on_greeting_audio:
                try:
                    self.on_greeting_audio(bytes(capture))
                except Exception as exc:
                    _log(f"greeting cache failed: {exc}", "WARNING")
        if greeting_done and self._deferred_turn:
            # ответ встанет в очередь воспроизведения сразу после приветствия — не перебиваем его
            text, self._deferred_turn = self._deferred_turn, None
            self.call_log.user_done()
            self._track(self._send_user_turn(text, interrupt=False))
        await self.emit({"type": "assistant.speech.ended", "timestamp": time.time()})

    # ------------------------------------------------------------------ ASR (режим «ASR → текст»)
    def attach_stt(self, stt) -> None:
        """Подключить распознавание: звук абонента пойдёт в stt, реплики — в OpenAI текстом."""
        self.stt = stt
        rate = getattr(stt, "sample_rate", TTS_RATE)
        if rate != TTS_RATE:
            from backend.websockets.sip_media_adapter import Resampler
            self._stt_resampler = Resampler(TTS_RATE, rate)
        stt.on_partial = self.on_asr_partial
        stt.on_committed = self.on_asr_committed
        stt.on_closed = self.on_asr_closed

    async def on_asr_partial(self, text: str) -> None:
        """Первые распознанные слова реплики: абонент заговорил."""
        if self.user_speaking:
            return
        assistant_busy = self.assistant_speaking or self.response_active or self.tts.speaking or self._audible()
        if not _has_letters(text):
            self.call_log.add("asr", f"Черновик без слов «{text}» — не считаем речью (шум или эхо)")
            return
        if self._is_echo(text):
            self.call_log.add("asr", f"Эхо речи ассистента «{text}» — не перебиваем")
            return
        if self._greeting_active() and _word_count(text) < GREETING_MIN_WORDS:
            return  # короткое «алло» не обрывает приветствие; реплика придёт окончательным текстом
        self.user_speaking = True
        self.call_log.add("asr", f"Клиент заговорил: «{text}»")
        await self.emit({"type": "speech.started", "timestamp": time.time()})
        if assistant_busy:
            await self.barge_in("asr_partial")

    def _is_echo(self, text: str) -> bool:
        """Черновик повторяет то, что ассистент говорит (или только что сказал), — это эхо линии."""
        if not self._speaking_text:
            return False
        speaking = self.assistant_speaking or self.tts.speaking or self._audible()
        if not speaking and time.monotonic() - max(self._speech_ended_at, self._play_until) > ECHO_TAIL_SEC:
            return False
        words = [w for w in _words(text) if len(w) > 1]
        if not words:
            return False
        said = set(_words(self._speaking_text))
        return sum(1 for w in words if w in said) / len(words) >= ECHO_WORD_SHARE

    async def on_asr_committed(self, text: str) -> None:
        """Фраза закончена (пауза ELEVEN_ASR_SILENCE_MS): отправить её модели текстом."""
        self.user_speaking = False
        await self.emit({"type": "speech.stopped", "timestamp": time.time()})
        if not text or not _has_letters(text):
            self.call_log.add("asr", f"Фраза без слов «{text}» — пропускаем (шум?)")
            return
        if self._is_echo(text):
            self.call_log.add("asr", f"Эхо речи ассистента «{text}» — пропускаем")
            return
        if self._greeting_active() and _word_count(text) < GREETING_MIN_WORDS:
            # «Алло» поверх приветствия: пусть договорит, реплику отправим сразу после
            self._deferred_turn = f"{self._deferred_turn} {text}".strip() if self._deferred_turn else text
            self.call_log.add("user", f"Клиент (во время приветствия): «{text}» — ответим после приветствия")
            return
        _log(f"user (asr): {text}")
        self.call_log.user_turns += 1
        self.call_log.user_done(back_ms=getattr(self.stt, "silence_ms", 0) if self.stt else 0)
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
        if not fatal and not getattr(self.llm, "supports_audio_input", True):
            # у текстовой модели нет запасного «звука в модель» — ещё пара попыток вернуть Scribe
            for attempt in range(2):
                await asyncio.sleep(1.0 + attempt)
                if self.closed:
                    return
                if await self.stt.connect():
                    self.call_log.add("asr", f"Scribe переподключён (попытка {attempt + 2})")
                    return
        if getattr(self.llm, "supports_audio_input", True):
            _log(f"ASR unavailable ({self.stt.fatal_error or 'connection lost'}): "
                 f"falling back to audio input in OpenAI", "WARNING")
            self.stt = None
            self.asr_fallback = True
            self.call_log.meta["asr"] = "openai audio (fallback)"
            await self.llm.switch_to_audio_input()
        else:
            _log(f"ASR unavailable ({self.stt.fatal_error or 'connection lost'}): "
                 f"the assistant can no longer hear the caller", "ERROR")
            self.stt = None
            self.asr_fallback = True
            self.call_log.meta["asr"] = "Scribe недоступен — клиент не слышен"

    async def _send_user_turn(self, text: str, interrupt: bool = True) -> None:
        """Реплика абонента текстом → новый ответ модели. Реплики идут строго по одной."""
        async with self._turn_lock:
            if self.closed:
                return
            if self._deferred_turn:  # «алло» из-под приветствия, которое потом всё же перебили
                text, self._deferred_turn = f"{self._deferred_turn} {text}".strip(), None
            # Реплика пришла без промежуточного текста, а ассистент ещё говорит — перебиваем здесь.
            if interrupt and (self.assistant_speaking or self.response_active or self.tts.speaking
                              or self._audible()):
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
        self.greeting_started = True
        self.call_log.add("assistant", f"Приветствие: «{greeting}»")
        self._speaking_text = greeting
        self._greeting_playing = True
        if self.on_greeting_audio:
            self._greeting_capture = bytearray()
        await self.llm.add_assistant_message(greeting)
        await self.tts.say(greeting)
        self.tts.end_of_response()
        _log(f"greeting sent to Fish: {greeting[:60]}")

    async def play_cached_greeting(self, pcm: bytes) -> None:
        """Приветствие готовой записью (кэш синтеза): звучит сразу, без ожидания ElevenLabs."""
        greeting = (getattr(self.assistant, "greeting_message", None) or DEFAULT_GREETING).strip()
        self.greeting_started = True
        self.call_log.add("assistant", f"Приветствие (готовая запись, без синтеза): «{greeting}»")
        self._speaking_text = greeting
        self._greeting_playing = True
        await self.llm.add_assistant_message(greeting)
        await self.on_tts_speech_started()
        step = TTS_RATE * 2 // 5  # по 200 мс
        for i in range(0, len(pcm), step):
            if not self._greeting_playing:  # перебили
                return
            await self.on_tts_audio(pcm[i:i + step])
        await self.on_tts_speech_ended()

    async def _regreet_if_silent(self, turns_before: int) -> None:
        """Приветствие оборвали, а клиент так ничего и не сказал — повторить его (один раз)."""
        await asyncio.sleep(REGREET_AFTER_SEC)
        if (self.closed or self._regreeted or self.asr_turns != turns_before or self.user_speaking
                or self.assistant_speaking or self.response_active or self.tts.speaking):
            return
        self._regreeted = True
        self.call_log.add("assistant", "Приветствие оборвали, клиент молчит — повторяем")
        greeting = (getattr(self.assistant, "greeting_message", None) or DEFAULT_GREETING).strip()
        self._speaking_text = greeting
        self._greeting_playing = True
        await self.tts.say(greeting)
        self.tts.end_of_response()

    async def _watch_loop_lag(self) -> None:
        """Event loop заблокирован (синхронная работа в async-коде) — в журнал звонка: это прямая задержка ответа."""
        tick = 0.2
        try:
            while not self.closed:
                t0 = time.monotonic()
                await asyncio.sleep(tick)
                lag_ms = int((time.monotonic() - t0 - tick) * 1000)
                if lag_ms >= LOOP_LAG_WARN_MS:
                    _log(f"event loop blocked for {lag_ms} ms", "WARNING")
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------ barge-in
    async def barge_in(self, reason: str) -> None:
        """Абонент заговорил поверх ассистента: остановить модель и синтез, сообщить клиенту."""
        self.interruptions += 1
        _log(f"barge-in #{self.interruptions} ({reason}); response_active={self.response_active}")
        self.call_log.interruptions += 1
        self.call_log.add("barge_in", f"Перебивание #{self.interruptions}", reason=reason)
        self._play_until = time.monotonic()  # очередь воспроизведения у клиента сброшена
        self._greeting_capture = None        # оборванное приветствие в кэш не кладём
        if self._greeting_active():
            self._greeting_playing = False
            self._greeting_until = 0.0
            self._greeting_interrupted = True
            if not self._regreeted:
                self._track(self._regreet_if_silent(self.asr_turns))
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
        if hasattr(self.llm, "events"):
            # ChatLLMClient (Chat Completions): те же события, но из очереди, а не из сокета
            try:
                async for event in self.llm.events():
                    await self._on_llm_event(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _log(f"LLM event loop error: {exc}\n{traceback.format_exc()}", "ERROR")
            finally:
                self.llm.is_connected = False
            return
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
            self.call_log.user_done(back_ms=int((getattr(self.llm, "vad_settings", None) or {}).get("silence_duration_ms") or 0))
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
            self._speaking_text = self.response_text
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

        if normalized == "hangup_call" and str(arguments.get("reason") or "") in HANGUP_REJECT_REASONS:
            self.call_log.add("function", f"hangup_call ({arguments.get('reason')}) отклонён: трубку из-за "
                                          f"непонимания не кладём, модель переспросит", level="warning")
            _log(f"hangup_call rejected (reason={arguments.get('reason')})", "WARNING")
            await self.llm.send_function_result(call_id, HANGUP_REJECT_RESULT)
            return

        self.function_calls += 1
        self.response_had_function_call = True
        await self.emit({"type": "function_call.executing", "function": normalized, "function_call_id": call_id,
                         "arguments": arguments, "async_execution": True})
        _log(f"function {normalized}({json.dumps(arguments, ensure_ascii=False)[:200]})")
        self.call_log.functions += 1
        self.call_log.add("function", f"Вызов функции {normalized}",
                          arguments=json.dumps(arguments, ensure_ascii=False)[:500])
        if normalized == "hangup_call":
            self._hangup_requested = True
        else:
            self._pending_function_calls.add(call_id)
            # один заполнитель на ответ модели и только если модель сама ничего не сказала перед вызовом
            if not self.response_text.strip() and self._filler_response_id != (self.response_id or call_id):
                self._filler_response_id = self.response_id or call_id
                self._track(self._filler_after_delay(call_id))

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

    async def _filler_after_delay(self, call_id: str) -> None:
        """Функция ещё выполняется через FILLER_DELAY_SEC, а в линии тишина — сказать «секунду»."""
        await asyncio.sleep(FILLER_DELAY_SEC)
        if (self.closed or call_id not in self._pending_function_calls or self._hangup_requested
                or self.assistant_speaking or self.tts.speaking or self.user_speaking):
            return
        language = (getattr(self.assistant, "language", None) or "ru")[:2].lower()
        phrase = FILLER_PHRASES.get(language, FILLER_PHRASES["ru"])
        self._filler_next_speech = True
        self._speaking_text = phrase
        self.call_log.add("assistant", f"Заполнитель, пока выполняется функция: «{phrase}»")
        await self.tts.say(phrase)
        self.tts.end_of_response()

    def _on_function_event(self, data: Dict[str, Any]) -> None:
        mtype = data.get("type")
        if mtype in ("function_call.completed", "function_call.error", "function_call.delivery_error"):
            self._pending_function_calls.discard(data.get("function_call_id"))
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
            self._track(asyncio.to_thread(asyncio.run, async_save_to_google_sheets(
                sheet_id=sheet_id, user_message=user_text, assistant_message=assistant_text,
                function_result=None, conversation_id=self.llm.conversation_record_id, context="Fish dialog",
            )))

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
                        if self._stt_resampler is not None:
                            audio = base64.b64encode(self._stt_resampler(base64.b64decode(audio))).decode("ascii")
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
        self._track(self._watch_loop_lag())
        try:
            if not self.greeting_started:  # handler_eleven запускает приветствие раньше, пока идут подключения
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
