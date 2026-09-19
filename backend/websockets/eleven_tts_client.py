# backend/websockets/eleven_tts_client.py
"""
Клиент потокового синтеза ElevenLabs для Eleven-хендлера.

Протокол — Text-to-Dialogue Multi-Context WebSocket (единственный realtime-путь
для семейства Eleven v3, а только оно умеет кыргызский):
    wss://api.elevenlabs.io/v1/text-to-dialogue/multi-stream-input
        ?model_id=eleven_v3_conversational&output_format=pcm_24000&language_code=ky
    → {"context_id": C, "voices": [voice_id], "voice_settings": {...}}   первое сообщение контекста
    → {"context_id": C, "inputs": [{"text": "...", "voice_id": V, "new_turn": bool}]}
    → {"context_id": C, "flush": true}          синтезировать накопленное сейчас
    → {"context_id": C, "close_context": true}  перебивание: бросить контекст
    → {"keep_alive": true}                      сервер закрывает сокет после 20 с тишины
    → {"close_socket": true}
    ← {"audio": "<base64 PCM16>", "context_id": C}
    ← {"is_final_audio_for_turn": true, "context_id": C}
    ← {"is_final": true, "context_id": C}
    ← {"message": ..., "error": ..., "code": ...}

Интерфейс повторяет FishTTSClient (хендлер один и тот же — FishVoiceSession):
say(text) / end_of_response() / clear() / close(), колбэки on_audio,
on_speech_started, on_speech_ended, счётчики audio_bytes / chunks, флаг speaking.

Границы реплики. Сервер буферизует примерно 40 символов перед первым звуком,
поэтому каждое предложение закрывается flush. Конец реплики — по тишине после
end_of_response() (UTTERANCE_IDLE_MS), как у Fish; is_final_audio_for_turn
приходит не на каждый flush, полагаться только на него нельзя.

Перебивание. Каждая реплика ассистента — свой context_id; clear() закрывает
текущий контекст (сервер бросает синтез), аудио от старых контекстов
отбрасывается, следующий say() открывает новый контекст на том же сокете.
Если сокет упал — переоткрывается при следующем say().

Лимит: не больше 5 открытых контекстов на соединение (и сервер сам закрывает
контекст после 20 с тишины), поэтому договорённый контекст закрываем сами —
как только его аудио доиграло (idle watcher) или при старте следующей реплики.
"""

import asyncio
import base64
import json
import time
import uuid
from typing import Awaitable, Callable, List, Optional
from urllib.parse import urlencode

import websockets

from backend.core.logging import get_logger
from backend.models.eleven_assistant import (
    DEFAULT_ELEVEN_LANGUAGE,
    DEFAULT_ELEVEN_STABILITY,
    DEFAULT_ELEVEN_TTS_MODEL,
    ELEVEN_TTS_MODEL_IDS,
)

logger = get_logger(__name__)

ELEVEN_TTD_WS_URL = "wss://api.elevenlabs.io/v1/text-to-dialogue/multi-stream-input"

# Тишина от ElevenLabs после конца ответа модели, которую считаем концом реплики.
UTTERANCE_IDLE_MS = 700
IDLE_POLL_SEC = 0.05
# Сервер рвёт сокет через 20 с без сообщений — шлём keep_alive заранее.
KEEPALIVE_SEC = 12

AsyncBytesCallback = Callable[[bytes], Awaitable[None]]
AsyncCallback = Callable[[], Awaitable[None]]


class ElevenTTSClient:
    """Одна сессия синтеза на весь диалог; реплики — контексты на одном сокете."""

    def __init__(
        self,
        api_key: str,
        assistant,
        sample_rate: int,
        on_audio: AsyncBytesCallback,
        on_speech_started: Optional[AsyncCallback] = None,
        on_speech_ended: Optional[AsyncCallback] = None,
        label: str = "",
    ) -> None:
        self.api_key = api_key
        self.assistant = assistant
        self.sample_rate = sample_rate
        self.on_audio = on_audio
        self.on_speech_started = on_speech_started
        self.on_speech_ended = on_speech_ended
        self.label = label or "eleven"

        self.voice_id = (getattr(assistant, "voice_id", None) or "").strip()
        model = getattr(assistant, "tts_model", None) or DEFAULT_ELEVEN_TTS_MODEL
        self.model = model if model in ELEVEN_TTS_MODEL_IDS else DEFAULT_ELEVEN_TTS_MODEL
        self.language = (getattr(assistant, "language", None) or DEFAULT_ELEVEN_LANGUAGE).strip().lower()
        stability = getattr(assistant, "stability", None)
        self.stability = DEFAULT_ELEVEN_STABILITY if stability is None else float(stability)

        self.ws = None
        self.closing = False
        self.generation = 0
        self.context_id: Optional[str] = None   # текущая реплика
        self._context_open = False
        self._finished_contexts: List[str] = []  # договорённые реплики, ещё не закрытые на сервере
        self._new_turn = True
        self.pending_text: List[str] = []
        self._lock = asyncio.Lock()
        self._reader_task: Optional[asyncio.Task] = None
        self._idle_task: Optional[asyncio.Task] = None
        self._keepalive_task: Optional[asyncio.Task] = None
        self._last_sent_at = 0.0

        self.speaking = False
        self.response_complete = False
        self.last_audio_at = 0.0
        self.audio_bytes = 0
        self.chunks = 0
        self.errors = 0

    # ------------------------------------------------------------------ соединение
    def _url(self) -> str:
        params = {
            "model_id": self.model,
            "output_format": f"pcm_{self.sample_rate}",
        }
        if self.language:
            params["language_code"] = self.language
        return f"{ELEVEN_TTD_WS_URL}?{urlencode(params)}"

    async def connect(self) -> None:
        """Открыть сокет к ElevenLabs и запустить читателя. Бросает при ошибке."""
        if not self.voice_id:
            raise RuntimeError("voice_id is not set for the Eleven assistant")
        headers = {"xi-api-key": self.api_key}
        try:
            ws = await asyncio.wait_for(
                websockets.connect(self._url(), extra_headers=headers, ping_interval=20, ping_timeout=20,
                                   max_size=None),
                timeout=15,
            )
        except TypeError as te:
            if "extra_headers" not in str(te):
                raise
            ws = await asyncio.wait_for(
                websockets.connect(self._url(), additional_headers=headers, ping_interval=20, ping_timeout=20,
                                   max_size=None),
                timeout=15,
            )
        self.ws = ws
        self._context_open = False
        self.context_id = None
        self._last_sent_at = time.monotonic()
        generation = self.generation
        self._reader_task = asyncio.create_task(self._read(ws, generation))
        if self._idle_task is None:
            self._idle_task = asyncio.create_task(self._idle_watch())
        if self._keepalive_task is None:
            self._keepalive_task = asyncio.create_task(self._keepalive())
        logger.info(
            f"[ELEVEN-TTS {self.label}] connected (gen={generation}) model={self.model} voice={self.voice_id} "
            f"lang={self.language} rate={self.sample_rate} stability={self.stability}"
        )
        pending, self.pending_text = self.pending_text, []
        for text in pending:
            await self._send_text(ws, text)

    async def _ensure_connected(self) -> bool:
        if self.ws is not None:
            return True
        try:
            await self.connect()
            return True
        except Exception as exc:
            logger.error(f"[ELEVEN-TTS {self.label}] connect failed: {exc}")
            return False

    async def _send(self, ws, payload: dict) -> None:
        await ws.send(json.dumps(payload, ensure_ascii=False))
        self._last_sent_at = time.monotonic()

    # ------------------------------------------------------------------ читатель
    async def _read(self, ws, generation: int) -> None:
        try:
            async for raw in ws:
                if self.closing:
                    return
                try:
                    message = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                ctx = message.get("context_id") or message.get("contextId")
                if message.get("audio"):
                    if ctx and ctx != self.context_id:
                        continue  # реплика, брошенная при перебивании
                    try:
                        audio = base64.b64decode(message["audio"])
                    except Exception:
                        continue
                    if not audio:
                        continue
                    self.last_audio_at = time.monotonic()
                    self.audio_bytes += len(audio)
                    self.chunks += 1
                    if not self.speaking:
                        self.speaking = True
                        if self.on_speech_started:
                            await self.on_speech_started()
                    await self.on_audio(audio)
                elif message.get("is_final") and ctx == self.context_id:
                    self._context_open = False
                elif message.get("error") or message.get("message") and message.get("code"):
                    self.errors += 1
                    logger.error(f"[ELEVEN-TTS {self.label}] error: {json.dumps(message, ensure_ascii=False)[:300]}")
        except websockets.exceptions.ConnectionClosed as exc:
            if not self.closing:
                logger.warning(f"[ELEVEN-TTS {self.label}] connection closed: code={exc.code} reason={exc.reason}")
        except Exception as exc:
            logger.error(f"[ELEVEN-TTS {self.label}] reader error: {exc}")
        finally:
            if self.ws is ws:
                self.ws = None  # следующий say() переподключится
                self._context_open = False

    async def _idle_watch(self) -> None:
        """Ловит конец реплики по тишине после end_of_response()."""
        try:
            while not self.closing:
                await asyncio.sleep(IDLE_POLL_SEC)
                if (
                    self.speaking
                    and self.response_complete
                    and (time.monotonic() - self.last_audio_at) * 1000.0 > UTTERANCE_IDLE_MS
                ):
                    self.speaking = False
                    self.response_complete = False
                    await self._close_finished_contexts()
                    if self.on_speech_ended:
                        await self.on_speech_ended()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning(f"[ELEVEN-TTS {self.label}] idle watcher stopped: {exc}")

    async def _keepalive(self) -> None:
        try:
            while not self.closing:
                await asyncio.sleep(1.0)
                ws = self.ws
                if ws is None or time.monotonic() - self._last_sent_at < KEEPALIVE_SEC:
                    continue
                try:
                    await self._send(ws, {"keep_alive": True})
                except Exception:
                    pass
        except asyncio.CancelledError:
            pass

    async def _close_finished_contexts(self) -> None:
        """Закрыть на сервере договорённые контексты (аудио уже доиграло)."""
        ctxs, self._finished_contexts = self._finished_contexts, []
        ws = self.ws
        for ctx in ctxs:
            if ctx == self.context_id and not self._context_open:
                self.context_id = None
            if ws is None:
                continue
            try:
                await self._send(ws, {"context_id": ctx, "close_context": True})
            except Exception as exc:
                logger.warning(f"[ELEVEN-TTS {self.label}] close_context({ctx}) failed: {exc}")

    # ------------------------------------------------------------------ команды
    async def _send_text(self, ws, text: str) -> None:
        if not self._context_open:
            if self._finished_contexts:
                await self._close_finished_contexts()
            self.context_id = f"ctx_{uuid.uuid4().hex[:12]}"
            await self._send(ws, {
                "context_id": self.context_id,
                "voices": [self.voice_id],
                "voice_settings": {"stability": self.stability},
            })
            self._context_open = True
            self._new_turn = True
        await self._send(ws, {
            "context_id": self.context_id,
            "inputs": [{"text": text, "voice_id": self.voice_id, "new_turn": self._new_turn}],
        })
        self._new_turn = False
        # Сервер буферизует ~40 символов — без flush короткая фраза не озвучится
        await self._send(ws, {"context_id": self.context_id, "flush": True})

    async def say(self, text: str) -> None:
        """Озвучить кусок текста (обычно предложение)."""
        text = (text or "").strip()
        if not text or self.closing:
            return
        self.response_complete = False
        async with self._lock:
            if not await self._ensure_connected():
                self.pending_text.append(text)
                return
            try:
                await self._send_text(self.ws, text)
            except Exception as exc:
                logger.warning(f"[ELEVEN-TTS {self.label}] send failed, will reconnect: {exc}")
                self.ws = None
                self._context_open = False
                self.pending_text.append(text)

    def end_of_response(self) -> None:
        """Хендлер отдал весь текст ответа: после тишины можно объявлять конец реплики."""
        self.response_complete = True
        # Следующая реплика ассистента — новый контекст (и new_turn для просодии);
        # этот закроем на сервере, когда аудио доиграет
        if self._context_open and self.context_id and self.context_id not in self._finished_contexts:
            self._finished_contexts.append(self.context_id)
        self._context_open = False

    async def clear(self) -> None:
        """Перебивание: закрыть текущий контекст, дальше аудио от него игнорируется."""
        self.generation += 1
        self.pending_text.clear()
        self.speaking = False
        self.response_complete = False
        ws, ctx = self.ws, self.context_id
        self.context_id = None
        self._context_open = False
        self._finished_contexts = [c for c in self._finished_contexts if c != ctx]
        if ws is not None and ctx:
            try:
                await self._send(ws, {"context_id": ctx, "close_context": True})
            except Exception as exc:
                logger.warning(f"[ELEVEN-TTS {self.label}] close_context failed: {exc}")
                self.ws = None
        logger.info(f"[ELEVEN-TTS {self.label}] barge-in: context {ctx} closed (gen={self.generation})")

    async def close(self) -> None:
        self.closing = True
        for task in (self._idle_task, self._keepalive_task):
            if task is not None and not task.done():
                task.cancel()
        ws, self.ws = self.ws, None
        if ws is not None:
            try:
                await self._send(ws, {"close_socket": True})
            except Exception:
                pass
            try:
                await ws.close()
            except Exception:
                pass
        if self._reader_task is not None and not self._reader_task.done():
            self._reader_task.cancel()
