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
import base64
import json
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
from backend.websockets.live_client import audioop
from backend.websockets.live_client import CALL_DECISION_TRIGGER, LIVE_MODEL, LIVE_VOICES, OpenAILiveClient

logger = get_logger(__name__)

LIVE_AUDIO_RATE = 24000
DEFAULT_GREETING = "Здравствуйте! Чем я могу вам помочь?"
# Пауза в потоке output-аудио, после которой считаем, что ассистент договорил
# (у Live нет события «аудио закончилось»; паузы между фразами короче).
OUTPUT_IDLE_SEC = 1.0
# GPT-Live шлёт звук непрерывно, в паузах — тишиной (живой звонок 21.10.2026: «озвучено» 189 с из
# 198, «Ассистент начал говорить» один раз за звонок, задержка ответа не мерилась вовсе). Поэтому
# речью ассистента считаем только куски громче порога (RMS PCM16), паузы — тишиной.
VOICED_RMS = 200
# Приветствие ушло в session.start, но модель молчит дольше — дублируем старым путём
# (session.instructions.append): документация не гарантирует речь по session.input
GREETING_FALLBACK_SEC = 3.0

# Слова-паразиты ассистента: считаем за звонок и пишем в журнал, чтобы повторы были видны цифрой.
# Звонок 21.10.2026: «понял» ~10 раз за 4 минуты. Порог: с этого числа повторов — строка-предупреждение.
REPEAT_WORDS = {
    "понял": re.compile(r"\bпонял[аи]?\b|\bпонимаю\b", re.IGNORECASE),
    "угу": re.compile(r"\bугу\b|\bага\b", re.IGNORECASE),
    "хорошо": re.compile(r"\bхорошо\b", re.IGNORECASE),
    "ясно": re.compile(r"\bясно\b", re.IGNORECASE),
    "сейчас уточню/посмотрю": re.compile(r"\bсейчас\s+(?:уточню|посмотрю|прикин\w+|проверю)\b", re.IGNORECASE),
}
REPEAT_WARN_AT = 3

# Native Decisions (триггер CALL_DECISION_TRIGGER): реакции на намерение и настроение клиента
DECISION_TRIGGERS = bool(getattr(settings, "LIVE_DECISION_TRIGGERS", True))
AMD_HANGUP = bool(getattr(settings, "LIVE_AMD_HANGUP", False))
AMD_WINDOW_SEC = 25.0       # автоответчик ищем только в начале исходящего звонка
# Живой звонок 21.10.2026 (сессия 46bb1565): 9 подсказок о настроении за 4 минуты, «confused»
# на обычной паузе раздумья — агент начал переспрашивать «вы про каталог спросили, верно?».
# Поэтому: «доволен» не подсказываем (агент и так тёплый), «запутался» — только если Decisions
# сказал это два раза подряд, и не чаще раза в 45 с.
# не чаще одной подсказки за столько секунд; раздражение важнее — ему интервал короче
MOOD_HINT_GAP_SEC = {"irritated": 15.0, "confused": 45.0}
MOOD_CONFIRM = {"confused": 2}  # сколько решений подряд нужно, чтобы поверить настроению
# Подсказки голосовой модели (session.thinking.append — не озвучиваются, влияют на манеру)
MOOD_HINTS = {
    "irritated": "Клиент сейчас звучит раздражённо или нетерпеливо. Не оправдывайся и не спорь: "
                 "спокойно признай это одной короткой фразой, говори ещё короче и сразу предложи один "
                 "понятный следующий шаг.",
    "confused": "Клиент, похоже, не до конца понял. Объясни проще и другими словами, одной короткой "
                "фразой. Не переспрашивай, понял ли он, и не уточняй, что он имел в виду.",
}
INTENT_HINTS = {
    "call_back_later": "Клиент сейчас занят. Не продолжай рассказ: коротко уточни, когда ему удобно "
                       "поговорить, и вежливо попрощайся.",
    "wants_human": "Клиент просит живого человека. Не отказывай и не спорь: скажи, что передашь его "
                   "просьбу менеджеру, и уточни, когда ему удобно, чтобы перезвонили.",
}
INTENT_LOG = {
    "call_back_later": "Клиент занят, просит поговорить позже",
    "wants_human": "Клиент просит живого человека / менеджера",
}
# Ошибки Live, которые не надо показывать клиенту (ответы на наши служебные события)
_QUIET_ERROR_PREFIXES = ("instr_", "think_", "comm_", "mute_")
# Потолок всплеска для пейсинга входящего аудио (секунды). OpenAI допускает всплески
# до 5 с на 1.2x реального темпа — держимся ниже порога.
AUDIO_BURST_CAP = 4.0

# Автозавершение звонка (только телефон): модель сама hangup_call вызывает не всегда
# (живой звонок 17.10.2026: клиент прощается — линия висит, пока он не положит трубку).
# После прощания (клиент или ассистент) и тишины хендлер сам шлёт call.hangup, SIP-адаптер
# кладёт трубку. Клиент заговорил после прощания — отмена, разговор продолжается.
AUTO_HANGUP = bool(getattr(settings, "LIVE_AUTO_HANGUP", True))
# Сколько клиент должен молчать после прощания, чтобы положить трубку. С запасом на то,
# чтобы ассистент успел договорить своё прощание (играет в реальном темпе ~3 с).
HANGUP_SILENCE_SEC = 4.0
_FAREWELL_RE = re.compile(
    r"до\s*свидани|всего\s+доброго|всего\s+хорошего|хорошего\s+(?:дня|вечера)|до\s+встречи|до\s+связи|"
    r"прощайте|счастливо\s+оставаться|рахмат.*бол|"
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
                       duration: Optional[float], record_id: Optional[str], transport: str,
                       conversation_model: Any = Conversation) -> int:
    """
    Записать диалог (в отдельном потоке со своим циклом событий).

    Запись 28 пар подряд — это ~1 секунда на пару синхронной работы с БД. Раньше она шла
    прямо в основном цикле воркера: звонок 21.10.2026 после положенной трубки «висел» ещё
    ~27 секунд, и всё это время замирал звук у ВСЕХ остальных звонков на этом воркере.
    В ConversationService.save_conversation нет ни одного await, поэтому безопасно
    выполнять его в потоке через asyncio.run.
    """
    if not pairs:
        return 0
    return await asyncio.to_thread(
        lambda: asyncio.run(_save_dialog_impl(
            assistant_id, session_id, pairs, duration, record_id, transport, conversation_model)))


async def _save_dialog_impl(assistant_id: str, session_id: str, pairs: List[Dict[str, str]],
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
def live_media_options(assistant: Any) -> Dict[str, Any]:
    """
    Кодек и триггеры для OpenAILiveClient. G.711 μ-law 8 кГц — только если SIP-роут выбрал
    адаптеру профиль без ресемплинга (assistant.live_phone_g711, не колонка): обе стороны
    должны договориться о частоте, иначе звук пойдёт не той скоростью.
    """
    return {
        "audio_codec": "pcmu" if getattr(assistant, "live_phone_g711", False) else "pcm",
        "decision_triggers": [CALL_DECISION_TRIGGER] if DECISION_TRIGGERS else None,
    }


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
        self._user_spoke_at: Optional[float] = None
        # Автозавершение звонка: последняя речь клиента (не обнуляется), прощание — когда и чьё,
        # отправлена ли команда «положить трубку»
        self._last_user_voice_at: float = 0.0
        self._farewell_at: Optional[float] = None
        self._hangup_sent = False
        # Пейсинг входящего аудио (token bucket): GPT-Live принимает не быстрее 1.2x
        # реального темпа всплесками до ~5 с. Мост копит аудио в очередь при задержке
        # хендлера (подключение, стопор event loop) и выгружает пачкой — без ограничения
        # OpenAI роняет её (input_audio_rate_limit_exceeded). Разрешение копится по 1.2x
        # реального времени, но не больше AUDIO_BURST_CAP секунд, и тратится на каждый
        # кусок; обгон — кусок пропускаем. Предыдущая (накопительная) схема копила кредит
        # без потолка, и на длинном звонке всплеск снова пробивал лимит.
        self._audio_allowance: float = AUDIO_BURST_CAP
        self._audio_last_ts: Optional[float] = None
        # Native Decisions: текущее настроение клиента, когда давали подсказку, какие намерения уже
        # отработали (каждое — один раз за звонок), направление звонка (автоответчик — только исходящие)
        self.decisions = 0
        self.repeats: Dict[str, int] = {}
        # Данные для автоматической диагностики звонка (_log_diagnostics)
        self.function_times: List[tuple] = []     # (имя функции, секунды)
        self.voiced_bytes = 0                     # сколько реально прозвучало речи ассистента
        self.close_seconds: Optional[float] = None  # сколько заняло закрытие сессии GPT-Live
        self._mood = "neutral"
        self._mood_hint_at = 0.0
        self._mood_seen = ("neutral", 0)  # последнее настроение от Decisions и сколько раз подряд
        self._intents_done: set = set()
        self.direction = getattr(assistant, "sip_direction", None)

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
        if self.client.greeting_in_start:
            _log(f"greeting sent in session.start: {greeting[:60]}")
            if self.call_log:
                self.call_log.add("session", "Приветствие передано в session.start (без отдельного запроса)")
            self._track(self._greeting_fallback(greeting))
            return
        if await self.client.say_greeting(greeting):
            _log(f"greeting requested: {greeting[:60]}")

    async def _greeting_fallback(self, greeting: str) -> None:
        try:
            await asyncio.sleep(GREETING_FALLBACK_SEC)
        except asyncio.CancelledError:
            return
        if self._first_audio_at is not None or self._user_spoke_at is not None or self.stop_event.is_set():
            return
        _log(f"no audio {GREETING_FALLBACK_SEC:.0f}s after start, re-requesting greeting via instructions.append",
             "WARNING")
        if self.call_log:
            self.call_log.add("session", "Модель молчит после старта — приветствие запрошено повторно")
        await self.client.say_greeting(greeting)

    # ------------------------------------------------------------------ speech state
    async def _on_audio_delta(self, delta_b64: str) -> None:
        if not delta_b64:
            return
        self.audio_bytes_out += len(delta_b64) * 3 // 4
        self.audio_deltas_out += 1
        if self._first_audio_at is None:
            self._first_audio_at = time.time()
            _log(f"first audio from model {self._first_audio_at - self.started_at:.2f}s after session start")
        if not self._is_voiced(delta_b64):
            await self.emit({"type": "response.audio.delta", "delta": delta_b64})
            return
        self._last_audio_at = time.time()
        self.voiced_bytes += len(delta_b64) * 3 // 4
        if not self.assistant_speaking:
            self.assistant_speaking = True
            if self.call_log:
                if self._user_spoke_at is not None:
                    # Грубая задержка ответа: от последнего фрагмента речи клиента до первого звука
                    # (у Live нет событий «клиент договорил»; фрагменты транскрипта идут с запаздыванием)
                    ms = int((time.time() - self._user_spoke_at) * 1000)
                    self.call_log.latencies_ms.append(ms)
                    self.call_log.add("tts", f"Ассистент начал говорить через {ms} мс после речи клиента")
                    self._user_spoke_at = None
                else:
                    self.call_log.add("tts", "Ассистент начал говорить")
            await self.emit({"type": "assistant.speech.started", "response_id": self.client.session_id,
                             "timestamp": time.time()})
        await self.emit({"type": "response.audio.delta", "delta": delta_b64})

    @staticmethod
    def _is_voiced(delta_b64: str) -> bool:
        if audioop is None:
            return True  # без audioop — как раньше: любой звук = речь
        try:
            return audioop.rms(base64.b64decode(delta_b64), 2) >= VOICED_RMS
        except Exception:
            return True

    # ------------------------------------------------------------------ прощание → завершение звонка
    def _is_phone(self) -> bool:
        return bool(self.telephony or (self.call_log is not None and self.call_log.channel == "phone"))

    def _scan_farewell(self, role: str, text: str) -> None:
        if not self._is_phone() or self._hangup_sent:
            return
        if is_farewell(text):
            if self._farewell_at is None and self.call_log:
                self.call_log.add("session",
                                  f"Прощание ({'клиент' if role == 'user' else 'ассистент'}): "
                                  f"ждём тишины клиента, чтобы завершить звонок")
            self._farewell_at = time.time()

    def _should_auto_hangup(self, now: float) -> bool:
        """
        Прощание было, после него КЛИЕНТ молчит достаточно долго.

        Тишину считаем только по клиенту (_last_user_voice_at): поток аудио от
        GPT-Live непрерывный (в паузах — тишина-аудио), поэтому «ассистент молчит»
        по приходу аудио определить нельзя — именно на этом автосброс не срабатывал
        17.10.2026. Порог тишины берём с запасом, чтобы ассистент успел договорить
        своё прощание (оно играет в реальном темпе ~3 с).
        """
        if not AUTO_HANGUP or self._hangup_sent or self._farewell_at is None or not self._is_phone():
            return False
        # клиент заговорил после прощания — разговор продолжается, отменяем прощание
        if self._last_user_voice_at > self._farewell_at + 1.5:
            self._farewell_at = None
            return False
        # клиент сейчас говорит (только что был фрагмент речи) — ждём
        if now - self._last_user_voice_at < 1.0:
            return False
        quiet_since = max(self._last_user_voice_at, self._farewell_at)
        return now - quiet_since >= HANGUP_SILENCE_SEC

    async def _auto_hangup(self) -> None:
        self._hangup_sent = True
        _log("auto hangup after farewell")
        if self.call_log:
            self.call_log.add("session", "Автозавершение звонка: прощание и тишина (модель hangup_call не вызвала)")
        await self.emit({"type": "call.hangup", "reason": "farewell"})

    async def _speech_watch(self) -> None:
        """Ассистент «договорил», если аудио не приходило OUTPUT_IDLE_SEC."""
        try:
            while not self.stop_event.is_set():
                await asyncio.sleep(0.1)
                if self.assistant_speaking and time.time() - self._last_audio_at > OUTPUT_IDLE_SEC:
                    self.assistant_speaking = False
                    self._flush_log("assistant")
                    await self.emit({"type": "assistant.speech.ended", "timestamp": time.time()})
                if self._should_auto_hangup(time.time()):
                    await self._auto_hangup()
        except asyncio.CancelledError:
            pass

    def _log_transcript(self, role: str, delta: str) -> None:
        """Копим фрагменты по ролям, пишем реплику в лог при смене говорящего."""
        if self._log_role and self._log_role != role:
            self._flush_log(self._log_role)
        self._log_role = role
        self._log_buf[role] += delta
        if AUTO_HANGUP and delta.strip():
            self._scan_farewell(role, self._log_buf[role])

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
        if role == "assistant" and text:
            for name, rx in REPEAT_WORDS.items():
                found = len(rx.findall(text))
                if found:
                    self.repeats[name] = self.repeats.get(name, 0) + found
        self._log_buf[role] = ""

    def _log_diagnostics(self, elapsed: float) -> None:
        """
        Автоматический «обход» звонка: итоговая диагностика одной пачкой строк в журнале.
        Владельцу не нужно читать весь журнал, чтобы понять, был ли звонок здоровым:
        задержки ответов, монологи, медленные функции, ошибки, повторы слов, закрытие сессии.
        """
        if not self.call_log:
            return
        issues: List[str] = []
        info: List[str] = []

        lat = self.call_log.latencies_ms
        if lat:
            slow = [x for x in lat if x > 3000]
            info.append(f"ответов с замером задержки {len(lat)}: средняя {int(sum(lat) / len(lat))} мс, "
                        f"максимум {max(lat)} мс")
            if slow:
                issues.append(f"{len(slow)} из {len(lat)} ответов медленнее 3 с (максимум {max(lat)} мс)")
        else:
            info.append("задержку ответов измерить не удалось (клиент не говорил или транскрипт не пришёл)")

        if elapsed > 20 and self.voiced_bytes:
            talk = self.voiced_bytes / (self.client.io_rate * 2)
            share = talk / elapsed
            info.append(f"агент говорил {int(talk)} с из {int(elapsed)} с ({int(share * 100)}%)")
            if share > 0.6:
                issues.append(f"агент говорил {int(share * 100)}% времени — это монолог, а не диалог (норма до 55%)")

        if self.function_times:
            name, secs = max(self.function_times, key=lambda x: x[1])
            info.append(f"функций {len(self.function_times)}, самая долгая — {name} {secs:.1f} с")
            if secs >= 3.0:
                issues.append(f"функция {name} выполнялась {secs:.1f} с — клиент слышал паузу")
            same = {}
            for n, _ in self.function_times:
                same[n] = same.get(n, 0) + 1
            for n, c in same.items():
                if c >= 3:
                    issues.append(f"функция {n} вызвана {c} раза — проверьте, что ответ не лежит в промпте")

        if self.repeats and any(v >= REPEAT_WARN_AT for v in self.repeats.values()):
            top = max(self.repeats.items(), key=lambda x: x[1])
            issues.append(f"слово-паразит «{top[0]}» повторено {top[1]} раз")

        if self.errors:
            issues.append(f"ошибок GPT-Live: {self.errors}")
        if self.call_log.function_errors:
            issues.append(f"функций с ошибкой: {self.call_log.function_errors}")
        if self.close_seconds is not None and self.close_seconds > 3.0:
            issues.append(f"закрытие сессии заняло {self.close_seconds:.1f} с (итоговый usage не пришёл)")

        if issues:
            for text in issues:
                self.call_log.add("diag", f"Диагностика: {text}", level="warning")
        else:
            self.call_log.add("diag", "Диагностика: замечаний нет")
        self.call_log.add("diag", "Итого по звонку: " + "; ".join(info))

    def _log_repeat_summary(self) -> None:
        """Одна строка в журнал звонка: какие слова ассистент повторял и сколько раз."""
        if not self.call_log or not self.repeats:
            return
        parts = ", ".join(f"«{k}» {v}" for k, v in sorted(self.repeats.items(), key=lambda x: -x[1]))
        noisy = any(v >= REPEAT_WARN_AT for v in self.repeats.values())
        self.call_log.add("session", f"Повторяющиеся слова ассистента: {parts}",
                          level="warning" if noisy else "info")

    # ------------------------------------------------------------------ Live events
    async def handle_live_events(self) -> None:
        try:
            # Читаем до конца потока, даже когда звонок уже остановлен: session.closed (итоговый
            # usage) приходит ПОСЛЕ session.close, и если здесь выйти раньше, client.close() ждёт его
            # весь таймаут впустую — на звонке 21.10.2026 это были лишние 15 секунд на каждом звонке
            async for event in self.client.receive_events():
                await self._on_live_event(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log(f"live event loop error: {exc}\n{traceback.format_exc()}", "ERROR")
        finally:
            self.stop_event.set()

    async def _on_live_event(self, event: Dict[str, Any]) -> None:
        etype = event.get("type", "")

        # Звонок уже остановлен, ждём только закрытия сессии: звук и транскрипт клиенту не нужны
        if self.stop_event.is_set() and etype not in ("session.closed", "session.usage.updated", "error"):
            return

        if etype == "session.output_audio.delta":
            await self._on_audio_delta(self.client.decode_output_b64(event.get("delta") or ""))
            return

        if etype == "decision.event":
            await self._on_decision(event)
            return

        if etype in ("session.input_transcript.delta", "session.output_transcript.delta"):
            role = "user" if etype.startswith("session.input") else "assistant"
            delta = event.get("delta") or event.get("text") or ""
            self.transcript.add(role, delta, event.get("start_ms"), event.get("end_ms"))
            self._log_transcript(role, delta)
            if role == "user" and delta.strip():
                self._user_spoke_at = time.time()
                self._last_user_voice_at = time.time()
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
            if self.call_log:
                self.call_log.add("llm", f"Голосовая модель передала запрос бэкенду ({self.client.delegation_model})")
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
            await self.emit({"type": "error", "error": {
                "code": err.get("code") or "live_error",
                "message": err.get("message") or json.dumps(event, ensure_ascii=False)[:300],
            }})
            return
        # session.started / *.appended / input_audio.muted и прочее клиенту не нужны

    # ------------------------------------------------------------------ Native Decisions
    async def _on_decision(self, event: Dict[str, Any]) -> None:
        inner = event.get("event") or {}
        if inner.get("type") != "decision.completed":
            return
        answers = {a.get("name"): a.get("choice") for a in (inner.get("decision") or {}).get("answers") or []
                   if isinstance(a, dict)}
        if not answers:
            return
        self.decisions += 1
        intent = answers.get("caller_intent") or "none"
        mood = answers.get("caller_mood") or "neutral"
        now = time.time()

        if intent == "answering_machine":
            if (self._is_phone() and self.direction == "outbound" and now - self.started_at < AMD_WINDOW_SEC
                    and "answering_machine" not in self._intents_done):
                self._intents_done.add("answering_machine")
                _log(f"decisions: answering machine suspected (hangup={'on' if AMD_HANGUP else 'off'})", "WARNING")
                if self.call_log:
                    self.call_log.add("session", "Похоже, ответил автоответчик (Decisions)"
                                      + ("; кладём трубку" if AMD_HANGUP else "; трубку не кладём — режим проверки"))
                if AMD_HANGUP and not self._hangup_sent:
                    self._hangup_sent = True
                    await self.emit({"type": "call.hangup", "reason": "answering_machine"})
            return

        if intent == "goodbye":
            # Прощание на любом языке (регулярка знает не все кыргызские формы): дальше — та же
            # логика автозавершения, что и по тексту (_should_auto_hangup ждёт тишины клиента)
            if self._is_phone() and not self._hangup_sent and self._farewell_at is None:
                if self.call_log:
                    self.call_log.add("session", "Прощание клиента (Decisions): ждём тишины, чтобы завершить звонок")
                self._farewell_at = now
        elif intent in INTENT_HINTS and intent not in self._intents_done:
            self._intents_done.add(intent)
            _log(f"decisions: intent {intent}")
            if self.call_log:
                self.call_log.add("session", f"{INTENT_LOG[intent]} (Decisions)")
            await self.client.append_thinking(INTENT_HINTS[intent])

        # Подсказку, придержанную интервалом, не теряем: настроение запоминаем только когда
        # подсказка ушла (или клиент снова спокоен), следующее решение попробует ещё раз
        streak = self._mood_seen[1] + 1 if self._mood_seen[0] == mood else 1
        self._mood_seen = (mood, streak)
        if mood != self._mood:
            if mood not in MOOD_HINTS:
                self._mood = mood
            elif streak >= MOOD_CONFIRM.get(mood, 1) and now - self._mood_hint_at >= MOOD_HINT_GAP_SEC.get(mood, 45.0):
                self._mood = mood
                self._mood_hint_at = now
                _log(f"decisions: caller mood {mood}")
                if self.call_log:
                    self.call_log.add("session", f"Настроение клиента: {mood} (Decisions) — подсказка голосовой модели")
                await self.client.append_thinking(MOOD_HINTS[mood])

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
            if isinstance(secs, (int, float)):
                self.function_times.append((name or "?", float(secs)))
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
                    # Темп входящего аудио держим ≤1.2x реального (лимит GPT-Live) всплесками
                    # до AUDIO_BURST_CAP. Устаревшее аудио из очереди (накопилось при задержке
                    # хендлера) обгоняет темп — такой кусок пропускаем, иначе OpenAI роняет всю
                    # пачку (input_audio_rate_limit_exceeded) и разговор рвётся.
                    dur = (len(audio) * 3 / 4) / 2 / self.client.io_rate
                    now = time.time()
                    if self._audio_last_ts is not None:
                        self._audio_allowance = min(
                            AUDIO_BURST_CAP, self._audio_allowance + (now - self._audio_last_ts) * 1.2)
                    self._audio_last_ts = now
                    if dur > self._audio_allowance:
                        continue
                    if await self.client.send_audio_b64(audio):
                        self._audio_allowance -= dur
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
            self._log_repeat_summary()
            # 1. Закрыть сессию у OpenAI (дождаться session.closed с итоговым usage)
            closed = None
            close_started = time.time()
            try:
                closed = await self.client.close()
            except Exception as exc:
                _log(f"client.close error: {exc}", "WARNING")
            self.close_seconds = time.time() - close_started
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
                f"audio_out={self.audio_bytes_out / (self.client.io_rate * 2):.1f}s in {self.audio_deltas_out} deltas "
                f"(first after {'-' if self._first_audio_at is None else f'{self._first_audio_at - self.started_at:.2f}s'}), "
                f"delegations={self.delegations}, "
                f"functions={self.function_calls}, errors={self.errors}, dialog_pairs={len(pairs)} saved={saved}"
            )
            if self.call_log:
                self.call_log.add(
                    "session",
                    f"Сессия завершена ({reason}): {elapsed:.1f} с, GPT-Live {usage_seconds if usage_seconds is not None else '—'} с, "
                    f"озвучено {self.audio_bytes_out / (self.client.io_rate * 2):.1f} с, обращений к бэкенду {self.delegations}, "
                    f"реплик в диалоге {len(pairs)}",
                )
                self._log_diagnostics(elapsed)
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
                                  voice_override=voice_override, telephony=telephony,
                                  greeting=assistant.greeting_message or DEFAULT_GREETING,
                                  **live_media_options(assistant))
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
            "audio_rate": client.io_rate,
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
