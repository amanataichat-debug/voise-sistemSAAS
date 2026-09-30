# backend/websockets/call_log.py
"""
Журнал событий голосовой сессии — для разбора звонка после его окончания.

CallLogRecorder копит в памяти таймлайн разговора (время от начала, уровень,
тип, текст) и в конце одной записью сохраняет его в call_logs
(backend/models/call_log.py). Страница «Диалоги» показывает журнал рядом с
диалогом и отдаёт его файлом .txt (render_text).

Что попадает в журнал:
  * явные события сессии (FishVoiceSession, SIP-адаптер): реплики клиента и
    ассистента, задержка ответа, перебивания, функции, режим распознавания;
  * все WARNING/ERROR любых логгеров, записанные в контексте этой сессии
    (ошибки Scribe, OpenAI, ElevenLabs, SIP) — через _ContextHandler.

Текущий журнал живёт в contextvar CURRENT_CALL_LOG: его наследуют все задачи,
созданные внутри хендлера (клиенты провайдеров, исполнение функций). Для звонка
журнал создаёт SIP-роут (api/sip_gateway.py) до запуска хендлера и сам его
сохраняет, дописав итог звонка; для виджета — хендлер, он же и сохраняет.
"""

import asyncio
import contextvars
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from backend.core.logging import get_logger

logger = get_logger(__name__)

CURRENT_CALL_LOG: contextvars.ContextVar = contextvars.ContextVar("current_call_log", default=None)

MAX_EVENTS = 3000
MAX_TEXT = 2000
DISPLAY_TZ = timezone(timedelta(hours=6))  # Бишкек, UTC+6 (без перехода на летнее время)
LEVELS = ("debug", "info", "warning", "error")


class _ContextHandler(logging.Handler):
    """Дублирует WARNING/ERROR в журнал текущей сессии (если он есть в контексте)."""

    def emit(self, record: logging.LogRecord) -> None:
        rec = CURRENT_CALL_LOG.get()
        if rec is None or rec.closed or record.name == __name__:
            return
        try:
            message = record.getMessage()
        except Exception:
            return
        rec.add("log", message, level="error" if record.levelno >= logging.ERROR else "warning",
                source=record.name.rsplit(".", 1)[-1])


_handler = _ContextHandler(level=logging.WARNING)


def _ensure_handler() -> None:
    # setup_logging пересоздаёт обработчики корневого логгера — проверяем при каждом новом журнале
    root = logging.getLogger()
    if _handler not in root.handlers:
        root.addHandler(_handler)


class CallLogRecorder:
    def __init__(self, channel: str = "widget") -> None:
        _ensure_handler()
        self.channel = channel
        self.started_at = datetime.utcnow()
        self._t0 = time.monotonic()
        self.events: List[Dict[str, Any]] = []
        self.dropped = 0
        self.closed = False
        self.saved = False
        self.meta: Dict[str, Any] = {}
        # к чему привязать журнал (заполняет сессия / SIP-роут)
        self.session_id: Optional[str] = None
        self.assistant_type: Optional[str] = None
        self.assistant_id: Optional[str] = None
        self.sip_call_id: Optional[str] = None
        # счётчики сводки
        self.user_turns = 0
        self.assistant_turns = 0
        self.interruptions = 0
        self.functions = 0
        self.function_errors = 0
        self.latencies_ms: List[int] = []
        self._user_done_at: Optional[float] = None

    # ------------------------------------------------------------------ контекст
    def activate(self) -> contextvars.Token:
        return CURRENT_CALL_LOG.set(self)

    @staticmethod
    def current() -> Optional["CallLogRecorder"]:
        return CURRENT_CALL_LOG.get()

    def elapsed(self) -> float:
        return time.monotonic() - self._t0

    # ------------------------------------------------------------------ события
    def add(self, kind: str, text: str, level: str = "info", **data: Any) -> None:
        if self.closed:
            return
        if len(self.events) >= MAX_EVENTS:
            self.dropped += 1
            return
        event: Dict[str, Any] = {
            "t": round(self.elapsed(), 3),
            "level": level if level in LEVELS else "info",
            "kind": kind,
            "text": (text or "")[:MAX_TEXT],
        }
        data = {k: v for k, v in data.items() if v is not None}
        if data:
            event["data"] = data
        self.events.append(event)

    def user_done(self) -> None:
        """Клиент закончил фразу — от этой точки считается задержка ответа."""
        self._user_done_at = time.monotonic()

    def reply_audio_started(self) -> Optional[int]:
        """Первый звук ответа ассистента. Возвращает задержку от конца фразы клиента, мс."""
        if self._user_done_at is None:
            return None
        latency = int((time.monotonic() - self._user_done_at) * 1000)
        self._user_done_at = None
        self.latencies_ms.append(latency)
        return latency

    def since_user_done_ms(self) -> Optional[int]:
        if self._user_done_at is None:
            return None
        return int((time.monotonic() - self._user_done_at) * 1000)

    # ------------------------------------------------------------------ сводка
    def summary(self) -> Dict[str, Any]:
        lat = self.latencies_ms
        return {
            "user_turns": self.user_turns,
            "assistant_turns": self.assistant_turns,
            "interruptions": self.interruptions,
            "functions": self.functions,
            "function_errors": self.function_errors,
            "warnings": sum(1 for e in self.events if e["level"] == "warning"),
            "errors": sum(1 for e in self.events if e["level"] == "error"),
            "latency_avg_ms": int(sum(lat) / len(lat)) if lat else None,
            "latency_max_ms": max(lat) if lat else None,
            "latencies_ms": lat[:200],
            "dropped_events": self.dropped,
        }

    # ------------------------------------------------------------------ сохранение
    def _save_sync(self) -> None:
        from backend.db.session import SessionLocal
        from backend.models.base import Base, engine
        from backend.models.call_log import CallLog

        Base.metadata.create_all(engine, tables=[CallLog.__table__], checkfirst=True)
        db = SessionLocal()
        try:
            db.add(CallLog(
                session_id=self.session_id,
                assistant_type=self.assistant_type,
                assistant_id=self.assistant_id,
                channel=self.channel,
                sip_call_id=self.sip_call_id,
                started_at=self.started_at,
                duration_sec=round(self.elapsed(), 1),
                meta=self.meta,
                summary=self.summary(),
                events=self.events,
            ))
            db.commit()
        finally:
            db.close()

    async def save(self) -> bool:
        """Записать журнал в call_logs (один раз). Без session_id не пишем — не к чему привязать."""
        if self.saved or not self.session_id:
            return False
        self.saved = True
        self.closed = True
        try:
            await asyncio.to_thread(self._save_sync)
            return True
        except Exception as exc:
            logger.error(f"[CALL-LOG] save failed for session {self.session_id}: {exc}")
            return False


# ---------------------------------------------------------------------- текстовый файл
_LEVEL_LABEL = {"debug": "DEBUG", "info": "INFO ", "warning": "WARN ", "error": "ERROR"}
_CHANNEL_LABEL = {"phone": "телефон", "widget": "виджет / браузер"}


def _fmt_t(seconds: float) -> str:
    seconds = max(0.0, float(seconds or 0))
    minutes, sec = divmod(seconds, 60)
    return f"{int(minutes):02d}:{sec:06.3f}"


def render_text(row) -> str:
    """Журнал (строка CallLog) в читаемый .txt для скачивания."""
    meta = row.meta or {}
    summary = row.summary or {}
    started = row.started_at
    if started is not None and started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    lines = [
        "Лог звонка — VoksiAI",
        "=" * 60,
        f"Сессия:        {row.session_id}",
        f"Ассистент:     {meta.get('assistant_name') or '—'} ({row.assistant_type or '—'}, {row.assistant_id or '—'})",
        f"Канал:         {_CHANNEL_LABEL.get(row.channel, row.channel or '—')}",
    ]
    if meta.get("phone"):
        lines.append(f"Номер:         {meta.get('phone')} ({meta.get('direction') or '—'})")
    if row.sip_call_id:
        lines.append(f"SIP call id:   {row.sip_call_id}")
    if started is not None:
        lines.append(f"Начало:        {started.astimezone(DISPLAY_TZ):%Y-%m-%d %H:%M:%S} (Бишкек, UTC+6)")
    lines.append(f"Длительность:  {row.duration_sec or 0:.1f} с")
    for key, label in (("asr", "Распознавание"), ("llm_model", "Модель"), ("tts_model", "Синтез"),
                       ("voice_id", "Голос"), ("language", "Язык")):
        if meta.get(key):
            lines.append(f"{label + ':':<15}{meta[key]}")
    lines += [
        "",
        "Итог:",
        f"  реплик клиента: {summary.get('user_turns', 0)}, ответов ассистента: {summary.get('assistant_turns', 0)}, "
        f"перебиваний: {summary.get('interruptions', 0)}",
        f"  функций: {summary.get('functions', 0)} (ошибок: {summary.get('function_errors', 0)}), "
        f"предупреждений: {summary.get('warnings', 0)}, ошибок: {summary.get('errors', 0)}",
    ]
    if summary.get("latency_avg_ms") is not None:
        lines.append(f"  задержка ответа: средняя {summary['latency_avg_ms']} мс, "
                     f"максимальная {summary.get('latency_max_ms')} мс")
    if summary.get("dropped_events"):
        lines.append(f"  (журнал обрезан: пропущено {summary['dropped_events']} событий)")
    lines += ["", "Время от начала  Уровень  Тип            Событие", "-" * 60]
    for e in row.events or []:
        text = e.get("text") or ""
        data = e.get("data")
        if data:
            text += "  " + ", ".join(f"{k}={v}" for k, v in data.items())
        lines.append(f"[{_fmt_t(e.get('t', 0))}]  {_LEVEL_LABEL.get(e.get('level'), 'INFO ')}  "
                     f"{(e.get('kind') or ''):<14} {text}")
    return "\n".join(lines) + "\n"
