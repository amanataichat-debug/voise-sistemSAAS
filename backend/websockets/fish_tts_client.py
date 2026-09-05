# backend/websockets/fish_tts_client.py
"""
Клиент потокового синтеза Fish Audio для Fish-хендлера.

Протокол Fish (wss://api.fish.audio/v1/tts/live, кадры MessagePack):
    → {"event":"start","request":{text:"",format:"pcm",sample_rate,latency,...}}
    → {"event":"text","text":"кусок реплики"}
    → {"event":"flush"}                      синтезировать накопленное сейчас
    → {"event":"stop"}
    ← {"event":"audio","audio":<bytes PCM16>}
    ← {"event":"finish","reason":"stop"|"error"}
    ← {"event":"log","message":...}

Клиент отдаёт аудио хендлеру через колбэк on_audio(bytes) сразу, как оно
пришло — темп воспроизведения держит получатель (браузер буферизует сам,
мост SIP-шлюза режет на 20 мс и играет в реальном времени).

Границы реплики. У Fish нет события «эта реплика доиграна»: после flush
аудио просто приходит, пока есть что синтезировать. Поэтому конец реплики
определяется по тишине: хендлер вызывает end_of_response() после последнего
текста ответа модели, и когда после этого от Fish нет аудио дольше
UTTERANCE_IDLE_MS, клиент зовёт on_speech_ended(). Начало — первый аудио-кадр
после паузы → on_speech_started().

Перебивание. У Fish нет команды отмены синтеза, единственный надёжный способ
оборвать начатую реплику — переподключиться. clear() поднимает номер
поколения, закрывает старый сокет и переоткрывает новый в фоне; аудио от
прошлого поколения отбрасывается, даже если долетело позже. Текст, пришедший
пока сокет поднимается, досылается после подключения.
"""

import asyncio
import time
from typing import Awaitable, Callable, List, Optional

import msgpack
import websockets

from backend.core.logging import get_logger
from backend.models.fish_assistant import DEFAULT_FISH_MODEL

logger = get_logger(__name__)

FISH_WS_URL = "wss://api.fish.audio/v1/tts/live"

# Тишина от Fish после конца ответа модели, которую считаем концом реплики.
# 700 мс: на реальных звонках Fish отдавал одну реплику несколькими пачками
# с паузами больше 400 мс, и меньшее значение давало ложные «конец реплики».
UTTERANCE_IDLE_MS = 700

# Как часто проверять тишину.
IDLE_POLL_SEC = 0.05

AsyncBytesCallback = Callable[[bytes], Awaitable[None]]
AsyncCallback = Callable[[], Awaitable[None]]


class FishTTSClient:
    """
    Одна сессия синтеза на весь диалог. Переживает перебивания (переподключение)
    и обрывы со стороны Fish (переподключение при следующем say()).
    """

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
        self.label = label or "fish"

        self.ws = None
        self.generation = 0
        self.closing = False
        self.pending_text: List[str] = []
        self._connect_task: Optional[asyncio.Task] = None
        self._idle_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

        # Границы реплики
        self.speaking = False           # между on_speech_started и on_speech_ended
        self.response_complete = False  # хендлер отдал весь текст ответа (end_of_response)
        self.last_audio_at = 0.0
        self.audio_bytes = 0
        self.chunks = 0

    # ------------------------------------------------------------------ соединение
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "model": getattr(self.assistant, "fish_model", None) or DEFAULT_FISH_MODEL,
        }

    async def connect(self) -> None:
        """Открыть сокет к Fish, послать StartEvent, запустить читателя. Бросает при ошибке."""
        fish = await asyncio.wait_for(
            websockets.connect(
                FISH_WS_URL,
                extra_headers=self._headers(),
                ping_interval=20,
                ping_timeout=20,
                max_size=None,
            ),
            timeout=15,
        )
        request = self.assistant.get_fish_start_request(sample_rate=self.sample_rate)
        await fish.send(msgpack.packb({"event": "start", "request": request}))

        self.ws = fish
        generation = self.generation
        asyncio.create_task(self._read(fish, generation))
        if self._idle_task is None:
            self._idle_task = asyncio.create_task(self._idle_watch())

        logger.info(
            f"[FISH-TTS {self.label}] connected (gen={generation}) model={self._headers()['model']} "
            f"voice={request.get('reference_id')} rate={request.get('sample_rate')} latency={request.get('latency')}"
        )

        pending, self.pending_text = self.pending_text, []
        for text in pending:
            await self._send_text(fish, text)

    async def _reconnect(self) -> None:
        try:
            await self.connect()
        except Exception as exc:
            logger.error(f"[FISH-TTS {self.label}] reconnect failed: {exc}")
            self.pending_text.clear()

    async def _ensure_connected(self) -> bool:
        """Сокет готов к тексту. Если Fish закрыл соединение сам — переоткрыть здесь же."""
        if self.ws is not None:
            return True
        if self._connect_task is not None and not self._connect_task.done():
            return False  # поднимается после clear(): текст уйдёт в pending
        try:
            await self.connect()
            return True
        except Exception as exc:
            logger.error(f"[FISH-TTS {self.label}] connect failed: {exc}")
            return False

    # ------------------------------------------------------------------ читатель
    async def _read(self, fish, generation: int) -> None:
        try:
            async for raw in fish:
                if self.closing or generation != self.generation:
                    return
                try:
                    message = msgpack.unpackb(raw, raw=False)
                except Exception as exc:
                    logger.warning(f"[FISH-TTS {self.label}] bad msgpack frame: {exc}")
                    continue

                event = message.get("event")
                if event == "audio":
                    audio = message.get("audio") or b""
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

                elif event == "finish":
                    reason = message.get("reason")
                    if reason == "error":
                        logger.error(f"[FISH-TTS {self.label}] Fish reported synthesis error")
                    else:
                        logger.info(f"[FISH-TTS {self.label}] Fish finished: {reason}")
                    if self.ws is fish:
                        self.ws = None  # следующий say() переподключится
                    return

                elif event == "log":
                    logger.debug(f"[FISH-TTS {self.label}] {message.get('message')}")

        except websockets.exceptions.ConnectionClosed:
            if generation == self.generation and not self.closing:
                logger.warning(f"[FISH-TTS {self.label}] connection closed by Fish")
                if self.ws is fish:
                    self.ws = None
        except Exception as exc:
            logger.error(f"[FISH-TTS {self.label}] reader error: {exc}")
            if self.ws is fish:
                self.ws = None

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
                    if self.on_speech_ended:
                        await self.on_speech_ended()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning(f"[FISH-TTS {self.label}] idle watcher stopped: {exc}")

    # ------------------------------------------------------------------ команды
    @staticmethod
    async def _send_text(fish, text: str) -> None:
        await fish.send(msgpack.packb({"event": "text", "text": text}))
        await fish.send(msgpack.packb({"event": "flush"}))

    async def say(self, text: str) -> None:
        """Озвучить кусок текста (обычно предложение). Каждый кусок закрывается flush."""
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
                logger.warning(f"[FISH-TTS {self.label}] send failed, will reconnect: {exc}")
                self.ws = None
                self.pending_text.append(text)

    def end_of_response(self) -> None:
        """
        Хендлер отдал весь текст ответа: после тишины можно объявлять конец реплики.

        Вызывается обычно ДО прихода аудио (текст модели опережает синтез), поэтому
        флаг просто взводится; сработает он только когда speaking уже True — то есть
        аудио пришло и затем стихло. Следующий say() флаг сбрасывает.
        """
        self.response_complete = True

    async def clear(self) -> None:
        """Перебивание: бросить недоигранную реплику и поднять свежее соединение."""
        self.generation += 1
        old, self.ws = self.ws, None
        self.pending_text.clear()
        self.speaking = False
        self.response_complete = False
        if old is not None:
            asyncio.create_task(self._close_socket(old))
        if self._connect_task is None or self._connect_task.done():
            self._connect_task = asyncio.create_task(self._reconnect())
        logger.info(f"[FISH-TTS {self.label}] barge-in: reconnecting (gen={self.generation})")

    @staticmethod
    async def _close_socket(fish) -> None:
        try:
            await fish.close()
        except Exception:
            pass

    async def close(self) -> None:
        self.closing = True
        if self._connect_task is not None and not self._connect_task.done():
            self._connect_task.cancel()
        if self._idle_task is not None and not self._idle_task.done():
            self._idle_task.cancel()
        fish, self.ws = self.ws, None
        if fish is not None:
            try:
                await fish.send(msgpack.packb({"event": "stop"}))
            except Exception:
                pass
            await self._close_socket(fish)
