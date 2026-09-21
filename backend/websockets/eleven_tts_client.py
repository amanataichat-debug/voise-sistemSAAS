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
    → {"context_id": C, "close_context": true}  дозвучить накопленное, прислать is_final и закрыть
    → {"keep_alive": true}                      сервер закрывает сокет после 20 с тишины
    → {"close_socket": true}
    ← {"audio": "<base64 PCM16>", "context_id": C}
    ← {"is_final_audio_for_turn": true, "context_id": C}
    ← {"is_final": true, "context_id": C}
    ← {"message": ..., "error": ..., "code": ...}

Интерфейс повторяет FishTTSClient (хендлер один и тот же — FishVoiceSession):
say(text) / end_of_response() / clear() / close(), колбэки on_audio,
on_speech_started, on_speech_ended, счётчики audio_bytes / chunks, флаг speaking.

Реплика ассистента = один контекст. Предложения приходят по мере генерации
модели, каждое закрывается flush (сервер буферизует ~40 символов до первого
звука). Когда модель закончила ответ, end_of_response() шлёт close_context:
по документации сервер дозвучивает всё накопленное и присылает is_final —
это и есть конец реплики (on_speech_ended). Ждать конец по тишине нельзя:
Eleven v3 синтезирует каждый flush отдельно, паузы между предложениями
доходят до 1–2 с, и «тишина» посреди ответа обрывала его после первого
предложения. Тишина осталась только страховкой: если is_final не пришёл
FINAL_TIMEOUT_MS после последнего звука, реплику считаем законченной.

Звук принимается от всех «живых» контекстов: текущего и уже закрытых
end_of_response(), но ещё дозвучивающих. Перебивание (clear) закрывает их
все на сервере и вычёркивает из живых — их звук дальше отбрасывается. Если
новая реплика началась, пока прошлая ещё дозвучивала (абонент заговорил до
первого звука приветствия), прошлую бросаем — иначе два голоса наложатся.

Лимит: не больше 5 открытых контекстов на соединение; каждый закрывается
сразу по концу ответа или при перебивании. Если сокет упал — переоткрывается
при следующем say().
"""

import asyncio
import base64
import json
import time
import uuid
from typing import Awaitable, Callable, List, Optional, Set
from urllib.parse import urlencode

import websockets
from websockets.exceptions import ConnectionClosed

from backend.core.logging import get_logger
from backend.models.eleven_assistant import (
    DEFAULT_ELEVEN_LANGUAGE,
    DEFAULT_ELEVEN_STABILITY,
    DEFAULT_ELEVEN_TTS_MODEL,
    ELEVEN_TTS_MODEL_IDS,
)

logger = get_logger(__name__)

ELEVEN_TTD_WS_URL = "wss://api.elevenlabs.io/v1/text-to-dialogue/multi-stream-input"

# Страховка: is_final после close_context не пришёл столько мс после последнего звука.
FINAL_TIMEOUT_MS = 5000
IDLE_POLL_SEC = 0.1
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
        self.context_id: Optional[str] = None   # контекст текущей реплики, ещё принимает текст
        self._live: Set[str] = set()            # контексты, чей звук отдаём клиенту
        self._finishing: Optional[str] = None   # закрыт end_of_response(), его is_final = конец реплики
        self._new_turn = True
        self.pending_text: List[str] = []
        self._close_after_pending = False      # end_of_response() пришёл, пока текст ждал переподключения
        self._lock = asyncio.Lock()
        self._reader_task: Optional[asyncio.Task] = None
        self._idle_task: Optional[asyncio.Task] = None
        self._keepalive_task: Optional[asyncio.Task] = None
        self._bg_tasks: Set[asyncio.Task] = set()
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
        self.context_id = None
        self._live = set()
        self._finishing = None
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
        if pending and self._close_after_pending:
            self._close_after_pending = False
            self.end_of_response()

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

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

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
                    if ctx and ctx not in self._live:
                        continue  # реплика, брошенная при перебивании или вытесненная следующей
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
                elif message.get("is_final"):
                    if ctx and ctx in self._live:
                        self._live.discard(ctx)
                        if ctx == self._finishing:
                            await self._utterance_ended("is_final")
                elif message.get("error") or message.get("message") and message.get("code"):
                    self.errors += 1
                    logger.error(f"[ELEVEN-TTS {self.label}] error: {json.dumps(message, ensure_ascii=False)[:300]}")
        except ConnectionClosed as exc:
            if not self.closing:
                logger.warning(f"[ELEVEN-TTS {self.label}] connection closed: code={exc.code} reason={exc.reason}")
        except Exception as exc:
            logger.error(f"[ELEVEN-TTS {self.label}] reader error: {exc}")
        finally:
            if self.ws is ws:
                self.ws = None  # следующий say() переподключится
                self.context_id = None
                self._live = set()
                if self._finishing is not None or self.speaking:
                    await self._utterance_ended("socket closed")

    async def _utterance_ended(self, reason: str) -> None:
        """Реплика доиграна (или бросать больше нечего): сообщить хендлеру."""
        self._finishing = None
        self.response_complete = False
        if not self.speaking:
            return
        self.speaking = False
        if reason != "is_final":
            logger.warning(f"[ELEVEN-TTS {self.label}] utterance ended without is_final ({reason})")
        if self.on_speech_ended:
            await self.on_speech_ended()

    async def _idle_watch(self) -> None:
        """Страховка: is_final после close_context так и не пришёл."""
        try:
            while not self.closing:
                await asyncio.sleep(IDLE_POLL_SEC)
                if (
                    self.speaking
                    and self.response_complete
                    and self._finishing is not None
                    and (time.monotonic() - self.last_audio_at) * 1000.0 > FINAL_TIMEOUT_MS
                ):
                    self._live.discard(self._finishing)
                    await self._utterance_ended("final timeout")
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

    async def _close_context(self, ctx: str) -> None:
        """Попросить сервер дозвучить контекст и прислать is_final."""
        ws = self.ws
        if ws is None:
            return
        try:
            await self._send(ws, {"context_id": ctx, "close_context": True})
        except Exception as exc:
            logger.warning(f"[ELEVEN-TTS {self.label}] close_context({ctx}) failed: {exc}")

    # ------------------------------------------------------------------ команды
    async def _send_text(self, ws, text: str) -> None:
        if self.context_id is None:
            if self._finishing is not None:
                # Прошлая реплика ещё дозвучивает, а модель уже отвечает дальше
                # (абонент заговорил до первого звука приветствия) — бросаем её,
                # иначе два голоса наложатся. close_context ей уже отправлен.
                self._live.discard(self._finishing)
                self._finishing = None
                self.response_complete = False
            self.context_id = f"ctx_{uuid.uuid4().hex[:12]}"
            self._live.add(self.context_id)
            await self._send(ws, {
                "context_id": self.context_id,
                "voices": [self.voice_id],
                "voice_settings": {"stability": self.stability},
            })
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
                self.context_id = None
                self._live = set()
                self.pending_text.append(text)

    def end_of_response(self) -> None:
        """Хендлер отдал весь текст ответа: закрыть контекст, сервер дозвучит и пришлёт is_final."""
        self.response_complete = True
        ctx, self.context_id = self.context_id, None
        if not ctx:
            self._close_after_pending = bool(self.pending_text)
            return
        self._finishing = ctx
        self._spawn(self._close_context(ctx))

    async def clear(self) -> None:
        """Перебивание: закрыть все живые контексты, дальше их звук игнорируется."""
        self.generation += 1
        self.pending_text.clear()
        self._close_after_pending = False
        self.speaking = False
        self.response_complete = False
        ws = self.ws
        ctxs, self._live = list(self._live), set()
        self.context_id = None
        self._finishing = None
        if ws is not None:
            for ctx in ctxs:
                try:
                    await self._send(ws, {"context_id": ctx, "close_context": True})
                except Exception as exc:
                    logger.warning(f"[ELEVEN-TTS {self.label}] close_context failed: {exc}")
                    self.ws = None
                    break
        logger.info(f"[ELEVEN-TTS {self.label}] barge-in: contexts {ctxs} closed (gen={self.generation})")

    async def close(self) -> None:
        self.closing = True
        for task in (self._idle_task, self._keepalive_task, *self._bg_tasks):
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
