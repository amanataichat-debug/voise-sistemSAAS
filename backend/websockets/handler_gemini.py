# backend/websockets/handler_gemini.py
"""
🚀 PRODUCTION VERSION 1.6.1 - Google Gemini Live API Handler
✅ PURE GEMINI VAD - removed client-side commit logic
✅ Continuous audio streaming - Gemini decides when to respond
✅ Complete function calling support with toolCall event handler
✅ Google Sheets logging with transcription support
✅ Database integration
✅ Interruption handling
✅ Screen context support
✅ Audio transcription support (input + output)
✅ Fallback logging on disconnect
✅ Maximum logging for debugging
✅ Ready for production deployment

CRITICAL FIXES in v1.4:
- Added toolCall event handler for top-level function calls
- Fixed execute_function signature (name, arguments, context)
- Added last_function_name assignment before send_function_result
- Added fallback logging in finally block

✨✨✨ NEW in v1.5 - FUNCTION LOG DATABASE TRACKING: ✨✨✨
🔥 Full function call logging to function_logs table!
🔥 Tracks: function_name, arguments, result, execution_time, status
🔥 Links to user_id, assistant_id, conversation_id
🔥 Error tracking with error_message field

✨✨✨ FIX in v1.5.1 - SESSION MANAGEMENT: ✨✨✨
🔧 FIX: async_save_function_log создаёт НОВУЮ сессию БД внутри задачи
🔧 Исправлена ошибка "Session is closed" при асинхронном логировании

✨✨✨ FIX in v1.6.1 - TURN-BASED DIALOG: ✨✨✨
🔥 КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: Поле `finished` НЕ СУЩЕСТВУЕТ в Gemini API!
🔥 Используем ТОЛЬКО `turnComplete` как маркер конца реплики
🔥 Каждый turnComplete = отдельная запись в БД и Google Sheets
🔥 Буферы очищаются ПОСЛЕ сохранения
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
from backend.models.gemini_assistant import GeminiAssistantConfig, GeminiConversation
from backend.utils.audio_utils import base64_to_audio_buffer
from backend.websockets.gemini_client import GeminiLiveClient
from backend.services.google_sheets_service import GoogleSheetsService
from backend.services.conversation_service import ConversationService
from backend.services.function_log_service import FunctionLogService
from backend.functions import execute_function, normalize_function_name

logger = get_logger(__name__)

# Force immediate log flushing to stdout for Render
import logging
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    force=True
)

# Active connections
active_gemini_connections: Dict[str, List[WebSocket]] = {}

# Debug mode
ENABLE_DETAILED_LOGGING = False
# 🆕 v1.6.1: Включить подробное логирование структуры транскриптов
ENABLE_TRANSCRIPT_DEBUG = False


def log_to_render(message: str, level: str = "INFO"):
    """Force log to Render stdout immediately"""
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    log_msg = f"{timestamp} - [GEMINI v1.6.1] {level} - {message}"
    print(log_msg, flush=True)
    if level == "ERROR":
        logger.error(message)
    elif level == "WARNING":
        logger.warning(message)
    else:
        logger.info(message)


# 🆕 v1.5.1: Async function log save (FIX: создаём новую сессию внутри задачи)
async def async_save_function_log(
    function_name: str,
    arguments: dict,
    result: dict,
    status: str,
    execution_time_ms: float,
    user_id: str = None,
    assistant_id: str = None,
    conversation_id: str = None,
    error_message: str = None
):
    """
    🆕 v1.5.1: Async function log save to function_logs table (non-blocking)
    ✅ FIX: Создаём НОВУЮ сессию внутри задачи, т.к. оригинальная уже закрыта
    """
    from backend.db.session import SessionLocal
    
    db = None
    try:
        db = SessionLocal()
        
        log_to_render(f"📝 [FUNC-LOG] Saving function call to database")
        log_to_render(f"   Function: {function_name}")
        log_to_render(f"   Status: {status}")
        log_to_render(f"   Execution time: {execution_time_ms:.2f}ms")
        
        log_entry = await FunctionLogService.log_function_call(
            db=db,
            function_name=function_name,
            arguments=arguments,
            result=result,
            status=status,
            execution_time_ms=execution_time_ms,
            user_id=user_id,
            assistant_id=assistant_id,
            conversation_id=conversation_id,
            error_message=error_message
        )
        
        if log_entry:
            log_to_render(f"✅ [FUNC-LOG] Function log saved: {log_entry.id}")
        else:
            log_to_render(f"⚠️ [FUNC-LOG] Function log save returned None", "WARNING")
        
    except Exception as e:
        log_to_render(f"❌ [FUNC-LOG] Error saving function log: {e}", "ERROR")
        log_to_render(f"Traceback: {traceback.format_exc()}", "ERROR")
    finally:
        if db:
            db.close()


async def handle_gemini_websocket_connection(
    websocket: WebSocket,
    assistant_id: str,
    db: Session
) -> None:
    """
    🚀 PRODUCTION v1.6.1 - Main WebSocket handler for Gemini Live API
    ✅ Pure Gemini VAD - continuous audio streaming
    ✅ Audio transcription support
    🆕 v1.5: FunctionLog tracking
    🔧 v1.5.1: Fixed db_session issue in async_save_function_log
    🔥 v1.6.1: Fixed turn-based dialog (no finished field, only turnComplete)
    """
    client_id = str(uuid.uuid4())
    gemini_client = None
    connection_start = time.time()
    
    log_to_render(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    log_to_render(f"🚀 NEW GEMINI CONNECTION INITIATED (v1.6.1 - Turn-Based Dialog)")
    log_to_render(f"   Client ID: {client_id}")
    log_to_render(f"   Assistant ID: {assistant_id}")
    log_to_render(f"   Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    log_to_render(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    
    user_agent = ""
    if hasattr(websocket, 'headers'):
        user_agent = websocket.headers.get('user-agent', '')
        log_to_render(f"📱 User-Agent: {user_agent[:100]}")

    try:
        await websocket.accept()
        log_to_render(f"✅ WebSocket accepted for client {client_id}")

        # Register connection
        active_gemini_connections.setdefault(assistant_id, []).append(websocket)
        log_to_render(f"📝 Active Gemini connections for {assistant_id}: {len(active_gemini_connections.get(assistant_id, []))}")

        # Load assistant
        log_to_render(f"🔍 Loading Gemini assistant: {assistant_id}")
        if assistant_id == "demo":
            assistant = db.query(GeminiAssistantConfig).filter(GeminiAssistantConfig.is_public.is_(True)).first()
            if not assistant:
                assistant = db.query(GeminiAssistantConfig).first()
        else:
            try:
                uuid_obj = uuid.UUID(assistant_id)
                assistant = db.query(GeminiAssistantConfig).get(uuid_obj)
            except ValueError:
                assistant = db.query(GeminiAssistantConfig).filter(
                    GeminiAssistantConfig.id.cast(str) == assistant_id
                ).first()

        if not assistant:
            log_to_render(f"❌ Gemini assistant not found: {assistant_id}", "ERROR")
            await websocket.send_json({
                "type": "error",
                "error": {"code": "assistant_not_found", "message": "Gemini assistant not found"}
            })
            await websocket.close(code=1008)
            return

        log_to_render(f"✅ Gemini assistant loaded: {getattr(assistant, 'name', assistant_id)}")
        log_to_render(f"   Voice: {getattr(assistant, 'voice', 'Aoede')}")
        log_to_render(f"   Model: gemini-2.5-flash-native-audio-preview-12-2025")
        log_to_render(f"   Thinking enabled: {getattr(assistant, 'enable_thinking', False)}")
        log_to_render(f"   Transcription: ENABLED")
        log_to_render(f"   🔥 Turn-based dialog: ENABLED (v1.6.1 - turnComplete only)")

        # Extract enabled functions
        functions = getattr(assistant, "functions", None)
        enabled_functions = []
        if isinstance(functions, list):
            enabled_functions = [normalize_function_name(f.get("name")) for f in functions if f.get("name")]
        elif isinstance(functions, dict) and "enabled_functions" in functions:
            enabled_functions = [normalize_function_name(name) for name in functions.get("enabled_functions", [])]
            
        log_to_render(f"🔧 Functions configuration:")
        log_to_render(f"   Enabled count: {len(enabled_functions)}")
        log_to_render(f"   Functions: {enabled_functions}")
        log_to_render(f"   🔥 v1.5.1: All function calls will be logged to function_logs table!")

        # Check Google Sheets config
        if hasattr(assistant, 'google_sheet_id') and assistant.google_sheet_id:
            log_to_render(f"📊 Google Sheets logging ENABLED")
            log_to_render(f"   Sheet ID: {assistant.google_sheet_id[:20]}...")
        else:
            log_to_render(f"⚠️ Google Sheets logging DISABLED (no sheet_id)")

        # Check subscription
        api_key = None
        if assistant.user_id:
            user = db.query(User).get(assistant.user_id)
            if user:
                log_to_render(f"👤 User loaded:")
                log_to_render(f"   Email: {user.email}")
                log_to_render(f"   User ID: {user.id}")
                
                if not user.is_admin and user.email != "amanat.aichat@gmail.com":
                    from backend.services.user_service import UserService
                    subscription_status = await UserService.check_subscription_status(db, str(user.id))
                    
                    log_to_render(f"💳 Subscription check:")
                    log_to_render(f"   Active: {subscription_status.get('active')}")
                    log_to_render(f"   Trial: {subscription_status.get('is_trial')}")
                    
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
                
                api_key = user.gemini_api_key
                if api_key:
                    log_to_render(f"🔑 Gemini API key loaded: {api_key[:10]}...{api_key[-5:]}")
                else:
                    log_to_render(f"⚠️ No Gemini API key for user", "WARNING")
        
        if not api_key:
            log_to_render(f"❌ No Gemini API key available", "ERROR")
            await websocket.send_json({
                "type": "error",
                "error": {"code": "no_api_key", "message": "Google Gemini API key required"}
            })
            await websocket.close(code=1008)
            return

        # Create Gemini Live client
        log_to_render(f"🚀 Creating Gemini Live client...")
        log_to_render(f"   Client ID: {client_id}")
        log_to_render(f"   API Key: {api_key[:10]}...")
        gemini_client = GeminiLiveClient(api_key, assistant, client_id, db, user_agent)
        
        log_to_render(f"🔌 Connecting to Gemini Live API...")
        connect_start = time.time()
        if not await gemini_client.connect():
            log_to_render(f"❌ Failed to connect to Gemini", "ERROR")
            await websocket.send_json({
                "type": "error",
                "error": {"code": "gemini_connection_failed", "message": "Failed to connect to Gemini"}
            })
            await websocket.close(code=1008)
            return

        connection_time = time.time() - connect_start
        log_to_render(f"✅ Connected to Gemini in {connection_time:.2f}s")

        # Send connection status
        await websocket.send_json({
            "type": "connection_status", 
            "status": "connected", 
            "message": "Connected to Gemini Live API (Production v1.6.1 - Turn-Based Dialog)",
            "model": "gemini-2.5-flash-native-audio-preview-12-2025",
            "functions_enabled": len(enabled_functions),
            "google_sheets": bool(getattr(assistant, 'google_sheet_id', None)),
            "thinking_enabled": getattr(assistant, 'enable_thinking', False),
            "transcription_enabled": True,
            "client_id": client_id,
            "vad_mode": "gemini_native",
            "function_logging": True,
            "turn_based_dialog": True  # 🆕 v1.6.1
        })

        # Interruption state
        interruption_state = {
            "is_user_speaking": False,
            "is_assistant_speaking": False,
            "last_speech_start": 0,
            "last_speech_stop": 0,
            "interruption_count": 0,
            "last_interruption_time": 0
        }

        log_to_render(f"🎬 Starting Gemini message handler (v1.6.1 - Turn-Based Dialog)...")
        # Start Gemini message handler
        gemini_task = asyncio.create_task(
            handle_gemini_messages(gemini_client, websocket, interruption_state)
        )

        # Main client receive loop
        log_to_render(f"🔄 Starting main WebSocket receive loop (continuous streaming mode)...")
        message_count = 0
        audio_chunks_sent = 0
        
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

                    # Screen context handler (silent mode)
                    if msg_type == "screen.context":
                        log_to_render(f"📸 Screen context received (silent mode)")
                        
                        image_data = data.get("image")
                        is_silent = data.get("silent", True)
                        
                        if not image_data:
                            log_to_render(f"❌ No image data in screen.context", "ERROR")
                            continue
                        
                        image_size_kb = len(image_data) // 1024
                        log_to_render(f"📸 Image size: {image_size_kb}KB")
                        log_to_render(f"📸 Silent mode: {is_silent}")
                        
                        if gemini_client.is_connected:
                            success = await gemini_client.send_screen_context(image_data, silent=is_silent)
                            if success:
                                log_to_render(f"✅ Screen context added to conversation")
                            else:
                                log_to_render(f"❌ Failed to send screen context", "ERROR")
                        else:
                            log_to_render(f"❌ Gemini not connected", "ERROR")
                        
                        continue

                    # ✅ PURE GEMINI VAD - continuous audio streaming
                    if msg_type == "input_audio_buffer.append":
                        audio_chunk = base64_to_audio_buffer(data["audio"])
                        audio_chunks_sent += 1
                        
                        if gemini_client.is_connected:
                            await gemini_client.process_audio(audio_chunk)
                        
                        await websocket.send_json({
                            "type": "input_audio_buffer.append.ack", 
                            "event_id": data.get("event_id")
                        })
                        
                        if audio_chunks_sent % 100 == 0:
                            log_to_render(f"📤 Sent {audio_chunks_sent} audio chunks (continuous stream)")
                        
                        continue

                    if msg_type == "response.cancel":
                        log_to_render(f"🛑 Response cancellation requested")
                        await websocket.send_json({
                            "type": "response.cancel.ack", 
                            "event_id": data.get("event_id")
                        })
                        continue
                    
                    # Interruption handling
                    if msg_type == "interruption.manual":
                        log_to_render(f"⚡ Manual interruption triggered")
                        await gemini_client.handle_interruption()
                        await websocket.send_json({
                            "type": "interruption.manual.ack", 
                            "event_id": data.get("event_id")
                        })
                        continue
                    
                    if msg_type == "audio_playback.stopped":
                        log_to_render(f"🔇 Client stopped playback")
                        gemini_client.set_assistant_speaking(False)
                        interruption_state["is_assistant_speaking"] = False
                        continue
                    
                    # Speech events - for logging only
                    if msg_type == "speech.user_started":
                        log_to_render(f"🗣️ User started speaking (UI event)")
                        interruption_state["is_user_speaking"] = True
                        interruption_state["last_speech_start"] = time.time()
                        
                        # Check if interrupting assistant
                        if interruption_state["is_assistant_speaking"]:
                            log_to_render(f"⚡ User interrupted assistant!")
                            await gemini_client.handle_interruption()
                            interruption_state["interruption_count"] += 1
                            interruption_state["last_interruption_time"] = time.time()
                        continue
                    
                    if msg_type == "speech.user_stopped":
                        log_to_render(f"🤐 User stopped speaking (UI event)")
                        interruption_state["is_user_speaking"] = False
                        interruption_state["last_speech_stop"] = time.time()
                        continue

                elif "bytes" in message:
                    audio_chunks_sent += 1
                    if gemini_client.is_connected:
                        await gemini_client.process_audio(message["bytes"])
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
        if not gemini_task.done():
            gemini_task.cancel()
            await asyncio.sleep(0)

        session_duration = time.time() - connection_start
        log_to_render(f"📊 Session stats:")
        log_to_render(f"   Duration: {session_duration:.2f}s")
        log_to_render(f"   Messages processed: {message_count}")
        log_to_render(f"   Audio chunks sent: {audio_chunks_sent}")
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
        if gemini_client:
            await gemini_client.close()
        
        conns = active_gemini_connections.get(assistant_id, [])
        if websocket in conns:
            conns.remove(websocket)
        log_to_render(f"👋 Connection closed: {client_id}")


async def handle_gemini_messages(
    gemini_client: GeminiLiveClient, 
    websocket: WebSocket, 
    interruption_state: Dict
):
    """
    🚀 PRODUCTION v1.6.1 - Handle messages from Gemini Live API
    
    🔥🔥🔥 КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ v1.6.1: 🔥🔥🔥
    Поле `finished` НЕ СУЩЕСТВУЕТ в Gemini Live API!
    Единственный маркер конца реплики — turnComplete.
    
    Логика:
    1. Накапливаем чанки транскрипции в буферы
    2. При turnComplete — сохраняем буферы как отдельную запись
    3. Очищаем буферы ПОСЛЕ сохранения
    """
    if not gemini_client.is_connected or not gemini_client.ws:
        log_to_render(f"❌ Gemini client not connected", "ERROR")
        return
    
    # ═══════════════════════════════════════════════════════════════
    # 🔥 v1.6.1: Простые буферы для текущего turn
    # НЕ используем списки user_turns/assistant_turns — 
    # каждый turnComplete = один turn = одна запись
    # ═══════════════════════════════════════════════════════════════
    current_user_transcript = ""      # Буфер транскрипции пользователя для текущего turn
    current_assistant_transcript = "" # Буфер транскрипции ассистента для текущего turn
    
    # Счётчики
    turn_count = 0
    transcript_events_received = 0
    
    # Function tracking
    pending_function_call = {
        "name": None,
        "call_id": None,
        "arguments": {}
    }
    
    # Metrics
    event_count = 0
    function_execution_count = 0
    
    try:
        log_to_render(f"🎭 Gemini message handler started (v1.6.1 - Turn-Based)")
        log_to_render(f"   Client ID: {gemini_client.client_id}")
        log_to_render(f"   Session ID: {gemini_client.session_id}")
        log_to_render(f"   Enabled functions: {gemini_client.enabled_functions}")
        log_to_render(f"   VAD mode: Pure Gemini (automatic)")
        log_to_render(f"   Transcription: ENABLED")
        log_to_render(f"   🔥 v1.6.1: turnComplete-based saving (NO finished field!)")
        
        while True:
            try:
                raw = await gemini_client.ws.recv()
                event_count += 1
                
                try:
                    response_data = json.loads(raw)
                except json.JSONDecodeError:
                    log_to_render(f"❌ JSON decode error: {raw[:200]}", "ERROR")
                    continue
                
                # Detailed logging
                should_log = (
                    ENABLE_DETAILED_LOGGING and (
                        event_count % 50 == 0 or
                        "toolCall" in response_data or
                        response_data.get("serverContent", {}).get("turnComplete")
                    )
                )
                
                if should_log:
                    log_to_render(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                    log_to_render(f"📡 Gemini Event #{event_count}")
                    log_to_render(f"   Keys: {list(response_data.keys())}")
                    log_to_render(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                
                # Setup complete
                if "setupComplete" in response_data:
                    log_to_render(f"✅ Gemini setup complete (v1.6.1 - turnComplete-based)")
                    await websocket.send_json({
                        "type": "gemini.setup.complete",
                        "timestamp": time.time(),
                        "transcription_enabled": True,
                        "turn_based_dialog": True
                    })

                    # ✅ Отправляем greeting ПОСЛЕ setupComplete (не по таймауту!)
                    greeting_message = getattr(gemini_client.assistant_config, "greeting_message", None)
                    if greeting_message and not gemini_client.greeting_sent:
                        log_to_render(f"👋 Sending greeting after setupComplete...")
                        await gemini_client.send_initial_greeting()

                    continue
                
                # ✅ Tool Call event (top-level, outside serverContent)
                if "toolCall" in response_data:
                    tool_call = response_data["toolCall"]
                    function_calls = tool_call.get("functionCalls", [])
                    
                    log_to_render(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                    log_to_render(f"🔧 TOOL CALL EVENT (top-level) - v1.6.1")
                    log_to_render(f"   Function calls: {len(function_calls)}")
                    log_to_render(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                    
                    for func_call in function_calls:
                        function_name = func_call.get("name")
                        function_id = func_call.get("id")
                        arguments = func_call.get("args", {})
                        
                        log_to_render(f"📞 Function: {function_name}")
                        log_to_render(f"   ID: {function_id}")
                        log_to_render(f"   Args: {json.dumps(arguments, ensure_ascii=False)[:200]}")
                        
                        # Store pending call
                        pending_function_call = {
                            "name": function_name,
                            "call_id": function_id,
                            "arguments": arguments
                        }
                        
                        # Execute immediately
                        execution_start = time.time()
                        status = "error"
                        error_message = None
                        result = None
                        
                        try:
                            normalized_name = normalize_function_name(function_name)
                            
                            log_to_render(f"⚙️ Executing function: {normalized_name}")
                            
                            gemini_client.last_function_name = normalized_name
                            
                            result = await execute_function(
                                name=normalized_name,
                                arguments=arguments,
                                context={
                                    "assistant_config": gemini_client.assistant_config,
                                    "client_id": gemini_client.client_id,
                                    "db_session": gemini_client.db_session,
                                    "websocket": websocket
                                }
                            )
                            
                            execution_time = time.time() - execution_start
                            execution_time_ms = execution_time * 1000
                            function_execution_count += 1
                            status = "success"
                            
                            log_to_render(f"✅ Function executed: {execution_time:.3f}s ({execution_time_ms:.2f}ms)")
                            
                            # 🆕 v1.5.1: Log to FunctionLog (async)
                            user_id = str(gemini_client.assistant_config.user_id) if gemini_client.assistant_config and gemini_client.assistant_config.user_id else None
                            assistant_id = str(gemini_client.assistant_config.id) if gemini_client.assistant_config else None
                            
                            asyncio.create_task(
                                async_save_function_log(
                                    function_name=normalized_name,
                                    arguments=arguments,
                                    result=result if isinstance(result, dict) else {"result": str(result)},
                                    status=status,
                                    execution_time_ms=execution_time_ms,
                                    user_id=user_id,
                                    assistant_id=assistant_id,
                                    conversation_id=gemini_client.conversation_record_id,
                                    error_message=None
                                )
                            )
                            log_to_render(f"⚡ [v1.6.1] FunctionLog save task created")
                            
                            # Send result to Gemini
                            log_to_render(f"📤 Sending function result to Gemini...")
                            delivery_status = await gemini_client.send_function_result(
                                function_id,
                                result
                            )
                            
                            if delivery_status and delivery_status.get("success"):
                                log_to_render(f"✅ Result delivered to Gemini")
                                
                                await websocket.send_json({
                                    "type": "function_call.completed",
                                    "function": normalized_name,
                                    "function_call_id": function_id,
                                    "result": result,
                                    "execution_time": execution_time
                                })
                            else:
                                log_to_render(f"❌ Delivery failed: {delivery_status.get('error')}", "ERROR")
                                
                                await websocket.send_json({
                                    "type": "function_call.delivery_error",
                                    "function_call_id": function_id,
                                    "error": delivery_status.get('error')
                                })
                                
                        except Exception as e:
                            execution_time = time.time() - execution_start
                            execution_time_ms = execution_time * 1000
                            status = "error"
                            error_message = str(e)
                            
                            log_to_render(f"❌ Function execution error: {e}", "ERROR")
                            log_to_render(f"   Traceback: {traceback.format_exc()}", "ERROR")
                            
                            # Log error to FunctionLog
                            user_id = str(gemini_client.assistant_config.user_id) if gemini_client.assistant_config and gemini_client.assistant_config.user_id else None
                            assistant_id = str(gemini_client.assistant_config.id) if gemini_client.assistant_config else None
                            
                            asyncio.create_task(
                                async_save_function_log(
                                    function_name=normalize_function_name(function_name) or function_name,
                                    arguments=arguments,
                                    result={"error": error_message},
                                    status=status,
                                    execution_time_ms=execution_time_ms,
                                    user_id=user_id,
                                    assistant_id=assistant_id,
                                    conversation_id=gemini_client.conversation_record_id,
                                    error_message=error_message
                                )
                            )
                            
                            await websocket.send_json({
                                "type": "error",
                                "error": {"code": "function_execution_error", "message": str(e)}
                            })
                    
                    continue

                # ✅ Tool Call Cancellation event
                if "toolCallCancellation" in response_data:
                    cancellation = response_data["toolCallCancellation"]
                    cancelled_ids = cancellation.get("ids", [])

                    log_to_render(f"⚠️ TOOL CALL CANCELLATION received")
                    log_to_render(f"   Cancelled IDs: {cancelled_ids}")

                    await websocket.send_json({
                        "type": "function_call.cancelled",
                        "cancelled_ids": cancelled_ids,
                        "timestamp": time.time()
                    })
                    continue

                # Server content (main response container)
                if "serverContent" in response_data:
                    server_content = response_data["serverContent"]
                    
                    # ═══════════════════════════════════════════════════════════════
                    # 🔥 v1.6.1: ТРАНСКРИПЦИЯ ВХОДЯЩЕГО АУДИО (пользователя)
                    # Просто накапливаем в буфер, сохраняем при turnComplete
                    # ═══════════════════════════════════════════════════════════════
                    if "inputTranscription" in server_content:
                        input_trans = server_content["inputTranscription"]
                        
                        if ENABLE_TRANSCRIPT_DEBUG:
                            log_to_render(f"🔍 INPUT_TRANS: {json.dumps(input_trans, ensure_ascii=False)[:200]}")
                        
                        if "text" in input_trans:
                            transcript_text = input_trans["text"]
                            transcript_events_received += 1
                            
                            # Накапливаем в буфер
                            current_user_transcript += transcript_text
                            
                            log_to_render(f"👤 USER CHUNK: '{transcript_text}' (buffer: {len(current_user_transcript)} chars)")
                            
                            # Отправляем клиенту стриминговый чанк
                            await websocket.send_json({
                                "type": "input.transcription",
                                "text": transcript_text,
                                "is_chunk": True
                            })
                    
                    # ═══════════════════════════════════════════════════════════════
                    # 🔥 v1.6.1: ТРАНСКРИПЦИЯ ОТВЕТА МОДЕЛИ (ассистента)
                    # Просто накапливаем в буфер, сохраняем при turnComplete
                    # ═══════════════════════════════════════════════════════════════
                    if "outputTranscription" in server_content:
                        output_trans = server_content["outputTranscription"]
                        
                        if ENABLE_TRANSCRIPT_DEBUG:
                            log_to_render(f"🔍 OUTPUT_TRANS: {json.dumps(output_trans, ensure_ascii=False)[:200]}")
                        
                        if "text" in output_trans:
                            transcript_text = output_trans["text"]
                            transcript_events_received += 1
                            
                            # Накапливаем в буфер
                            current_assistant_transcript += transcript_text
                            
                            log_to_render(f"🤖 ASSISTANT CHUNK: '{transcript_text}' (buffer: {len(current_assistant_transcript)} chars)")
                            
                            # Отправляем клиенту стриминговый чанк
                            await websocket.send_json({
                                "type": "output.transcription",
                                "text": transcript_text,
                                "is_chunk": True
                            })
                    
                    # ═══════════════════════════════════════════════════════════════
                    # Check for interruption
                    # ═══════════════════════════════════════════════════════════════
                    if server_content.get("interrupted"):
                        log_to_render(f"⚡ Conversation interrupted by Gemini")
                        interruption_state["interruption_count"] += 1
                        interruption_state["last_interruption_time"] = time.time()
                        interruption_state["is_assistant_speaking"] = False
                        gemini_client.set_assistant_speaking(False)
                        
                        # 🔥 v1.6.1: При прерывании сохраняем что есть
                        if current_user_transcript.strip() or current_assistant_transcript.strip():
                            turn_count += 1
                            log_to_render(f"⚡ INTERRUPTED TURN #{turn_count} - saving partial data")
                            
                            user_msg = current_user_transcript.strip() or ""
                            assistant_msg = (current_assistant_transcript.strip() + " [прервано]") if current_assistant_transcript.strip() else ""
                            
                            # Save to DB
                            try:
                                await ConversationService.save_conversation(
                                    db=gemini_client.db_session,
                                    assistant_id=str(gemini_client.assistant_config.id),
                                    user_message=user_msg or "[no user input]",
                                    assistant_message=assistant_msg or "[interrupted]",
                                    session_id=gemini_client.session_id,
                                    caller_number=None,
                                    tokens_used=0
                                )
                                log_to_render(f"✅ Interrupted turn #{turn_count} saved to DB")
                            except Exception as e:
                                log_to_render(f"❌ Error saving interrupted turn: {e}", "ERROR")
                            
                            # Save to Google Sheets
                            if gemini_client.assistant_config and gemini_client.assistant_config.google_sheet_id:
                                try:
                                    await GoogleSheetsService.log_conversation(
                                        sheet_id=gemini_client.assistant_config.google_sheet_id,
                                        user_message=user_msg or "[no user input]",
                                        assistant_message=assistant_msg or "[interrupted]",
                                        function_result=None,
                                        conversation_id=gemini_client.conversation_record_id
                                    )
                                    log_to_render(f"✅ Interrupted turn #{turn_count} saved to Sheets")
                                except Exception as e:
                                    log_to_render(f"❌ Sheets error: {e}", "ERROR")
                            
                            # 🔥 ОЧИЩАЕМ БУФЕРЫ
                            current_user_transcript = ""
                            current_assistant_transcript = ""
                        
                        await websocket.send_json({
                            "type": "conversation.interrupted",
                            "timestamp": interruption_state["last_interruption_time"]
                        })
                        continue
                    
                    # ═══════════════════════════════════════════════════════════════
                    # Model turn (audio data)
                    # ═══════════════════════════════════════════════════════════════
                    if "modelTurn" in server_content:
                        model_turn = server_content["modelTurn"]
                        parts = model_turn.get("parts", [])
                        
                        for part in parts:
                            # Text content
                            if "text" in part:
                                text = part["text"]
                                await websocket.send_json({
                                    "type": "response.text.delta",
                                    "delta": text
                                })
                            
                            # Inline audio data
                            if "inlineData" in part:
                                inline_data = part["inlineData"]
                                mime_type = inline_data.get("mimeType", "")
                                data = inline_data.get("data", "")
                                
                                if "audio/pcm" in mime_type:
                                    if not interruption_state["is_assistant_speaking"]:
                                        log_to_render(f"🔊 Assistant started speaking")
                                        interruption_state["is_assistant_speaking"] = True
                                        gemini_client.set_assistant_speaking(True)
                                        
                                        await websocket.send_json({
                                            "type": "assistant.speech.started",
                                            "timestamp": time.time()
                                        })
                                    
                                    await websocket.send_json({
                                        "type": "response.audio.delta",
                                        "delta": data
                                    })
                                    
                                    sample_count = len(base64.b64decode(data)) // 2
                                    gemini_client.increment_audio_samples(sample_count)
                            
                            # Function call (tool call) inside modelTurn
                            if "functionCall" in part:
                                function_call = part["functionCall"]
                                function_name = function_call.get("name")
                                arguments = function_call.get("args", {})
                                
                                log_to_render(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                                log_to_render(f"🔧 FUNCTION CALL DETECTED (modelTurn) - v1.6.1")
                                log_to_render(f"   Function: {function_name}")
                                log_to_render(f"   Arguments: {json.dumps(arguments, ensure_ascii=False)[:200]}")
                                log_to_render(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                                
                                normalized_name = normalize_function_name(function_name) or function_name
                                
                                if normalized_name not in gemini_client.enabled_functions:
                                    log_to_render(f"❌ UNAUTHORIZED function: {normalized_name}", "WARNING")
                                    
                                    await websocket.send_json({
                                        "type": "function_call.error",
                                        "function": normalized_name,
                                        "error": f"Function {function_name} not activated"
                                    })
                                    continue
                                
                                # Store for later
                                pending_function_call = {
                                    "name": normalized_name,
                                    "call_id": f"call_{int(time.time() * 1000)}",
                                    "arguments": arguments
                                }
                                gemini_client.last_function_name = normalized_name
                                
                                await websocket.send_json({
                                    "type": "function_call.started",
                                    "function": normalized_name,
                                    "function_call_id": pending_function_call["call_id"]
                                })
                                
                                # Execute function
                                execution_start = time.time()
                                status = "error"
                                error_message = None
                                result = None
                                
                                try:
                                    await websocket.send_json({
                                        "type": "function_call.executing",
                                        "function": normalized_name,
                                        "function_call_id": pending_function_call["call_id"],
                                        "arguments": arguments
                                    })
                                    
                                    log_to_render(f"🚀 EXECUTING FUNCTION: {normalized_name}")
                                    
                                    result = await execute_function(
                                        name=normalized_name,
                                        arguments=arguments,
                                        context={
                                            "assistant_config": gemini_client.assistant_config,
                                            "client_id": gemini_client.client_id,
                                            "db_session": gemini_client.db_session,
                                            "websocket": websocket
                                        }
                                    )
                                    
                                    execution_time = time.time() - execution_start
                                    execution_time_ms = execution_time * 1000
                                    function_execution_count += 1
                                    status = "success"
                                    
                                    log_to_render(f"✅ FUNCTION EXECUTED SUCCESSFULLY")
                                    log_to_render(f"   Execution time: {execution_time:.3f}s ({execution_time_ms:.2f}ms)")
                                    
                                    # Log to FunctionLog (async)
                                    user_id = str(gemini_client.assistant_config.user_id) if gemini_client.assistant_config and gemini_client.assistant_config.user_id else None
                                    assistant_id_str = str(gemini_client.assistant_config.id) if gemini_client.assistant_config else None
                                    
                                    asyncio.create_task(
                                        async_save_function_log(
                                            function_name=normalized_name,
                                            arguments=arguments,
                                            result=result if isinstance(result, dict) else {"result": str(result)},
                                            status=status,
                                            execution_time_ms=execution_time_ms,
                                            user_id=user_id,
                                            assistant_id=assistant_id_str,
                                            conversation_id=gemini_client.conversation_record_id,
                                            error_message=None
                                        )
                                    )
                                    
                                    # Fast display for query_llm
                                    if normalized_name == "query_llm":
                                        log_to_render(f"⚡ QUERY_LLM - sending result IMMEDIATELY")
                                        
                                        llm_response_content = ""
                                        llm_model = "gpt-4"
                                        
                                        if isinstance(result, dict):
                                            llm_response_content = result.get("full_response", result.get("response", str(result)))
                                            llm_model = result.get("model_used", "gpt-4")
                                        else:
                                            llm_response_content = str(result)
                                        
                                        await websocket.send_json({
                                            "type": "llm_result",
                                            "content": llm_response_content,
                                            "model": llm_model,
                                            "function": normalized_name,
                                            "execution_time": execution_time,
                                            "timestamp": time.time()
                                        })
                                    
                                    # Google Sheets logging for function calls
                                    if gemini_client.assistant_config and gemini_client.assistant_config.google_sheet_id:
                                        sheet_id = gemini_client.assistant_config.google_sheet_id
                                        
                                        try:
                                            user_msg = current_user_transcript.strip() if current_user_transcript.strip() else f"[Function call: {normalized_name}]"
                                            
                                            sheets_result = await GoogleSheetsService.log_conversation(
                                                sheet_id=sheet_id,
                                                user_message=user_msg,
                                                assistant_message=f"[Function executed: {normalized_name}]",
                                                function_result=result,
                                                conversation_id=gemini_client.conversation_record_id
                                            )
                                            
                                            if sheets_result:
                                                log_to_render(f"✅ Google Sheets logged (function call)")
                                            else:
                                                log_to_render(f"❌ Google Sheets failed", "WARNING")
                                        except Exception as e:
                                            log_to_render(f"❌ Sheets error: {e}", "ERROR")
                                    
                                    # Send result to Gemini
                                    log_to_render(f"📤 Sending function result to Gemini...")
                                    delivery_status = await gemini_client.send_function_result(
                                        pending_function_call["call_id"], 
                                        result
                                    )
                                    
                                    if delivery_status["success"]:
                                        log_to_render(f"✅ Function result delivered")
                                        
                                        await websocket.send_json({
                                            "type": "function_call.completed",
                                            "function": normalized_name,
                                            "function_call_id": pending_function_call["call_id"],
                                            "result": result,
                                            "execution_time": execution_time
                                        })
                                    else:
                                        log_to_render(f"❌ Delivery failed: {delivery_status['error']}", "ERROR")
                                        
                                        await websocket.send_json({
                                            "type": "function_call.delivery_error",
                                            "function_call_id": pending_function_call["call_id"],
                                            "error": delivery_status['error']
                                        })
                                    
                                except Exception as e:
                                    execution_time = time.time() - execution_start
                                    execution_time_ms = execution_time * 1000
                                    status = "error"
                                    error_message = str(e)
                                    
                                    log_to_render(f"❌ Function execution ERROR: {e}", "ERROR")
                                    log_to_render(f"Traceback: {traceback.format_exc()}", "ERROR")
                                    
                                    # Log error to FunctionLog
                                    user_id = str(gemini_client.assistant_config.user_id) if gemini_client.assistant_config and gemini_client.assistant_config.user_id else None
                                    assistant_id_str = str(gemini_client.assistant_config.id) if gemini_client.assistant_config else None
                                    
                                    asyncio.create_task(
                                        async_save_function_log(
                                            function_name=normalized_name,
                                            arguments=arguments,
                                            result={"error": error_message},
                                            status=status,
                                            execution_time_ms=execution_time_ms,
                                            user_id=user_id,
                                            assistant_id=assistant_id_str,
                                            conversation_id=gemini_client.conversation_record_id,
                                            error_message=error_message
                                        )
                                    )
                                    
                                    await websocket.send_json({
                                        "type": "error",
                                        "error": {"code": "function_execution_error", "message": str(e)}
                                    })
                                
                                # Clear pending
                                pending_function_call = {"name": None, "call_id": None, "arguments": {}}
                    
                    # ═══════════════════════════════════════════════════════════════
                    # 🔥🔥🔥 v1.6.1: TURN COMPLETE - ГЛАВНЫЙ МАРКЕР! 🔥🔥🔥
                    # Это ЕДИНСТВЕННОЕ место где мы сохраняем диалог!
                    # ═══════════════════════════════════════════════════════════════
                    if server_content.get("turnComplete"):
                        turn_count += 1
                        
                        log_to_render(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                        log_to_render(f"🏁 TURN COMPLETE #{turn_count}")
                        log_to_render(f"   User transcript: '{current_user_transcript.strip()[:100]}...' ({len(current_user_transcript)} chars)")
                        log_to_render(f"   Assistant transcript: '{current_assistant_transcript.strip()[:100]}...' ({len(current_assistant_transcript)} chars)")
                        log_to_render(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                        
                        if interruption_state["is_assistant_speaking"]:
                            interruption_state["is_assistant_speaking"] = False
                            gemini_client.set_assistant_speaking(False)
                            
                            await websocket.send_json({
                                "type": "assistant.speech.ended",
                                "timestamp": time.time()
                            })
                        
                        # Отправляем завершённые транскрипты клиенту
                        if current_user_transcript.strip():
                            await websocket.send_json({
                                "type": "input.transcription.complete",
                                "text": current_user_transcript.strip(),
                                "turn_number": turn_count
                            })
                        
                        if current_assistant_transcript.strip():
                            await websocket.send_json({
                                "type": "output.transcription.complete",
                                "text": current_assistant_transcript.strip(),
                                "turn_number": turn_count
                            })
                        
                        # ═══════════════════════════════════════════════════════════
                        # 💾 СОХРАНЯЕМ TURN В БД И GOOGLE SHEETS
                        # ═══════════════════════════════════════════════════════════
                        user_msg = current_user_transcript.strip()
                        assistant_msg = current_assistant_transcript.strip()
                        
                        if user_msg or assistant_msg:
                            log_to_render(f"💾 Saving turn #{turn_count} to DB and Sheets...")
                            
                            # Save to DB
                            try:
                                await ConversationService.save_conversation(
                                    db=gemini_client.db_session,
                                    assistant_id=str(gemini_client.assistant_config.id),
                                    user_message=user_msg or "[no user input]",
                                    assistant_message=assistant_msg or "[no response]",
                                    session_id=gemini_client.session_id,
                                    caller_number=None,
                                    tokens_used=0
                                )
                                log_to_render(f"✅ Turn #{turn_count} saved to DB")
                            except Exception as e:
                                log_to_render(f"❌ Error saving turn #{turn_count} to DB: {e}", "ERROR")
                            
                            # Save to Google Sheets
                            if gemini_client.assistant_config and gemini_client.assistant_config.google_sheet_id:
                                try:
                                    sheets_result = await GoogleSheetsService.log_conversation(
                                        sheet_id=gemini_client.assistant_config.google_sheet_id,
                                        user_message=user_msg or "[no user input]",
                                        assistant_message=assistant_msg or "[no response]",
                                        function_result=None,
                                        conversation_id=gemini_client.conversation_record_id
                                    )
                                    
                                    if sheets_result:
                                        log_to_render(f"✅ Turn #{turn_count} saved to Google Sheets")
                                    else:
                                        log_to_render(f"❌ Google Sheets save failed for turn #{turn_count}", "ERROR")
                                except Exception as e:
                                    log_to_render(f"❌ Sheets error for turn #{turn_count}: {e}", "ERROR")
                            
                            # Отправляем клиенту событие о сохранении
                            await websocket.send_json({
                                "type": "turn.saved",
                                "turn_number": turn_count,
                                "user_message": user_msg,
                                "assistant_message": assistant_msg[:200] + "..." if len(assistant_msg) > 200 else assistant_msg
                            })
                        else:
                            log_to_render(f"⚠️ Turn #{turn_count} skipped: no content to save", "WARNING")
                        
                        # ═══════════════════════════════════════════════════════════
                        # 🔥 КРИТИЧЕСКИ ВАЖНО: ОЧИЩАЕМ БУФЕРЫ ПОСЛЕ СОХРАНЕНИЯ!
                        # ═══════════════════════════════════════════════════════════
                        current_user_transcript = ""
                        current_assistant_transcript = ""
                        
                        log_to_render(f"✅ Turn #{turn_count} completed, buffers cleared")
                
                # ═══════════════════════════════════════════════════════════════
                # Top-level transcriptions (if they come separately)
                # ═══════════════════════════════════════════════════════════════
                if "inputTranscription" in response_data and "serverContent" not in response_data:
                    input_trans = response_data["inputTranscription"]
                    if "text" in input_trans:
                        transcript_text = input_trans["text"]
                        transcript_events_received += 1
                        current_user_transcript += transcript_text
                        log_to_render(f"👤 [TOP-LEVEL] USER: '{transcript_text}'")
                        
                        await websocket.send_json({
                            "type": "input.transcription",
                            "text": transcript_text,
                            "is_chunk": True
                        })
                
                if "outputTranscription" in response_data and "serverContent" not in response_data:
                    output_trans = response_data["outputTranscription"]
                    if "text" in output_trans:
                        transcript_text = output_trans["text"]
                        transcript_events_received += 1
                        current_assistant_transcript += transcript_text
                        log_to_render(f"🤖 [TOP-LEVEL] ASSISTANT: '{transcript_text}'")
                        
                        await websocket.send_json({
                            "type": "output.transcription",
                            "text": transcript_text,
                            "is_chunk": True
                        })

            except ConnectionClosed as e:
                log_to_render(f"⚠️ Gemini connection closed: {e}", "WARNING")
                if await gemini_client.reconnect():
                    log_to_render(f"✅ Reconnected to Gemini")
                    continue
                else:
                    log_to_render(f"❌ Reconnection failed", "ERROR")
                    await websocket.send_json({
                        "type": "error",
                        "error": {"code": "gemini_connection_lost", "message": "Connection lost"}
                    })
                    break

    except (ConnectionClosed, asyncio.CancelledError):
        log_to_render(f"👋 Handler terminated for {gemini_client.client_id}")
        return
    except Exception as e:
        log_to_render(f"❌ CRITICAL Handler error: {e}", "ERROR")
        log_to_render(f"Traceback: {traceback.format_exc()}", "ERROR")
    finally:
        # ═══════════════════════════════════════════════════════════════
        # 🔥 v1.6.1: РЕЗЕРВНОЕ СОХРАНЕНИЕ при disconnect
        # ═══════════════════════════════════════════════════════════════
        log_to_render(f"💾 FINAL SAVE CHECK on disconnect...")
        log_to_render(f"   Current user buffer: {len(current_user_transcript)} chars")
        log_to_render(f"   Current assistant buffer: {len(current_assistant_transcript)} chars")
        
        # Сохраняем оставшиеся буферы
        if (current_user_transcript.strip() or current_assistant_transcript.strip()) and gemini_client.assistant_config:
            turn_count += 1
            log_to_render(f"💾 FINAL SAVE: Found unsaved content on disconnect - saving turn #{turn_count}")
            
            user_msg = current_user_transcript.strip()
            assistant_msg = current_assistant_transcript.strip()
            if assistant_msg:
                assistant_msg += " [disconnected]"
            
            # Save to DB
            try:
                await ConversationService.save_conversation(
                    db=gemini_client.db_session,
                    assistant_id=str(gemini_client.assistant_config.id),
                    user_message=user_msg or "[no user input]",
                    assistant_message=assistant_msg or "[incomplete - disconnected]",
                    session_id=gemini_client.session_id,
                    caller_number=None,
                    tokens_used=0
                )
                log_to_render(f"✅ Final turn #{turn_count} saved to DB")
            except Exception as e:
                log_to_render(f"❌ Final DB save error: {e}", "ERROR")
            
            # Save to Google Sheets
            if gemini_client.assistant_config.google_sheet_id:
                try:
                    sheets_result = await GoogleSheetsService.log_conversation(
                        sheet_id=gemini_client.assistant_config.google_sheet_id,
                        user_message=user_msg or "[no user input]",
                        assistant_message=assistant_msg or "[incomplete - disconnected]",
                        function_result=None,
                        conversation_id=gemini_client.conversation_record_id
                    )
                    
                    if sheets_result:
                        log_to_render(f"✅ Final turn #{turn_count} saved to Google Sheets")
                    else:
                        log_to_render(f"❌ Final Sheets save failed", "ERROR")
                except Exception as e:
                    log_to_render(f"❌ Final Sheets error: {e}", "ERROR")

        # ═══════════════════════════════════════════════════════════════
        # 🆕 v4.0: WEBHOOK УВЕДОМЛЕНИЕ при disconnect
        # Отправляем один итоговый webhook с полным диалогом из БД
        # ═══════════════════════════════════════════════════════════════
        try:
            if gemini_client and gemini_client.assistant_config and gemini_client.assistant_config.user_id:
                from backend.db.session import SessionLocal
                from backend.models.user import User
                from backend.services.webhook_notification import send_webhook_safe

                webhook_db = SessionLocal()
                try:
                    user = webhook_db.query(User).get(gemini_client.assistant_config.user_id)
                    if user and user.has_webhook_config():
                        log_to_render(f"🔗 [WEBHOOK] Sending conversation.completed for session {gemini_client.session_id}")

                        await send_webhook_safe(
                            db=webhook_db,
                            webhook_url=user.webhook_url,
                            webhook_enabled=user.webhook_enabled,
                            source="web_chat",
                            session_id=gemini_client.session_id,
                            assistant_id=str(gemini_client.assistant_config.id),
                            assistant_name=gemini_client.assistant_config.name,
                            assistant_type="gemini",
                            # Эти поля None для веб-чата:
                            caller_number=None,
                            call_direction=None,
                            duration_seconds=None,
                            call_cost=None,
                            record_url=None,
                        )
                        log_to_render(f"✅ [WEBHOOK] Notification sent")
                    else:
                        log_to_render(f"ℹ️ [WEBHOOK] No webhook configured for user")
                finally:
                    webhook_db.close()
        except Exception as wh_error:
            log_to_render(f"❌ [WEBHOOK] Error sending webhook: {wh_error}", "ERROR")

        log_to_render(f"📊 Final handler stats:")
        log_to_render(f"   Total events processed: {event_count}")
        log_to_render(f"   Functions executed: {function_execution_count}")
        log_to_render(f"   Transcript events received: {transcript_events_received}")
        log_to_render(f"   Total turns saved: {turn_count}")
