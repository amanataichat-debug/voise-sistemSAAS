#!/usr/bin/env python3
"""
Voksy AI SIP gateway bridge.

Sits between Asterisk and the Voksy backend on the SIP gateway VPS:

    operator  <-SIP/RTP->  Asterisk  <-AudioSocket (TCP, loopback)->  THIS  <-WebSocket (TLS)->  backend

Responsibilities
  * AudioSocket server (127.0.0.1:9092): receives the call audio from Asterisk
    (PCM16, 8 kHz, mono, 20 ms frames) and plays back what the backend sends.
  * Per-call media WebSocket to the backend:  {BACKEND_WS_URL}/ws/sip/{call_id}
  * Persistent control WebSocket to the backend: {BACKEND_WS_URL}/ws/sip-gateway/control
    — the backend pushes "originate"/"hangup" commands, the bridge reports call events.
    All connections are OUTBOUND from the VPS, so no extra inbound ports are needed.
  * AMI client (127.0.0.1:5038): originates outbound calls through the operator
    trunk, failing over from the first operator server to the second.
  * Tiny HTTP server (127.0.0.1:9091) used by the Asterisk dialplan to register
    inbound calls before AudioSocket connects.

Wire protocol (bridge <-> backend media socket)
  bridge -> backend, first message (text/JSON):
      {"type":"start","call_id":..., "direction":"inbound"|"outbound", "did":..., "caller":...,
       "to":..., "assistant_id":..., "assistant_type":..., "metadata":{...},
       "format":{"encoding":"pcm16","sample_rate":8000,"channels":1,"frame_ms":20}}
  bridge -> backend, then: binary frames = raw PCM16 LE 8 kHz mono (320 bytes = 20 ms)
  bridge -> backend, text: {"type":"dtmf","digit":"5"}   {"type":"ended","reason":...}
  backend -> bridge, binary frames = PCM16 LE 8 kHz mono, any size (bridge re-chunks and paces)
  backend -> bridge, text: {"type":"clear"}  (drop queued audio: barge-in)
                           {"type":"hangup"}
                           {"type":"mark","name":...}  (echoed back when playback reaches it)

Wire protocol (bridge <-> backend control socket)
  bridge -> backend: {"type":"hello","gateway_id":...,"version":...,"max_outbound":N,"public_ip":...}
                     {"type":"call.event","event":"started"|"answered"|"ended"|"failed", ...call fields...}
                     {"type":"pong"}
  backend -> bridge: {"type":"originate","call_id":?,"to":"996...", "caller_id":"996705579977",
                      "assistant_id":..., "assistant_type":..., "metadata":{...}}
                     {"type":"hangup","call_id":...}
                     {"type":"ping"}
"""

import asyncio
import json
import logging
import os
import signal
import struct
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from urllib.parse import quote

import websockets
from aiohttp import web

VERSION = "1.0.1"

# ----------------------------------------------------------------------------
# Configuration (environment, see /etc/voksy-bridge/bridge.env)
# ----------------------------------------------------------------------------


def _env(name: str, default: Optional[str] = None, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        print(f"FATAL: environment variable {name} is required", file=sys.stderr)
        sys.exit(2)
    return value or ""


BACKEND_WS_URL = _env("BACKEND_WS_URL", "wss://voksyai.online").rstrip("/")
GATEWAY_TOKEN = _env("GATEWAY_TOKEN", required=True)
GATEWAY_ID = _env("GATEWAY_ID", "sip-gw-1")
PUBLIC_IP = _env("PUBLIC_IP", "")

AUDIOSOCKET_HOST = _env("AUDIOSOCKET_HOST", "127.0.0.1")
AUDIOSOCKET_PORT = int(_env("AUDIOSOCKET_PORT", "9092"))
HTTP_HOST = _env("BRIDGE_HTTP_HOST", "127.0.0.1")
HTTP_PORT = int(_env("BRIDGE_HTTP_PORT", "9091"))

AMI_HOST = _env("AMI_HOST", "127.0.0.1")
AMI_PORT = int(_env("AMI_PORT", "5038"))
AMI_USER = _env("AMI_USER", "bridge")
AMI_SECRET = _env("AMI_SECRET", required=True)

TRUNK_ENDPOINT = _env("TRUNK_ENDPOINT", "o-trunk")
TRUNK_HOSTS = [h.strip() for h in _env("TRUNK_HOSTS", "195.216.237.6:5070,195.216.237.7:5070").split(",") if h.strip()]
MAX_OUTBOUND = int(_env("MAX_OUTBOUND", "4"))
ORIGINATE_TIMEOUT_MS = int(_env("ORIGINATE_TIMEOUT_MS", "45000"))
OUTBOUND_CONTEXT = _env("OUTBOUND_CONTEXT", "outbound-answered")
BACKEND_CONNECT_TIMEOUT = float(_env("BACKEND_CONNECT_TIMEOUT", "6"))
MAX_CALL_SECONDS = int(_env("MAX_CALL_SECONDS", "3600"))

LOG_LEVEL = _env("LOG_LEVEL", "INFO").upper()

# AudioSocket protocol
AS_TERMINATE = 0x00
AS_UUID = 0x01
AS_DTMF = 0x03
AS_AUDIO = 0x10
AS_ERROR = 0xFF

FRAME_MS = 20
FRAME_BYTES = 320  # 8000 Hz * 2 bytes * 0.02 s
SAMPLE_RATE = 8000
KEEPALIVE_SILENCE = _env("KEEPALIVE_SILENCE", "1") == "1"
SILENCE_FRAME = struct.pack("!BH", AS_AUDIO, FRAME_BYTES) + bytes(FRAME_BYTES)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("bridge")


# ----------------------------------------------------------------------------
# Call state
# ----------------------------------------------------------------------------


@dataclass
class Call:
    call_id: str
    direction: str  # "inbound" | "outbound"
    did: str = ""  # number dialled by the caller (inbound) / our caller id (outbound)
    caller: str = ""  # caller's number (inbound)
    to: str = ""  # callee number (outbound)
    assistant_id: str = ""
    assistant_type: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    channel: str = ""
    created_at: float = field(default_factory=time.time)
    answered_at: Optional[float] = None
    ended_at: Optional[float] = None
    end_reason: str = ""
    trunk_host: str = ""

    asterisk_writer: Optional[asyncio.StreamWriter] = None
    backend_ws: Optional[Any] = None
    backend_task: Optional[asyncio.Task] = None
    pacer_task: Optional[asyncio.Task] = None
    out_buf: bytearray = field(default_factory=bytearray)
    marks: list = field(default_factory=list)  # (byte_offset_when_reached, name)
    played_bytes: int = 0
    playing: bool = False
    ended: bool = False
    frames_in: int = 0
    frames_out: int = 0

    def public(self) -> Dict[str, Any]:
        duration = None
        if self.answered_at:
            duration = round((self.ended_at or time.time()) - self.answered_at, 1)
        return {
            "call_id": self.call_id,
            "direction": self.direction,
            "did": self.did,
            "caller": self.caller,
            "to": self.to,
            "assistant_id": self.assistant_id,
            "assistant_type": self.assistant_type,
            "metadata": self.metadata,
            "channel": self.channel,
            "trunk_host": self.trunk_host,
            "created_at": self.created_at,
            "answered_at": self.answered_at,
            "ended_at": self.ended_at,
            "duration_sec": duration,
            "reason": self.end_reason,
            "frames_in": self.frames_in,
            "frames_out": self.frames_out,
        }


class Bridge:
    def __init__(self) -> None:
        self.calls: Dict[str, Call] = {}
        self.control_out: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self.control_ws: Optional[Any] = None
        self.ami: Optional["AmiClient"] = None
        self.stopping = False

    # ------------------------------------------------------------------ calls
    def active_outbound(self) -> int:
        return sum(1 for c in self.calls.values() if c.direction == "outbound" and not c.ended)

    def new_call(self, direction: str, **kw: Any) -> Call:
        call_id = kw.pop("call_id", None) or str(uuid.uuid4())
        call = Call(call_id=call_id, direction=direction, **kw)
        self.calls[call_id] = call
        return call

    def emit(self, event: str, call: Call, **extra: Any) -> None:
        msg = {"type": "call.event", "event": event, "ts": time.time(), **call.public(), **extra}
        try:
            self.control_out.put_nowait(msg)
        except asyncio.QueueFull:
            log.warning("control queue full, dropping event %s for %s", event, call.call_id)

    async def end_call(self, call: Call, reason: str) -> None:
        if call.ended:
            return
        call.ended = True
        call.ended_at = time.time()
        call.end_reason = reason
        log.info("call %s ended: %s (in=%d out=%d dur=%s)", call.call_id, reason,
                 call.frames_in, call.frames_out, call.public()["duration_sec"])

        if call.pacer_task:
            call.pacer_task.cancel()
        # Tell Asterisk to hang up (no-op if it already did).
        if call.asterisk_writer and not call.asterisk_writer.is_closing():
            try:
                call.asterisk_writer.write(struct.pack("!BH", AS_TERMINATE, 0))
                await call.asterisk_writer.drain()
            except Exception:
                pass
            try:
                call.asterisk_writer.close()
            except Exception:
                pass
        # Tell the backend and close the media socket.
        if call.backend_ws:
            try:
                await call.backend_ws.send(json.dumps({"type": "ended", "reason": reason, **call.public()}))
            except Exception:
                pass
            try:
                await call.backend_ws.close()
            except Exception:
                pass
        if call.backend_task and call.backend_task is not asyncio.current_task():
            call.backend_task.cancel()

        self.emit("ended", call)
        # Keep the record briefly for late AudioSocket connections / debugging.
        asyncio.get_running_loop().call_later(120, self.calls.pop, call.call_id, None)

    # ---------------------------------------------------------- AudioSocket
    async def audiosocket_server(self) -> None:
        server = await asyncio.start_server(self._handle_audiosocket, AUDIOSOCKET_HOST, AUDIOSOCKET_PORT)
        log.info("AudioSocket listening on %s:%d", AUDIOSOCKET_HOST, AUDIOSOCKET_PORT)
        async with server:
            await server.serve_forever()

    @staticmethod
    async def _read_msg(reader: asyncio.StreamReader):
        header = await reader.readexactly(3)
        kind, length = struct.unpack("!BH", header)
        payload = await reader.readexactly(length) if length else b""
        return kind, payload

    async def _handle_audiosocket(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        call: Optional[Call] = None
        try:
            kind, payload = await asyncio.wait_for(self._read_msg(reader), timeout=5)
            if kind != AS_UUID or len(payload) != 16:
                log.warning("AudioSocket: first message is not a UUID (type=0x%02x len=%d)", kind, len(payload))
                writer.close()
                return
            call_id = str(uuid.UUID(bytes=payload))
            call = self.calls.get(call_id)
            if call is None or call.ended:
                log.warning("AudioSocket: unknown or ended call %s, hanging up", call_id)
                writer.write(struct.pack("!BH", AS_TERMINATE, 0))
                await writer.drain()
                writer.close()
                return

            call.asterisk_writer = writer
            call.answered_at = call.answered_at or time.time()
            log.info("AudioSocket connected for %s call %s", call.direction, call.call_id)
            self.emit("answered", call)

            # Connect the media socket to the backend and start the playback pacer.
            call.backend_task = asyncio.create_task(self._backend_media(call))
            call.pacer_task = asyncio.create_task(self._pacer(call))

            deadline = time.time() + MAX_CALL_SECONDS
            while not call.ended:
                kind, payload = await self._read_msg(reader)
                if kind == AS_AUDIO:
                    call.frames_in += 1
                    ws = call.backend_ws
                    if ws is not None:
                        try:
                            await ws.send(payload)
                        except Exception:
                            pass  # backend task handles the disconnect
                elif kind == AS_DTMF:
                    digit = payload.decode("ascii", "ignore")
                    log.info("call %s DTMF %s", call.call_id, digit)
                    if call.backend_ws is not None:
                        try:
                            await call.backend_ws.send(json.dumps({"type": "dtmf", "digit": digit}))
                        except Exception:
                            pass
                elif kind == AS_TERMINATE:
                    await self.end_call(call, "asterisk_hangup")
                    break
                elif kind == AS_ERROR:
                    log.error("call %s AudioSocket error code %s", call.call_id, payload.hex())
                if time.time() > deadline:
                    await self.end_call(call, "max_duration")
                    break
        except (asyncio.IncompleteReadError, ConnectionResetError, asyncio.TimeoutError):
            pass
        except Exception:
            log.exception("AudioSocket handler error")
        finally:
            if call is not None and not call.ended:
                await self.end_call(call, "asterisk_disconnected")
            try:
                writer.close()
            except Exception:
                pass

    async def _pacer(self, call: Call) -> None:
        """Send queued backend audio to Asterisk one 20 ms frame at a time."""
        loop = asyncio.get_running_loop()
        next_at = loop.time()
        try:
            while not call.ended:
                writer = call.asterisk_writer
                if writer is None or writer.is_closing():
                    return
                if len(call.out_buf) >= FRAME_BYTES:
                    chunk = bytes(call.out_buf[:FRAME_BYTES])
                    del call.out_buf[:FRAME_BYTES]
                    writer.write(struct.pack("!BH", AS_AUDIO, len(chunk)) + chunk)
                    await writer.drain()
                    call.frames_out += 1
                    call.played_bytes += len(chunk)
                    if not call.playing:
                        call.playing = True
                        log.info("call %s: playback started (%.1f s queued)", call.call_id, len(call.out_buf) / (SAMPLE_RATE * 2))
                else:
                    if call.playing:
                        call.playing = False
                        log.info("call %s: playback idle after %d frames", call.call_id, call.frames_out)
                    if KEEPALIVE_SILENCE:
                        # Тишина вместо пауз: Asterisk шлёт RTP непрерывно, NAT у абонента не закрывается
                        writer.write(SILENCE_FRAME)
                        await writer.drain()
                    # Fire marks whose position has been reached.
                    while call.marks and call.marks[0][0] <= call.played_bytes:
                        _, name = call.marks.pop(0)
                        if call.backend_ws is not None:
                            try:
                                await call.backend_ws.send(json.dumps({"type": "mark", "name": name}))
                            except Exception:
                                pass
                next_at += FRAME_MS / 1000.0
                delay = next_at - loop.time()
                if delay < -0.2:  # fell far behind (e.g. blocked drain) — resync
                    next_at = loop.time()
                    delay = 0
                await asyncio.sleep(max(0.0, delay))
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("pacer error for call %s", call.call_id)

    async def _backend_media(self, call: Call) -> None:
        url = f"{BACKEND_WS_URL}/ws/sip/{call.call_id}?token={quote(GATEWAY_TOKEN)}&gateway={quote(GATEWAY_ID)}"
        try:
            ws = await asyncio.wait_for(
                websockets.connect(url, max_size=4 * 1024 * 1024, ping_interval=20, ping_timeout=20),
                timeout=BACKEND_CONNECT_TIMEOUT,
            )
        except Exception as exc:
            log.error("call %s: cannot reach backend media socket: %s", call.call_id, exc)
            await self.end_call(call, "backend_unavailable")
            return

        call.backend_ws = ws
        start = {
            "type": "start",
            "gateway_id": GATEWAY_ID,
            "format": {"encoding": "pcm16", "sample_rate": SAMPLE_RATE, "channels": 1, "frame_ms": FRAME_MS},
            **call.public(),
        }
        try:
            await ws.send(json.dumps(start))
            async for message in ws:
                if isinstance(message, (bytes, bytearray)):
                    call.out_buf.extend(message)
                    continue
                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    log.warning("call %s: non-JSON text from backend ignored", call.call_id)
                    continue
                mtype = data.get("type")
                if mtype == "clear":
                    dropped_ms = len(call.out_buf) / (SAMPLE_RATE * 2) * 1000
                    log.info("call %s: clear from backend, dropping %.0f ms of queued audio", call.call_id, dropped_ms)
                    call.out_buf.clear()
                    call.marks.clear()
                elif mtype == "hangup":
                    await self.end_call(call, data.get("reason") or "backend_hangup")
                    return
                elif mtype == "mark":
                    call.marks.append((call.played_bytes + len(call.out_buf), data.get("name", "")))
                elif mtype == "ping":
                    await ws.send(json.dumps({"type": "pong"}))
        except websockets.ConnectionClosed as exc:
            log.info("call %s: backend media socket closed (%s)", call.call_id, exc.code)
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("call %s: backend media error", call.call_id)
        finally:
            call.backend_ws = None
        if not call.ended:
            # Let the tail of queued audio play before hanging up.
            await asyncio.sleep(min(2.0, len(call.out_buf) / (SAMPLE_RATE * 2)))
            await self.end_call(call, "backend_closed")

    # ---------------------------------------------------------- control link
    async def control_client(self) -> None:
        backoff = 1.0
        url = f"{BACKEND_WS_URL}/ws/sip-gateway/control?token={quote(GATEWAY_TOKEN)}&gateway={quote(GATEWAY_ID)}"
        while not self.stopping:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20, max_size=1024 * 1024) as ws:
                    self.control_ws = ws
                    backoff = 1.0
                    log.info("control socket connected to %s", BACKEND_WS_URL)
                    hello = {
                        "type": "hello",
                        "gateway_id": GATEWAY_ID,
                        "version": VERSION,
                        "max_outbound": MAX_OUTBOUND,
                        "public_ip": PUBLIC_IP,
                        "active_calls": [c.public() for c in self.calls.values() if not c.ended],
                    }
                    await ws.send(json.dumps(hello))
                    sender = asyncio.create_task(self._control_sender(ws))
                    try:
                        async for message in ws:
                            if isinstance(message, (bytes, bytearray)):
                                continue
                            try:
                                data = json.loads(message)
                            except json.JSONDecodeError:
                                continue
                            asyncio.create_task(self._handle_command(data))
                    finally:
                        sender.cancel()
                        self.control_ws = None
            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.warning("control socket: %s (retry in %.0fs)", exc, backoff)
            if self.stopping:
                return
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    async def _control_sender(self, ws: Any) -> None:
        while True:
            msg = await self.control_out.get()
            try:
                await ws.send(json.dumps(msg))
            except Exception:
                # Put it back for the next connection and stop.
                try:
                    self.control_out.put_nowait(msg)
                except asyncio.QueueFull:
                    pass
                return

    async def _handle_command(self, data: Dict[str, Any]) -> None:
        ctype = data.get("type")
        if ctype == "ping":
            await self.control_out.put({"type": "pong", "ts": time.time()})
        elif ctype == "originate":
            await self.originate(data)
        elif ctype == "hangup":
            call = self.calls.get(str(data.get("call_id", "")))
            if call:
                await self.end_call(call, data.get("reason") or "backend_request")
        elif ctype == "status":
            await self.control_out.put({"type": "status", "gateway_id": GATEWAY_ID,
                                        "active_calls": [c.public() for c in self.calls.values() if not c.ended]})
        else:
            log.warning("unknown control command: %s", ctype)

    # -------------------------------------------------------------- outbound
    async def originate(self, cmd: Dict[str, Any]) -> None:
        to = str(cmd.get("to", "")).strip().lstrip("+")
        caller_id = str(cmd.get("caller_id", "")).strip().lstrip("+")
        call = self.new_call(
            "outbound",
            call_id=cmd.get("call_id"),
            to=to,
            did=caller_id,
            assistant_id=str(cmd.get("assistant_id", "") or ""),
            assistant_type=str(cmd.get("assistant_type", "") or ""),
            metadata=cmd.get("metadata") or {},
        )
        if not to.isdigit() or not caller_id.isdigit():
            await self._fail(call, "bad_number")
            return
        if self.active_outbound() > MAX_OUTBOUND:
            await self._fail(call, "channel_limit")
            return
        if self.ami is None or not self.ami.connected:
            await self._fail(call, "ami_unavailable")
            return

        self.emit("started", call)
        last_reason = "failed"
        for index, host in enumerate(TRUNK_HOSTS):
            if call.ended:
                return
            call.trunk_host = host
            action_id = f"{call.call_id}:{index}"
            log.info("call %s: originating to %s via %s (cid=%s)", call.call_id, to, host, caller_id)
            try:
                result = await self.ami.originate(
                    action_id=action_id,
                    channel=f"PJSIP/{TRUNK_ENDPOINT}/sip:{to}@{host}",
                    context=OUTBOUND_CONTEXT,
                    exten="s",
                    priority="1",
                    caller_id=f'"{caller_id}" <{caller_id}>',
                    timeout_ms=ORIGINATE_TIMEOUT_MS,
                    variables={"VOKSY_UUID": call.call_id},
                )
            except Exception as exc:
                log.error("call %s: AMI originate error: %s", call.call_id, exc)
                last_reason = "ami_error"
                continue
            if result["ok"]:
                call.channel = result.get("channel", "")
                # Answered: AudioSocket will connect and emit "answered".
                return
            last_reason = result["reason"]
            log.info("call %s: originate via %s failed: %s", call.call_id, host, last_reason)
            if last_reason not in ("trunk_unavailable", "congestion"):
                break  # busy / no answer — the callee did not pick up, no point trying server B
        await self._fail(call, last_reason)

    async def _fail(self, call: Call, reason: str) -> None:
        call.ended = True
        call.ended_at = time.time()
        call.end_reason = reason
        log.info("call %s failed: %s", call.call_id, reason)
        self.emit("failed", call)
        asyncio.get_running_loop().call_later(120, self.calls.pop, call.call_id, None)

    # -------------------------------------------------------------- HTTP
    async def http_server(self) -> None:
        app = web.Application()
        app.router.add_get("/asterisk/inbound", self._http_inbound)
        app.router.add_get("/health", self._http_health)
        app.router.add_get("/calls", self._http_calls)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, HTTP_HOST, HTTP_PORT)
        await site.start()
        log.info("HTTP listening on %s:%d", HTTP_HOST, HTTP_PORT)

    async def _http_inbound(self, request: web.Request) -> web.Response:
        call = self.new_call(
            "inbound",
            did=request.query.get("did", ""),
            caller=request.query.get("cid", ""),
            channel=request.query.get("channel", ""),
        )
        log.info("inbound call %s did=%s caller=%s", call.call_id, call.did, call.caller)
        self.emit("started", call)
        return web.Response(text=call.call_id)

    async def _http_health(self, request: web.Request) -> web.Response:
        return web.json_response({
            "ok": True,
            "version": VERSION,
            "gateway_id": GATEWAY_ID,
            "control_connected": self.control_ws is not None,
            "ami_connected": bool(self.ami and self.ami.connected),
            "active_calls": sum(1 for c in self.calls.values() if not c.ended),
            "active_outbound": self.active_outbound(),
            "max_outbound": MAX_OUTBOUND,
        })

    async def _http_calls(self, request: web.Request) -> web.Response:
        return web.json_response([c.public() for c in self.calls.values()])

    # -------------------------------------------------------------- lifecycle
    async def run(self) -> None:
        self.ami = AmiClient(AMI_HOST, AMI_PORT, AMI_USER, AMI_SECRET)
        await self.http_server()
        tasks = [
            asyncio.create_task(self.audiosocket_server()),
            asyncio.create_task(self.control_client()),
            asyncio.create_task(self.ami.run()),
        ]
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        log.info("voksy bridge %s started (gateway_id=%s backend=%s)", VERSION, GATEWAY_ID, BACKEND_WS_URL)
        await stop.wait()
        log.info("shutting down")
        self.stopping = True
        for call in list(self.calls.values()):
            if not call.ended:
                await self.end_call(call, "gateway_shutdown")
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


# ----------------------------------------------------------------------------
# Minimal AMI client
# ----------------------------------------------------------------------------


class AmiClient:
    # OriginateResponse "Reason" codes (Asterisk control frame values)
    REASONS = {
        "0": "trunk_unavailable",  # could not start the call at all
        "1": "no_answer",  # channel hung up before answer
        "3": "no_answer",  # ringing then timeout
        "4": "answered",
        "5": "busy",
        "8": "congestion",
    }

    def __init__(self, host: str, port: int, user: str, secret: str) -> None:
        self.host, self.port, self.user, self.secret = host, port, user, secret
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.connected = False
        self._responses: Dict[str, asyncio.Future] = {}
        self._originates: Dict[str, asyncio.Future] = {}
        self._seq = 0

    async def run(self) -> None:
        backoff = 1.0
        while True:
            try:
                await self._session()
                backoff = 1.0
            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.warning("AMI: %s (retry in %.0fs)", exc, backoff)
            self.connected = False
            for fut in list(self._responses.values()) + list(self._originates.values()):
                if not fut.done():
                    fut.set_exception(ConnectionError("AMI disconnected"))
            self._responses.clear()
            self._originates.clear()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 15.0)

    async def _session(self) -> None:
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
        banner = await self.reader.readline()
        log.info("AMI connected: %s", banner.decode(errors="ignore").strip())
        resp = await self._action({"Action": "Login", "Username": self.user, "Secret": self.secret, "Events": "call"},
                                  wait=False)
        # Read packets forever; the login response resolves through the same loop.
        reader_task = asyncio.create_task(self._read_loop())
        try:
            login = await asyncio.wait_for(resp, timeout=10)
            if login.get("Response") != "Success":
                raise ConnectionError(f"AMI login failed: {login.get('Message')}")
            self.connected = True
            log.info("AMI logged in as %s", self.user)
            ping_task = asyncio.create_task(self._ping_loop())
            done, _ = await asyncio.wait({reader_task, ping_task}, return_when=asyncio.FIRST_COMPLETED)
            ping_task.cancel()
            for task in done:
                task.result()  # re-raise whatever ended the session
            raise ConnectionError("AMI session ended")
        finally:
            self.connected = False
            reader_task.cancel()
            try:
                self.writer.close()
            except Exception:
                pass

    async def _ping_loop(self) -> None:
        while True:
            await asyncio.sleep(30)
            await asyncio.wait_for(self._action({"Action": "Ping"}), timeout=10)

    async def _read_loop(self) -> None:
        assert self.reader is not None
        packet: Dict[str, str] = {}
        while True:
            line = await self.reader.readline()
            if not line:
                raise ConnectionError("AMI connection closed")
            text = line.decode(errors="ignore").rstrip("\r\n")
            if text == "":
                if packet:
                    self._dispatch(packet)
                    packet = {}
                continue
            key, _, value = text.partition(":")
            packet[key.strip()] = value.strip()

    def _dispatch(self, packet: Dict[str, str]) -> None:
        action_id = packet.get("ActionID", "")
        if "Response" in packet and action_id in self._responses:
            fut = self._responses.pop(action_id)
            if not fut.done():
                fut.set_result(packet)
            return
        if packet.get("Event") == "OriginateResponse" and action_id in self._originates:
            fut = self._originates.pop(action_id)
            if not fut.done():
                fut.set_result(packet)

    async def _action(self, fields: Dict[str, str], wait: bool = True) -> Any:
        assert self.writer is not None
        self._seq += 1
        action_id = fields.get("ActionID") or f"a{self._seq}"
        fields["ActionID"] = action_id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._responses[action_id] = fut
        payload = "".join(f"{k}: {v}\r\n" for k, v in fields.items()) + "\r\n"
        self.writer.write(payload.encode())
        await self.writer.drain()
        return await fut if wait else fut

    async def originate(self, *, action_id: str, channel: str, context: str, exten: str, priority: str,
                        caller_id: str, timeout_ms: int, variables: Dict[str, str]) -> Dict[str, Any]:
        if not self.connected:
            raise ConnectionError("AMI not connected")
        fields = {
            "Action": "Originate",
            "ActionID": action_id,
            "Channel": channel,
            "Context": context,
            "Exten": exten,
            "Priority": priority,
            "CallerID": caller_id,
            "Timeout": str(timeout_ms),
            "Async": "true",
            "Variable": ",".join(f"{k}={v}" for k, v in variables.items()),
        }
        done: asyncio.Future = asyncio.get_running_loop().create_future()
        self._originates[action_id] = done
        try:
            resp = await asyncio.wait_for(self._action(fields), timeout=10)
        except Exception:
            self._originates.pop(action_id, None)
            raise
        if resp.get("Response") != "Success":
            self._originates.pop(action_id, None)
            return {"ok": False, "reason": "trunk_unavailable", "message": resp.get("Message", "")}
        try:
            event = await asyncio.wait_for(done, timeout=timeout_ms / 1000 + 15)
        except asyncio.TimeoutError:
            self._originates.pop(action_id, None)
            return {"ok": False, "reason": "no_answer"}
        if event.get("Response") == "Success":
            return {"ok": True, "channel": event.get("Channel", ""), "uniqueid": event.get("Uniqueid", "")}
        reason = self.REASONS.get(event.get("Reason", ""), "failed")
        return {"ok": False, "reason": reason, "raw_reason": event.get("Reason", "")}


def main() -> None:
    try:
        asyncio.run(Bridge().run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
