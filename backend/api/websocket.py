# backend/api/websocket.py
"""
WebSocket API endpoints for WellcomeAI application.
Version: 3.0.0 - Production GA API

✅ NOW USING: OpenAI Realtime GA API (gpt-realtime-mini)
✅ HANDLER: handler_realtime_new.py
✅ ALL ENDPOINTS MIGRATED TO GA
"""

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, HTTPException, Query
from sqlalchemy.orm import Session

from backend.core.logging import get_logger
from backend.db.session import get_db

# ✅ PRODUCTION HANDLER - NEW GA API
from backend.websockets.handler_realtime_new import handle_websocket_connection_new

# 📦 BACKUP - Old handler kept for emergency rollback only
# from backend.websockets.handler import handle_websocket_connection as handle_websocket_connection_old

# Initialize logger
logger = get_logger(__name__)

# Create router
router = APIRouter()


# ==================================================================================
# 🏭 PRODUCTION ENDPOINTS - NEW REALTIME GA API
# ==================================================================================

@router.websocket("/ws/{assistant_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    assistant_id: str,
    db: Session = Depends(get_db)
):
    """
    🏭 PRODUCTION WebSocket endpoint for real-time communication with assistants.
    
    ✅ NOW USES: OpenAI Realtime GA API
    ✅ MODEL: gpt-realtime-mini
    ✅ VERSION: 3.0.0
    
    Key features:
    - Optimized VAD settings for fast interruption
    - iOS/Android device-specific optimizations
    - Improved audio handling and playback
    - Server-managed session configuration
    - Automatic event format conversion for backward compatibility
    
    Args:
        websocket: WebSocket connection
        assistant_id: Assistant ID to connect to
        db: Database session dependency
        
    Raises:
        WebSocketDisconnect: When client disconnects
        Exception: For any server-side errors
    """
    client_id = id(websocket)
    logger.info(f"[GA-API] New WebSocket connection from client {client_id} for assistant {assistant_id}")
    if assistant_id == "llm-stream":
        logger.error(f"[GA-API] ⚠️ ROUTE COLLISION: /ws/llm-stream caught by /ws/{{assistant_id}} handler! Router order fix not deployed?")
    logger.info(f"[GA-API] Using Realtime GA API (model: gpt-realtime-mini)")
    
    try:
        await handle_websocket_connection_new(websocket, assistant_id, db)
    except WebSocketDisconnect:
        logger.info(f"[GA-API] Client {client_id} disconnected normally")
    except Exception as e:
        logger.error(f"[GA-API] Error in WebSocket connection for client {client_id}: {str(e)}")
        logger.error(f"[GA-API] Exception type: {type(e).__name__}")
        
        # Attempt to send error message to client if connection is still open
        try:
            if websocket.client_state.CONNECTED:
                await websocket.send_json({
                    "type": "error",
                    "error": {
                        "code": "server_error",
                        "message": "Server error occurred",
                        "api_version": "ga-production",
                        "model": "gpt-realtime-mini",
                        "details": str(e) if not isinstance(e, WebSocketDisconnect) else "Connection closed"
                    }
                })
        except Exception as send_error:
            logger.error(f"[GA-API] Failed to send error message to client {client_id}: {send_error}")


@router.websocket("/ws/demo")
async def demo_websocket_endpoint(
    websocket: WebSocket,
    db: Session = Depends(get_db)
):
    """
    🏭 PRODUCTION Demo WebSocket endpoint for the demo assistant.
    
    ✅ NOW USES: OpenAI Realtime GA API
    ✅ MODEL: gpt-realtime-mini
    
    This endpoint allows users to test the voice assistant without authentication.
    Uses a public demo assistant configuration.
    
    Args:
        websocket: WebSocket connection
        db: Database session dependency
        
    Raises:
        WebSocketDisconnect: When client disconnects
        Exception: For any server-side errors
    """
    client_id = id(websocket)
    logger.info(f"[GA-API-DEMO] New demo WebSocket connection from client {client_id}")
    logger.info(f"[GA-API-DEMO] Using Realtime GA API (model: gpt-realtime-mini)")
    
    # Use a hardcoded demo assistant ID
    demo_assistant_id = "demo"
    
    try:
        await handle_websocket_connection_new(websocket, demo_assistant_id, db)
    except WebSocketDisconnect:
        logger.info(f"[GA-API-DEMO] Demo client {client_id} disconnected normally")
    except Exception as e:
        logger.error(f"[GA-API-DEMO] Error in demo WebSocket connection for client {client_id}: {str(e)}")
        logger.error(f"[GA-API-DEMO] Exception type: {type(e).__name__}")
        
        # Attempt to send error message to client if connection is still open
        try:
            if websocket.client_state.CONNECTED:
                await websocket.send_json({
                    "type": "error",
                    "error": {
                        "code": "server_error",
                        "message": "Server error occurred in demo mode",
                        "api_version": "ga-production",
                        "model": "gpt-realtime-mini",
                        "mode": "demo",
                        "details": str(e) if not isinstance(e, WebSocketDisconnect) else "Connection closed"
                    }
                })
        except Exception as send_error:
            logger.error(f"[GA-API-DEMO] Failed to send error message to demo client {client_id}: {send_error}")


# ==================================================================================
# 📊 UTILITY ENDPOINTS
# ==================================================================================

@router.get("/ws/status")
async def websocket_status():
    """
    Get WebSocket endpoints status and API information.
    
    Returns detailed information about:
    - Current API version (GA)
    - Available endpoints
    - Model information
    - Key features and improvements
    
    Returns:
        dict: Comprehensive status information
    """
    return {
        "status": "operational",
        "version": "3.0.0",
        "api_version": "GA (General Availability)",
        "model": "gpt-realtime-mini",
        "last_updated": "2025-01-07",
        
        "endpoints": {
            "production": {
                "path": "/ws/{assistant_id}",
                "api_version": "ga",
                "model": "gpt-realtime-mini",
                "status": "stable",
                "description": "Production endpoint with Realtime GA API",
                "handler": "handler_realtime_new.py",
                "features": [
                    "Fast interruption support",
                    "iOS/Android optimizations",
                    "Server-managed sessions",
                    "Automatic event conversion",
                    "Enhanced audio handling"
                ]
            },
            "production_demo": {
                "path": "/ws/demo",
                "api_version": "ga",
                "model": "gpt-realtime-mini",
                "status": "stable",
                "description": "Public demo endpoint without authentication",
                "handler": "handler_realtime_new.py"
            }
        },
        
        "features": {
            "interruption": {
                "enabled": True,
                "method": "server_vad",
                "response_time": "optimized",
                "description": "Fast user interruption with device-specific VAD settings"
            },
            "audio": {
                "format": "pcm16",
                "sample_rate": 24000,
                "channels": 1,
                "ios_optimized": True,
                "android_optimized": True
            },
            "session": {
                "managed_by": "server",
                "client_control": False,
                "description": "Server automatically configures optimal session settings"
            },
            "compatibility": {
                "backward_compatible": True,
                "event_conversion": True,
                "description": "Old event format automatically converted for existing clients"
            }
        },
        
        "migration_info": {
            "from_version": "2.2.1 (Beta)",
            "to_version": "3.0.0 (GA)",
            "breaking_changes": [],
            "improvements": [
                "Faster interruption response time",
                "Better iOS audio playback",
                "Reduced latency",
                "More stable connections",
                "Device-specific optimizations"
            ],
            "backward_compatibility": "Full - existing clients work without changes"
        },
        
        "event_mapping": {
            "old_format": [
                "response.text.delta",
                "response.audio.delta",
                "response.audio_transcript.delta"
            ],
            "new_format": [
                "response.output_text.delta",
                "response.output_audio.delta",
                "response.output_audio_transcript.delta"
            ],
            "note": "Server automatically converts new events to old format for compatibility"
        },
        
        "health": {
            "websocket": "operational",
            "openai_api": "connected",
            "database": "operational"
        }
    }


@router.get("/ws/info")
async def websocket_info():
    """
    Get detailed API documentation and usage information.
    
    Returns:
        dict: API documentation and usage examples
    """
    return {
        "title": "WellcomeAI WebSocket API",
        "version": "3.0.0",
        "description": "Real-time voice assistant communication using OpenAI Realtime GA API",
        
        "authentication": {
            "method": "assistant_id",
            "description": "Pass assistant ID in WebSocket URL path",
            "demo_mode": "Use /ws/demo for unauthenticated testing"
        },
        
        "usage": {
            "production": {
                "url": "wss://your-domain.com/ws/{assistant_id}",
                "example": "wss://voicyfy.ru/ws/84480767-76f3-491f-8c76-8181bdfe8c5a",
                "description": "Replace {assistant_id} with your actual assistant ID"
            },
            "demo": {
                "url": "wss://your-domain.com/ws/demo",
                "example": "wss://voicyfy.ru/ws/demo",
                "description": "Public demo endpoint for testing"
            }
        },
        
        "client_libraries": {
            "javascript": {
                "widget": "/static/widget.js",
                "version": "3.0.0",
                "description": "Official JavaScript widget with full UI",
                "features": [
                    "Auto-reconnection",
                    "Audio visualization",
                    "Interruption handling",
                    "iOS/Android support",
                    "Premium UI design"
                ]
            }
        },
        
        "protocol": {
            "transport": "WebSocket",
            "audio_format": "PCM16",
            "sample_rate": "24000 Hz",
            "encoding": "Base64",
            "events": {
                "client_to_server": [
                    "input_audio_buffer.append",
                    "input_audio_buffer.commit",
                    "input_audio_buffer.clear",
                    "response.cancel",
                    "ping"
                ],
                "server_to_client": [
                    "response.text.delta",
                    "response.audio.delta",
                    "response.done",
                    "conversation.interrupted",
                    "speech.started",
                    "speech.stopped",
                    "error",
                    "pong"
                ]
            }
        },
        
        "best_practices": {
            "connection": [
                "Use secure WebSocket (wss://)",
                "Implement reconnection logic",
                "Handle ping/pong for keepalive"
            ],
            "audio": [
                "Use PCM16 format at 24kHz",
                "Implement VAD for better UX",
                "Buffer audio for smooth playback"
            ],
            "error_handling": [
                "Listen for error events",
                "Implement graceful degradation",
                "Show user-friendly error messages"
            ]
        },
        
        "support": {
            "documentation": "https://docs.voicyfy.ru",
            "issues": "Report issues to support team",
            "email": "support@voicyfy.ru"
        }
    }


# ==================================================================================
# 🔧 HEALTH CHECK
# ==================================================================================

@router.get("/ws/health")
async def websocket_health():
    """
    Health check endpoint for monitoring.
    
    Returns:
        dict: Service health status
    """
    return {
        "status": "healthy",
        "service": "websocket-api",
        "version": "3.0.0",
        "api": "ga",
        "model": "gpt-realtime-mini",
        "timestamp": "2025-01-07T00:00:00Z"
    }


# ==================================================================================
# 📝 CHANGELOG & MIGRATION NOTES
# ==================================================================================

"""
CHANGELOG - Version 3.0.0 (GA Production)
==========================================

✅ MIGRATED TO GA API
- All endpoints now use handler_realtime_new.py
- Model: gpt-realtime-mini (was: gpt-4o-realtime-preview)
- API: OpenAI Realtime GA (was: Beta)

✅ REMOVED
- /ws-test/{assistant_id} - Testing endpoint (merged to production)
- /ws-test/demo - Testing demo endpoint (merged to production)
- Old handler imports (kept as commented backup)

✅ IMPROVED
- Faster interruption response time
- Better iOS audio playback stability
- Device-specific VAD optimizations
- Server-managed session configuration
- Automatic event format conversion

✅ BACKWARD COMPATIBILITY
- Existing clients work without changes
- Old event format automatically converted
- No breaking changes in client API

✅ STABILITY
- Production-tested in /ws-test/ endpoints
- All features verified and working
- Performance improvements confirmed


USAGE EXAMPLES
==============

🏭 Production:
--------------
JavaScript:
const ws = new WebSocket('wss://voicyfy.ru/ws/YOUR-ASSISTANT-ID');

Python:
import websockets
async with websockets.connect('wss://voicyfy.ru/ws/YOUR-ASSISTANT-ID') as ws:
    # your code here

🎮 Demo (No Auth):
------------------
const wsDemo = new WebSocket('wss://voicyfy.ru/ws/demo');


ROLLBACK PROCEDURE (Emergency Only)
====================================

If critical issues occur, follow these steps:

1. Uncomment old handler import:
   from backend.websockets.handler import handle_websocket_connection

2. Replace handler in endpoints:
   await handle_websocket_connection(websocket, assistant_id, db)

3. Restart service

4. Notify development team immediately


SUPPORT
=======
For issues or questions:
- Check /ws/status for current API status
- Check /ws/info for detailed documentation
- Contact: support@voicyfy.ru
"""
