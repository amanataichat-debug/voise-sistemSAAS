# backend/websockets/handler_realtime_new.py
"""
🚀 PRODUCTION VERSION 3.0 - OpenAI Realtime API Handler (gpt-realtime-2)

✅ v3.0: Migration to gpt-realtime-2
✅ Compatible with openai_client_new v4.0
✅ Function logs linked via conversation_id
✅ Async function execution (non-blocking)
✅ Server VAD only — client commit ignored

Previous versions:
✅ v2.12.4: Server VAD only
✅ v2.12.3: Function logs linked
✅ v2.12.2: Function call transcript preservation
✅ v2.12.1: DB session fix for async tasks
✅ v2.12: No duplicate conversations - single source of truth
✅ v2.11.2: Function name detection, FunctionLog tracking
✅ v2.10: Async function calls (non-blocking)
✅ v2.9: Async logging optimizations
✅ v2.8: Save each dialog as separate DB record

✅ Ready for production deployment
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
from backend.models.assistant import AssistantConfig
from backend.models.conversation import Conversation
from backend.models.elevenlabs import ElevenLabsAgent
from backend.utils.audio_utils import base64_to_audio_buffer
from backend.websockets.openai_client_new import OpenAIRealtimeClientNew
from backend.services.google_sheets_service import GoogleSheetsService
from backend.services.conversation_service import ConversationService
from backend.services.function_log_service import FunctionLogService

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
active_connections_new: Dict[str, List[WebSocket]] = {}

# 🔍 DEBUG MODE - Set to False in production after debugging
ENABLE_DETAILED_LOGGING = False


def log_to_render(message: str, level: str = "INFO"):
    """Force log to Render stdout immediately"""
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    log_msg = f"{timestamp} - [REALTIME-2 v3.0] {level} - {message}"
    print(log_msg, flush=True)  # Force flush to stdout
    if level == "ERROR":
        logger.error(message)
    elif level == "WARNING":
        logger.warning(message)
    else:
        logger.info(message)


async def async_save_to_google_sheets(sheet_id: str, user_message: str, assistant_message: str, function_result=None, conversation_id: str = None, context: str = ""):
    """
    🚀 v2.9: Async Google Sheets save (non-blocking)
    """
    try:
        if not sheet_id:
            return
        
        log_to_render(f"📊 [ASYNC] Logging to Google Sheets ({context})")
        log_to_render(f"   Sheet ID: {sheet_id[:20]}...")
        
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
            log_to_render(f"✅ [ASYNC] Google Sheets logged successfully ({sheets_time:.3f}s) - {context}")
        else:
            log_to_render(f"❌ [ASYNC] Google Sheets logging failed ({sheets_time:.3f}s) - {context}", "WARNING")
            
    except Exception as e:
        log_to_render(f"❌ [ASYNC] Google Sheets error: {e}", "ERROR")
        log_to_render(f"Traceback: {traceback.format_exc()}", "ERROR")


async def async_save_dialog_to_db(assistant_id: str, user_message: str, assistant_message: str, session_id: str):
    """
    🚀 v3.0: Save dialog to database.
    
    ✅ FIX: Creates NEW db session inside task (original session may be closed)
    
    NOTE: In v3.3 of openai_client, a conversation record is created at session start.
    This function creates ADDITIONAL records for each dialog turn.
    """
    from backend.db.session import SessionLocal
    
    db = None
    try:
        if not user_message or not assistant_message:
            log_to_render(f"⚠️ [v3.0] Skipping dialog save - missing data")
            log_to_render(f"   user: {bool(user_message)}, assistant: {bool(assistant_message)}")
            return
        
        # 🆕 v2.12.1 FIX: Create NEW session inside async task
        db = SessionLocal()
        
        log_to_render(f"💾 [v3.0] Saving dialog as NEW DB record")
        log_to_render(f"   Session ID: {session_id}")
        log_to_render(f"   User: {user_message[:50]}...")
        log_to_render(f"   Assistant: {assistant_message[:50]}...")
        
        result = await ConversationService.save_conversation(
            db=db,
            assistant_id=assistant_id,
            user_message=user_message,
            assistant_message=assistant_message,
            session_id=session_id,
            caller_number=None,
            tokens_used=0
        )
        
        if result:
            log_to_render(f"✅ [v3.0] Dialog saved successfully: {result.id}")
        else:
            log_to_render(f"⚠️ [v3.0] Dialog save returned None", "WARNING")
        
    except Exception as e:
        log_to_render(f"❌ [v3.0] Dialog save error: {e}", "ERROR")
        log_to_render(f"Traceback: {traceback.format_exc()}", "ERROR")
    finally:
        if db:
            db.close()


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
    🆕 v3.0: Async function log save to function_logs table (non-blocking)
    ✅ FIX: Creates NEW session inside task (original session may be closed)
    ✅ FIX: conversation_id now properly passed from openai_client.conversation_record_id
    """
    from backend.db.session import SessionLocal
    
    db = None
    try:
        db = SessionLocal()
        
        log_to_render(f"📝 [FUNC-LOG v3.0] Saving function call to database")
        log_to_render(f"   Function: {function_name}")
        log_to_render(f"   Status: {status}")
        log_to_render(f"   Execution time: {execution_time_ms:.2f}ms")
        log_to_render(f"   Conversation ID: {conversation_id}")  # 🆕 v3.0: Log conversation_id
        
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
            log_to_render(f"✅ [FUNC-LOG v3.0] Function log saved: {log_entry.id}")
        else:
            log_to_render(f"⚠️ [FUNC-LOG v3.0] Function log save returned None", "WARNING")
        
    except Exception as e:
        log_to_render(f"❌ [FUNC-LOG v3.0] Error saving function log: {e}", "ERROR")
        log_to_render(f"Traceback: {traceback.format_exc()}", "ERROR")
    finally:
        if db:
            db.close()


# 🔥🔥🔥 v3.0: Async function execution with FunctionLog tracking (FIXED)
async def execute_and_send_function_result(
    openai_client: 'OpenAIRealtimeClientNew',
    websocket: WebSocket,
    function_call_id: str,
    function_name: str,
    arguments: dict,
    context: dict,
    user_transcript: str = ""
):
    """
    🔥 v3.0: Execute function in background WITHOUT blocking assistant speech!
    
    ✨ FIX in v3.0:
    - conversation_id now passed from openai_client.conversation_record_id
    - Function logs properly linked to conversations
    
    Flow:
    1. Function starts executing (this runs in background task)
    2. Assistant continues talking ("Let me check that for you...")
    3. Function completes -> result sent to OpenAI
    4. Assistant integrates result into speech ("Here's what I found...")
    5. Function call logged to function_logs table WITH conversation_id
    """
    execution_start = time.time()
    status = "error"
    error_message = None
    result = None
    
    try:
        log_to_render(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        log_to_render(f"🔥 [ASYNC FUNCTION v3.0] Background execution started")
        log_to_render(f"   Function: {function_name}")
        log_to_render(f"   Call ID: {function_call_id}")
        log_to_render(f"   Arguments: {json.dumps(arguments, ensure_ascii=False)[:200]}")
        log_to_render(f"   Conversation ID: {openai_client.conversation_record_id}")  # 🆕 v3.0
        log_to_render(f"   ⚡ Assistant can CONTINUE speaking while this runs!")
        log_to_render(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        
        # Execute function (this may take 1-10 seconds, but doesn't block!)
        result = await execute_function(
            name=function_name,
            arguments=arguments,
            context=context
        )
        
        execution_time = time.time() - execution_start
        execution_time_ms = execution_time * 1000
        status = "success"
        
        log_to_render(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        log_to_render(f"✅ [ASYNC FUNCTION v3.0] Execution completed!")
        log_to_render(f"   Function: {function_name}")
        log_to_render(f"   Execution time: {execution_time:.3f}s ({execution_time_ms:.2f}ms)")
        log_to_render(f"   Result preview: {str(result)[:200]}...")
        log_to_render(f"   🎯 User heard NO SILENCE during execution!")
        log_to_render(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        
        # 🚀 Fast LLM result display for query_llm
        if function_name == "query_llm":
            log_to_render(f"⚡ [ASYNC v3.0] QUERY_LLM - sending immediate result to frontend")
            
            llm_response_content = ""
            llm_model = "gpt-4"
            
            if isinstance(result, dict):
                llm_response_content = result.get("full_response", result.get("response", result.get("answer", str(result))))
                llm_model = result.get("model_used", result.get("model", "gpt-4"))
            else:
                llm_response_content = str(result)
            
            log_to_render(f"📤 [ASYNC v3.0] Sending llm_result to frontend:")
            log_to_render(f"   Content length: {len(llm_response_content)}")
            log_to_render(f"   Model: {llm_model}")
            
            # Send to frontend IMMEDIATELY
            await websocket.send_json({
                "type": "llm_result",
                "content": llm_response_content,
                "model": llm_model,
                "function": function_name,
                "execution_time": execution_time,
                "timestamp": time.time(),
                "async_execution": True
            })
            
            log_to_render(f"🎯 [ASYNC v3.0] llm_result sent! (execution was in background)")
        
        # 🚀 v3.0: Async background logging (non-blocking)
        log_to_render(f"💾 [ASYNC v3.0] Starting background logging for function result")
        
        # Google Sheets logging (async, non-blocking)
        if openai_client.assistant_config and openai_client.assistant_config.google_sheet_id:
            sheet_id = openai_client.assistant_config.google_sheet_id
            
            asyncio.create_task(
                async_save_to_google_sheets(
                    sheet_id=sheet_id,
                    user_message=user_transcript or f"[Function call: {function_name}]",
                    assistant_message=f"[Async function executed: {function_name}]",
                    function_result=result,
                    conversation_id=openai_client.conversation_record_id,  # 🆕 v3.0: Pass conversation_id
                    context="Async Function Call v3.0"
                )
            )
            log_to_render(f"⚡ [ASYNC v3.0] Google Sheets task created")
        
        # 🆕 v3.0 FIX: FunctionLog save with proper conversation_id
        user_id = str(openai_client.assistant_config.user_id) if openai_client.assistant_config and openai_client.assistant_config.user_id else None
        assistant_id = str(openai_client.assistant_config.id) if openai_client.assistant_config else None
        
        asyncio.create_task(
            async_save_function_log(
                function_name=function_name,
                arguments=arguments,
                result=result if isinstance(result, dict) else {"result": str(result)},
                status=status,
                execution_time_ms=execution_time_ms,
                user_id=user_id,
                assistant_id=assistant_id,
                conversation_id=openai_client.conversation_record_id,  # 🆕 v3.0 FIX: Now properly linked!
                error_message=None
            )
        )
        log_to_render(f"⚡ [ASYNC v3.0] FunctionLog save task created with conversation_id={openai_client.conversation_record_id}")
        
        log_to_render(f"✅ [ASYNC v3.0] All background logging tasks created")
        
        # Send result to OpenAI (v3.3 client with auto response.create)
        log_to_render(f"📤 [ASYNC v3.0] Sending function result to OpenAI...")
        
        delivery_status = await openai_client.send_function_result(function_call_id, result)
        
        log_to_render(f"📬 [ASYNC v3.0] Delivery status:")
        log_to_render(f"   Success: {delivery_status['success']}")
        if not delivery_status['success']:
            log_to_render(f"   Error: {delivery_status['error']}", "ERROR")
        
        if delivery_status["success"]:
            log_to_render(f"✅ [ASYNC v3.0] Function result delivered to OpenAI")
            log_to_render(f"   🎭 Assistant will integrate result into ongoing speech!")
            
            # Notify frontend
            await websocket.send_json({
                "type": "function_call.completed",
                "function": function_name,
                "function_call_id": function_call_id,
                "result": result,
                "execution_time": execution_time,
                "async_execution": True
            })
        else:
            log_to_render(f"❌ [ASYNC v3.0] Function result delivery FAILED", "ERROR")
            
            await websocket.send_json({
                "type": "function_call.delivery_error",
                "function_call_id": function_call_id,
                "error": delivery_status['error'],
                "async_execution": True
            })
        
    except Exception as e:
        execution_time = time.time() - execution_start
        execution_time_ms = execution_time * 1000
        status = "error"
        error_message = str(e)
        
        log_to_render(f"❌ [ASYNC FUNCTION v3.0] Execution ERROR: {e}", "ERROR")
        log_to_render(f"Traceback: {traceback.format_exc()}", "ERROR")
        
        # 🆕 v3.0 FIX: Log error with proper conversation_id
        user_id = str(openai_client.assistant_config.user_id) if openai_client.assistant_config and openai_client.assistant_config.user_id else None
        assistant_id = str(openai_client.assistant_config.id) if openai_client.assistant_config else None
        
        asyncio.create_task(
            async_save_function_log(
                function_name=function_name,
                arguments=arguments,
                result={"error": error_message},
                status=status,
                execution_time_ms=execution_time_ms,
                user_id=user_id,
                assistant_id=assistant_id,
                conversation_id=openai_client.conversation_record_id,  # 🆕 v3.0 FIX: Now properly linked!
                error_message=error_message
            )
        )
        log_to_render(f"⚡ [ASYNC v3.0] Error FunctionLog save task created with conversation_id={openai_client.conversation_record_id}")
        
        # Send error to frontend
        await websocket.send_json({
            "type": "function_call.error",
            "function": function_name,
            "function_call_id": function_call_id,
            "error": str(e),
            "async_execution": True
        })


# Import execute_function for the async handler
from backend.functions import execute_function, normalize_function_name


async def handle_websocket_connection_new(
    websocket: WebSocket,
    assistant_id: str,
    db: Session
) -> None:
    """
    🚀 PRODUCTION v3.0 - Main WebSocket handler with Function Logs Fix
    
    v3.0 improvements:
    - Fixed: Function logs now properly linked via conversation_id
    - Fixed: Messages after function calls saved correctly
    - User transcript preserved across function call responses
    
    Previous features maintained:
    - Full function logging to function_logs table
    - Async function execution
    - Async logging
    """
    client_id = str(uuid.uuid4())
    openai_client = None
    connection_start = time.time()
    
    log_to_render(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    log_to_render(f"🚀 NEW CONNECTION INITIATED (v3.0 - Function Logs Fix)")
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

        # Check for ElevenLabs agents
        elevenlabs_agent = db.query(ElevenLabsAgent).filter(
            ElevenLabsAgent.id == assistant_id
        ).first()
        if elevenlabs_agent:
            log_to_render(f"🔊 ElevenLabs agent detected: {assistant_id}")
            await websocket.send_json({
                "type": "elevenlabs_agent_detected",
                "agent_info": {
                    "id": str(elevenlabs_agent.id),
                    "name": elevenlabs_agent.name
                }
            })
            await asyncio.sleep(1)
            await websocket.close(code=1000)
            return

        # Register connection
        active_connections_new.setdefault(assistant_id, []).append(websocket)
        log_to_render(f"📝 Active connections for {assistant_id}: {len(active_connections_new.get(assistant_id, []))}")

        # Load assistant
        log_to_render(f"🔍 Loading assistant: {assistant_id}")
        if assistant_id == "demo":
            assistant = db.query(AssistantConfig).filter(AssistantConfig.is_public.is_(True)).first()
            if not assistant:
                assistant = db.query(AssistantConfig).first()
        else:
            try:
                uuid_obj = uuid.UUID(assistant_id)
                assistant = db.query(AssistantConfig).get(uuid_obj)
            except ValueError:
                assistant = db.query(AssistantConfig).filter(AssistantConfig.id.cast(str) == assistant_id).first()

        if not assistant:
            log_to_render(f"❌ Assistant not found: {assistant_id}", "ERROR")
            await websocket.send_json({
                "type": "error",
                "error": {"code": "assistant_not_found", "message": "Assistant not found"}
            })
            await websocket.close(code=1008)
            return

        log_to_render(f"✅ Assistant loaded: {getattr(assistant, 'name', assistant_id)}")
        log_to_render(f"   Voice: {getattr(assistant, 'voice', 'default')}")
        log_to_render(f"   Model: gpt-realtime-2")

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
        log_to_render(f"   🔥 v3.0: All function calls will be logged with conversation_id!")

        # Check Google Sheets config
        if hasattr(assistant, 'google_sheet_id') and assistant.google_sheet_id:
            log_to_render(f"📊 Google Sheets logging ENABLED (async mode)")
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
                    log_to_render(f"   Status: {subscription_status}")
                    
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
                
                api_key = user.openai_api_key
                if api_key:
                    log_to_render(f"🔑 API key loaded: {api_key[:10]}...{api_key[-5:]}")
                else:
                    log_to_render(f"⚠️ No API key for user", "WARNING")
        
        if not api_key:
            log_to_render(f"❌ No API key available", "ERROR")
            await websocket.send_json({
                "type": "error",
                "error": {"code": "no_api_key", "message": "OpenAI API key required"}
            })
            await websocket.close(code=1008)
            return

        # Create OpenAI Realtime client
        log_to_render(f"🚀 Creating OpenAI Realtime client v3.3...")
        log_to_render(f"   Client ID: {client_id}")
        log_to_render(f"   API Key: {api_key[:10]}...")
        openai_client = OpenAIRealtimeClientNew(api_key, assistant, client_id, db, user_agent)
        
        log_to_render(f"🔌 Connecting to OpenAI GA API...")
        log_to_render(f"   URL: wss://api.openai.com/v1/realtime?model=gpt-realtime-2")
        connect_start = time.time()
        if not await openai_client.connect():
            log_to_render(f"❌ Failed to connect to OpenAI", "ERROR")
            await websocket.send_json({
                "type": "error",
                "error": {"code": "openai_connection_failed", "message": "Failed to connect to OpenAI"}
            })
            await websocket.close(code=1008)
            return

        connection_time = time.time() - connect_start
        log_to_render(f"✅ Connected to OpenAI in {connection_time:.2f}s")
        log_to_render(f"   Conversation Record ID: {openai_client.conversation_record_id}")  # 🆕 v3.0

        # Send connection status
        await websocket.send_json({
            "type": "connection_status",
            "status": "connected",
            "message": "Connected to Realtime API (v3.0 - gpt-realtime-2)",
            "model": "gpt-realtime-2",
            "functions_enabled": len(enabled_functions),
            "google_sheets": bool(getattr(assistant, 'google_sheet_id', None)),
            "client_id": client_id,
            "performance_mode": "optimized",
            "async_functions": True,
            "function_logging": True,
            "function_logs_linked": True,  # 🆕 v3.0
            "enable_vision": assistant.enable_vision if hasattr(assistant, 'enable_vision') and assistant.enable_vision else False,
            "greeting_message": assistant.greeting_message or "Здравствуйте! Чем я могу вам помочь?"
        })

        # ✅ Ассистент говорит первым после создания сессии
        greeting = assistant.greeting_message or "Здравствуйте! Чем я могу вам помочь?"

        initial_item = {
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": f"[SYSTEM INSTRUCTION: Start the conversation immediately by saying your greeting. Your greeting is: '{greeting}'. Say it now in a natural, friendly way.]"
                    }
                ]
            }
        }
        await openai_client.ws.send(json.dumps(initial_item))

        response_trigger = {
            "type": "response.create"
        }
        await openai_client.ws.send(json.dumps(response_trigger))

        log_to_render(f"[HANDLER] ✅ Initial greeting triggered: {greeting[:50]}...")

        # Interruption state
        interruption_state = {
            "is_user_speaking": False,
            "is_assistant_speaking": False,
            "last_speech_start": 0,
            "last_speech_stop": 0,
            "interruption_count": 0,
            "last_interruption_time": 0
        }

        log_to_render(f"🎬 Starting OpenAI message handler (v3.0 - Function Logs Fix)...")
        # Start OpenAI message handler
        openai_task = asyncio.create_task(
            handle_openai_messages_new(openai_client, websocket, interruption_state)
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
                        
                        if openai_client.is_connected:
                            success = await openai_client.send_screen_context(image_data, silent=is_silent)
                            if success:
                                log_to_render(f"✅ Screen context added to conversation (no response)")
                            else:
                                log_to_render(f"❌ Failed to send screen context", "ERROR")
                        else:
                            log_to_render(f"❌ OpenAI not connected", "ERROR")
                        
                        continue

                    # Audio processing
                    if msg_type == "input_audio_buffer.append":
                        audio_chunk = base64_to_audio_buffer(data["audio"])

                        if openai_client.is_connected:
                            await openai_client.process_audio(audio_chunk)
                        
                        await websocket.send_json({
                            "type": "input_audio_buffer.append.ack", 
                            "event_id": data.get("event_id")
                        })
                        continue

                    if msg_type == "input_audio_buffer.commit":
                        # v3.0: server VAD manages commits, client commit ignored
                        log_to_render(f"⚠️ Client sent manual commit — ignored (server VAD active)")
                        await websocket.send_json({
                            "type": "input_audio_buffer.commit.ack",
                            "event_id": data.get("event_id"),
                            "note": "server_vad_active"
                        })
                        continue

                    if msg_type == "input_audio_buffer.clear":
                        log_to_render(f"🗑️ Clearing audio buffer")
                        if openai_client.is_connected:
                            await openai_client.clear_audio_buffer()
                        await websocket.send_json({
                            "type": "input_audio_buffer.clear.ack",
                            "event_id": data.get("event_id")
                        })
                        continue

                    if msg_type == "response.cancel":
                        log_to_render(f"🛑 Response cancellation requested")
                        if openai_client.is_connected:
                            await openai_client.ws.send(json.dumps({
                                "type": "response.cancel",
                                "event_id": data.get("event_id")
                            }))
                        await websocket.send_json({
                            "type": "response.cancel.ack", 
                            "event_id": data.get("event_id")
                        })
                        continue
                    
                    # Interruption handling
                    if msg_type == "interruption.manual":
                        log_to_render(f"⚡ Manual interruption triggered")
                        await openai_client.handle_interruption()
                        await websocket.send_json({
                            "type": "interruption.manual.ack", 
                            "event_id": data.get("event_id")
                        })
                        continue
                    
                    if msg_type == "audio_playback.stopped":
                        log_to_render(f"🔇 Client stopped playback")
                        openai_client.set_assistant_speaking(False)
                        interruption_state["is_assistant_speaking"] = False
                        continue
                    
                    if msg_type == "speech.user_started":
                        log_to_render(f"🗣️ User started speaking")
                        interruption_state["is_user_speaking"] = True
                        interruption_state["last_speech_start"] = time.time()
                        
                        if interruption_state["is_assistant_speaking"]:
                            log_to_render(f"⚡ User interrupted assistant!")
                            await openai_client.handle_interruption()
                            interruption_state["interruption_count"] += 1
                            interruption_state["last_interruption_time"] = time.time()
                        continue
                    
                    if msg_type == "speech.user_stopped":
                        log_to_render(f"🤐 User stopped speaking")
                        interruption_state["is_user_speaking"] = False
                        interruption_state["last_speech_stop"] = time.time()
                        continue

                elif "bytes" in message:
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
        if not openai_task.done():
            openai_task.cancel()
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
        if openai_client:
            await openai_client.close()
        
        conns = active_connections_new.get(assistant_id, [])
        if websocket in conns:
            conns.remove(websocket)
        log_to_render(f"👋 Connection closed: {client_id}")


async def handle_openai_messages_new(
    openai_client: 'OpenAIRealtimeClientNew', 
    websocket: WebSocket, 
    interruption_state: Dict
):
    """
    🚀 PRODUCTION v3.0 - Handle messages from OpenAI with Function Logs Fix
    
    ✨ FIX in v3.0 - FUNCTION LOGS LINKED:
    - conversation_id properly passed to function log saves
    
    Previous features maintained:
    ✅ v2.12.2: User transcript preservation
    ✅ v2.12: No duplicate conversations
    ✅ v2.11: FunctionLog tracking
    ✅ v2.10: Async function execution (non-blocking)
    ✅ v2.9: Async logging (non-blocking)
    ✅ v2.8: Fast LLM result display
    """
    if not openai_client.is_connected or not openai_client.ws:
        log_to_render(f"❌ OpenAI client not connected", "ERROR")
        return
    
    # Transcripts
    user_transcript = ""
    assistant_transcript = ""
    last_user_transcript = ""  # v2.12.2: Keep user context for function responses
    
    # Function tracking map (call_id -> function metadata)
    function_calls_map = {}
    
    # v2.12.2: Track if current response includes function call
    response_had_function_call = False
    
    # Function buffer
    pending_function_call = {
        "name": None,
        "call_id": None,
        "arguments_buffer": ""
    }
    
    # Metrics
    event_count = 0
    function_execution_count = 0
    
    try:
        log_to_render(f"🎭 OpenAI message handler started (v3.0 - Function Logs Fix)")
        log_to_render(f"   Client ID: {openai_client.client_id}")
        log_to_render(f"   Session ID: {openai_client.session_id}")
        log_to_render(f"   Conversation Record ID: {openai_client.conversation_record_id}")  # 🆕 v3.0
        log_to_render(f"   Enabled functions: {openai_client.enabled_functions}")
        log_to_render(f"   🔥 Functions will execute ASYNC (non-blocking)!")
        log_to_render(f"   📝 All function calls will be logged with conversation_id!")
        
        while True:
            try:
                raw = await openai_client.ws.recv()
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
                        "item.created" in msg_type or
                        "content_part" in msg_type or
                        msg_type in [
                            "input_audio_buffer.speech_started",
                            "input_audio_buffer.speech_stopped",
                            "conversation.interrupted",
                            "response.done",
                            "error"
                        ]
                    )
                )
                
                if should_log:
                    log_to_render(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                    log_to_render(f"📡 OpenAI Event #{event_count}: {msg_type}")
                    log_to_render(f"   Event ID: {response_data.get('event_id', 'N/A')}")
                    
                    if "function" in msg_type or msg_type == "conversation.item.created":
                        log_to_render(f"   🔑 All keys: {list(response_data.keys())}")
                        
                        for field in ["name", "function_name", "call_id", "item_id", "arguments", "item"]:
                            if field in response_data:
                                value = response_data[field]
                                if isinstance(value, dict):
                                    log_to_render(f"   📦 {field}: {json.dumps(value, ensure_ascii=False)[:300]}")
                                else:
                                    log_to_render(f"   📦 {field}: {str(value)[:300]}")
                    log_to_render(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                
                # Track conversation.item.created for function metadata
                if msg_type == "conversation.item.created":
                    item = response_data.get("item", {})
                    item_type = item.get("type")
                    
                    log_to_render(f"📦 Item created:")
                    log_to_render(f"   Type: {item_type}")
                    log_to_render(f"   Item ID: {item.get('id')}")
                    
                    if item_type == "function_call":
                        call_id = item.get("call_id")
                        function_name = item.get("name")
                        
                        log_to_render(f"🔧 Function call item detected:")
                        log_to_render(f"   Call ID: {call_id}")
                        log_to_render(f"   Function name: {function_name}")
                        
                        if call_id and function_name:
                            normalized_name = normalize_function_name(function_name)
                            function_calls_map[call_id] = {
                                "name": normalized_name,
                                "original_name": function_name,
                                "item_id": item.get("id"),
                                "status": "pending",
                                "timestamp": time.time()
                            }
                            log_to_render(f"✅ Function registered in map:")
                            log_to_render(f"   Normalized: {normalized_name}")
                            log_to_render(f"   Map size: {len(function_calls_map)}")
                
                # VAD events
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
                
                if msg_type == "conversation.interrupted":
                    log_to_render(f"⚡ Conversation interrupted by OpenAI")
                    interruption_state["interruption_count"] += 1
                    interruption_state["last_interruption_time"] = time.time()
                    
                    await openai_client.handle_interruption()
                    
                    interruption_state["is_assistant_speaking"] = False
                    openai_client.set_assistant_speaking(False)
                    
                    await websocket.send_json({
                        "type": "conversation.interrupted",
                        "timestamp": interruption_state["last_interruption_time"],
                        "interruption_count": interruption_state["interruption_count"]
                    })
                    continue
                
                if msg_type == "response.cancelled":
                    log_to_render(f"🚫 Response cancelled")
                    interruption_state["is_assistant_speaking"] = False
                    openai_client.set_assistant_speaking(False)
                    
                    await websocket.send_json({
                        "type": "response.cancelled",
                        "timestamp": time.time()
                    })
                    continue
                
                # Error handling
                if msg_type == "error":
                    log_to_render(f"❌ OpenAI API Error:")
                    log_to_render(f"   Full error: {json.dumps(response_data, ensure_ascii=False, indent=2)}", "ERROR")
                    await websocket.send_json(response_data)
                    continue
                
                # Audio output
                if msg_type == "response.output_audio.delta":
                    if not interruption_state["is_assistant_speaking"]:
                        response_id = response_data.get("response_id", f"resp_{time.time()}")
                        log_to_render(f"🔊 Assistant started speaking:")
                        log_to_render(f"   Response ID: {response_id}")
                        interruption_state["is_assistant_speaking"] = True
                        openai_client.set_assistant_speaking(True, response_id)
                        
                        await websocket.send_json({
                            "type": "assistant.speech.started",
                            "response_id": response_id,
                            "timestamp": time.time()
                        })
                    
                    delta_audio = response_data.get("delta", "")
                    if delta_audio:
                        sample_count = len(base64.b64decode(delta_audio)) // 2
                        openai_client.increment_audio_samples(sample_count)
                
                if msg_type == "response.output_audio.done":
                    # gpt-realtime-2 may emit this event more than once; only log/act
                    # on the real transition to avoid duplicate "stopped speaking" logs
                    if interruption_state["is_assistant_speaking"]:
                        log_to_render(f"🔇 Assistant stopped speaking")
                        interruption_state["is_assistant_speaking"] = False
                        openai_client.set_assistant_speaking(False)
                        
                        await websocket.send_json({
                            "type": "assistant.speech.ended",
                            "timestamp": time.time()
                        })
                
                # Text output
                if msg_type == "response.output_text.delta":
                    delta_text = response_data.get("delta", "")
                    if delta_text:
                        await websocket.send_json({
                            "type": "response.text.delta",
                            "delta": delta_text
                        })
                
                if msg_type == "response.output_text.done":
                    await websocket.send_json({
                        "type": "response.text.done"
                    })
                
                # Function execution events
                if msg_type == "response.function_call.started":
                    function_name = response_data.get("function_name") or response_data.get("name")
                    function_call_id = response_data.get("call_id")
                    
                    log_to_render(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                    log_to_render(f"🔧 FUNCTION CALL STARTED")
                    log_to_render(f"   Function: {function_name}")
                    log_to_render(f"   Call ID: {function_call_id}")
                    log_to_render(f"   Timestamp: {time.time()}")
                    log_to_render(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                    
                    if function_name:
                        normalized_name = normalize_function_name(function_name)
                        log_to_render(f"🔄 Normalized name: {normalized_name}")
                        
                        if normalized_name not in openai_client.enabled_functions:
                            log_to_render(f"❌ UNAUTHORIZED function: {normalized_name}", "WARNING")
                            log_to_render(f"   Allowed functions: {openai_client.enabled_functions}", "WARNING")
                            
                            error_response = {
                                "type": "function_call.error",
                                "function": normalized_name,
                                "error": f"Function {function_name} not activated"
                            }
                            await websocket.send_json(error_response)
                            
                            if function_call_id:
                                dummy_result = {
                                    "error": f"Function {normalized_name} not allowed",
                                    "status": "error"
                                }
                                await openai_client.send_function_result(function_call_id, dummy_result)
                            continue
                        
                        pending_function_call = {
                            "name": normalized_name,
                            "call_id": function_call_id,
                            "arguments_buffer": ""
                        }
                        
                        if function_call_id:
                            function_calls_map[function_call_id] = {
                                "name": normalized_name,
                                "original_name": function_name,
                                "status": "started",
                                "timestamp": time.time()
                            }
                            log_to_render(f"✅ Function tracked in map (from .started)")
                        
                        await websocket.send_json({
                            "type": "function_call.started",
                            "function": normalized_name,
                            "function_call_id": function_call_id
                        })
                
                elif msg_type == "response.function_call_arguments.delta":
                    delta = response_data.get("delta", "")
                    call_id = response_data.get("call_id")
                    
                    function_name = response_data.get("name") or response_data.get("function_name")
                    
                    if ENABLE_DETAILED_LOGGING:
                        log_to_render(f"📝 Function arguments delta:")
                        log_to_render(f"   Call ID: {call_id}")
                        log_to_render(f"   Delta length: {len(delta)}")
                        log_to_render(f"   Has name: {bool(function_name)}")
                        if function_name:
                            log_to_render(f"   Name in delta: {function_name}")
                    
                    if function_name and not pending_function_call["name"]:
                        normalized_name = normalize_function_name(function_name)
                        pending_function_call["name"] = normalized_name
                        log_to_render(f"✅ Function name from delta: {normalized_name}")
                        
                        if call_id:
                            function_calls_map[call_id] = {
                                "name": normalized_name,
                                "original_name": function_name,
                                "status": "streaming",
                                "timestamp": time.time()
                            }
                    
                    if call_id and not pending_function_call["call_id"]:
                        pending_function_call["call_id"] = call_id
                    
                    pending_function_call["arguments_buffer"] += delta
                
                # 🔥🔥🔥 v3.0: ASYNC FUNCTION EXECUTION + LOGGING (FIXED)
                elif msg_type == "response.function_call_arguments.done":
                    log_to_render(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                    log_to_render(f"📋 FUNCTION ARGUMENTS DONE (v3.0 ASYNC + LOG LINKED)")
                    log_to_render(f"   Event count: {event_count}")
                    
                    # Multi-source detection strategy
                    function_name = response_data.get("function_name") or response_data.get("name")
                    function_call_id = response_data.get("call_id")
                    arguments_str = response_data.get("arguments", "")
                    
                    log_to_render(f"🔍 Detection attempt #1 (response_data):")
                    log_to_render(f"   Name: {function_name}")
                    log_to_render(f"   Call ID: {function_call_id}")
                    log_to_render(f"   Arguments: {arguments_str[:100]}...")
                    
                    if not function_name:
                        function_name = pending_function_call.get("name")
                        log_to_render(f"🔍 Detection attempt #2 (pending buffer):")
                        log_to_render(f"   Name: {function_name}")
                    
                    if not function_call_id:
                        function_call_id = pending_function_call.get("call_id")
                        log_to_render(f"   Call ID from pending: {function_call_id}")
                    
                    if not arguments_str:
                        arguments_str = pending_function_call.get("arguments_buffer", "")
                        log_to_render(f"   Arguments from buffer: {arguments_str[:100]}...")
                    
                    if not function_name and function_call_id and function_call_id in function_calls_map:
                        function_name = function_calls_map[function_call_id]["name"]
                        log_to_render(f"🔍 Detection attempt #3 (function_calls_map):")
                        log_to_render(f"   Name recovered: {function_name}")
                    
                    if not function_name and len(openai_client.enabled_functions) == 1:
                        function_name = openai_client.enabled_functions[0]
                        log_to_render(f"🔍 Detection attempt #4 (single function fallback):")
                        log_to_render(f"   Using only enabled function: {function_name}")
                    
                    log_to_render(f"📊 Final detection result:")
                    log_to_render(f"   Function: {function_name}")
                    log_to_render(f"   Call ID: {function_call_id}")
                    log_to_render(f"   Arguments length: {len(arguments_str)}")
                    log_to_render(f"   Conversation ID: {openai_client.conversation_record_id}")  # 🆕 v3.0
                    log_to_render(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                    
                    if not function_name:
                        log_to_render(f"❌ CRITICAL: Cannot determine function name!", "ERROR")
                        
                        await websocket.send_json({
                            "type": "function_call.error",
                            "error": "Cannot determine function name",
                            "call_id": function_call_id
                        })
                        
                        pending_function_call = {"name": None, "call_id": None, "arguments_buffer": ""}
                        continue
                    
                    if not function_call_id:
                        log_to_render(f"❌ Missing call_id in response", "ERROR")
                        pending_function_call = {"name": None, "call_id": None, "arguments_buffer": ""}
                        continue
                    
                    normalized_name = normalize_function_name(function_name) or function_name
                    log_to_render(f"🔄 Final normalized name: {normalized_name}")
                    
                    if normalized_name and normalized_name not in openai_client.enabled_functions:
                        log_to_render(f"❌ UNAUTHORIZED function: {normalized_name}", "WARNING")
                        
                        error_response = {
                            "type": "function_call.error",
                            "function": normalized_name,
                            "error": f"Function {function_name} not activated"
                        }
                        await websocket.send_json(error_response)
                        
                        if function_call_id:
                            dummy_result = {
                                "error": f"Function {normalized_name} not allowed",
                                "status": "error"
                            }
                            await openai_client.send_function_result(function_call_id, dummy_result)
                        
                        pending_function_call = {"name": None, "call_id": None, "arguments_buffer": ""}
                        continue
                    
                    # Parse arguments
                    try:
                        arguments = json.loads(arguments_str)
                        log_to_render(f"✅ Arguments parsed successfully:")
                        log_to_render(f"   Type: {type(arguments)}")
                        
                        await websocket.send_json({
                            "type": "function_call.executing",
                            "function": normalized_name,
                            "function_call_id": function_call_id,
                            "arguments": arguments,
                            "async_execution": True
                        })
                        
                        function_execution_count += 1
                        
                        # 🔥🔥🔥 v3.0: ASYNC EXECUTION WITH PROPER conversation_id
                        log_to_render(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                        log_to_render(f"🔥 v3.0: LAUNCHING ASYNC FUNCTION EXECUTION")
                        log_to_render(f"   Function: {normalized_name}")
                        log_to_render(f"   Conversation ID: {openai_client.conversation_record_id}")
                        log_to_render(f"   ⚡ Assistant will CONTINUE speaking while function executes!")
                        log_to_render(f"   📝 Function call will be logged with conversation_id!")
                        log_to_render(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                        
                        # 🚀 Launch function execution in background (NON-BLOCKING!)
                        asyncio.create_task(
                            execute_and_send_function_result(
                                openai_client=openai_client,
                                websocket=websocket,
                                function_call_id=function_call_id,
                                function_name=normalized_name,
                                arguments=arguments,
                                context={
                                    "assistant_config": openai_client.assistant_config,
                                    "client_id": openai_client.client_id,
                                    "db_session": openai_client.db_session,
                                    "websocket": websocket
                                },
                                user_transcript=user_transcript
                            )
                        )
                        
                        log_to_render(f"🎯 v3.0: Function task created!")
                        log_to_render(f"   ⚡ Continuing to process messages immediately!")
                        
                        # v2.12.2: Mark that this response has function call
                        response_had_function_call = True
                        log_to_render(f"📝 [v3.0] response_had_function_call = True")
                        
                    except json.JSONDecodeError as e:
                        log_to_render(f"❌ Function args parse error: {e}", "ERROR")
                        await websocket.send_json({
                            "type": "error",
                            "error": {"code": "function_args_error", "message": str(e)}
                        })
                    except Exception as e:
                        log_to_render(f"❌ Function setup ERROR: {e}", "ERROR")
                        log_to_render(f"Traceback: {traceback.format_exc()}", "ERROR")
                        await websocket.send_json({
                            "type": "error",
                            "error": {"code": "function_setup_error", "message": str(e)}
                        })
                    
                    # Clear pending
                    pending_function_call = {"name": None, "call_id": None, "arguments_buffer": ""}
                    
                    # Update map status
                    if function_call_id in function_calls_map:
                        function_calls_map[function_call_id]["status"] = "executing_async"

                elif msg_type == "response.content_part.added":
                    if "text" in response_data.get("content", {}):
                        new_text = response_data.get("content", {}).get("text", "")
                        assistant_transcript = new_text
                        log_to_render(f"📝 Assistant text content: {new_text[:100]}...")
                
                # Transcripts
                if msg_type == "conversation.item.input_audio_transcription.completed":
                    if "transcript" in response_data:
                        user_transcript = response_data.get("transcript", "")
                        log_to_render(f"👤 USER TRANSCRIPT: {user_transcript}")
                
                # Transcript events (gpt-realtime-2 uses "output_audio_transcript", legacy GA uses "audio_transcript")
                if msg_type in ("response.audio_transcript.delta", "response.output_audio_transcript.delta"):
                    delta_text = response_data.get("delta", "")
                    assistant_transcript += delta_text

                if msg_type in ("response.audio_transcript.done", "response.output_audio_transcript.done"):
                    transcript = response_data.get("transcript", "")
                    if transcript:
                        assistant_transcript = transcript
                        log_to_render(f"🤖 ASSISTANT TRANSCRIPT: {assistant_transcript}")
                
                # Convert audio delta for client
                if msg_type == "response.output_audio.delta":
                    await websocket.send_json({
                        "type": "response.audio.delta",
                        "delta": response_data.get("delta", "")
                    })
                    continue
                
                # ============================================================================
                # v3.0: RESPONSE DONE - Save dialog
                # ============================================================================
                if msg_type == "response.done":
                    log_to_render(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                    log_to_render(f"🏁 RESPONSE DONE EVENT RECEIVED (v3.0)")
                    log_to_render(f"   user_transcript: '{user_transcript}' (len={len(user_transcript)})")
                    log_to_render(f"   assistant_transcript: '{assistant_transcript}' (len={len(assistant_transcript)})")
                    log_to_render(f"   last_user_transcript: '{last_user_transcript}' (len={len(last_user_transcript)})")
                    log_to_render(f"   response_had_function_call: {response_had_function_call}")
                    
                    # Wait for transcripts
                    if not user_transcript and not last_user_transcript:
                        log_to_render(f"⏳ Waiting 0.5s for transcripts to arrive...")
                        await asyncio.sleep(0.5)
                    
                    log_to_render(f"   Total events: {event_count}")
                    log_to_render(f"   Functions executed: {function_execution_count}")
                    log_to_render(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                    
                    if interruption_state["is_assistant_speaking"]:
                        interruption_state["is_assistant_speaking"] = False
                        openai_client.set_assistant_speaking(False)
                        
                        await websocket.send_json({
                            "type": "assistant.speech.ended",
                            "timestamp": time.time()
                        })
                    
                    # Determine effective user message for saving
                    effective_user_transcript = user_transcript
                    
                    # If user_transcript is empty but we have last_user_transcript
                    if not effective_user_transcript and last_user_transcript:
                        effective_user_transcript = last_user_transcript
                        log_to_render(f"🔄 [v3.0] Using last_user_transcript: '{effective_user_transcript[:50]}...'")
                    
                    # Save conversation if we have assistant response
                    if effective_user_transcript and assistant_transcript:
                        log_to_render(f"💾 [v3.0] Saving dialog with context")
                        log_to_render(f"   User: {effective_user_transcript[:50]}...")
                        log_to_render(f"   Assistant: {assistant_transcript[:50]}...")
                        
                        asyncio.create_task(
                            async_save_dialog_to_db(
                                str(openai_client.assistant_config.id),
                                effective_user_transcript,
                                assistant_transcript,
                                openai_client.session_id
                            )
                        )
                        log_to_render(f"⚡ [v3.0] Dialog save task created")
                        
                        # Google Sheets logging (async, non-blocking)
                        if openai_client.assistant_config and openai_client.assistant_config.google_sheet_id:
                            asyncio.create_task(
                                async_save_to_google_sheets(
                                    sheet_id=openai_client.assistant_config.google_sheet_id,
                                    user_message=effective_user_transcript,
                                    assistant_message=assistant_transcript,
                                    function_result=None,
                                    conversation_id=openai_client.conversation_record_id,  # 🆕 v3.0
                                    context="Dialog v3.0"
                                )
                            )
                            log_to_render(f"⚡ [v3.0] Google Sheets task created")
                    else:
                        log_to_render(f"⚠️ [v3.0] Skipping dialog save - missing transcripts")
                        log_to_render(f"   effective_user: '{effective_user_transcript}'")
                        log_to_render(f"   assistant: '{assistant_transcript}'")
                    
                    # Save current user_transcript for potential function response
                    if user_transcript:
                        last_user_transcript = user_transcript
                        log_to_render(f"📝 [v3.0] Saved last_user_transcript: '{last_user_transcript[:50]}...'")
                    
                    # Reset transcripts for next turn
                    user_transcript = ""
                    assistant_transcript = ""
                    
                    # Reset function call flag for next response
                    response_had_function_call = False
                
                # Forward all other messages to client
                await websocket.send_json(response_data)

            except ConnectionClosed as e:
                log_to_render(f"⚠️ OpenAI connection closed: {e}", "WARNING")
                if await openai_client.reconnect():
                    log_to_render(f"✅ Reconnected to OpenAI")
                    continue
                else:
                    log_to_render(f"❌ Reconnection failed", "ERROR")
                    await websocket.send_json({
                        "type": "error",
                        "error": {"code": "openai_connection_lost", "message": "Connection lost"}
                    })
                    break

    except (ConnectionClosed, asyncio.CancelledError):
        log_to_render(f"👋 Handler terminated for {openai_client.client_id}")
        return
    except Exception as e:
        log_to_render(f"❌ CRITICAL Handler error: {e}", "ERROR")
        log_to_render(f"Traceback: {traceback.format_exc()}", "ERROR")
    finally:
        # ═══════════════════════════════════════════════════════════════
        # 🆕 v4.0: WEBHOOK УВЕДОМЛЕНИЕ при disconnect
        # Отправляем один итоговый webhook с полным диалогом из БД
        # ═══════════════════════════════════════════════════════════════
        try:
            if openai_client and openai_client.assistant_config and openai_client.assistant_config.user_id:
                from backend.db.session import SessionLocal
                from backend.models.user import User
                from backend.services.webhook_notification import send_webhook_safe

                webhook_db = SessionLocal()
                try:
                    user = webhook_db.query(User).get(openai_client.assistant_config.user_id)
                    if user and user.has_webhook_config():
                        log_to_render(f"🔗 [WEBHOOK] Sending conversation.completed for session {openai_client.session_id}")

                        await send_webhook_safe(
                            db=webhook_db,
                            webhook_url=user.webhook_url,
                            webhook_enabled=user.webhook_enabled,
                            source="web_chat",
                            session_id=openai_client.session_id,
                            assistant_id=str(openai_client.assistant_config.id),
                            assistant_name=openai_client.assistant_config.name,
                            assistant_type="openai",
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
        log_to_render(f"   Functions executed (async): {function_execution_count}")
        log_to_render(f"   Function map entries: {len(function_calls_map)}")
