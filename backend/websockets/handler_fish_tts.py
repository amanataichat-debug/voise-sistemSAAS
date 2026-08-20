# backend/websockets/handler_fish_tts.py
"""
Прокси Voximplant ⇄ Fish Audio для озвучки реплик ассистента.

Зачем он нужен
--------------
У Voximplant нет встроенного модуля Fish (в отличие от Modules.Cartesia),
а медиа-канал VoxEngine принимает только собственный JSON-протокол:

    {"event":"start", "start":{"mediaFormat":{"encoding":"PCM16","sampleRate":8000}}}
    {"event":"media", "media":{"timestamp":..,"chunk":..,"payload":"<base64 PCM>"}}
    {"event":"stop",  "stop":{"mediaInfo":{"bytesSent":..,"duration":..}}}

Fish Audio говорит по своему протоколу на MessagePack
(wss://api.fish.audio/v1/tts/live). Состыковать их напрямую нельзя —
этот модуль и есть переходник.

Протокол со стороны сценария (текстовые JSON-фреймы)
----------------------------------------------------
    → {"event":"text","text":"кусок реплики"}   отправить текст в синтез
    → {"event":"flush"}                          синтезировать накопленное сейчас
    → {"event":"clear"}                          barge-in: бросить очередь аудио
    → {"event":"stop"}                           завершить сессию

Обратно сценарий получает media-фреймы Voximplant, которые
`websocket.sendMediaTo(call)` проигрывает в звонок.

Аудио идёт из Fish сразу в PCM16 на нужной частоте (format: "pcm"),
поэтому перекодировать ничего не требуется — только нарезать на кадры
и отдавать в темпе реального времени.
"""

import asyncio
import audioop
import base64
import json
import os
import time
import traceback
from typing import Optional

import msgpack
import websockets
from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from backend.core.logging import get_logger
from backend.models.fish_assistant import FishAssistantConfig, DEFAULT_FISH_MODEL
from backend.models.user import User

logger = get_logger(__name__)

FISH_WS_URL = "wss://api.fish.audio/v1/tts/live"

# Кодек, которым представляемся Voximplant в StartEvent.
#
# Названия берём из WebSocketAudioEncoding (voxengine.d.ts), а не из примера
# в доке: там показан кадр, который Voximplant ОТПРАВЛЯЕТ, и в нём
# encoding="PCM16" при sampleRate=8000 — с перечислением это расходится.
#
#     PCM16_8KHZ  = 'PCM8'    ← 8 кГц
#     PCM16_16KHZ = 'PCM16'   ← 16 кГц
#     ULAW        = 'ULAW'    ← 8 кГц, G.711 μ-law
#
# На первом боевом звонке мы слали "PCM16" с sampleRate 8000 — Voximplant
# счёл StartEvent невалидным, MEDIA_STARTED не сработал, и звук в трубку не
# попал вообще. Отсюда явное сопоставление частоты и имени кодека.
PCM_ENCODING_BY_RATE = {
    8000:  "PCM8",
    16000: "PCM16",
}

# Запасной вариант: отдавать G.711 μ-law. Именно эта пара (ULAW + 8000)
# фигурирует в единственном рабочем примере отправителя в документации
# Voximplant, поэтому её оставляем переключателем на случай, если PCM8
# тоже не примут. Включается переменной окружения FISH_TTS_ENCODING=ULAW.
FORCE_ENCODING = (os.getenv("FISH_TTS_ENCODING") or "").strip().upper()

# Длительность одного медиа-кадра в звонок. 20 мс — рекомендованный докой
# шаг; при 8 кГц PCM16 это ровно 320 байт.
FRAME_MS = 20

# Сколько тишины от Fish считать концом реплики. MEDIA_STARTED/MEDIA_ENDED
# у Voximplant относятся к медиапотоку целиком, а он у нас живёт весь звонок,
# поэтому границу реплики сценарию сообщаем сами — служебными сообщениями
# speech_started / speech_done.
#
# 400 мс оказалось мало: на боевом звонке Fish отдавал одну реплику тремя
# пачками с паузами больше 400 мс, и прокси рапортовал три конца реплики
# подряд (remaining_ms 56 → 3403 → 4831). Для сценария это опасно: по
# первому speech_done он кладёт трубку после прощания и открывает шлюз
# приветствия в исходящем — то есть оборвал бы речь на полуслове.
UTTERANCE_IDLE_MS = 700

# Насколько далеко вперёд реального времени разрешено убегать.
# Voximplant буферизует медиа сам и проигрывает в реальном времени, но буфер
# ограничен 10 секундами — всё сверх того молча отбрасывается. Держим запас
# вдвое меньше предела: и от переполнения защищает, и не мешает Fish
# синтезировать быстрее реального времени.
LEAD_LIMIT_MS = 5000

# Сколько байт PCM16 приходится на кадр при заданной частоте.
def _frame_bytes(sample_rate: int) -> int:
    return int(sample_rate * (FRAME_MS / 1000.0)) * 2  # 2 байта на сэмпл, моно


class _FishTTSSession:
    """
    Одна сессия озвучки: живёт столько же, сколько WebSocket от сценария.

    Держит два соединения (сценарий и Fish) и очередь PCM между ними.
    Очередь нужна, чтобы отдавать аудио в звонок равномерно, а не
    выплёскивать разом всё, что Fish успел насинтезировать.
    """

    def __init__(self, websocket: WebSocket, assistant: FishAssistantConfig, api_key: str):
        self.ws = websocket
        self.assistant = assistant
        self.api_key = api_key
        self.sample_rate = assistant.sample_rate or 8000
        self.frame_size = _frame_bytes(self.sample_rate)

        # Кодек, которым представляемся Voximplant. Из Fish всегда идёт PCM16;
        # при ULAW кадр перекодируется перед отправкой (см. _encode_frame).
        if FORCE_ENCODING in ("ULAW", "ALAW"):
            self.encoding = FORCE_ENCODING
        else:
            self.encoding = PCM_ENCODING_BY_RATE.get(self.sample_rate, "PCM8")

        self.fish: Optional[websockets.WebSocketClientProtocol] = None
        self.audio_buffer = bytearray()
        self.buffer_lock = asyncio.Lock()

        self.sequence = 0
        self.chunk = 0
        self.bytes_sent = 0
        self.started = False
        self.closing = False

        # Учёт опережения реального времени (см. _pump_to_call). Сбрасывается
        # при barge-in: сценарий чистит буфер Voximplant, и всё, что мы
        # успели туда отдать, к воспроизведению больше не относится.
        self.stream_started_at: Optional[float] = None
        self.queued_ms = 0.0

        # Границы реплики (см. UTTERANCE_IDLE_MS).
        self.utterance_active = False
        self.last_audio_at = 0.0

        # Конец реплики привязан к flush, а не к одной лишь тишине от Fish:
        # флаг взводится каждым flush и гасится первым же speech_done. Иначе
        # пауза в середине реплики выглядит как её конец, и сценарий получает
        # несколько speech_done на один ход.
        self.flush_pending = False

        # Поколение соединения с Fish. У Fish нет события отмены синтеза
        # (только start/text/flush/stop), поэтому единственный способ
        # гарантированно оборвать начатую реплику при barge-in —
        # переподключиться. Аудио из соединения прошлого поколения
        # отбрасывается, даже если долетело уже после разрыва.
        self.generation = 0

        # Текст, пришедший от сценария, пока новое соединение ещё
        # поднимается. Досылается в Fish, как только сокет готов.
        self.pending_text: list[str] = []
        self.reconnect_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Сторона Voximplant
    # ------------------------------------------------------------------

    async def _send_start(self) -> None:
        """
        StartEvent — обязан быть первым фреймом в сторону VoxEngine.

        Без tag: сценарий вызывает sendMediaTo(call) без тега, а помеченный
        поток к непомеченному потребителю не привязывается. В рабочем примере
        из документации tag тоже отсутствует.
        """
        await self.ws.send_text(json.dumps({
            "event": "start",
            "sequenceNumber": self.sequence,
            "start": {
                "mediaFormat": {
                    "encoding": self.encoding,
                    "sampleRate": self.sample_rate,
                },
            },
        }))
        self.sequence += 1
        self.started = True
        logger.info(
            f"[FISH-TTS] StartEvent sent: {self.encoding} @ {self.sample_rate} Hz "
            f"(frame {self.frame_size} bytes)"
        )

    async def _send_frame(self, payload: bytes) -> None:
        """
        Один media-фрейм в звонок.

        timestamp — это сумма сэмплов в предыдущих кадрах (поле таймстампа
        RTP), а не миллисекунды: для PCM16 число сэмплов равно длине в
        байтах, делённой на 2. chunk — порядковый номер пакета.
        """
        # timestamp считаем по сэмплам ДО перекодирования: в μ-law на сэмпл
        # приходится один байт, в PCM16 — два, а счётчик должен остаться
        # счётчиком сэмплов.
        samples = self.bytes_sent // 2

        await self.ws.send_text(json.dumps({
            "event": "media",
            "sequenceNumber": self.sequence,
            "media": {
                "timestamp": samples,
                "chunk": self.chunk,
                "payload": base64.b64encode(self._encode_frame(payload)).decode("ascii"),
            },
        }))
        self.sequence += 1
        self.chunk += 1
        self.bytes_sent += len(payload)

    def _encode_frame(self, pcm16: bytes) -> bytes:
        """Приводит кадр из Fish (PCM16) к кодеку, заявленному в StartEvent."""
        if self.encoding == "ULAW":
            return audioop.lin2ulaw(pcm16, 2)
        if self.encoding == "ALAW":
            return audioop.lin2alaw(pcm16, 2)
        return pcm16

    async def _send_control(self, event: str, payload: dict) -> None:
        """
        Служебное сообщение сценарию.

        Идёт по тому же сокету обычным текстовым фреймом: Voximplant отдаёт
        его в WebSocketEvents.MESSAGE, а медиа-конвейер такие кадры не трогает.
        """
        try:
            await self.ws.send_text(json.dumps({"event": event, **payload}))
        except Exception as e:
            logger.warning(f"[FISH-TTS] Failed to send control '{event}': {e}")

    async def _send_stop(self) -> None:
        try:
            await self.ws.send_text(json.dumps({
                "event": "stop",
                "sequenceNumber": self.sequence,
                "stop": {
                    "mediaInfo": {
                        "bytesSent": self.bytes_sent,
                        "duration": int(self.bytes_sent / 2 / self.sample_rate * 1000),
                    },
                },
            }))
            self.sequence += 1
        except Exception:
            pass

    async def _pump_to_call(self) -> None:
        """
        Отдаёт накопленный PCM в звонок кадрами по FRAME_MS.

        Темп держит сам Voximplant: он складывает медиа в буфер и проигрывает
        в реальном времени. Поэтому кадры уходят сразу, как появляются, — так
        первый звук доходит до абонента без лишней задержки на нашей стороне.

        Единственное ограничение — буфер Voximplant в 10 секунд: всё, что
        сверх, отбрасывается молча. Поэтому следим, чтобы отданное аудио не
        убегало от реального времени дальше LEAD_LIMIT_MS.
        """
        while not self.closing:
            async with self.buffer_lock:
                if len(self.audio_buffer) >= self.frame_size:
                    frame = bytes(self.audio_buffer[:self.frame_size])
                    del self.audio_buffer[:self.frame_size]
                else:
                    frame = None

            if frame is None:
                # Буфер пуст. Если Fish молчит дольше UTTERANCE_IDLE_MS —
                # реплика закончилась, сообщаем сценарию, сколько аудио ещё
                # доигрывает в буфере Voximplant.
                if (
                    self.utterance_active
                    and self.flush_pending
                    and (time.monotonic() - self.last_audio_at) * 1000.0 > UTTERANCE_IDLE_MS
                ):
                    self.utterance_active = False
                    self.flush_pending = False
                    await self._send_control("speech_done", {
                        "remaining_ms": max(0, int(self._lead_ms())),
                    })

                # Ждём следующую порцию от Fish. Шаг мелкий, чтобы не
                # добавлять задержки к началу реплики.
                await asyncio.sleep(0.005)
                continue

            if not self.utterance_active:
                self.utterance_active = True
                await self._send_control("speech_started", {})

            lead_ms = self._lead_ms()
            if lead_ms > LEAD_LIMIT_MS:
                await asyncio.sleep((lead_ms - LEAD_LIMIT_MS) / 1000.0)

            try:
                await self._send_frame(frame)
            except Exception as e:
                logger.warning(f"[FISH-TTS] Failed to send frame: {e}")
                return

            if self.stream_started_at is None:
                self.stream_started_at = time.monotonic()
            self.queued_ms += FRAME_MS

    def _lead_ms(self) -> float:
        """На сколько миллисекунд отданное аудио опережает реальное время."""
        if self.stream_started_at is None:
            return 0.0
        elapsed_ms = (time.monotonic() - self.stream_started_at) * 1000.0
        return self.queued_ms - elapsed_ms

    # ------------------------------------------------------------------
    # Сторона Fish Audio
    # ------------------------------------------------------------------

    async def connect_fish(self) -> None:
        """
        Открывает WS к Fish, досылает StartEvent и запускает читателя.

        Возвращается только после того, как сокет готов принимать текст,
        поэтому первый вызов можно ждать синхронно при старте сессии.
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "model": self.assistant.fish_model or DEFAULT_FISH_MODEL,
        }

        fish = await websockets.connect(
            FISH_WS_URL,
            extra_headers=headers,
            ping_interval=20,
            ping_timeout=20,
            max_size=None,
        )

        request = self.assistant.get_fish_start_request()
        await fish.send(msgpack.packb({"event": "start", "request": request}))

        self.fish = fish
        generation = self.generation

        logger.info(
            f"[FISH-TTS] Connected to Fish (generation={generation}): "
            f"model={headers['model']} voice={request.get('reference_id')} "
            f"rate={request.get('sample_rate')}"
        )

        asyncio.create_task(self._read_fish(fish, generation))

        # Текст, накопленный, пока сокет поднимался.
        pending, self.pending_text = self.pending_text, []
        for text in pending:
            await fish.send(msgpack.packb({"event": "text", "text": text}))

    async def _reconnect_fish(self) -> None:
        """Поднимает свежее соединение после barge-in."""
        try:
            await self.connect_fish()
        except Exception as e:
            logger.error(f"[FISH-TTS] Reconnect to Fish failed: {e}")
            self.pending_text.clear()

    async def _read_fish(self, fish, generation: int) -> None:
        """
        Читает аудио конкретного соединения и складывает в буфер.

        `generation` фиксируется на момент подключения: если к моменту
        прихода кадра поколение сессии ушло вперёд (был barge-in), аудио
        принадлежит прерванной реплике и в звонок не попадает.
        """
        try:
            async for raw in fish:
                if self.closing or generation != self.generation:
                    return

                try:
                    message = msgpack.unpackb(raw, raw=False)
                except Exception as e:
                    logger.warning(f"[FISH-TTS] Bad msgpack frame from Fish: {e}")
                    continue

                event = message.get("event")

                if event == "audio":
                    audio = message.get("audio") or b""
                    if audio:
                        self.last_audio_at = time.monotonic()
                        async with self.buffer_lock:
                            self.audio_buffer.extend(audio)

                elif event == "finish":
                    reason = message.get("reason")
                    logger.info(f"[FISH-TTS] Fish finished: reason={reason}")
                    if reason == "error":
                        logger.error("[FISH-TTS] Fish reported synthesis error")
                    return

                elif event == "log":
                    logger.debug(f"[FISH-TTS] Fish log: {message.get('message')}")

        except websockets.exceptions.ConnectionClosed:
            # Ожидаемо при barge-in — старый сокет закрывается намеренно.
            if generation == self.generation and not self.closing:
                logger.warning("[FISH-TTS] Fish connection closed unexpectedly")
        except Exception as e:
            logger.error(f"[FISH-TTS] Fish reader error: {e}")

    # ------------------------------------------------------------------
    # Команды от сценария
    # ------------------------------------------------------------------

    async def handle_command(self, message: dict) -> bool:
        """Обрабатывает команду сценария. Возвращает False, если пора закрываться."""
        event = message.get("event")

        if event == "text":
            text = message.get("text") or ""
            if text:
                if self.fish is not None:
                    await self.fish.send(msgpack.packb({"event": "text", "text": text}))
                else:
                    # Соединение ещё поднимается после barge-in.
                    self.pending_text.append(text)

        elif event == "flush":
            # Каждый flush переоткрывает окно ожидания конца реплики: ранний
            # flush первого предложения и финальный flush хода дают один
            # speech_done — по последнему из них.
            self.flush_pending = True
            if self.fish is not None:
                await self.fish.send(msgpack.packb({"event": "flush"}))

        elif event == "clear":
            # Barge-in: абонент перебил ассистента. Гасим накопленное аудио
            # и обрываем соединение с Fish — иначе «хвост» прерванной реплики
            # доиграет поверх следующей.
            self.generation += 1
            old_fish, self.fish = self.fish, None
            self.pending_text.clear()

            async with self.buffer_lock:
                self.audio_buffer.clear()

            # Очередь в Voximplant сценарий гасит сам (clearMediaBuffer),
            # поэтому учёт опережения начинаем заново — иначе следующая
            # реплика простояла бы в throttle из-за уже сброшенного аудио.
            self.stream_started_at = None
            self.queued_ms = 0.0
            self.utterance_active = False
            self.flush_pending = False

            if old_fish is not None:
                asyncio.create_task(self._close_socket(old_fish))

            self.reconnect_task = asyncio.create_task(self._reconnect_fish())
            logger.info(f"[FISH-TTS] Barge-in: reconnecting (generation={self.generation})")

        elif event == "stop":
            return False

        else:
            logger.warning(f"[FISH-TTS] Unknown command from scenario: {event}")

        return True

    @staticmethod
    async def _close_socket(fish) -> None:
        """Аккуратно закрывает соединение с Fish, не роняя вызывающего."""
        try:
            await fish.close()
        except Exception:
            pass

    async def close(self) -> None:
        self.closing = True

        if self.reconnect_task is not None:
            self.reconnect_task.cancel()

        fish, self.fish = self.fish, None
        if fish is not None:
            try:
                await fish.send(msgpack.packb({"event": "stop"}))
            except Exception:
                pass
            await self._close_socket(fish)


async def handle_fish_tts_connection(
    websocket: WebSocket,
    assistant_id: str,
    db: Session,
) -> None:
    """
    Точка входа для /ws/fish/tts/{assistant_id}.

    Подключение инициирует сценарий Voximplant через
    VoxEngine.createWebSocket(); ответные media-фреймы он направляет
    в звонок вызовом websocket.sendMediaTo(call).
    """
    await websocket.accept()

    session: Optional[_FishTTSSession] = None
    tasks: list[asyncio.Task] = []

    try:
        # --- ассистент и ключ пользователя ---------------------------------
        assistant = db.query(FishAssistantConfig).filter(
            FishAssistantConfig.id == assistant_id
        ).first()

        if not assistant:
            logger.warning(f"[FISH-TTS] Assistant not found: {assistant_id}")
            await websocket.close(code=1008, reason="Assistant not found")
            return

        if not assistant.is_active:
            logger.warning(f"[FISH-TTS] Assistant is inactive: {assistant_id}")
            await websocket.close(code=1008, reason="Assistant is inactive")
            return

        user = db.query(User).filter(User.id == assistant.user_id).first()
        api_key = user.fish_api_key if user else None

        if not api_key:
            logger.warning(f"[FISH-TTS] No Fish API key for assistant {assistant_id}")
            await websocket.close(code=1008, reason="Fish API key is not configured")
            return

        # --- поднимаем сессию ----------------------------------------------
        session = _FishTTSSession(websocket, assistant, api_key)

        try:
            await session.connect_fish()
        except Exception as e:
            logger.error(f"[FISH-TTS] Cannot connect to Fish Audio: {e}")
            await websocket.close(code=1011, reason="TTS provider unavailable")
            return

        await session._send_start()

        # Читатель Fish запускается внутри connect_fish (он же переживает
        # переподключения при barge-in); здесь остаётся только «насос» в звонок.
        tasks = [asyncio.create_task(session._pump_to_call())]

        # --- команды от сценария --------------------------------------------
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(f"[FISH-TTS] Non-JSON frame from scenario: {raw[:120]}")
                continue

            if not await session.handle_command(message):
                break

    except WebSocketDisconnect:
        logger.info(f"[FISH-TTS] Scenario disconnected: assistant_id={assistant_id}")
    except Exception as e:
        logger.error(f"[FISH-TTS] Session error for {assistant_id}: {e}")
        logger.error(f"[FISH-TTS] Traceback: {traceback.format_exc()}")
    finally:
        if session is not None:
            await session._send_stop()
            await session.close()

        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        try:
            await websocket.close()
        except Exception:
            pass

        logger.info(f"[FISH-TTS] Session closed: assistant_id={assistant_id}")
