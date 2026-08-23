# backend/api/translate_ws.py
"""
WebSocket роутер для OpenAI Realtime Translation API.
Переводит речь в реальном времени (gpt-realtime-translate).

✅ v1.0: Initial Translate WebSocket router
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from sqlalchemy.orm import Session
import traceback

from backend.core.logging import get_logger
from backend.db.session import get_db
from backend.websockets.handler_translate import handle_translate_connection

logger = get_logger(__name__)

router = APIRouter()


@router.websocket("/ws/translate/{assistant_id}")
async def translate_websocket_endpoint(
    websocket: WebSocket,
    assistant_id: str,
    db: Session = Depends(get_db)
):
    """
    🌐 WebSocket эндпоинт для перевода речи (OpenAI Realtime Translation).

    Клиент шлёт PCM16 24kHz аудио (base64), получает переведённое аудио
    и транскрипты исходника/перевода.

    Example:
        wss://voksyai.online/ws/translate/550e8400-e29b-41d4-a716-446655440000
    """
    try:
        logger.info(f"[TRANSLATE-WS] New connection: assistant_id={assistant_id}")
        await handle_translate_connection(websocket, assistant_id, db)
    except WebSocketDisconnect:
        logger.info(f"[TRANSLATE-WS] Client disconnected: assistant_id={assistant_id}")
    except Exception as e:
        logger.error(f"[TRANSLATE-WS] Error for assistant {assistant_id}: {e}")
        logger.error(f"[TRANSLATE-WS] Traceback: {traceback.format_exc()}")
        try:
            await websocket.close(code=1011, reason="Internal server error")
        except Exception:
            pass
