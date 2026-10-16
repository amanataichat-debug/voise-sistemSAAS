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

Тот же LiveVoiceSession обслуживает карточки ElevenLabs с движком GPT-Live
(voice_engine="gpt_live", вход — handler_eleven.run_gpt_live_session): серверный
ключ, диалоги в eleven_conversations, журнал звонка (call_logs) как у Eleven.
"""

import asyncio
import audioop
import base64
import json
import math
import re
import time
import traceback
import uuid
from typing import Any, Dict, List, Optional

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from websockets.exceptions import ConnectionClosed

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.functions import normalize_function_name
from backend.models.assistant import AssistantConfig
from backend.models.conversation import Conversation
from backend.models.user import User
from backend.services.conversation_service import ConversationService
from backend.websockets.call_log import CallLogRecorder
from backend.websockets.handler_fish import _EventTap
from backend.websockets.function_calls import (
    async_save_to_google_sheets,
    execute_and_send_function_result,
)
from backend.websockets.live_client import LIVE_MODEL, LIVE_VOICES, OpenAILiveClient
from backend.websockets.openai_stt_client import _EnergyVAD

logger = get_logger(__name__)

LIVE_AUDIO_RATE = 24000
DEFAULT_GREETING = "Здравствуйте! Чем я могу вам помочь?"
# Пауза в потоке output-аудио, после которой считаем, что ассистент договорил
# (у Live нет события «аудио закончилось»; паузы между фразами короче).
OUTPUT_IDLE_SEC = 1.0
# Тишина во входящем аудио, после которой считаем, что клиент договорил (для замера задержки)
END_OF_SPEECH_SEC = 0.5
# Модель заговорила, а клиент молчит уже не меньше этого — реплика закончена, ответ быстрый
MIN_QUIET_SEC = 0.15
# Задержка длиннее — это не ответ на реплику (клиент ушёл, пауза в разговоре): в замер не берём
MAX_REPLY_LATENCY_SEC = 30.0
# Ответ медленнее этого порога пишем в журнал как предупреждение
SLOW_REPLY_MS = 3000
# Бэкенд делегирования: через сколько тишины говорим «проверяю», и когда сдаёмся (см. _delegation_watch)
FILLER_DELAY_SEC = float(getattr(settings, "LIVE_FILLER_DELAY_SEC", 0.9) or 0.9)
FILLER_REPEAT_SEC = 4.0
# Слежение за бэкендом (заполнитель, «не получилось») выключено по умолчанию: LIVE_DELEGATION_WATCH=1
DELEGATION_WATCH = bool(getattr(settings, "LIVE_DELEGATION_WATCH", False))
# Автозавершение звонка (только телефон): прощание + тишина. Сама модель hangup_call вызывает не всегда
# (живой звонок 17.10.2026: «Пока.» — и линия висела, пока не положил клиент).
_AUTO_HANGUP_RAW = str(getattr(settings, "LIVE_AUTO_HANGUP", "judge") or "judge").strip().lower()
AUTO_HANGUP_MODE = "off" if _AUTO_HANGUP_RAW in ("0", "off", "false", "no") else (
    "keywords" if _AUTO_HANGUP_RAW == "keywords" else "judge")
AUTO_HANGUP = AUTO_HANGUP_MODE != "off"
JUDGE_MODEL = str(getattr(settings, "LIVE_HANGUP_JUDGE_MODEL", "gpt-5.6-luna") or "gpt-5.6-luna")
JUDGE_SILENCE_SEC = float(getattr(settings, "LIVE_HANGUP_SILENCE_SEC", 3.0) or 3.0)
JUDGE_MIN_CALL_SEC = 10.0   # в первые секунды разговор «закончиться» не может
JUDGE_MAX_CALLS = 12        # потолок обращений к модели за звонок
JUDGE_BROKEN_AFTER = 2      # столько ошибок подряд — переходим на запасной путь по словам
HANGUP_SILENCE_USER_FAREWELL_SEC = 2.5       # клиент попрощался, ассистент ответил, обе стороны молчат
HANGUP_SILENCE_ASSISTANT_FAREWELL_SEC = 5.0  # попрощался только ассистент
HANGUP_WAIT_ASSISTANT_REPLY_SEC = 8.0        # клиент попрощался, а ассистент так и не ответил
_FAREWELL_RE = re.compile(
    r"до\s*свидани|всего\s+доброго|всего\s+хорошего|хорошего\s+(?:дня|вечера)|до\s+встречи|до\s+связи|"
    r"прощайте|счастливо\s+оставаться|"
    r"кош\s*бол|сау\s*бол|жакшы\s*кал|жакшы\s*бар|кайра\s*көрүш|күнүңүз\s+жакшы|"
    r"\bgood\s?bye\b|\bbye\b", re.IGNORECASE)
# «пока» — прощание только в конце короткой реплики («давай, пока»), а не «пока не знаю»
_PAKA_RE = re.compile(r"(?:^|[\s,.!?])пока[\s.!?…]*$", re.IGNORECASE)


def is_farewell(text: str) -> bool:
    t = " ".join((text or "").split())
    if not t:
        return False
    if _FAREWELL_RE.search(t):
        return True
    return len(t.split()) <= 5 and bool(_PAKA_RE.search(t))


# GPT-Live шлёт звук непрерывно (в паузах — тишина), поэтому «ассистент заговорил» ищем по уровню звука
OUT_VOICE_DB = -48.0
OUT_QUIET_SEC = 0.35
OUT_MIN_VOICE_SEC = 0.06
DELEGATION_TIMEOUT_SEC = float(getattr(settings, "LIVE_DELEGATION_TIMEOUT_SEC", 10) or 0)
FILLER_TEXTS = (
    "Бэкенд ещё работает. Одной короткой фразой, на языке собеседника, скажи, что проверяешь и нужна "
    "секунда. Результат не обещай и причин не называй.",
    "Проверка ещё идёт. Коротко, на языке собеседника, скажи, что ещё уточняешь, и поблагодари за ожидание.",
)
BACKEND_FAILED_TEXT = (
    "Бэкенд не ответил или сообщил об ошибке, результата нет. Сейчас же коротко, на языке собеседника, "
    "честно скажи, что уточнить это не получилось, извинись и предложи перезвонить позже или написать "
    "в мессенджер. Не говори, что проверяешь, и не выдумывай ответ."
)
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


class EndOfSpeechTracker:
    """
    Конец реплики клиента по входящему аудио. У GPT-Live нет событий VAD, а фрагменты
    транскрипта приходят с запаздыванием (уже во время ответа), поэтому «клиент договорил»
    ищем сами: энергетический детектор речи на потоке микрофона. Конец речи — момент
    последнего «громкого» кадра, как только после него прошло END_OF_SPEECH_SEC тишины.
    """

    def __init__(self, rate: int = LIVE_AUDIO_RATE, silence_sec: float = END_OF_SPEECH_SEC) -> None:
        self._vad = _EnergyVAD(rate)
        self._silence_sec = silence_sec

    def feed(self, pcm: bytes, now: float) -> Optional[float]:
        """Кадр с микрофона. Вернуть время реального конца речи, если реплика только что закончилась."""
        self._vad.feed(pcm, now)
        return self.poll(now)

    def poll(self, now: float) -> Optional[float]:
        """Проверка по таймеру: кадры могли перестать приходить, пока клиент молчит."""
        if self._vad.in_speech and now - self._vad.last_voice_at >= self._silence_sec:
            self._vad.in_speech = False
            self._vad.loud_sec = 0.0
            return self._vad.last_voice_at
        return None

    @property
    def last_voice_at(self) -> float:
        return self._vad.last_voice_at

    @property
    def in_speech(self) -> bool:
        return self._vad.in_speech

    def take_pending_end(self, now: float) -> Optional[float]:
        """
        Модель начала отвечать раньше, чем прошла пауза END_OF_SPEECH_SEC (быстрый ответ — лучший случай).
        Если клиент уже замолчал (последний громкий кадр не свежее MIN_QUIET_SEC), забрать конец
        его речи; если ещё говорит — это перебивание, замер не нужен.
        """
        if self._vad.in_speech and now - self._vad.last_voice_at >= MIN_QUIET_SEC:
            self._vad.in_speech = False
            self._vad.loud_sec = 0.0
            return self._vad.last_voice_at
        return None


class OutputSpeechDetector:
    """
    Речь ассистента в потоке выходного аудио. Поток идёт непрерывно, и приход данных ничего не
    говорит о том, говорит ли модель: смотрим на громкость. Начало речи — OUT_MIN_VOICE_SEC громкого
    звука подряд, конец — OUT_QUIET_SEC тишины (по длительности звука, а не по часам).
    """

    def __init__(self, rate: int = LIVE_AUDIO_RATE) -> None:
        self.rate = rate
        self.voiced = False
        self._loud = 0.0
        self._quiet = 0.0

    def feed(self, pcm: bytes) -> Optional[str]:
        n = len(pcm) // 2
        if n == 0:
            return None
        rms = audioop.rms(pcm[:n * 2], 2)
        level_db = 20 * math.log10(max(rms, 1.0) / 32768.0)
        dur = n / self.rate
        if level_db > OUT_VOICE_DB:
            self._loud += dur
            self._quiet = 0.0
            if not self.voiced and self._loud >= OUT_MIN_VOICE_SEC:
                self.voiced = True
                return "start"
        else:
            self._loud = 0.0
            if self.voiced:
                self._quiet += dur
                if self._quiet >= OUT_QUIET_SEC:
                    self.voiced = False
                    self._quiet = 0.0
                    return "end"
        return None


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
                       duration: Optional[float], record_id: Optional[str], transport: str,
                       conversation_model: Any = Conversation) -> int:
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
                conv = db.get(conversation_model, uuid.UUID(record_id))
            except Exception:
                conv = None
            if conv is not None and not (conv.user_message or conv.assistant_message):
                conv.user_message = pairs[0]["user"] or ""
                conv.assistant_message = pairs[0]["assistant"] or ""
                if hasattr(conv, "audio_duration"):
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

    def __init__(self, websocket: WebSocket, assistant: Any, client: OpenAILiveClient,
                 db: Optional[Session], client_id: str, telephony: bool = False,
                 assistant_type: str = "openai", call_log: Optional[CallLogRecorder] = None,
                 default_greeting: str = DEFAULT_GREETING) -> None:
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
        self._last_voice_at = 0.0  # последний момент, когда в выходном потоке была речь, а не тишина
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
        self.assistant_type = assistant_type
        self.default_greeting = default_greeting
        # Журнал звонка (страница «Диалоги»): у карточек ElevenLabs на GPT-Live. Виджет сохраняет
        # журнал сам в конце сессии, у телефонного звонка его сохраняет SIP-роут.
        self.call_log = call_log
        self._owns_call_log = bool(call_log) and call_log.channel != "phone"
        self.on_session_end = None  # конец разговора (кошелёк), до сохранения журнала
        # Замер задержки: конец речи клиента (по входящему аудио) → первый звук ответа
        self._eos = EndOfSpeechTracker()
        self._out_det = OutputSpeechDetector()
        self._user_done_at: Optional[float] = None
        self._reply_with_function = False
        # Обращение к бэкенду: пока оно идёт, следим за тишиной (заполнитель / «не получилось»)
        self._delegation_since: Optional[float] = None
        self._delegation_gen = 0
        self._round_has_calls: Dict[str, bool] = {}
        # Прощание: когда прозвучало, кто попрощался, отправлена ли команда «положить трубку»
        self._farewell_at: Optional[float] = None
        self._farewell_user = False
        self._hangup_sent = False
        # Судья «разговор закончен»: идёт ли запрос, сколько их было, на каком отрезке тишины уже спрашивали
        self._judge_running = False
        self._judge_calls = 0
        self._judge_errors = 0
        self._judged_voice_mark = -1.0
        self._judged_sig = -1
        self._judged_at = 0.0

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
        greeting = (getattr(self.assistant, "greeting_message", None) or self.default_greeting).strip()
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
        try:
            if self._out_det.feed(base64.b64decode(delta_b64)) == "start":
                self._record_reply_latency()
            if self._out_det.voiced:
                self._last_voice_at = time.time()
        except Exception:
            pass
        if not self.assistant_speaking:
            self.assistant_speaking = True
            await self.emit({"type": "assistant.speech.started", "response_id": self.client.session_id,
                             "timestamp": time.time()})
        await self.emit({"type": "response.audio.delta", "delta": delta_b64})

    def _track_input_audio(self, audio_b64: str) -> None:
        try:
            ended = self._eos.feed(base64.b64decode(audio_b64), time.time())
        except Exception:
            return
        if ended is not None:
            self._on_user_speech_end(ended)

    def _on_user_speech_end(self, ended_at: float) -> None:
        """Клиент договорил. Если ассистент в этот момент говорит — это перебивание/поддакивание, не вопрос."""
        if self._out_det.voiced:
            return
        self._user_done_at = ended_at
        self._reply_with_function = False

    def _record_reply_latency(self) -> None:
        """Ассистент начал говорить: записать задержку от конца речи клиента, если ждали ответа."""
        done_at, self._user_done_at = self._user_done_at, None
        if done_at is None:
            done_at = self._eos.take_pending_end(time.time())
        if done_at is None:
            if self.call_log:
                self.call_log.add("tts", "Ассистент начал говорить")
            return
        elapsed = time.time() - done_at
        with_function = self._reply_with_function
        self._reply_with_function = False
        if elapsed > MAX_REPLY_LATENCY_SEC or not self.call_log:
            return
        ms = int(elapsed * 1000)
        self.call_log.add_latency(ms, with_function)
        self.call_log.add(
            "latency",
            f"Первый звук ответа через {ms} мс после конца речи клиента"
            + (" (после похода за функцией)" if with_function else " (без функции)"),
            level="warning" if ms >= SLOW_REPLY_MS else "info", ms=ms, with_function=with_function,
        )

    # ------------------------------------------------------------------ бэкенд делегирования
    def _delegation_started(self) -> None:
        """Голосовая модель передала запрос бэкенду: следим, чтобы клиент не слушал тишину."""
        if not DELEGATION_WATCH:
            return
        self._delegation_since = time.time()
        self._delegation_gen += 1
        self._track(self._delegation_watch(self._delegation_gen))

    def _delegation_finished(self) -> None:
        self._delegation_since = None

    async def _delegation_watch(self, gen: int) -> None:
        """
        Пока бэкенд работает, а звука нет дольше FILLER_DELAY_SEC — просим голосовую модель сказать
        «проверяю» (session.commentary.append, delegation_id=null: у Responses-делегирования другого
        не принимают). Бэкенд молчит дольше DELEGATION_TIMEOUT_SEC — просим честно сказать, что не вышло.
        """
        fillers = 0
        last_filler_at = 0.0
        try:
            while not self.stop_event.is_set() and self._delegation_gen == gen and self._delegation_since is not None:
                await asyncio.sleep(0.2)
                if self._delegation_gen != gen or self._delegation_since is None:
                    return
                now = time.time()
                silent_for = now - max(self._last_voice_at, self._delegation_since)
                if self._out_det.voiced:
                    continue
                waited = now - self._delegation_since
                if DELEGATION_TIMEOUT_SEC and waited >= DELEGATION_TIMEOUT_SEC and silent_for >= FILLER_DELAY_SEC:
                    await self._backend_failed(f"бэкенд не ответил за {waited:.1f} с")
                    return
                if (fillers < len(FILLER_TEXTS) and silent_for >= FILLER_DELAY_SEC
                        and now - last_filler_at >= FILLER_REPEAT_SEC):
                    if await self.client.append_commentary(FILLER_TEXTS[fillers]):
                        fillers += 1
                        last_filler_at = now
                        _log(f"filler #{fillers}: backend silent {silent_for:.1f}s (waited {waited:.1f}s)")
                        if self.call_log:
                            self.call_log.add("llm", f"Заполнитель #{fillers}: бэкенд работает {waited:.1f} с, "
                                                     f"звука нет {silent_for:.1f} с")
        except asyncio.CancelledError:
            pass

    async def _backend_failed(self, reason: str) -> None:
        """Бэкенд упал или молчит: голосовая модель сама об этом не узнает и будет тянуть «проверяю»."""
        if self._delegation_since is None:
            return
        self._delegation_finished()
        _log(f"backend failed: {reason}", "WARNING")
        if self.call_log:
            self.call_log.add("llm", f"Бэкенд недоступен: {reason}; агент сообщает клиенту", level="error")
        await self.client.append_instructions(BACKEND_FAILED_TEXT)

    # ------------------------------------------------------------------ прощание → завершение звонка
    def _is_phone(self) -> bool:
        return bool(self.telephony or (self.call_log is not None and self.call_log.channel == "phone"))

    def _scan_farewell(self, role: str) -> None:
        if not self._is_phone() or self._hangup_sent:
            return
        if is_farewell(self._log_buf.get(role, "")):
            now = time.time()
            if (self._farewell_at is None or now - self._farewell_at > 1.0) and AUTO_HANGUP_MODE == "keywords":
                if self.call_log:
                    self.call_log.add("session", f"Прощание ({'клиент' if role == 'user' else 'ассистент'}): ждём тишины, чтобы завершить звонок")
            self._farewell_at = now
            self._farewell_user = self._farewell_user or role == "user"

    def _should_auto_hangup(self, now: float) -> bool:
        """Прощание было, после него обе стороны молчат достаточно долго."""
        keywords_active = AUTO_HANGUP_MODE == "keywords" or self._judge_errors >= JUDGE_BROKEN_AFTER
        if not AUTO_HANGUP or not keywords_active or self._hangup_sent or self._farewell_at is None or not self._is_phone():
            return False
        user_voice = self._eos.last_voice_at
        if user_voice > self._farewell_at + 2.0:
            # клиент заговорил после прощания — разговор продолжается
            self._farewell_at = None
            self._farewell_user = False
            return False
        if self._out_det.voiced or self._eos.in_speech:
            return False
        assistant_replied = self._last_voice_at > self._farewell_at
        if self._farewell_user and not assistant_replied:
            return now - self._farewell_at >= HANGUP_WAIT_ASSISTANT_REPLY_SEC
        quiet_since = max(user_voice, self._last_voice_at, self._farewell_at)
        need = HANGUP_SILENCE_USER_FAREWELL_SEC if self._farewell_user else HANGUP_SILENCE_ASSISTANT_FAREWELL_SEC
        return now - quiet_since >= need

    def _maybe_judge(self, now: float) -> None:
        """Тишина после реплик: просим быструю модель оценить, закончен ли разговор (один запрос на отрезок тишины)."""
        if (AUTO_HANGUP_MODE != "judge" or self._hangup_sent or self._judge_running or not self._is_phone()
                or self._judge_calls >= JUDGE_MAX_CALLS or self._judge_errors >= JUDGE_BROKEN_AFTER):
            return
        if self._out_det.voiced or self._eos.in_speech or now - self.started_at < JUDGE_MIN_CALL_SEC:
            return
        last_voice = max(self._eos.last_voice_at, self._last_voice_at)
        if last_voice <= 0 or now - last_voice < JUDGE_SILENCE_SEC:
            return
        turns = self.transcript.turns()
        if len(turns) < 2:
            return
        # Тот же отрезок тишины второй раз спрашиваем, только если в транскрипте появился новый текст
        # (реплики клиента приходят с запаздыванием в 1–2 с) или прошло ещё 10 с тишины
        sig = sum(len(t.get("text") or "") for t in turns)
        if (last_voice == self._judged_voice_mark and sig == self._judged_sig
                and now - self._judged_at < 10.0):
            return
        self._judged_voice_mark = last_voice
        self._judged_sig = sig
        self._judged_at = now
        self._judge_running = True
        self._judge_calls += 1
        self._track(self._run_judge(turns, last_voice))

    async def _run_judge(self, turns, last_voice: float) -> None:
        from backend.websockets.call_end_judge import call_is_finished

        try:
            verdict = await call_is_finished(self.client.api_key, turns, JUDGE_MODEL)
            if verdict is None:
                self._judge_errors += 1
                if self.call_log:
                    self.call_log.add("session", "Проверка «разговор закончен» не удалась"
                                      + ("; дальше по словам прощания" if self._judge_errors >= JUDGE_BROKEN_AFTER else ""),
                                      level="warning")
                return
            self._judge_errors = 0
            if self.call_log:
                self.call_log.add("session", f"Проверка «разговор закончен»: {'да' if verdict else 'нет'}", verdict=verdict)
            # за время запроса кто-то мог заговорить — тогда звонок продолжается
            still_silent = (not self._out_det.voiced and not self._eos.in_speech
                            and max(self._eos.last_voice_at, self._last_voice_at) == last_voice)
            if verdict and still_silent and not self._hangup_sent:
                await self._auto_hangup()
        finally:
            self._judge_running = False

    async def _auto_hangup(self) -> None:
        self._hangup_sent = True
        _log("auto hangup after farewell")
        if self.call_log:
            self.call_log.add("session", "Автозавершение звонка: разговор закончен и тишина (модель hangup_call не вызвала)")
        await self.emit({"type": "call.hangup", "reason": "farewell"})

    async def _speech_watch(self) -> None:
        """Ассистент «договорил», если аудио не приходило OUTPUT_IDLE_SEC."""
        try:
            while not self.stop_event.is_set():
                await asyncio.sleep(0.1)
                ended = self._eos.poll(time.time())
                if ended is not None:
                    self._on_user_speech_end(ended)
                if self._should_auto_hangup(time.time()):
                    await self._auto_hangup()
                else:
                    self._maybe_judge(time.time())
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
        if AUTO_HANGUP and delta.strip():
            self._scan_farewell(role)

    def _flush_log(self, role: str) -> None:
        text = " ".join(self._log_buf.get(role, "").split())
        if text:
            _log(f"{role}: {text[:300]}")
            if self.call_log:
                if role == "user":
                    self.call_log.user_turns += 1
                    self.call_log.add("user", f"Клиент: «{text}»")
                else:
                    self.call_log.assistant_turns += 1
                    self.call_log.add("assistant", f"Ассистент: «{text}»")
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
            self._round_has_calls[event.get("delegation_id") or ""] = True
            await self._on_function_call(event)
            return

        if etype == "session.delegation.created":
            self.delegations += 1
            if self._user_done_at is not None:
                self._reply_with_function = True
            self._delegation_started()
            d = event.get("delegation") or {}
            _log(f"delegation #{self.delegations} created: id={d.get('id')} target={d.get('target')}")
            if self.call_log:
                self.call_log.add("llm", f"Голосовая модель передала запрос бэкенду ({self.client.delegation_model})")
            return

        if etype == "response.event":
            inner = event.get("event") or {}
            itype = inner.get("type")
            if itype == "response.output_text.delta":
                self.backend_text += inner.get("delta") or ""
            elif itype in ("response.completed", "response.done"):
                if self.backend_text:
                    _log(f"backend: {self.backend_text[:200]}")
                    self.backend_text = ""
                # Ответ бэкенда с вызовами функций — промежуточный (после результатов будет ещё один),
                # без вызовов — финальный: дальше голосовая модель озвучивает результат
                did = event.get("delegation_id") or ""
                if self._round_has_calls.pop(did, False):
                    pass
                else:
                    self._delegation_finished()
            elif itype in ("response.failed", "response.incomplete"):
                err = inner.get("response", {}).get("error") if isinstance(inner.get("response"), dict) else None
                await self._backend_failed(f"{itype}: {json.dumps(err, ensure_ascii=False)[:200] if err else '—'}")
            return

        if etype == "session.usage.updated":
            usage = event.get("usage") or {}
            ctx = event.get("context_window") or {}
            await self.emit({"type": "usage", "seconds": usage.get("seconds"),
                             "context_usage_ratio": ctx.get("usage_ratio")})
            return

        if etype == "session.closed":
            _log(f"session.closed reason={event.get('reason')} usage={event.get('usage')}")
            if self.call_log:
                self.call_log.add("session", f"GPT-Live закрыл сессию: {event.get('reason') or '—'}")
            self.stop_event.set()
            return

        if etype == "error":
            self.errors += 1
            err = event.get("error") or {}
            client_event_id = str(err.get("client_event_id") or event.get("client_event_id") or "")
            if client_event_id.startswith(_QUIET_ERROR_PREFIXES):
                return  # уже в логе клиента; пользователю показывать нечего
            if self.call_log:
                self.call_log.add("error", f"Ошибка GPT-Live: {err.get('code') or ''} {err.get('message') or ''}".strip(),
                                  level="error")
            if self._delegation_since is not None:
                await self._backend_failed(f"ошибка {err.get('code') or ''}: {(err.get('message') or '')[:120]}")
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
        if self._user_done_at is not None:
            self._reply_with_function = True
        if self.call_log:
            self.call_log.functions += 1
            self.call_log.add("function", f"Вызов функции {normalized}",
                              arguments=json.dumps(arguments, ensure_ascii=False, default=str)[:500])
        await self.emit({"type": "function_call.executing", "function": normalized, "function_call_id": call_id,
                         "arguments": arguments, "async_execution": True})

        user_text = ""
        for t in reversed(self.transcript.turns()):
            if t["role"] == "user":
                user_text = t["text"]
                break

        self._track(execute_and_send_function_result(
            openai_client=self.client,
            websocket=_EventTap(self.ws, self._on_function_event) if self.call_log else self.ws,
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

    def _on_function_event(self, data: Dict[str, Any]) -> None:
        """Результат функции → журнал звонка (те же записи, что у Eleven)."""
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
                    self._track_input_audio(audio)
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
            if self.on_session_end is not None:
                try:
                    await self.on_session_end()
                except Exception as exc:
                    _log(f"on_session_end failed: {exc}", "ERROR")
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
                saved = await _save_dialog(str(self.assistant.id), self.client.dialog_session_id or self.client_id, pairs,
                                           usage_seconds if usage_seconds is not None else elapsed,
                                           self.client.conversation_record_id, LIVE_MODEL,
                                           conversation_model=self.client.conversation_model)
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
            if self.call_log:
                self.call_log.add(
                    "session",
                    f"Сессия завершена ({reason}): {elapsed:.1f} с, GPT-Live {usage_seconds if usage_seconds is not None else '—'} с, "
                    f"озвучено {self.audio_bytes_out / (LIVE_AUDIO_RATE * 2):.1f} с, обращений к бэкенду {self.delegations}, "
                    f"реплик в диалоге {len(pairs)}",
                )
                if self._owns_call_log:
                    await self.call_log.save()
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
                        session_id=self.client.dialog_session_id or self.client_id,
                        assistant_id=str(self.assistant.id),
                        assistant_name=self.assistant.name,
                        assistant_type=self.assistant_type,
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
