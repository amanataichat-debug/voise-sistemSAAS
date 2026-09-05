"""
Адаптер между мостом SIP-шлюза и браузерными голосовыми хендлерами.

Мост (infra/sip-gateway/bridge/bridge.py) открывает на бэкенд WebSocket
/ws/sip/{call_id}: первым сообщением шлёт JSON "start", дальше бинарные кадры
PCM16 8 кГц (320 байт = 20 мс). Обратно ждёт бинарные кадры PCM16 8 кГц и
текстовые команды {"type":"clear"|"hangup"|"mark"}.

Браузерные хендлеры (handler_realtime_new / handler_gemini) ждут объект с
интерфейсом FastAPI WebSocket и JSON-протокол виджета:
    клиент → {"type":"input_audio_buffer.append","audio":"<base64 PCM16>"}
    сервер → {"type":"response.audio.delta","delta":"<base64 PCM16 24 кГц>"}
    плюс события speech.started / conversation.interrupted / function_call.* ...

`HandlerSocket` притворяется WebSocket'ом для хендлера и переводит один
протокол в другой, включая ресемплинг 8 кГц ↔ 24 кГц (OpenAI) или 8 → 16 кГц
на вход и 24 → 8 кГц на выход (Gemini). Так телефонный звонок проходит через ту
же логику функций, транскриптов и записи диалогов, что и виджет.
"""

import asyncio
import audioop
import base64
import json
import time
from typing import Any, Dict, Optional

from fastapi import WebSocket, WebSocketDisconnect

from backend.core.logging import get_logger

logger = get_logger(__name__)

PHONE_RATE = 8000
HANDLER_OUT_RATE = 24000  # оба хендлера отдают 24 кГц
HANDLER_IN_RATE = {"openai": 24000, "gemini": 16000}

# события хендлера, после которых нужно сбросить очередь воспроизведения у моста (перебивание)
BARGE_IN_EVENTS = {"speech.started", "conversation.interrupted", "response.cancelled"}
# события начала/конца речи ассистента
SPEECH_STARTED_EVENTS = {"assistant.speech.started"}
SPEECH_ENDED_EVENTS = {"assistant.speech.ended", "response.output_audio.done", "response.audio.done"}


class Resampler:
    """Потоковый ресемплер PCM16 mono на audioop.ratecv с сохранением состояния между кадрами."""

    def __init__(self, src_rate: int, dst_rate: int) -> None:
        self.src, self.dst = src_rate, dst_rate
        self._state = None

    def __call__(self, pcm: bytes) -> bytes:
        if not pcm:
            return b""
        if self.src == self.dst:
            return pcm
        out, self._state = audioop.ratecv(pcm, 2, 1, self.src, self.dst, self._state)
        return out


class HandlerSocket:
    """
    Псевдо-WebSocket для голосового хендлера. Реальное соединение с мостом — `self.ws`.

    Использует ровно то подмножество интерфейса WebSocket, которое трогают хендлеры:
    accept(), receive(), send_json(), close(), headers.
    """

    def __init__(self, ws: WebSocket, provider: str, call_id: str, on_event=None) -> None:
        self.ws = ws
        self.provider = provider
        self.call_id = call_id
        self.on_event = on_event  # необязательный колбэк(dict) для наблюдения за событиями хендлера
        self._queue: "asyncio.Queue[Optional[Dict[str, Any]]]" = asyncio.Queue()
        self._up = Resampler(PHONE_RATE, HANDLER_IN_RATE.get(provider, 24000))
        self._down = Resampler(HANDLER_OUT_RATE, PHONE_RATE)
        self._reader_task: Optional[asyncio.Task] = None
        self._hangup_task: Optional[asyncio.Task] = None
        self.closed = False
        self.hangup_sent = False
        self.ended_by_bridge = False
        self.end_reason: Optional[str] = None
        self.handler_error: Optional[Dict[str, Any]] = None
        self.frames_in = 0
        self.frames_out = 0
        self.barge_ins = 0
        self.audio_bytes_out = 0
        self._last_delta_at = 0.0
        self.started_at = time.time()
        self._speech_started = asyncio.Event()
        self._speech_ended = asyncio.Event()
        self._mark_hangup = asyncio.Event()

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        self._reader_task = asyncio.create_task(self._read_bridge())

    async def _read_bridge(self) -> None:
        """Читает реальный сокет моста и складывает сообщения для хендлера в очередь."""
        try:
            while not self.closed:
                raw = await self.ws.receive()
                if raw.get("type") == "websocket.disconnect":
                    self.ended_by_bridge = True
                    break
                if raw.get("bytes") is not None:
                    self.frames_in += 1
                    pcm = self._up(raw["bytes"])
                    await self._queue.put({
                        "type": "websocket.receive",
                        "text": json.dumps({
                            "type": "input_audio_buffer.append",
                            "audio": base64.b64encode(pcm).decode("ascii"),
                        }),
                    })
                    continue
                text = raw.get("text")
                if not text:
                    continue
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    continue
                mtype = data.get("type")
                if mtype == "ended":
                    self.ended_by_bridge = True
                    self.end_reason = data.get("reason")
                    break
                if mtype == "mark" and data.get("name") == "hangup":
                    self._mark_hangup.set()
                elif mtype == "dtmf":
                    logger.info(f"[SIP-MEDIA] call {self.call_id}: DTMF {data.get('digit')}")
                elif mtype == "ping":
                    await self._send_text({"type": "pong"})
        except (WebSocketDisconnect, RuntimeError):
            self.ended_by_bridge = True
        except Exception as exc:
            logger.warning(f"[SIP-MEDIA] call {self.call_id}: bridge reader stopped: {exc}")
            self.ended_by_bridge = True
        finally:
            await self._queue.put(None)

    async def _send_text(self, data: Dict[str, Any]) -> None:
        if self.closed:
            return
        try:
            await self.ws.send_text(json.dumps(data))
        except Exception:
            pass

    async def hangup(self, reason: str = "backend_hangup") -> None:
        """Попросить мост положить трубку (один раз)."""
        if self.hangup_sent or self.ended_by_bridge:
            return
        self.hangup_sent = True
        self.end_reason = self.end_reason or reason
        await self._send_text({"type": "hangup", "reason": reason})

    async def finish(self) -> None:
        """Завершить всё: трубка, задачи, реальный сокет. Вызывается после выхода хендлера."""
        if self._hangup_task and not self._hangup_task.done():
            self._hangup_task.cancel()
        await self.hangup("handler_finished")
        self.closed = True
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
        try:
            await self.ws.close()
        except Exception:
            pass

    # ------------------------------------------------ WebSocket interface for the handler
    @property
    def headers(self):
        return self.ws.headers

    @property
    def query_params(self):
        return self.ws.query_params

    @property
    def client(self):
        return self.ws.client

    async def accept(self, *args, **kwargs) -> None:
        return None  # реальный сокет уже принят роутером

    async def receive(self) -> Dict[str, Any]:
        msg = await self._queue.get()
        if msg is None:
            raise WebSocketDisconnect(code=1000)
        return msg

    async def receive_text(self) -> str:
        msg = await self.receive()
        return msg.get("text", "")

    async def receive_json(self) -> Any:
        return json.loads(await self.receive_text())

    async def send_text(self, text: str) -> None:
        try:
            await self.send_json(json.loads(text))
        except json.JSONDecodeError:
            pass

    async def send_json(self, data: Dict[str, Any], mode: str = "text") -> None:
        if self.closed:
            return
        mtype = data.get("type", "")
        if self.on_event:
            try:
                self.on_event(data)
            except Exception:
                pass

        if mtype == "response.audio.delta":
            delta = data.get("delta") or ""
            if delta:
                pcm = self._down(base64.b64decode(delta))
                if pcm:
                    self.frames_out += 1
                    self.audio_bytes_out += len(pcm)
                    self._last_delta_at = time.time()
                    try:
                        await self.ws.send_bytes(pcm)
                    except Exception:
                        pass
            return

        if mtype in BARGE_IN_EVENTS:
            self.barge_ins += 1
            since_delta = (time.time() - self._last_delta_at) if self._last_delta_at else -1
            logger.info(
                f"[SIP-MEDIA] call {self.call_id}: barge-in #{self.barge_ins} ({mtype}, {self.provider}), "
                f"{since_delta:.1f}s after last audio delta, {self.audio_bytes_out / 16000:.1f}s of audio sent so far"
            )
            await self._send_text({"type": "clear"})
            self._down = Resampler(HANDLER_OUT_RATE, PHONE_RATE)
            # Хендлер ждёт от клиента подтверждение остановки воспроизведения, как от браузера
            await self._queue.put({"type": "websocket.receive", "text": json.dumps({"type": "audio_playback.stopped"})})
            return

        if mtype in SPEECH_STARTED_EVENTS:
            self._speech_started.set()
            self._speech_ended.clear()
            return
        if mtype in SPEECH_ENDED_EVENTS:
            self._speech_ended.set()
            return

        if mtype in ("function_call.started", "function_call.executing"):
            if data.get("function") == "hangup_call" and self._hangup_task is None:
                logger.info(f"[SIP-MEDIA] call {self.call_id}: assistant requested hangup")
                self._speech_started.clear()
                self._speech_ended.clear()
                self._hangup_task = asyncio.create_task(self._hangup_after_farewell())
            return

        if mtype == "error":
            self.handler_error = data.get("error") or data
            logger.warning(f"[SIP-MEDIA] call {self.call_id}: handler error {self.handler_error}")
            return
        # connection_status, транскрипты, acks и прочее телефону не нужны

    async def close(self, code: int = 1000, reason: Optional[str] = None) -> None:
        await self.hangup("handler_closed" if code == 1000 else f"handler_close_{code}")

    # ------------------------------------------------------------- hangup flow
    async def _hangup_after_farewell(self) -> None:
        """Дать ассистенту договорить прощание, дождаться, пока мост его проиграет, и положить трубку."""
        try:
            try:
                await asyncio.wait_for(self._speech_started.wait(), timeout=8)
                try:
                    await asyncio.wait_for(self._speech_ended.wait(), timeout=40)
                except asyncio.TimeoutError:
                    pass
            except asyncio.TimeoutError:
                pass  # ассистент ничего не сказал после функции — кладём трубку так
            await self._send_text({"type": "mark", "name": "hangup"})
            try:
                await asyncio.wait_for(self._mark_hangup.wait(), timeout=15)
            except asyncio.TimeoutError:
                pass
            await self.hangup("assistant_hangup")
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning(f"[SIP-MEDIA] call {self.call_id}: hangup flow error: {exc}")
            await self.hangup("assistant_hangup")
