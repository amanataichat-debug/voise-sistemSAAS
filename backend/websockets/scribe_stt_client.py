# backend/websockets/scribe_stt_client.py
"""
Потоковое распознавание речи абонента через ElevenLabs Scribe Realtime.

Используется Eleven-хендлером в режиме «ASR → текст»: звук абонента уходит не в
OpenAI, а сюда; Scribe сам режет речь на фразы (commit_strategy=vad, пауза
ELEVEN_ASR_SILENCE_MS), и готовая фраза текстом уходит в диалоговую модель
(FishLLMClient в текстовом режиме).

    wss://api.elevenlabs.io/v1/speech-to-text/realtime
        ?model_id=scribe_v2_realtime&audio_format=pcm_24000&language_code=ky
        &commit_strategy=vad&vad_silence_threshold_secs=0.5
    header xi-api-key: ELEVENLABS_API_KEY

    клиент → {"message_type":"input_audio_chunk","audio_base_64":..., "commit":false, "sample_rate":24000}
    сервер ← session_started / partial_transcript {text} / committed_transcript {text} / ошибки

Промежуточный текст = «абонент заговорил» (перебивание срабатывает на первые
распознанные слова, а не на кашель и шум), окончательный = «фраза закончена».

Виджет не шлёт микрофон, пока ассистент говорит (half-duplex), поэтому в паузах
потока клиент сам досылает тишину в реальном темпе: таймлайн VAD у Scribe
остаётся непрерывным и сокет не простаивает.
"""

import asyncio
import base64
import json
import time
from typing import Awaitable, Callable, Optional
from urllib.parse import urlencode

import websockets
from websockets.exceptions import ConnectionClosed

from backend.core.logging import get_logger

logger = get_logger(__name__)

SCRIBE_URL = "wss://api.elevenlabs.io/v1/speech-to-text/realtime"
SCRIBE_MODEL = "scribe_v2_realtime"
SUPPORTED_RATES = (8000, 16000, 22050, 24000, 44100, 48000)
CONNECT_TIMEOUT_SEC = 8
FILLER_TICK_SEC = 0.1      # шаг досылки тишины, когда клиент молчит
FILLER_AFTER_SEC = 0.3     # сколько клиент должен молчать, прежде чем начнём досылать тишину

# Ошибки, после которых переподключаться бессмысленно — сессия уходит в прежний режим
FATAL_ERRORS = {"auth_error", "quota_exceeded", "unaccepted_terms", "invalid_request"}
ERROR_TYPES = FATAL_ERRORS | {
    "error", "commit_throttled", "rate_limited", "queue_overflow", "resource_exhausted",
    "session_time_limit_exceeded", "input_error", "chunk_size_exceeded",
    "insufficient_audio_activity", "transcriber_error",
}

TextCallback = Callable[[str], Awaitable[None]]


class ScribeSTTClient:
    def __init__(
        self,
        api_key: str,
        language: str,
        sample_rate: int = 24000,
        silence_ms: int = 500,
        on_partial: Optional[TextCallback] = None,
        on_committed: Optional[TextCallback] = None,
        on_closed: Optional[Callable[[bool], Awaitable[None]]] = None,
        label: str = "",
    ) -> None:
        if sample_rate not in SUPPORTED_RATES:
            raise ValueError(f"Scribe does not accept pcm_{sample_rate}")
        self.api_key = api_key
        self.language = (language or "").strip().lower()
        self.sample_rate = sample_rate
        self.silence_ms = max(100, int(silence_ms))
        self.on_partial = on_partial
        self.on_committed = on_committed
        self.on_closed = on_closed  # колбэк(fatal) при неожиданном обрыве
        self.label = label

        self.ws = None
        self.is_connected = False
        self.session_id: Optional[str] = None
        self.fatal_error: Optional[str] = None
        self.audio_seconds = 0.0
        self.commits = 0
        self._last_audio_at = 0.0
        self._closing = False
        self._reader_task: Optional[asyncio.Task] = None
        self._filler_task: Optional[asyncio.Task] = None
        self._send_lock = asyncio.Lock()

    def _log(self, message: str, level: str = "INFO") -> None:
        text = f"[SCRIBE {self.label}] {message}"
        if level == "ERROR":
            logger.error(text)
        elif level == "WARNING":
            logger.warning(text)
        else:
            logger.info(text)

    def _url(self) -> str:
        params = {
            "model_id": SCRIBE_MODEL,
            "audio_format": f"pcm_{self.sample_rate}",
            "commit_strategy": "vad",
            "vad_silence_threshold_secs": f"{self.silence_ms / 1000:.2f}",
        }
        if self.language:
            params["language_code"] = self.language
        return f"{SCRIBE_URL}?{urlencode(params)}"

    # ------------------------------------------------------------------ соединение
    async def connect(self) -> bool:
        """Открыть сокет и дождаться session_started. False — Scribe недоступен."""
        if not self.api_key:
            self._log("ELEVENLABS_API_KEY is not configured", "ERROR")
            return False
        self._closing = False
        try:
            self.ws = await asyncio.wait_for(
                websockets.connect(
                    self._url(),
                    extra_headers=[("xi-api-key", self.api_key)],
                    max_size=4 * 1024 * 1024,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                ),
                timeout=CONNECT_TIMEOUT_SEC,
            )
            while True:
                raw = await asyncio.wait_for(self.ws.recv(), timeout=CONNECT_TIMEOUT_SEC)
                msg = json.loads(raw) if isinstance(raw, str) else {}
                mtype = msg.get("message_type")
                if mtype == "session_started":
                    self.session_id = msg.get("session_id")
                    break
                if mtype in ERROR_TYPES:
                    self.fatal_error = mtype
                    raise RuntimeError(f"{mtype}: {msg.get('error') or msg.get('message') or msg}")
        except Exception as exc:
            self._log(f"connect failed: {exc}", "ERROR")
            await self._drop_socket()
            return False

        self.is_connected = True
        self._last_audio_at = time.monotonic()
        self._reader_task = asyncio.create_task(self._reader())
        self._filler_task = asyncio.create_task(self._filler())
        self._log(f"session {self.session_id} started: lang={self.language or 'auto'} "
                  f"rate={self.sample_rate} silence={self.silence_ms}ms")
        return True

    async def _drop_socket(self) -> None:
        self.is_connected = False
        ws, self.ws = self.ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass

    # ------------------------------------------------------------------ аудио
    async def _send_chunk(self, audio_b64: str, nbytes: int) -> bool:
        if not self.is_connected or self.ws is None:
            return False
        try:
            async with self._send_lock:
                await self.ws.send(json.dumps({
                    "message_type": "input_audio_chunk",
                    "audio_base_64": audio_b64,
                    "commit": False,
                    "sample_rate": self.sample_rate,
                }))
            self.audio_seconds += nbytes / (self.sample_rate * 2)
            return True
        except Exception as exc:
            self._log(f"send failed: {exc}", "WARNING")
            return False

    async def send_audio(self, audio_b64: str) -> bool:
        """Звук абонента: base64 PCM16 mono с частотой sample_rate."""
        self._last_audio_at = time.monotonic()
        return await self._send_chunk(audio_b64, len(audio_b64) * 3 // 4)

    async def _filler(self) -> None:
        """Пока клиент молчит (виджет во время речи ассистента), досылаем тишину в реальном темпе."""
        chunk_bytes = int(self.sample_rate * FILLER_TICK_SEC) * 2
        silence_b64 = base64.b64encode(b"\x00" * chunk_bytes).decode("ascii")
        try:
            while self.is_connected:
                await asyncio.sleep(FILLER_TICK_SEC)
                if time.monotonic() - self._last_audio_at >= FILLER_AFTER_SEC:
                    await self._send_chunk(silence_b64, chunk_bytes)
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
                mtype = msg.get("message_type")
                text = (msg.get("text") or "").strip()
                if mtype == "partial_transcript":
                    if text and self.on_partial:
                        await self.on_partial(text)
                elif mtype in ("committed_transcript", "committed_transcript_with_timestamps"):
                    self.commits += 1
                    if self.on_committed:
                        await self.on_committed(text)
                elif mtype == "warning":
                    self._log(f"warning: {msg.get('warning')}", "WARNING")
                elif mtype in ERROR_TYPES:
                    detail = msg.get("error") or msg.get("message") or ""
                    self._log(f"{mtype}: {detail}", "ERROR")
                    if mtype in FATAL_ERRORS:
                        self.fatal_error = mtype
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
            if self._filler_task:
                self._filler_task.cancel()
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
        for task in (self._filler_task, self._reader_task):
            if task and task is not current and not task.done():
                task.cancel()
        await self._drop_socket()
        for task in (self._filler_task, self._reader_task):
            if task and task is not current:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
