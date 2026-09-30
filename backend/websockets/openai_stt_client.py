# backend/websockets/openai_stt_client.py
"""
Потоковое распознавание речи абонента через OpenAI gpt-live-transcribe
(Realtime API, сессия транскрипции). Альтернатива ScribeSTTClient с тем же
интерфейсом: connect / send_audio / close, колбэки on_partial / on_committed /
on_closed, поля sample_rate, silence_ms, language, secondary_languages.

    wss://api.openai.com/v1/realtime?intent=transcription
    → session.update {type: "transcription", audio.input: {format pcm 24 кГц,
       transcription {model, languages, prompt, keywords, delay}, turn_detection: null}}
    → input_audio_buffer.append … input_audio_buffer.commit (конец фразы)
    ← conversation.item.input_audio_transcription.delta (текст по ходу речи)
    ← conversation.item.input_audio_transcription.completed (итог фразы)

У gpt-live-transcribe нет серверного VAD («turn_detection must be null»): конец
фразы определяет клиент. Здесь это энергетический детектор с подстройкой под шум
линии (_EnergyVAD): речь началась — копим; тишина silence_ms после речи — commit.
Что именно сказано (и не эхо ли это ассистента), дальше решает сессия по тексту.
"""

import asyncio
import audioop
import base64
import json
import math
import time
from typing import Awaitable, Callable, Dict, List, Optional

import websockets
from websockets.exceptions import ConnectionClosed

from backend.core.logging import get_logger

logger = get_logger(__name__)

TRANSCRIBE_URL = "wss://api.openai.com/v1/realtime?intent=transcription"
DEFAULT_MODEL = "gpt-live-transcribe"
INPUT_RATE = 24000
CONNECT_TIMEOUT_SEC = 8
MAX_TURN_SEC = 20.0            # очень длинная реплика — закрываем принудительно
WATCH_TICK_SEC = 0.05          # проверка «клиент замолчал», когда звук перестал приходить
FATAL_CODES = {"invalid_api_key", "insufficient_quota", "model_not_found"}

TextCallback = Callable[[str], Awaitable[None]]

TRANSCRIBE_PROMPT = ("Телефонный разговор клиента с голосовым ассистентом компании VoksyAI. "
                     "Клиент говорит по-кыргызски или по-русски, иногда смешивает языки.")


class _EnergyVAD:
    """Простой детектор речи по уровню сигнала с плавающим уровнем шума."""

    MIN_SPEECH_SEC = 0.12      # столько «громких» кадров подряд — начало речи
    SPEECH_MARGIN_DB = 10.0    # на столько громче шума — речь
    ABS_FLOOR_DB = -50.0       # тише — не речь при любом шуме

    def __init__(self, rate: int) -> None:
        self.rate = rate
        self.noise_db = -60.0
        self.in_speech = False
        self.loud_sec = 0.0
        self.last_voice_at = 0.0

    def feed(self, pcm: bytes, now: float) -> Optional[str]:
        """Вернуть 'start' при начале речи, иначе None (конец речи — по таймеру снаружи)."""
        n = len(pcm) // 2
        if n == 0:
            return None
        rms = audioop.rms(pcm[:n * 2], 2)
        level_db = 20 * math.log10(max(rms, 1.0) / 32768.0)
        dur = n / self.rate
        voiced = level_db > max(self.noise_db + self.SPEECH_MARGIN_DB, self.ABS_FLOOR_DB)
        if voiced:
            self.loud_sec += dur
            self.last_voice_at = now
        else:
            self.loud_sec = 0.0
            if not self.in_speech:
                # шум линии подстраиваем только в тишине: быстро вниз, медленно вверх
                k = 0.3 if level_db < self.noise_db else 0.03
                self.noise_db += (level_db - self.noise_db) * k
        if not self.in_speech and self.loud_sec >= self.MIN_SPEECH_SEC:
            self.in_speech = True
            return "start"
        return None


class OpenAISTTClient:
    provider = "openai"

    def __init__(
        self,
        api_key: str,
        language: str,
        sample_rate: int = INPUT_RATE,
        silence_ms: int = 500,
        on_partial: Optional[TextCallback] = None,
        on_committed: Optional[TextCallback] = None,
        on_closed: Optional[Callable[[bool], Awaitable[None]]] = None,
        label: str = "",
        secondary_languages: Optional[List[str]] = None,
        model: str = DEFAULT_MODEL,
        delay: str = "low",
    ) -> None:
        self.api_key = api_key
        self.language = (language or "").strip().lower()
        self.secondary_languages = [l for l in (secondary_languages or []) if l and l != self.language]
        self.sample_rate = INPUT_RATE  # документирован только pcm 24 кГц
        self.silence_ms = max(200, int(silence_ms))
        self.on_partial = on_partial
        self.on_committed = on_committed
        self.on_closed = on_closed
        self.label = label
        self.model = model or DEFAULT_MODEL
        self.delay = delay

        self.ws = None
        self.is_connected = False
        self.fatal_error: Optional[str] = None
        self.audio_seconds = 0.0
        self.commits = 0
        self.session_id: Optional[str] = None
        self._closing = False
        self._reader_task: Optional[asyncio.Task] = None
        self._watch_task: Optional[asyncio.Task] = None
        self._send_lock = asyncio.Lock()
        self._vad = _EnergyVAD(self.sample_rate)
        self._turn_started_at = 0.0
        self._pending_since: Dict[str, float] = {}   # item_id → время commit (для замера)
        self._commit_times: List[float] = []
        self._partials: Dict[str, str] = {}
        self.transcribe_ms: List[int] = []
        self.accepted: Dict[str, object] = {}

    @property
    def provider_label(self) -> str:
        langs = "+".join([self.language] + self.secondary_languages) or "auto"
        return f"OpenAI {self.model} ({langs}, 24 кГц, пауза {self.silence_ms} мс, delay={self.delay})"

    def _log(self, message: str, level: str = "INFO") -> None:
        text = f"[OPENAI-STT {self.label}] {message}"
        getattr(logger, {"ERROR": "error", "WARNING": "warning"}.get(level, "info"))(text)

    def _optional_fields(self) -> Dict[str, object]:
        fields: Dict[str, object] = {}
        langs = [l for l in [self.language] + self.secondary_languages if l]
        if langs:
            fields["languages"] = langs
        fields["prompt"] = TRANSCRIBE_PROMPT
        fields["keywords"] = ["VoksyAI", "Voksy"]
        if self.delay:
            fields["delay"] = self.delay
        return fields

    def _session_payload(self, fields: Dict[str, object]) -> dict:
        transcription = {"model": self.model, **fields}
        return {"type": "session.update", "session": {
            "type": "transcription",
            "audio": {"input": {
                "format": {"type": "audio/pcm", "rate": self.sample_rate},
                "transcription": transcription,
                "turn_detection": None,
            }},
        }}

    # ------------------------------------------------------------------ соединение
    async def connect(self) -> bool:
        if not self.api_key:
            self._log("OPENAI_API_KEY is not configured", "ERROR")
            return False
        self._closing = False
        try:
            self.ws = await asyncio.wait_for(
                websockets.connect(TRANSCRIBE_URL, extra_headers=[("Authorization", f"Bearer {self.api_key}")],
                                   max_size=4 * 1024 * 1024, ping_interval=20, ping_timeout=20, close_timeout=5),
                timeout=CONNECT_TIMEOUT_SEC,
            )
            # Полная настройка; поле, которое сервер не принял, убираем и пробуем снова
            fields = self._optional_fields()
            while True:
                await self.ws.send(json.dumps(self._session_payload(fields)))
                err = await self._await_session_updated()
                if err is None:
                    break
                bad = next((k for k in fields if k in err), None) or (next(iter(fields), None) if fields else None)
                if bad is None:
                    raise RuntimeError(err)
                self._log(f"session.update rejected ({err}), retrying without '{bad}'", "WARNING")
                fields.pop(bad)
            self.accepted = fields
            if "languages" not in fields:
                self.secondary_languages = []
            if "delay" not in fields:
                self.delay = "default"
        except Exception as exc:
            self._log(f"connect failed: {exc}", "ERROR")
            await self._drop_socket()
            return False
        self.is_connected = True
        self._reader_task = asyncio.create_task(self._reader())
        self._watch_task = asyncio.create_task(self._watch_silence())
        self._log(f"session ready: {self.provider_label}")
        return True

    async def _await_session_updated(self) -> Optional[str]:
        deadline = time.monotonic() + CONNECT_TIMEOUT_SEC
        while time.monotonic() < deadline:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=max(0.1, deadline - time.monotonic()))
            msg = json.loads(raw) if isinstance(raw, str) else {}
            t = msg.get("type")
            if t in ("session.created",):
                self.session_id = (msg.get("session") or {}).get("id")
            elif t in ("session.updated", "transcription_session.updated"):
                return None
            elif t == "error":
                err = msg.get("error") or {}
                if err.get("code") in FATAL_CODES:
                    self.fatal_error = err.get("code")
                return f"{err.get('code')}: {err.get('message')}"
        return "timeout waiting for session.updated"

    async def _drop_socket(self) -> None:
        self.is_connected = False
        ws, self.ws = self.ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass

    # ------------------------------------------------------------------ аудио и конец фразы
    async def _send(self, payload: dict) -> bool:
        if not self.is_connected or self.ws is None:
            return False
        try:
            async with self._send_lock:
                await self.ws.send(json.dumps(payload))
            return True
        except Exception as exc:
            self._log(f"send failed: {exc}", "WARNING")
            return False

    async def send_audio(self, audio_b64: str) -> bool:
        pcm = base64.b64decode(audio_b64)
        self.audio_seconds += len(pcm) / (self.sample_rate * 2)
        now = time.monotonic()
        if self._vad.feed(pcm, now) == "start":
            self._turn_started_at = now
        return await self._send({"type": "input_audio_buffer.append", "audio": audio_b64})

    async def _commit(self, reason: str) -> None:
        self._vad.in_speech = False
        self._vad.loud_sec = 0.0
        self._commit_times.append(time.monotonic())
        await self._send({"type": "input_audio_buffer.commit"})

    async def _watch_silence(self) -> None:
        """Конец фразы: тишина silence_ms после речи (звук может и вовсе перестать приходить)."""
        try:
            while self.is_connected:
                await asyncio.sleep(WATCH_TICK_SEC)
                if not self._vad.in_speech:
                    continue
                now = time.monotonic()
                if (now - self._vad.last_voice_at) * 1000 >= self.silence_ms:
                    await self._commit("silence")
                elif now - self._turn_started_at >= MAX_TURN_SEC:
                    await self._commit("max_turn")
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------ события
    async def _reader(self) -> None:
        fatal = False
        try:
            async for raw in self.ws:
                if not isinstance(raw, str):
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                t = msg.get("type", "")
                if t == "input_audio_buffer.committed":
                    item = msg.get("item_id")
                    if item and self._commit_times:
                        self._pending_since[item] = self._commit_times.pop(0)
                elif t == "conversation.item.input_audio_transcription.delta":
                    item = msg.get("item_id") or ""
                    text = self._partials.get(item, "") + (msg.get("delta") or "")
                    self._partials[item] = text
                    if text.strip() and self.on_partial:
                        await self.on_partial(text.strip())
                elif t == "conversation.item.input_audio_transcription.completed":
                    item = msg.get("item_id") or ""
                    self._partials.pop(item, None)
                    started = self._pending_since.pop(item, None)
                    if started:
                        self.transcribe_ms.append(int((time.monotonic() - started) * 1000))
                    self.commits += 1
                    if self.on_committed:
                        await self.on_committed((msg.get("transcript") or "").strip())
                elif t == "conversation.item.input_audio_transcription.failed":
                    self._log(f"transcription failed: {json.dumps(msg.get('error'), ensure_ascii=False)[:300]}", "WARNING")
                elif t == "error":
                    err = msg.get("error") or {}
                    code = err.get("code")
                    if code == "input_audio_buffer_commit_empty":
                        continue  # коммит без звука (шумовой всплеск) — не ошибка
                    self._log(f"error {code}: {err.get('message')}", "ERROR")
                    if code in FATAL_CODES:
                        self.fatal_error = code
                        fatal = True
                        break
        except ConnectionClosed as exc:
            if not self._closing:
                self._log(f"connection closed: {exc}", "WARNING")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._log(f"reader error: {exc}", "ERROR")
        finally:
            was_closing = self._closing
            self.is_connected = False
            if self._watch_task:
                self._watch_task.cancel()
            if not was_closing:
                await self._drop_socket()
                if self.on_closed:
                    try:
                        await self.on_closed(fatal)
                    except Exception as exc:
                        self._log(f"on_closed failed: {exc}", "ERROR")

    async def close(self) -> None:
        self._closing = True
        self.is_connected = False
        current = asyncio.current_task()
        for task in (self._watch_task, self._reader_task):
            if task and task is not current and not task.done():
                task.cancel()
        await self._drop_socket()
        for task in (self._watch_task, self._reader_task):
            if task and task is not current:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
