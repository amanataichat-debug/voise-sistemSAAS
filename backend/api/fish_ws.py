# backend/api/fish_ws.py
"""
WebSocket router Fish-ассистентов.

/ws/fish/{assistant_id} — голосовой диалог по протоколу виджета: OpenAI Realtime
ведёт диалог текстом, Fish Audio озвучивает (backend/websockets/handler_fish.py).
Тот же хендлер обслуживает телефонные звонки через SIP-шлюз
(backend/api/sip_gateway.py → HandlerSocket), поэтому виджет и телефон ведут
себя одинаково.

Ключи серверные (OPENAI_API_KEY, FISH_API_KEY), пользовательские не нужны.

ВАЖНО: роутер подключается в app.py ДО websocket.router, иначе
/ws/{assistant_id} перехватит /ws/fish/....
"""

import traceback

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.db.session import get_db
from backend.websockets.handler_fish import handle_fish_websocket_connection

logger = get_logger(__name__)

router = APIRouter()


@router.websocket("/ws/fish/{assistant_id}")
async def fish_websocket_endpoint(
    websocket: WebSocket,
    assistant_id: str,
    db: Session = Depends(get_db),
):
    """🐟 Голосовой диалог с Fish-ассистентом (виджет)."""
    try:
        logger.info(f"[FISH-WS] New connection: assistant_id={assistant_id}")
        await handle_fish_websocket_connection(websocket, assistant_id, db)
    except WebSocketDisconnect:
        logger.info(f"[FISH-WS] Client disconnected: assistant_id={assistant_id}")
    except Exception as e:
        logger.error(f"[FISH-WS] WebSocket error for assistant {assistant_id}: {e}")
        logger.error(f"[FISH-WS] Traceback: {traceback.format_exc()}")
        try:
            await websocket.close(code=1011, reason="Internal server error")
        except Exception:
            pass


@router.get("/fish/health")
async def fish_health_check():
    """Health check Fish-ассистентов: настроены ли серверные ключи."""
    return {
        "status": "ok" if (settings.OPENAI_API_KEY and settings.FISH_API_KEY) else "not_configured",
        "service": "Fish voice assistants",
        "endpoint": "/ws/fish/{assistant_id}",
        "openai_key": bool(settings.OPENAI_API_KEY),
        "fish_key": bool(settings.FISH_API_KEY),
        "upstream": ["wss://api.openai.com/v1/realtime", "wss://api.fish.audio/v1/tts/live"],
    }
