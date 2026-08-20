# backend/api/grok_ws.py
"""
WebSocket router for xAI Grok Voice Agent API integration.
Handles real-time voice conversations with Grok assistants.

🚀 PRODUCTION VERSION 1.0
✅ Complete WebSocket handling
✅ Database integration
✅ Subscription validation
✅ Error handling
✅ Telephony support (Voximplant)
✅ Multiple voices (Ara, Rex, Sal, Eve, Leo)
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Query
from sqlalchemy.orm import Session
import traceback
from typing import Optional

from backend.core.logging import get_logger
from backend.db.session import get_db
from backend.websockets.handler_grok import handle_grok_websocket_connection

logger = get_logger(__name__)

router = APIRouter()


# =============================================================================
# 🎤 WEB WIDGET - Standard WebSocket endpoint
# =============================================================================

@router.websocket("/ws/grok/{assistant_id}")
async def grok_websocket_endpoint(
    websocket: WebSocket,
    assistant_id: str,
    db: Session = Depends(get_db)
):
    """
    🎤 WebSocket endpoint for xAI Grok Voice Agent API.
    
    Uses PCM audio format at 24kHz for web applications.
    
    Features:
    - Real-time bidirectional audio streaming (PCM 24kHz)
    - Server-side Voice Activity Detection (VAD)
    - Function calling support (including native web_search, x_search)
    - Multiple voices: Ara, Rex, Sal, Eve, Leo
    - Google Sheets logging
    - Database conversation storage
    - Subscription validation
    
    Args:
        websocket: WebSocket connection from client
        assistant_id: UUID of Grok assistant or "demo" for public assistant
        db: Database session
    
    Example:
        wss://api.yourserver.com/ws/grok/550e8400-e29b-41d4-a716-446655440000
    """
    try:
        logger.info(f"[GROK-WS] New connection attempt: assistant_id={assistant_id}")
        logger.info(f"[GROK-WS] Mode: Web Widget (PCM 24kHz)")
        
        await handle_grok_websocket_connection(
            websocket=websocket,
            assistant_id=assistant_id,
            db=db,
            is_telephony=False,
            sample_rate=24000
        )
        
    except WebSocketDisconnect:
        logger.info(f"[GROK-WS] Client disconnected: assistant_id={assistant_id}")
    except Exception as e:
        logger.error(f"[GROK-WS] WebSocket error for assistant {assistant_id}: {e}")
        logger.error(f"[GROK-WS] Traceback: {traceback.format_exc()}")
        
        try:
            await websocket.close(code=1011, reason="Internal server error")
        except:
            pass


# =============================================================================
# 📞 TELEPHONY - Voximplant endpoint (G.711)
# =============================================================================

@router.websocket("/ws/grok/voximplant/{assistant_id}")
async def grok_voximplant_websocket_endpoint(
    websocket: WebSocket,
    assistant_id: str,
    db: Session = Depends(get_db)
):
    """
    📞 WebSocket endpoint for Grok Voice + Voximplant telephony.
    
    Uses G.711 μ-law (PCMU) audio format at 8kHz for telephony.
    
    Features:
    - G.711 μ-law audio format (telephony standard)
    - 8kHz sample rate
    - Server-side VAD
    - All function calling features
    - Call metadata tracking (caller_number, call_direction)
    - Database conversation storage with telephony info
    
    Args:
        websocket: WebSocket connection from Voximplant
        assistant_id: UUID of Grok assistant
        db: Database session
    
    Example:
        wss://api.yourserver.com/ws/grok/voximplant/550e8400-e29b-41d4-a716-446655440000
    
    Voximplant VoxEngine Integration:
        const ws = new WebSocket("wss://your-server/ws/grok/voximplant/" + assistantId);
        call.sendMediaTo(ws);
        ws.sendMediaTo(call);
    """
    try:
        logger.info(f"[GROK-VOXIMPLANT-WS] New telephony connection: assistant_id={assistant_id}")
        logger.info(f"[GROK-VOXIMPLANT-WS] Mode: Telephony (G.711 μ-law, 8kHz)")
        
        await handle_grok_websocket_connection(
            websocket=websocket,
            assistant_id=assistant_id,
            db=db,
            is_telephony=True,
            sample_rate=8000
        )
        
    except WebSocketDisconnect:
        logger.info(f"[GROK-VOXIMPLANT-WS] Voximplant disconnected: assistant_id={assistant_id}")
    except Exception as e:
        logger.error(f"[GROK-VOXIMPLANT-WS] WebSocket error for assistant {assistant_id}: {e}")
        logger.error(f"[GROK-VOXIMPLANT-WS] Traceback: {traceback.format_exc()}")
        
        try:
            await websocket.close(code=1011, reason="Internal server error")
        except:
            pass


# =============================================================================
# 🎛️ CUSTOM SAMPLE RATE - Flexible endpoint
# =============================================================================

@router.websocket("/ws/grok/custom/{assistant_id}")
async def grok_custom_websocket_endpoint(
    websocket: WebSocket,
    assistant_id: str,
    sample_rate: int = Query(default=24000, ge=8000, le=48000, description="Audio sample rate"),
    telephony: bool = Query(default=False, description="Use telephony mode (G.711)"),
    db: Session = Depends(get_db)
):
    """
    🎛️ WebSocket endpoint with custom audio settings.
    
    Allows specifying custom sample rate and audio mode.
    
    Query Parameters:
        sample_rate: Audio sample rate (8000-48000, default 24000)
        telephony: Use G.711 format for telephony (default false)
    
    Supported Sample Rates (PCM only):
        - 8000 Hz: Telephone quality
        - 16000 Hz: Wideband (good for speech)
        - 21050 Hz: Standard
        - 24000 Hz: High quality (default)
        - 32000 Hz: Very high
        - 44100 Hz: CD quality
        - 48000 Hz: Professional/Browser
    
    Args:
        websocket: WebSocket connection
        assistant_id: UUID of Grok assistant
        sample_rate: Audio sample rate
        telephony: Enable telephony mode
        db: Database session
    
    Example:
        wss://api.yourserver.com/ws/grok/custom/xxx?sample_rate=16000
        wss://api.yourserver.com/ws/grok/custom/xxx?telephony=true
    """
    try:
        logger.info(f"[GROK-CUSTOM-WS] New custom connection: assistant_id={assistant_id}")
        logger.info(f"[GROK-CUSTOM-WS] Sample rate: {sample_rate}, Telephony: {telephony}")
        
        await handle_grok_websocket_connection(
            websocket=websocket,
            assistant_id=assistant_id,
            db=db,
            is_telephony=telephony,
            sample_rate=sample_rate
        )
        
    except WebSocketDisconnect:
        logger.info(f"[GROK-CUSTOM-WS] Client disconnected: assistant_id={assistant_id}")
    except Exception as e:
        logger.error(f"[GROK-CUSTOM-WS] WebSocket error for assistant {assistant_id}: {e}")
        logger.error(f"[GROK-CUSTOM-WS] Traceback: {traceback.format_exc()}")
        
        try:
            await websocket.close(code=1011, reason="Internal server error")
        except:
            pass


# =============================================================================
# 📊 HEALTH & INFO ENDPOINTS
# =============================================================================

@router.get("/grok/health")
async def grok_health_check():
    """
    Health check endpoint for Grok WebSocket service.
    """
    return {
        "status": "healthy",
        "service": "grok_websocket",
        "version": "1.0",
        "provider": "xAI",
        "api_endpoint": "wss://api.x.ai/v1/realtime",
        "features": [
            "real_time_audio",
            "server_vad",
            "function_calling",
            "web_search",
            "x_search",
            "file_search",
            "multiple_voices",
            "telephony_support",
            "google_sheets_logging"
        ]
    }


@router.get("/grok/info")
async def grok_info():
    """
    Get information about Grok Voice Agent API integration.
    """
    return {
        "service": "xAI Grok Voice Agent API",
        "version": "1.0",
        "api_endpoint": "wss://api.x.ai/v1/realtime",
        
        "audio": {
            "web": {
                "format": "audio/pcm",
                "sample_rates": [8000, 16000, 21050, 24000, 32000, 44100, 48000],
                "default_rate": 24000,
                "channels": "mono",
                "encoding": "Base64 PCM16"
            },
            "telephony": {
                "format": "audio/pcmu",
                "sample_rate": 8000,
                "description": "G.711 μ-law for Voximplant"
            }
        },
        
        "voices": [
            {"id": "Ara", "gender": "Female", "tone": "Warm, friendly"},
            {"id": "Rex", "gender": "Male", "tone": "Confident, clear"},
            {"id": "Sal", "gender": "Neutral", "tone": "Smooth, balanced"},
            {"id": "Eve", "gender": "Female", "tone": "Energetic, upbeat"},
            {"id": "Leo", "gender": "Male", "tone": "Authoritative, strong"}
        ],
        
        "native_tools": {
            "web_search": "Search the web for current information",
            "x_search": "Search X (Twitter) for posts",
            "file_search": "Search through document collections"
        },
        
        "features": {
            "vad": "server_vad (automatic)",
            "function_calling": "supported",
            "interruptions": "automatic via VAD",
            "telephony": "G.711 μ-law support"
        },
        
        "endpoints": {
            "websocket_web": "/ws/grok/{assistant_id}",
            "websocket_voximplant": "/ws/grok/voximplant/{assistant_id}",
            "websocket_custom": "/ws/grok/custom/{assistant_id}?sample_rate=X",
            "health": "/grok/health",
            "info": "/grok/info"
        },
        
        "authentication": {
            "method": "xAI API key",
            "header": "Authorization: Bearer {api_key}",
            "source": "User.grok_api_key"
        }
    }


@router.get("/grok/voices")
async def grok_voices():
    """
    Get list of available Grok voices with details.
    """
    return {
        "voices": [
            {
                "id": "Ara",
                "name": "Ara",
                "gender": "Female",
                "tone": "Warm, friendly",
                "description": "Default voice, balanced and conversational",
                "best_for": "General purpose, customer service"
            },
            {
                "id": "Rex",
                "name": "Rex",
                "gender": "Male",
                "tone": "Confident, clear",
                "description": "Professional and articulate",
                "best_for": "Business applications, presentations"
            },
            {
                "id": "Sal",
                "name": "Sal",
                "gender": "Neutral",
                "tone": "Smooth, balanced",
                "description": "Versatile voice suitable for various contexts",
                "best_for": "Diverse audiences, accessibility"
            },
            {
                "id": "Eve",
                "name": "Eve",
                "gender": "Female",
                "tone": "Energetic, upbeat",
                "description": "Engaging and enthusiastic",
                "best_for": "Interactive experiences, entertainment"
            },
            {
                "id": "Leo",
                "name": "Leo",
                "gender": "Male",
                "tone": "Authoritative, strong",
                "description": "Decisive and commanding",
                "best_for": "Instructional content, announcements"
            }
        ],
        "default": "Ara",
        "note": "All voices support multiple languages"
    }
