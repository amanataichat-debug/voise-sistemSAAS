"""
WebSocket-роутер Google Gemini Live и изолированного текстового LLM-стрима.

  * /ws/gemini/{assistant_id} — голосовой Gemini-ассистент (gemini-3.8-live,
    хендлер backend/websockets/handler_gemini.py; тот же хендлер обслуживает
    телефон через SIP_HANDLERS["gemini"]).
  * /ws/llm-stream           — текстовый LLM-стрим (OpenAI-ключ владельца),
    отдельный канал, чтобы текст не мешал голосу.
  * /ws/vox-gemini/{id}      — мост Voximplant ↔ Gemini: МЁРТВЫЙ КОД (Voximplant не используется).
  * /gemini/health, /gemini/info

Роутер регистрируется в app.py ДО websocket.router: иначе /ws/llm-stream и
/ws/gemini/* перехватит /ws/{assistant_id}.

Удалены (сентябрь 2026, переход на gemini-3.8-live): /ws/gemini-31/{id}
(gemini-3.1-flash-live-preview) и /ws/gemini-browser/{id} (browser-агент).
"""

import traceback
from typing import Optional

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from backend.core.logging import get_logger
from backend.db.session import get_db
from backend.websockets import handle_gemini_websocket_connection
from backend.websockets.gemini_client import GEMINI_LIVE_MODEL, GEMINI_TOOL_SCHEDULING
from backend.websockets.openai_client_streaming import handle_openai_streaming_websocket
from backend.websockets.handler_vox_gemini import handle_vox_gemini_websocket  # dead code (Voximplant)

logger = get_logger(__name__)
router = APIRouter()


@router.websocket("/ws/gemini/{assistant_id}")
async def gemini_websocket_endpoint(websocket: WebSocket, assistant_id: str, db: Session = Depends(get_db)):
    """
    Голосовой WebSocket Gemini-ассистента (протокол виджета).

    PCM16 16 кГц на вход, 24 кГц на выход, VAD Gemini, функции (асинхронные),
    транскрипция, запись диалогов в gemini_conversations, Google Sheets.
    assistant_id — UUID ассистента или "demo".
    """
    try:
        logger.info(f"[GEMINI-WS] New connection: assistant_id={assistant_id} ({GEMINI_LIVE_MODEL})")
        await handle_gemini_websocket_connection(websocket=websocket, assistant_id=assistant_id, db=db)
    except WebSocketDisconnect:
        logger.info(f"[GEMINI-WS] Client disconnected: assistant_id={assistant_id}")
    except Exception as e:
        logger.error(f"[GEMINI-WS] WebSocket error for assistant {assistant_id}: {e}")
        logger.error(f"[GEMINI-WS] Traceback: {traceback.format_exc()}")
        try:
            await websocket.close(code=1011, reason="Internal server error")
        except Exception:
            pass


@router.websocket("/ws/vox-gemini/{assistant_id}")
async def vox_gemini_websocket_endpoint(
    websocket: WebSocket,
    assistant_id: str,
    caller: Optional[str] = Query(None, description="Caller phone number"),
    call_id: Optional[str] = Query(None, description="Voximplant call ID"),
    db: Session = Depends(get_db),
):
    """МЁРТВЫЙ КОД: мост Voximplant ↔ Gemini. Voximplant отключён, телефония идёт через SIP-шлюз."""
    try:
        logger.warning(f"[VOX-GEMINI-WS] dead endpoint hit: assistant={assistant_id} caller={caller} call_id={call_id}")
        await handle_vox_gemini_websocket(websocket=websocket, assistant_id=assistant_id, db=db,
                                          caller_number=caller, call_id=call_id)
    except WebSocketDisconnect:
        logger.info(f"[VOX-GEMINI-WS] disconnected: assistant_id={assistant_id}")
    except Exception as e:
        logger.error(f"[VOX-GEMINI-WS] Error for assistant {assistant_id}: {e}")
        try:
            await websocket.close(code=1011, reason="Internal server error")
        except Exception:
            pass


@router.websocket("/ws/llm-stream")
async def llm_stream_websocket_endpoint(
    websocket: WebSocket,
    assistant_id: Optional[str] = Query(None, description="Gemini Assistant ID to get OpenAI key from user"),
    db: Session = Depends(get_db),
):
    """
    Изолированный текстовый LLM-стрим. OpenAI-ключ берётся по цепочке
    assistant_id → GeminiAssistantConfig → User.openai_api_key.

    Client → Server: {"type": "llm.query", "query": "...", "request_id": "req_123"}
    Server → Client: llm.stream.start / llm.stream.delta / llm.stream.done / llm.stream.error
    """
    try:
        logger.info(f"[LLM-STREAM-WS] New connection (assistant_id={assistant_id})")
        await handle_openai_streaming_websocket(websocket=websocket, assistant_id=assistant_id, db=db)
    except WebSocketDisconnect:
        logger.info("[LLM-STREAM-WS] Client disconnected")
    except Exception as e:
        logger.error(f"[LLM-STREAM-WS] Error: {e}")
        logger.error(f"[LLM-STREAM-WS] Traceback: {traceback.format_exc()}")
        try:
            await websocket.close(code=1011, reason="Internal server error")
        except Exception:
            pass


@router.get("/gemini/health")
async def gemini_health_check():
    return {
        "status": "healthy",
        "service": "gemini_websocket",
        "version": "4.0",
        "model": GEMINI_LIVE_MODEL,
        "llm_model": "gpt-4o-mini",
        "features": [
            "real_time_audio", "automatic_vad", "async_function_calling", "screen_context",
            "google_sheets_logging", "isolated_llm_streaming", "user_api_keys", "sip_telephony",
        ],
    }


@router.get("/gemini/info")
async def gemini_info():
    return {
        "service": "Google Gemini Live API",
        "version": "4.0",
        "models": {"voice": GEMINI_LIVE_MODEL, "llm_streaming": "gpt-4o-mini"},
        "audio": {
            "input_format": "PCM 16kHz mono 16-bit",
            "output_format": "PCM 24kHz mono 16-bit",
            "vad": "automatic (Gemini, profile from GEMINI_VAD_*)",
        },
        "features": {
            "function_calling": f"asynchronous (NON_BLOCKING, scheduling={GEMINI_TOOL_SCHEDULING})",
            "thinking": "not available on gemini-3.8-live (fast model only)",
            "screen_context": "supported (silent mode)",
            "interruptions": "automatic via VAD",
            "voices": "30 HD voices",
            "session_limits": "audio-only session 15 min, connection ~10 min (no context compression)",
            "api_keys": "from User model (not environment)",
        },
        "endpoints": {
            "websocket_voice": "/ws/gemini/{assistant_id}",
            "websocket_llm": "/ws/llm-stream?assistant_id={assistant_id}",
            "health": "/gemini/health",
            "info": "/gemini/info",
        },
    }
