# backend/api/owner_voice_ws.py
"""
/ws/agent-voice — голосовой разговор владельца со своим агентом (оркестратором) через Gemini Live.
Авторизация — JWT кабинета первым сообщением {"type": "auth", "token": …} (не в адресе: адрес попадает в логи), агент — ?agent_id=… (только свой), голос — ?voice=….
Логика — backend/websockets/handler_owner_voice.py, сервис — backend/services/owner_voice.py.

ВАЖНО: роутер подключается в app.py ДО websocket.router, иначе /ws/{assistant_id} перехватит адрес.
"""
import traceback

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from backend.core.logging import get_logger
from backend.db.session import get_db
from backend.websockets.handler_owner_voice import handle_owner_voice_connection

logger = get_logger(__name__)
router = APIRouter()


@router.websocket("/ws/agent-voice")
async def owner_voice_endpoint(websocket: WebSocket, db: Session = Depends(get_db)):
    try:
        await handle_owner_voice_connection(websocket, db)
    except WebSocketDisconnect:
        logger.info("[OWNER-VOICE] client disconnected")
    except Exception as exc:
        logger.error(f"[OWNER-VOICE] websocket error: {exc}\n{traceback.format_exc()}")
        try:
            await websocket.close(code=1011, reason="Internal server error")
        except Exception:
            pass
