# backend/api/eleven_ws.py
"""
WebSocket router Eleven-ассистентов.

/ws/eleven/{assistant_id} — голосовой диалог по протоколу виджета: OpenAI Realtime
ведёт диалог текстом, ElevenLabs озвучивает (backend/websockets/handler_eleven.py).
Тот же хендлер обслуживает телефонные звонки через SIP-шлюз.

Ключи серверные (OPENAI_API_KEY, ELEVENLABS_API_KEY).
ВАЖНО: роутер подключается в app.py ДО websocket.router, иначе
/ws/{assistant_id} перехватит /ws/eleven/....
"""

import traceback

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.db.session import get_db
from backend.websockets.handler_eleven import handle_eleven_websocket_connection

logger = get_logger(__name__)
router = APIRouter()


@router.websocket("/ws/eleven/{assistant_id}")
async def eleven_websocket_endpoint(websocket: WebSocket, assistant_id: str, db: Session = Depends(get_db)):
    """Голосовой диалог с Eleven-ассистентом (виджет)."""
    try:
        logger.info(f"[ELEVEN-WS] New connection: assistant_id={assistant_id}")
        await handle_eleven_websocket_connection(websocket, assistant_id, db)
    except WebSocketDisconnect:
        logger.info(f"[ELEVEN-WS] Client disconnected: assistant_id={assistant_id}")
    except Exception as e:
        logger.error(f"[ELEVEN-WS] WebSocket error for assistant {assistant_id}: {e}")
        logger.error(f"[ELEVEN-WS] Traceback: {traceback.format_exc()}")
        try:
            await websocket.close(code=1011, reason="Internal server error")
        except Exception:
            pass


@router.get("/eleven/health")
async def eleven_health_check():
    return {
        "status": "ok" if (settings.OPENAI_API_KEY and settings.ELEVENLABS_API_KEY) else "not_configured",
        "service": "Eleven voice assistants (OpenAI Realtime text + ElevenLabs TTS)",
        "endpoint": "/ws/eleven/{assistant_id}",
        "openai_key": bool(settings.OPENAI_API_KEY),
        "elevenlabs_key": bool(settings.ELEVENLABS_API_KEY),
        "upstream": ["wss://api.openai.com/v1/realtime", "wss://api.elevenlabs.io/v1/text-to-dialogue/multi-stream-input"],
    }
