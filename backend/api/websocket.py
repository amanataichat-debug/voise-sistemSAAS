# backend/api/websocket.py
"""
WebSocket-эндпоинты OpenAI-ассистентов.

Транспорт — OpenAI GPT-Live (gpt-live-1, full-duplex), хендлер
backend/websockets/handler_live.py. Тот же хендлер обслуживает телефонию
(SIP_HANDLERS["openai"] в api/sip_gateway.py).

Роутер регистрируется в app.py ПОСЛЕ gemini_ws / translate_ws / fish_ws /
sip_gateway: иначе /ws/{assistant_id} перехватит их пути.
"""

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from backend.core.logging import get_logger
from backend.db.session import get_db
from backend.websockets.handler_live import (
    LIVE_AUDIO_RATE,
    active_live_connections,
    handle_live_websocket_connection,
)
from backend.websockets.live_client import LIVE_DELEGATION_MODEL, LIVE_MODEL, LIVE_VOICES

logger = get_logger(__name__)
router = APIRouter()

API_VERSION = "4.0.0"


async def _run(websocket: WebSocket, assistant_id: str, db: Session, label: str) -> None:
    client_id = id(websocket)
    logger.info(f"[{label}] new connection {client_id} for assistant {assistant_id} ({LIVE_MODEL})")
    try:
        await handle_live_websocket_connection(websocket, assistant_id, db)
    except WebSocketDisconnect:
        logger.info(f"[{label}] client {client_id} disconnected")
    except Exception as e:
        logger.error(f"[{label}] error for client {client_id}: {type(e).__name__}: {e}")
        try:
            await websocket.send_json({
                "type": "error",
                "error": {"code": "server_error", "message": "Server error occurred", "model": LIVE_MODEL},
            })
        except Exception:
            pass


@router.websocket("/ws/{assistant_id}")
async def websocket_endpoint(websocket: WebSocket, assistant_id: str, db: Session = Depends(get_db)):
    """Голосовой WebSocket ассистента (протокол виджета, см. handler_live.py)."""
    if assistant_id == "llm-stream":
        logger.error("[LIVE-WS] ROUTE COLLISION: /ws/llm-stream caught by /ws/{assistant_id}! Router order fix not deployed?")
    await _run(websocket, assistant_id, db, "LIVE-WS")


@router.websocket("/ws/demo")
async def demo_websocket_endpoint(websocket: WebSocket, db: Session = Depends(get_db)):
    """Публичный демо-ассистент без авторизации (первый is_public ассистент)."""
    await _run(websocket, "demo", db, "LIVE-WS-DEMO")


@router.get("/ws/status")
async def websocket_status():
    """Состояние голосового API OpenAI."""
    return {
        "status": "operational",
        "version": API_VERSION,
        "model": LIVE_MODEL,
        "backend_model": LIVE_DELEGATION_MODEL,
        "handler": "handler_live.py",
        "active_connections": sum(len(v) for v in active_live_connections.values()),
        "endpoints": {
            "production": {"path": "/ws/{assistant_id}", "model": LIVE_MODEL, "status": "stable"},
            "production_demo": {"path": "/ws/demo", "model": LIVE_MODEL, "status": "stable"},
        },
        "features": {
            "full_duplex": True,
            "interruption": {"enabled": True, "method": "model_controlled",
                             "description": "Модель сама слушает во время речи и останавливается при перебивании"},
            "audio": {"format": "pcm16", "sample_rate": LIVE_AUDIO_RATE, "channels": 1, "output_pacing": "realtime"},
            "functions": {"enabled": True, "executed_by": "delegation.responses backend"},
            "vision": {"enabled": False, "reason": f"{LIVE_MODEL} has no image input"},
            "voices": LIVE_VOICES,
        },
    }


@router.get("/ws/info")
async def websocket_info():
    """Краткая документация протокола для интеграторов."""
    return {
        "title": "Voksy AI WebSocket API",
        "version": API_VERSION,
        "description": f"Real-time voice assistants on OpenAI {LIVE_MODEL} (full-duplex)",
        "authentication": {
            "method": "assistant_id",
            "description": "Pass assistant ID in WebSocket URL path",
            "demo_mode": "Use /ws/demo for unauthenticated testing",
        },
        "usage": {
            "production": {"url": "wss://your-domain.com/ws/{assistant_id}"},
            "demo": {"url": "wss://your-domain.com/ws/demo"},
        },
        "client_libraries": {"javascript": {"widget": "/static/widget.js", "version": "5.0"}},
        "protocol": {
            "transport": "WebSocket",
            "audio_format": "PCM16",
            "sample_rate": f"{LIVE_AUDIO_RATE} Hz",
            "encoding": "Base64",
            "notes": [
                "Stream microphone audio continuously, including while the assistant speaks (full-duplex)",
                "Assistant audio arrives at real-time pace: keep a ~200 ms playback buffer",
                "No VAD events: the model manages turns and interruptions itself",
            ],
            "events": {
                "client_to_server": ["input_audio_buffer.append", "input_text", "session.close", "ping"],
                "server_to_client": [
                    "connection_status", "response.audio.delta", "assistant.speech.started", "assistant.speech.ended",
                    "transcript.delta", "function_call.started", "function_call.executing", "function_call.completed",
                    "function_call.error", "usage", "error", "pong",
                ],
            },
        },
        "support": {"documentation": "https://voksyai.online", "email": "voksyai@gmail.com"},
    }


@router.get("/ws/health")
async def websocket_health():
    return {"status": "healthy", "service": "websocket-api", "version": API_VERSION, "model": LIVE_MODEL}
