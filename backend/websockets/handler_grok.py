# backend/websockets/handler_grok.py
"""
🚀 PRODUCTION VERSION 1.1 - xAI Grok Voice Agent API Handler
WebSocket endpoint: wss://api.x.ai/v1/realtime

✨ Features:
✅ Full xAI Grok Voice Agent API support
✅ Async function execution (non-blocking)
✅ Multiple voices: Ara, Rex, Sal, Eve, Leo
✅ Audio formats: PCM (8-48kHz), G.711 μ-law, G.711 A-law
✅ Server-side VAD support
✅ Google Sheets logging
✅ Database conversation logging
✅ Telephony support (Voximplant)

Based on OpenAI Realtime handler architecture for Voicyfy platform.

CHANGELOG v1.1:
- Fixed: AssistantConfig -> GrokAssistantConfig (correct table lookup)
"""

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
import json
import asyncio
import uuid
import base64
import traceback
import time
import sys
from typing import Dict, List
from websockets.exceptions import ConnectionClosed

from backend.core.logging import get_logger
from backend.core.config import settings
from backend.models.user import User
from backend.models.grok_assistant import GrokAssistantConfig
from backend.models.conversation import Conversation
from backend.utils.audio_utils import base64_to_audio_buffer
from backend.websockets.grok_client import GrokVoiceClient, map_voice_to_grok
from backend.services.google_sheets_service import GoogleSheetsService
from backend.services.conversation_service import ConversationService
from backend.functions import execute_function, normalize_function_name

logger = get_logger(__name__)

# Force immediate log flushing
import logging
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    force=True
)

# Active connections
active_grok_connections: Dict[str, List[WebSocket]] = {}

# Debug mode
ENABLE_DETAILED_LOGGING = True


def log_to_render(message: str, level: str = "INFO"):
    """Force log to stdout immediately"""
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    log_msg = f"{timestamp} - [GROK-HANDLER v1.1] {level} - {message}"
    print(log_msg, flush=True)
    if level == "ERROR":
        logger.error(message)
    elif level == "WARNING":
        logger.warning(message)
    else:
        logger.info(message)


# ==================== ASYNC HELPERS ====================

async def async_save_to_database(db_session, conversation_record_id: str, user_message: str = None, assistant_message: str = None, function_summary: str = None):
    """Async database save (non-blocking)"""
    try:
        if not db_session or not conversation_record_id:
            return
        
        log_to_render(f"💾 [ASYNC] Saving to database (conversation: {conversation_record_id})")
        
        conv = db_session.query(Conversation).get(uuid.UUID(conversation_record_id))
        if not conv:
            log_to_render(f"⚠️ [ASYNC] Conversation record not found", "WARNING")
            return
        
        if function_summary:
            conv.assistant_message = function_summary
        elif assistant_message and not conv.assistant_message:
            conv.assistant_message = assistant_message
            
        if user_message and not conv.user_message:
            conv.user_message = user_message
        
        db_session.commit()
        log_to_render(f"✅ [ASYNC] Database save successful")
        
    except Exception as e:
        log_to_render(f"❌ [ASYNC] Database save error: {e}", "ERROR")


async def async_save_to_google_sheets(sheet_id: str, user_message: str, assistant_message: str, function_result=None, conversation_id: str = None, context: str = ""):
    """Async Google Sheets save (non-blocking)"""
    try:
        if not sheet_id:
            return
        
        log_to_render(f"📊 [ASYNC] Logging to Google Sheets ({context})")
        
        sheets_start = time.time()
        sheets_result = await GoogleSheetsService.log_conversation(
            sheet_id=sheet_id,
            user_message=user_message,
            assistant_message=assistant_message,
            function_result=function_result,
            conversation_id=conversation_id
        )
        sheets_time = time.time() - sheets_start
        
        if sheets_result:
            log_to_render(f"✅ [ASYNC] Google Sheets logged ({sheets_time:.3f}s)")
        else:
            log_to_render(f"❌ [ASYNC] Google Sheets logging failed", "WARNING")
            
    except Exception as e:
        log_to_render(f"❌ [ASYNC] Google Sheets error: {e}", "ERROR")


async def async_save_dialog_to_db(db_session, assistant_id: str, user_message: str, assistant_message: str, session_id: str):
    """Async dialog save as separate DB record"""
    try:
        if not db_session or not user_message or not assistant_message:
            return
        
        log_to_render(f"💾 [ASYNC] Saving dialog as separate DB record")
        
        await ConversationService.save_conversation(
            db=db_session,
            assistant_id=assistant_id,
            user_message=user_message,
            assistant_message=assistant_message,
            session_id=session_id,
            caller_number=None,
            tokens_used=0
        )
        
        log_to_render(f"✅ [ASYNC] Dialog saved")
        
    except Exception as e:
        log_to_render(f"❌ [ASYNC] Dialog save error: {e}", "ERROR")


# ==================== ASYNC FUNCTION EXECUTION ====================

async def execute_and_send_function_result(
    grok_client: 'GrokVoiceClient',
    websocket: WebSocket,
    call_id: str,
    function_name: str,
    arguments: dict,
    context: dict,
    user_transcript: str = ""
):
    """
    Execute function in background WITHOUT blocking assistant speech.
    
    This is the key feature - assistant continues speaking while function executes.
    """
    execution_start = time.time()
    
    try:
        log_to_render(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        log_to_render(f"🔥 [ASYNC FUNCTION] Background execution started")
        log_to_render(f"   Function: {function_name}")
        log_to_render(f"   Call ID: {call_id}")
        log_to_render(f"   Arguments: {json.dumps(arguments, ensure_ascii=False)[:200]}")
        log_to_render(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        
        # Execute function
        result = await execute_function(
            name=function_name,
            arguments=arguments,
            context=context
        )
        
        execution_time = time.time() - execution_start
        
        log_to_render(f"✅ [ASYNC FUNCTION] Execution completed in {execution_time:.3f}s")
        log_to_render(f"   Result preview: {str(result)[:200]}...")
        
        # Fast LLM result display for query_llm
        if function_name == "query_llm":
            log_to_render(f"⚡ [ASYNC] QUERY_LLM - sending immediate result to frontend")
            
            llm_response_content = ""
            llm_model = "grok"
            
            if isinstance(result, dict):
                llm_response_content = result.get("full_response", result.get("response", result.get("answer", str(result))))
                llm_model = result.get("model_used", result.get("model", "grok"))
            else:
                llm_response_content = str(result)
            
            await websocket.send_json({
                "type": "llm_result",
                "content": llm_response_content,
                "model": llm_model,
                "function": function_name,
                "execution_time": execution_time,
                "timestamp": time.time(),
                "async_execution": True
            })
        
        # Async background logging
        if grok_client.db_session and grok_client.conversation_record_id:
            function_summary = f"[Function: {function_name}] Result: {json.dumps(result, ensure_ascii=False)[:200]}"
            
            asyncio.create_task(
                async_save_to_database(
                    grok_client.db_session,
                    grok_client.conversation_record_id,
                    user_transcript if user_transcript else None,
                    None,
                    function_summary
                )
            )
        
        # Google Sheets logging
        if grok_client.assistant_config and grok_client.assistant_config.google_sheet_id:
            asyncio.create_task(
                async_save_to_google_sheets(
                    sheet_id=grok_client.assistant_config.google_sheet_id,
                    user_message=user_transcript or f"[Function call: {function_name}]",
                    assistant_message=f"[Function executed: {function_name}]",
                    function_result=result,
                    conversation_id=grok_client.conversation_record_id,
                    context="Grok Async Function"
                )
            )
        
        # Send result to Grok
        log_to_render(f"📤 [ASYNC] Sending function result to Grok...")
        
        delivery_status = await grok_client.send_function_result(call_id, result)
        
        if delivery_status["success"]:
            log_to_render(f"✅ [ASYNC] Function result delivered to Grok")
            
            await websocket.send_json({
                "type": "function_call.completed",
                "function": function_name,
                "call_id": call_id,
                "result": result,
                "execution_time": execution_time,
                "async_execution": True
            })
        else:
            log_to_render(f"❌ [ASYNC] Function result delivery FAILED", "ERROR")
            
            await websocket.send_json({
                "type": "function_call.delivery_error",
                "call_id": call_id,
                "error": delivery_status['error'],
                "async_execution": True
            })
        
    except Exception as e:
        log_to_render(f"❌ [ASYNC FUNCTION] Execution ERROR: {e}", "ERROR")
        log_to_render(f"Traceback: {traceback.format_exc()}", "ERROR")
        
        await websocket.send_json({
            "type": "function_call.error",
            "function": function_name,
            "call_id": call_id,
            "error": str(e),
            "async_execution": True
        })


# ==================== MAIN HANDLER ====================

async def handle_grok_websocket_connection(
    websocket: WebSocket,
    assistant_id: str,
    db: Session,
    is_telephony: bool = False,
    sample_rate: int = 24000
) -> None:
    """
    🚀 PRODUCTION v1.1 - Main WebSocket handler for Grok Voice Agent API
    
    Args:
        websocket: FastAPI WebSocket connection
        assistant_id: Assistant ID from database
        db: SQLAlchemy database session
        is_telephony: True for Voximplant telephony (uses G.711)
        sample_rate: Audio sample rate (default 24000Hz)
    """
    client_id = str(uuid.uuid4())
    grok_client = None
    connection_start = time.time()
    
    log_to_render(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    log_to_render(f"🚀 NEW GROK CONNECTION INITIATED (v1.1)")
    log_to_render(f"   Client ID: {client_id}")
    log_to_render(f"   Assistant ID: {assistant_id}")
    log_to_render(f"   Telephony: {is_telephony}")
    log_to_render(f"   Sample Rate: {sample_rate}")
    log_to_render(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    
    user_agent = ""
    if hasattr(websocket, 'headers'):
        user_agent = websocket.headers.get('user-agent', '')
        log_to_render(f"📱 User-Agent: {user_agent[:100]}")

    try:
        await websocket.accept()
        log_to_render(f"✅ WebSocket accepted for client {client_id}")

        # Register connection
        active_grok_connections.setdefault(assistant_id, []).append(websocket)
        log_to_render(f"📝 Active Grok connections for {assistant_id}: {len(active_grok_connections.get(assistant_id, []))}")

        # Load assistant from GrokAssistantConfig table
        log_to_render(f"🔍 Loading Grok assistant: {assistant_id}")
        assistant = None
        
        if assistant_id == "demo":
            # Demo mode - try to find public assistant first
            assistant = db.query(GrokAssistantConfig).filter(GrokAssistantConfig.is_public.is_(True)).first()
            if not assistant:
                assistant = db.query(GrokAssistantConfig).first()
            if assistant:
                log_to_render(f"✅ Demo assistant loaded: {getattr(assistant, 'name', 'unnamed')}")
        else:
            # Normal mode - load by UUID
            try:
                uuid_obj = uuid.UUID(assistant_id)
                assistant = db.query(GrokAssistantConfig).get(uuid_obj)
                if assistant:
                    log_to_render(f"✅ Assistant loaded by UUID: {getattr(assistant, 'name', assistant_id)}")
            except ValueError:
                # Fallback: try string comparison
                log_to_render(f"⚠️ Invalid UUID format, trying string match...")
                assistant = db.query(GrokAssistantConfig).filter(
                    GrokAssistantConfig.id.cast(str) == assistant_id
                ).first()

        if not assistant:
            log_to_render(f"❌ Grok Assistant not found: {assistant_id}", "ERROR")
            log_to_render(f"   Table: grok_assistant_configs", "ERROR")
            await websocket.send_json({
                "type": "error",
                "error": {
                    "code": "assistant_not_found", 
                    "message": f"Grok assistant not found: {assistant_id}",
                    "table": "grok_assistant_configs"
                }
            })
            await websocket.close(code=1008)
            return

        grok_voice = map_voice_to_grok(getattr(assistant, 'voice', 'alloy'))
        log_to_render(f"✅ Grok Assistant loaded successfully:")
        log_to_render(f"   Name: {getattr(assistant, 'name', assistant_id)}")
        log_to_render(f"   ID: {assistant.id}")
        log_to_render(f"   Voice: {grok_voice}")
        log_to_render(f"   Provider: xAI Grok")

        # Extract enabled functions
        functions = getattr(assistant, "functions", None)
        enabled_functions = []
        if isinstance(functions, list):
            enabled_functions = [normalize_function_name(f.get("name")) for f in functions if f.get("name")]
        elif isinstance(functions, dict) and "enabled_functions" in functions:
            enabled_functions = [normalize_function_name(name) for name in functions.get("enabled_functions", [])]
            
        log_to_render(f"🔧 Functions: {enabled_functions}")

        # Check subscription and get API key
        api_key = None
        if assistant.user_id:
            user = db.query(User).get(assistant.user_id)
            if user:
                log_to_render(f"👤 User: {user.email}")
                
                # Check subscription
                if not user.is_admin and user.email != "well96well@gmail.com":
                    from backend.services.user_service import UserService
                    subscription_status = await UserService.check_subscription_status(db, str(user.id))
                    
                    if not subscription_status["active"]:
                        log_to_render(f"❌ Subscription expired for user {user.id}", "WARNING")
                        
                        error_code = "TRIAL_EXPIRED" if subscription_status.get("is_trial") else "SUBSCRIPTION_EXPIRED"
                        error_message = "Ваш пробный период истек" if subscription_status.get("is_trial") else "Ваша подписка истекла"
                        
                        await websocket.send_json({
                            "type": "error",
                            "error": {
                                "code": error_code,
                                "message": error_message,
                                "subscription_status": subscription_status,
                                "requires_payment": True
                            }
                        })
                        await websocket.close(code=1008)
                        return
                
                # Get xAI API key (try grok_api_key first, then xai_api_key)
                api_key = getattr(user, 'grok_api_key', None) or getattr(user, 'xai_api_key', None)
                
                if api_key:
                    log_to_render(f"🔑 xAI API key loaded: {api_key[:10]}...{api_key[-5:]}")
                else:
                    log_to_render(f"⚠️ No xAI API key for user", "WARNING")
        
        if not api_key:
            log_to_render(f"❌ No xAI API key available", "ERROR")
            await websocket.send_json({
                "type": "error",
                "error": {"code": "no_api_key", "message": "xAI API key required for Grok Voice"}
            })
            await websocket.close(code=1008)
            return

        # Create Grok client
        log_to_render(f"🚀 Creating Grok Voice client...")
        grok_client = GrokVoiceClient(
            api_key=api_key,
            assistant_config=assistant,
            client_id=client_id,
            db_session=db,
            user_agent=user_agent,
            is_telephony=is_telephony,
            sample_rate=sample_rate
        )
        
        log_to_render(f"🔌 Connecting to Grok Voice Agent API...")
        connect_start = time.time()
        if not await grok_client.connect():
            log_to_render(f"❌ Failed to connect to Grok", "ERROR")
            await websocket.send_json({
                "type": "error",
                "error": {"code": "grok_connection_failed", "message": "Failed to connect to Grok Voice API"}
            })
            await websocket.close(code=1008)
            return

        connection_time = time.time() - connect_start
        log_to_render(f"✅ Connected to Grok in {connection_time:.2f}s")

        # Send connection status
        await websocket.send_json({
            "type": "connection_status", 
            "status": "connected", 
            "message": "Connected to Grok Voice Agent API (v1.1)",
            "provider": "xai",
            "model": "grok-voice",
            "voice": grok_voice,
            "functions_enabled": len(enabled_functions),
            "google_sheets": bool(getattr(assistant, 'google_sheet_id', None)),
            "client_id": client_id,
            "is_telephony": is_telephony,
            "async_functions": True
        })

        # Audio buffer
        audio_buffer = bytearray()
        is_processing = False
        
        # Interruption state
        interruption_state = {
            "is_user_speaking": False,
            "is_assistant_speaking": False,
            "last_speech_start": 0,
            "last_speech_stop": 0,
            "interruption_count": 0,
            "last_interruption_time": 0
        }

        log_to_render(f"🎬 Starting Grok message handler...")
        # Start Grok message handler
        grok_task = asyncio.create_task(
            handle_grok_messages(grok_client, websocket, interruption_state)
        )

        # Main client receive loop
        log_to_render(f"🔄 Starting main WebSocket receive loop...")
        message_count = 0
        while True:
            try:
                message = await websocket.receive()
                message_count += 1

                if "text" in message:
                    data = json.loads(message["text"])
                    msg_type = data.get("type", "")

                    if ENABLE_DETAILED_LOGGING and message_count % 10 == 0:
                        log_to_render(f"📨 Client message #{message_count}: {msg_type}")

                    if msg_type == "ping":
                        await websocket.send_json({"type": "pong"})
                        continue

                    if msg_type == "session.update":
                        log_to_render(f"📝 Client session.update received")
                        await websocket.send_json({
                            "type": "session.update.ack", 
                            "event_id": data.get("event_id", f"ack_{int(time.time() * 1000)}")
                        })
                        continue

                    # Text message
                    if msg_type == "text.send":
                        text = data.get("text", "")
                        if text and grok_client.is_connected:
                            await grok_client.send_text_message(text, trigger_response=True)
                            await websocket.send_json({
                                "type": "text.send.ack", 
                                "event_id": data.get("event_id")
                            })
                        continue

                    # Audio processing
                    if msg_type == "input_audio_buffer.append":
                        audio_chunk = base64_to_audio_buffer(data["audio"])
                        audio_buffer.extend(audio_chunk)
                        
                        if grok_client.is_connected:
                            await grok_client.process_audio(audio_chunk)
                        
                        await websocket.send_json({
                            "type": "input_audio_buffer.append.ack", 
                            "event_id": data.get("event_id")
                        })
                        continue

                    if msg_type == "input_audio_buffer.commit" and not is_processing:
                        is_processing = True
                        log_to_render(f"📤 Committing audio buffer: {len(audio_buffer)} bytes")
                        
                        if grok_client.is_connected:
                            await grok_client.commit_audio()
                            await websocket.send_json({
                                "type": "input_audio_buffer.commit.ack", 
                                "event_id": data.get("event_id")
                            })
                        else:
                            log_to_render(f"⚠️ Grok not connected, attempting reconnect...", "WARNING")
                            if await grok_client.reconnect():
                                await grok_client.commit_audio()
                                await websocket.send_json({
                                    "type": "input_audio_buffer.commit.ack", 
                                    "event_id": data.get("event_id")
                                })

                        audio_buffer.clear()
                        is_processing = False
                        continue

                    if msg_type == "input_audio_buffer.clear":
                        log_to_render(f"🗑️ Clearing audio buffer ({len(audio_buffer)} bytes)")
                        audio_buffer.clear()
                        if grok_client.is_connected:
                            await grok_client.clear_audio_buffer()
                        await websocket.send_json({
                            "type": "input_audio_buffer.clear.ack", 
                            "event_id": data.get("event_id")
                        })
                        continue

                    if msg_type == "response.create":
                        log_to_render(f"📤 Manual response.create requested")
                        if grok_client.is_connected:
                            await grok_client.create_response()
                        await websocket.send_json({
                            "type": "response.create.ack", 
                            "event_id": data.get("event_id")
                        })
                        continue
                    
                    # Interruption handling
                    if msg_type == "interruption.manual":
                        log_to_render(f"⚡ Manual interruption triggered")
                        await grok_client.handle_interruption()
                        await websocket.send_json({
                            "type": "interruption.manual.ack", 
                            "event_id": data.get("event_id")
                        })
                        continue
                    
                    if msg_type == "audio_playback.stopped":
                        log_to_render(f"🔇 Client stopped playback")
                        grok_client.set_assistant_speaking(False)
                        interruption_state["is_assistant_speaking"] = False
                        continue
                    
                    if msg_type == "speech.user_started":
                        log_to_render(f"🗣️ User started speaking")
                        interruption_state["is_user_speaking"] = True
                        interruption_state["last_speech_start"] = time.time()
                        
                        if interruption_state["is_assistant_speaking"]:
                            log_to_render(f"⚡ User interrupted assistant!")
                            await grok_client.handle_interruption()
                            interruption_state["interruption_count"] += 1
                            interruption_state["last_interruption_time"] = time.time()
                        continue
                    
                    if msg_type == "speech.user_stopped":
                        log_to_render(f"🤐 User stopped speaking")
                        interruption_state["is_user_speaking"] = False
                        interruption_state["last_speech_stop"] = time.time()
                        continue

                elif "bytes" in message:
                    audio_buffer.extend(message["bytes"])
                    await websocket.send_json({"type": "binary.ack"})

            except (WebSocketDisconnect, ConnectionClosed):
                log_to_render(f"🔌 Client WebSocket disconnected: {client_id}")
                break
            except Exception as e:
                log_to_render(f"❌ Error in WebSocket loop: {e}", "ERROR")
                log_to_render(f"Traceback: {traceback.format_exc()}", "ERROR")
                break

        # Cleanup
        log_to_render(f"🧹 Cleaning up connection...")
        if not grok_task.done():
            grok_task.cancel()
            await asyncio.sleep(0)

        session_duration = time.time() - connection_start
        log_to_render(f"📊 Session stats:")
        log_to_render(f"   Duration: {session_duration:.2f}s")
        log_to_render(f"   Messages processed: {message_count}")
        log_to_render(f"   Interruptions: {interruption_state['interruption_count']}")
        log_to_render(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    except Exception as outer_e:
        log_to_render(f"❌ CRITICAL ERROR: {outer_e}", "ERROR")
        log_to_render(f"Traceback: {traceback.format_exc()}", "ERROR")
        
        try:
            await websocket.send_json({
                "type": "error",
                "error": {"code": "server_error", "message": "Internal server error"}
            })
        except:
            pass
    finally:
        if grok_client:
            await grok_client.close()
        
        conns = active_grok_connections.get(assistant_id, [])
        if websocket in conns:
            conns.remove(websocket)
        log_to_render(f"👋 Grok connection closed: {client_id}")


# ==================== GROK MESSAGE HANDLER ====================

async def handle_grok_messages(
    grok_client: 'GrokVoiceClient', 
    websocket: WebSocket, 
    interruption_state: Dict
):
    """
    Handle messages from Grok Voice Agent API.
    
    Processes:
    - Audio output (response.output_audio.delta)
    - Transcripts (audio_transcript.delta/done)
    - Function calls (function_call_arguments.done)
    - VAD events (speech_started/stopped)
    - Session events
    """
    if not grok_client.is_connected or not grok_client.ws:
        log_to_render(f"❌ Grok client not connected", "ERROR")
        return
    
    # Transcripts
    user_transcript = ""
    assistant_transcript = ""
    
    # Function tracking
    function_calls_map = {}
    pending_function_call = {
        "name": None,
        "call_id": None,
        "arguments_buffer": ""
    }
    
    # Metrics
    event_count = 0
    function_execution_count = 0
    
    try:
        log_to_render(f"🎭 Grok message handler started")
        log_to_render(f"   Client ID: {grok_client.client_id}")
        log_to_render(f"   Session ID: {grok_client.session_id}")
        log_to_render(f"   Enabled functions: {grok_client.enabled_functions}")
        
        while True:
            try:
                raw = await grok_client.ws.recv()
                event_count += 1
                
                try:
                    response_data = json.loads(raw)
                except json.JSONDecodeError:
                    log_to_render(f"❌ JSON decode error: {raw[:200]}", "ERROR")
                    continue
                    
                msg_type = response_data.get("type", "unknown")
                
                # Detailed logging for important events
                should_log = (
                    ENABLE_DETAILED_LOGGING and (
                        event_count % 20 == 0 or
                        "function" in msg_type or
                        "item" in msg_type or
                        msg_type in [
                            "input_audio_buffer.speech_started",
                            "input_audio_buffer.speech_stopped",
                            "response.created",
                            "response.done",
                            "error"
                        ]
                    )
                )
                
                if should_log:
                    log_to_render(f"📡 Grok Event #{event_count}: {msg_type}")
                
                # ==================== SESSION EVENTS ====================
                
                if msg_type == "session.updated":
                    log_to_render(f"✅ Session updated confirmed")
                    await websocket.send_json({
                        "type": "session.updated",
                        "session": response_data.get("session", {})
                    })
                    continue
                
                if msg_type == "conversation.created":
                    log_to_render(f"✅ Conversation created: {response_data.get('conversation', {}).get('id')}")
                    continue
                
                # ==================== VAD EVENTS ====================
                
                if msg_type == "input_audio_buffer.speech_started":
                    log_to_render(f"🎤 VAD: User speech detected")
                    interruption_state["is_user_speaking"] = True
                    interruption_state["last_speech_start"] = time.time()
                    
                    await websocket.send_json({
                        "type": "speech.started",
                        "timestamp": interruption_state["last_speech_start"]
                    })
                    continue
                
                if msg_type == "input_audio_buffer.speech_stopped":
                    log_to_render(f"🤐 VAD: User speech ended")
                    interruption_state["is_user_speaking"] = False
                    interruption_state["last_speech_stop"] = time.time()
                    
                    await websocket.send_json({
                        "type": "speech.stopped",
                        "timestamp": interruption_state["last_speech_stop"]
                    })
                    continue
                
                if msg_type == "input_audio_buffer.committed":
                    log_to_render(f"✅ Audio buffer committed")
                    continue
                
                if msg_type == "input_audio_buffer.cleared":
                    log_to_render(f"✅ Audio buffer cleared")
                    continue
                
                # ==================== ERROR HANDLING ====================
                
                if msg_type == "error":
                    log_to_render(f"❌ Grok API Error: {json.dumps(response_data, ensure_ascii=False)}", "ERROR")
                    await websocket.send_json(response_data)
                    continue
                
                # ==================== RESPONSE EVENTS ====================
                
                if msg_type == "response.created":
                    response_id = response_data.get("response", {}).get("id")
                    log_to_render(f"🎬 Response created: {response_id}")
                    await websocket.send_json({
                        "type": "response.created",
                        "response_id": response_id
                    })
                    continue
                
                if msg_type == "response.output_item.added":
                    item = response_data.get("item", {})
                    log_to_render(f"📦 Output item added: {item.get('type')}")
                    continue
                
                # ==================== AUDIO OUTPUT ====================
                
                if msg_type == "response.output_audio.delta":
                    if not interruption_state["is_assistant_speaking"]:
                        response_id = response_data.get("response_id", f"resp_{time.time()}")
                        log_to_render(f"🔊 Assistant started speaking: {response_id}")
                        interruption_state["is_assistant_speaking"] = True
                        grok_client.set_assistant_speaking(True, response_id)
                        
                        await websocket.send_json({
                            "type": "assistant.speech.started",
                            "response_id": response_id,
                            "timestamp": time.time()
                        })
                    
                    delta_audio = response_data.get("delta", "")
                    if delta_audio:
                        sample_count = len(base64.b64decode(delta_audio)) // 2
                        grok_client.increment_audio_samples(sample_count)
                        
                        # Forward audio to client
                        await websocket.send_json({
                            "type": "response.audio.delta",
                            "delta": delta_audio
                        })
                    continue
                
                if msg_type == "response.output_audio.done":
                    log_to_render(f"🔇 Assistant audio done")
                    if interruption_state["is_assistant_speaking"]:
                        interruption_state["is_assistant_speaking"] = False
                        grok_client.set_assistant_speaking(False)
                        
                        await websocket.send_json({
                            "type": "assistant.speech.ended",
                            "timestamp": time.time()
                        })
                    continue
                
                # ==================== TRANSCRIPTS ====================
                
                if msg_type == "conversation.item.input_audio_transcription.completed":
                    transcript = response_data.get("transcript", "")
                    if transcript:
                        user_transcript = transcript
                        log_to_render(f"👤 USER TRANSCRIPT: {user_transcript}")
                        
                        # Async save
                        if grok_client.db_session and grok_client.conversation_record_id:
                            asyncio.create_task(
                                async_save_to_database(
                                    grok_client.db_session,
                                    grok_client.conversation_record_id,
                                    user_transcript,
                                    None,
                                    None
                                )
                            )
                        
                        await websocket.send_json({
                            "type": "transcript.user",
                            "transcript": user_transcript
                        })
                    continue
                
                if msg_type == "response.output_audio_transcript.delta":
                    delta_text = response_data.get("delta", "")
                    assistant_transcript += delta_text
                    
                    await websocket.send_json({
                        "type": "transcript.assistant.delta",
                        "delta": delta_text
                    })
                    continue
                
                if msg_type == "response.output_audio_transcript.done":
                    transcript = response_data.get("transcript", "")
                    if transcript:
                        assistant_transcript = transcript
                        log_to_render(f"🤖 ASSISTANT TRANSCRIPT: {assistant_transcript}")
                        
                        await websocket.send_json({
                            "type": "transcript.assistant.done",
                            "transcript": assistant_transcript
                        })
                    continue
                
                # ==================== FUNCTION CALLS ====================
                
                if msg_type == "conversation.item.added":
                    item = response_data.get("item", {})
                    item_type = item.get("type")
                    
                    if item_type == "function_call":
                        call_id = item.get("call_id")
                        function_name = item.get("name")
                        
                        log_to_render(f"🔧 Function call item detected:")
                        log_to_render(f"   Call ID: {call_id}")
                        log_to_render(f"   Function: {function_name}")
                        
                        if call_id and function_name:
                            normalized_name = normalize_function_name(function_name)
                            function_calls_map[call_id] = {
                                "name": normalized_name,
                                "original_name": function_name,
                                "status": "pending",
                                "timestamp": time.time()
                            }
                    continue
                
                if msg_type == "response.function_call_arguments.delta":
                    delta = response_data.get("delta", "")
                    call_id = response_data.get("call_id")
                    function_name = response_data.get("name")
                    
                    if function_name and not pending_function_call["name"]:
                        pending_function_call["name"] = normalize_function_name(function_name)
                    
                    if call_id and not pending_function_call["call_id"]:
                        pending_function_call["call_id"] = call_id
                    
                    pending_function_call["arguments_buffer"] += delta
                    continue
                
                if msg_type == "response.function_call_arguments.done":
                    log_to_render(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                    log_to_render(f"📋 FUNCTION ARGUMENTS DONE")
                    
                    # Get function details
                    function_name = response_data.get("name") or pending_function_call.get("name")
                    call_id = response_data.get("call_id") or pending_function_call.get("call_id")
                    arguments_str = response_data.get("arguments", "") or pending_function_call.get("arguments_buffer", "")
                    
                    # Try to get from map
                    if not function_name and call_id and call_id in function_calls_map:
                        function_name = function_calls_map[call_id]["name"]
                    
                    log_to_render(f"   Function: {function_name}")
                    log_to_render(f"   Call ID: {call_id}")
                    log_to_render(f"   Arguments: {arguments_str[:200]}...")
                    log_to_render(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                    
                    if not function_name or not call_id:
                        log_to_render(f"❌ Missing function name or call_id", "ERROR")
                        pending_function_call = {"name": None, "call_id": None, "arguments_buffer": ""}
                        continue
                    
                    normalized_name = normalize_function_name(function_name)
                    
                    # Check if function is enabled
                    if normalized_name not in grok_client.enabled_functions:
                        log_to_render(f"❌ UNAUTHORIZED function: {normalized_name}", "WARNING")
                        
                        await websocket.send_json({
                            "type": "function_call.error",
                            "function": normalized_name,
                            "error": f"Function {function_name} not activated"
                        })
                        
                        # Send error result to Grok
                        await grok_client.send_function_result(call_id, {
                            "error": f"Function {normalized_name} not allowed",
                            "status": "error"
                        })
                        
                        pending_function_call = {"name": None, "call_id": None, "arguments_buffer": ""}
                        continue
                    
                    # Parse arguments
                    try:
                        arguments = json.loads(arguments_str) if arguments_str else {}
                        
                        await websocket.send_json({
                            "type": "function_call.executing",
                            "function": normalized_name,
                            "call_id": call_id,
                            "arguments": arguments,
                            "async_execution": True
                        })
                        
                        function_execution_count += 1
                        
                        # ASYNC EXECUTION (non-blocking!)
                        log_to_render(f"🔥 Launching async function execution: {normalized_name}")
                        
                        asyncio.create_task(
                            execute_and_send_function_result(
                                grok_client=grok_client,
                                websocket=websocket,
                                call_id=call_id,
                                function_name=normalized_name,
                                arguments=arguments,
                                context={
                                    "assistant_config": grok_client.assistant_config,
                                    "client_id": grok_client.client_id,
                                    "db_session": grok_client.db_session,
                                    "websocket": websocket
                                },
                                user_transcript=user_transcript
                            )
                        )
                        
                        log_to_render(f"⚡ Function task created - continuing immediately!")
                        
                    except json.JSONDecodeError as e:
                        log_to_render(f"❌ Function args parse error: {e}", "ERROR")
                        await websocket.send_json({
                            "type": "error",
                            "error": {"code": "function_args_error", "message": str(e)}
                        })
                    except Exception as e:
                        log_to_render(f"❌ Function setup error: {e}", "ERROR")
                        log_to_render(f"Traceback: {traceback.format_exc()}", "ERROR")
                    
                    # Clear pending
                    pending_function_call = {"name": None, "call_id": None, "arguments_buffer": ""}
                    
                    if call_id in function_calls_map:
                        function_calls_map[call_id]["status"] = "executing_async"
                    continue
                
                # ==================== RESPONSE DONE ====================
                
                if msg_type == "response.done":
                    log_to_render(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                    log_to_render(f"🏁 RESPONSE DONE")
                    log_to_render(f"   User: '{user_transcript[:50]}...' (len={len(user_transcript)})")
                    log_to_render(f"   Assistant: '{assistant_transcript[:50]}...' (len={len(assistant_transcript)})")
                    log_to_render(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                    
                    if interruption_state["is_assistant_speaking"]:
                        interruption_state["is_assistant_speaking"] = False
                        grok_client.set_assistant_speaking(False)
                        
                        await websocket.send_json({
                            "type": "assistant.speech.ended",
                            "timestamp": time.time()
                        })
                    
                    # Async database save
                    if grok_client.db_session and grok_client.conversation_record_id and assistant_transcript:
                        asyncio.create_task(
                            async_save_to_database(
                                grok_client.db_session,
                                grok_client.conversation_record_id,
                                user_transcript if user_transcript else None,
                                assistant_transcript,
                                None
                            )
                        )
                    
                    # Async dialog save
                    if user_transcript and assistant_transcript:
                        asyncio.create_task(
                            async_save_dialog_to_db(
                                grok_client.db_session,
                                str(grok_client.assistant_config.id),
                                user_transcript,
                                assistant_transcript,
                                grok_client.session_id
                            )
                        )
                        
                        # Google Sheets
                        if grok_client.assistant_config and grok_client.assistant_config.google_sheet_id:
                            asyncio.create_task(
                                async_save_to_google_sheets(
                                    sheet_id=grok_client.assistant_config.google_sheet_id,
                                    user_message=user_transcript,
                                    assistant_message=assistant_transcript,
                                    function_result=None,
                                    conversation_id=grok_client.conversation_record_id,
                                    context="Grok Dialog"
                                )
                            )
                    
                    await websocket.send_json({
                        "type": "response.done",
                        "response": response_data.get("response", {})
                    })
                    
                    # Reset transcripts
                    user_transcript = ""
                    assistant_transcript = ""
                    continue
                
                # Forward other messages
                await websocket.send_json(response_data)

            except ConnectionClosed as e:
                log_to_render(f"⚠️ Grok connection closed: {e}", "WARNING")
                if await grok_client.reconnect():
                    log_to_render(f"✅ Reconnected to Grok")
                    continue
                else:
                    log_to_render(f"❌ Reconnection failed", "ERROR")
                    await websocket.send_json({
                        "type": "error",
                        "error": {"code": "grok_connection_lost", "message": "Connection lost"}
                    })
                    break

    except (ConnectionClosed, asyncio.CancelledError):
        log_to_render(f"👋 Handler terminated for {grok_client.client_id}")
        return
    except Exception as e:
        log_to_render(f"❌ CRITICAL Handler error: {e}", "ERROR")
        log_to_render(f"Traceback: {traceback.format_exc()}", "ERROR")
    finally:
        log_to_render(f"📊 Final handler stats:")
        log_to_render(f"   Total events processed: {event_count}")
        log_to_render(f"   Functions executed (async): {function_execution_count}")
