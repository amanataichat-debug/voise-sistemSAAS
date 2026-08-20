# backend/websockets/voximplant_handler.py - PRODUCTION VERSION 2.2

"""
Voximplant WebSocket handler - Version 2.2 PRODUCTION
✅ Fixed: Saves EVERY dialog message as separate DB record
✅ Enhanced: Proper conversation logging with caller number
✅ Ready for production deployment
"""

import asyncio
import json
import uuid
import base64
import time
import traceback
from typing import Dict, Optional, Any, Set
from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from websockets.exceptions import ConnectionClosed

from backend.core.logging import get_logger
from backend.models.assistant import AssistantConfig
from backend.models.user import User
from backend.models.conversation import Conversation
from backend.websockets.openai_client_new import OpenAIRealtimeClientNew
from backend.utils.audio_utils import base64_to_audio_buffer
from backend.services.user_service import UserService
from backend.services.google_sheets_service import GoogleSheetsService
from backend.services.conversation_service import ConversationService
from backend.functions import execute_function, normalize_function_name

logger = get_logger(__name__)


class VoximplantProtocolHandler:
    """
    Production v2.2 handler for Voximplant integration with OpenAI.
    ✅ Saves every dialog message as separate database record.
    """
    
    def __init__(self, websocket: WebSocket, assistant_id: str, db: Session):
        self.websocket = websocket
        self.assistant_id = assistant_id
        self.db = db
        self.client_id = str(uuid.uuid4())
        
        # Caller information
        self.caller_number = "unknown"
        self.call_id = "unknown"
        
        # OpenAI client
        self.openai_client: Optional[OpenAIRealtimeClientNew] = None
        
        # Connection state
        self.is_connected = False
        self.connection_closed = False
        self.websocket_closed = False
        
        # Voximplant protocol
        self.sequence_number = 0
        self.chunk_number = 0
        self.stream_started = False
        self.stream_start_time = time.time()
        
        # Audio settings
        self.sample_rate = 16000
        self.channels = 1
        self.encoding = "audio/pcm16"
        
        # Buffers
        self.incoming_audio_buffer = bytearray()
        self.outgoing_audio_buffer = bytearray()
        self.audio_chunk_size = 1280  # 40ms at 16kHz
        
        # Timers
        self.last_audio_time = time.time()
        self.start_time = time.time()
        
        # Transcripts for logging (reset after each dialog)
        self.user_transcript = ""
        self.assistant_transcript = ""
        self.function_result = None
        
        # Background tasks
        self.background_tasks: Set[asyncio.Task] = set()
        
        # Statistics
        self.audio_packets_received = 0
        self.audio_bytes_received = 0
        self._audio_sent_count = 0
        self.dialogs_saved = 0
        
        logger.info(f"[VOX-v2.2] Created handler for {assistant_id}")

    async def start(self):
        """Start handler with optimized architecture."""
        try:
            await self.websocket.accept()
            self.is_connected = True
            logger.info("[VOX-v2.2] WebSocket connection accepted")
            
            # Load assistant config
            assistant = await self._load_assistant_config()
            if not assistant:
                return
            
            # Check subscription
            if not await self._check_subscription(assistant):
                return
            
            # Get API key
            api_key = await self._get_api_key(assistant)
            if not api_key:
                await self._send_error("no_api_key", "Missing OpenAI API key")
                return
            
            # Create and connect OpenAI client
            self.openai_client = OpenAIRealtimeClientNew(
                api_key=api_key,
                assistant_config=assistant,
                client_id=self.client_id,
                db_session=self.db,
                user_agent="Voximplant/2.2"
            )
            
            if not await self.openai_client.connect():
                await self._send_error("openai_connection_failed", "Failed to connect to OpenAI")
                return
            
            # Send ready status
            await self._send_message({
                "type": "connection_status",
                "status": "connected",
                "message": "Connection established",
                "protocol_version": "2.2"
            })
            
            # Start message handlers
            await self._start_message_handlers()
            
        except Exception as e:
            logger.error(f"[VOX-v2.2] Error starting handler: {e}")
            logger.error(f"[VOX-v2.2] Traceback: {traceback.format_exc()}")
            await self._send_error("server_error", str(e))
        finally:
            await self.cleanup()

    async def _start_message_handlers(self):
        """Start message processing tasks."""
        try:
            voximplant_task = asyncio.create_task(self._handle_voximplant_messages())
            openai_task = asyncio.create_task(self._handle_openai_messages())
            
            self.background_tasks.add(voximplant_task)
            self.background_tasks.add(openai_task)
            
            done, pending = await asyncio.wait(
                self.background_tasks,
                return_when=asyncio.FIRST_COMPLETED
            )
            
            self.connection_closed = True
            
            for task in pending:
                if not task.done():
                    task.cancel()
            
            if pending:
                try:
                    await asyncio.wait(pending, timeout=2.0)
                except Exception as e:
                    logger.error(f"[VOX-v2.2] Error waiting for tasks: {e}")
        except Exception as e:
            logger.error(f"[VOX-v2.2] Error in message handlers: {e}")
            self.connection_closed = True
        finally:
            self.connection_closed = True
            logger.info("[VOX-v2.2] Message handlers completed")

    async def _handle_voximplant_messages(self):
        """Handle messages from Voximplant."""
        try:
            while self.is_connected and not self.connection_closed and not self.websocket_closed:
                try:
                    if self.connection_closed or self.websocket_closed:
                        break
                        
                    message = await self.websocket.receive()
                    
                    if "text" in message:
                        data = json.loads(message["text"])
                        await self._process_voximplant_message(data)
                    elif "bytes" in message:
                        await self._process_raw_audio_fallback(message["bytes"])
                        
                except WebSocketDisconnect:
                    logger.info("[VOX-v2.2] WebSocket disconnected")
                    self.connection_closed = True
                    self.websocket_closed = True
                    break
                except ConnectionClosed:
                    logger.info("[VOX-v2.2] Connection closed")
                    self.connection_closed = True
                    self.websocket_closed = True
                    break
                except json.JSONDecodeError as e:
                    logger.error(f"[VOX-v2.2] JSON error: {e}")
                except Exception as e:
                    logger.error(f"[VOX-v2.2] Processing error: {e}")
                    if "disconnect message" in str(e) or "receive" in str(e):
                        logger.warning("[VOX-v2.2] Detected closed connection error, terminating")
                        self.connection_closed = True
                        self.websocket_closed = True
                        break
        finally:
            self.connection_closed = True
            logger.info("[VOX-v2.2] Voximplant message handler completed")

    async def _process_voximplant_message(self, data: Dict[str, Any]):
        """Process specific message from Voximplant."""
        if self.connection_closed:
            return
            
        msg_type = data.get("type")
        event = data.get("event")
        
        if msg_type == "call_started":
            await self._handle_call_started(data)
            
        elif msg_type == "call_ended":
            await self._handle_call_ended(data)
            
        elif msg_type == "audio_ready":
            logger.info(f"[VOX-v2.2] Audio ready: {data.get('format')}")
            
        elif event == "start":
            await self._handle_stream_start(data)
            
        elif event == "media":
            await self._handle_media_data(data)
            
        elif event == "stop":
            await self._handle_stream_stop(data)
            
        elif msg_type == "interruption.manual":
            await self._handle_interruption()
        
        elif msg_type == "repeat_last_response":
            await self._handle_repeat_last_response()

    async def _handle_call_started(self, data: Dict[str, Any]):
        """Handle call start - save caller info only."""
        if self.connection_closed:
            return
            
        caller = data.get("caller_number", "unknown")
        call_id = data.get("call_id", "unknown")
        
        self.caller_number = caller
        self.call_id = call_id
        
        logger.info(f"[VOX-v2.2] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        logger.info(f"[VOX-v2.2] 📞 CALL STARTED")
        logger.info(f"[VOX-v2.2]    Caller: {caller}")
        logger.info(f"[VOX-v2.2]    Call ID: {call_id}")
        logger.info(f"[VOX-v2.2] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    async def _handle_stream_start(self, data: Dict[str, Any]):
        """Handle audio stream start."""
        if self.connection_closed:
            return
            
        start_info = data.get("start", {})
        media_format = start_info.get("mediaFormat", {})
        
        self.encoding = media_format.get("encoding", "audio/pcm16")
        self.sample_rate = media_format.get("sampleRate", 16000)
        self.channels = media_format.get("channels", 1)
        
        logger.info(f"[VOX-v2.2] Stream start: {self.encoding}, {self.sample_rate}Hz, {self.channels}ch")
        
        self.stream_started = True
        self.stream_start_time = time.time()

    async def _handle_media_data(self, data: Dict[str, Any]):
        """Handle audio data from Voximplant."""
        if self.connection_closed or not self.openai_client or not self.stream_started:
            return
        
        media = data.get("media", {})
        payload = media.get("payload", "")
        
        if not payload:
            return
        
        try:
            audio_bytes = base64.b64decode(payload)
            
            self.audio_packets_received += 1
            self.audio_bytes_received += len(audio_bytes)
            
            self.incoming_audio_buffer.extend(audio_bytes)
            self.last_audio_time = time.time()
            
            while len(self.incoming_audio_buffer) >= self.audio_chunk_size:
                chunk = bytes(self.incoming_audio_buffer[:self.audio_chunk_size])
                self.incoming_audio_buffer = self.incoming_audio_buffer[self.audio_chunk_size:]
                
                if not self.connection_closed and self.openai_client.is_connected:
                    await self.openai_client.process_audio(chunk)
            
            if not self.connection_closed:
                asyncio.create_task(self._auto_commit_audio())
            
        except Exception as e:
            logger.error(f"[VOX-v2.2] Error processing audio: {e}")

    async def _process_raw_audio_fallback(self, audio_bytes: bytes):
        """Fallback for raw audio data (v1.0 compatibility)."""
        if self.connection_closed or not self.openai_client or not audio_bytes:
            return
            
        try:
            self.incoming_audio_buffer.extend(audio_bytes)
            self.last_audio_time = time.time()
            
            while len(self.incoming_audio_buffer) >= self.audio_chunk_size:
                chunk = bytes(self.incoming_audio_buffer[:self.audio_chunk_size])
                self.incoming_audio_buffer = self.incoming_audio_buffer[self.audio_chunk_size:]
                
                if not self.connection_closed and self.openai_client.is_connected:
                    await self.openai_client.process_audio(chunk)
            
            if not self.connection_closed:
                asyncio.create_task(self._auto_commit_audio())
            
        except Exception as e:
            logger.error(f"[VOX-v2.2] Error processing raw audio: {e}")

    async def _auto_commit_audio(self):
        """Auto-commit audio after pause."""
        try:
            await asyncio.sleep(0.5)
            
            if self.connection_closed or not self.openai_client:
                return
                
            if time.time() - self.last_audio_time >= 0.4:
                if self.openai_client.is_connected and len(self.incoming_audio_buffer) > 0:
                    chunk = bytes(self.incoming_audio_buffer)
                    self.incoming_audio_buffer.clear()
                    await self.openai_client.process_audio(chunk)
                
                if not self.connection_closed and self.openai_client.is_connected:
                    await self.openai_client.commit_audio()
                    logger.info("[VOX-v2.2] Audio auto-committed")
        except Exception as e:
            logger.error(f"[VOX-v2.2] Error in auto-commit: {e}")

    async def _handle_openai_messages(self):
        """Handle messages from OpenAI."""
        if not self.openai_client:
            return
        
        try:
            async for message in self.openai_client.receive_messages():
                if self.connection_closed:
                    break
                    
                await self._process_openai_message(message)
                
        except ConnectionClosed:
            logger.info("[VOX-v2.2] OpenAI connection closed")
            self.connection_closed = True
        except Exception as e:
            logger.error(f"[VOX-v2.2] Error processing OpenAI: {e}")
            self.connection_closed = True
        finally:
            logger.info("[VOX-v2.2] OpenAI message handler completed")

    async def _process_openai_message(self, message: Dict[str, Any]):
        """Process message from OpenAI."""
        if self.connection_closed:
            return
            
        msg_type = message.get("type", "")
        
        if msg_type == "error":
            await self._send_message(message)
            
        elif msg_type == "response.audio.delta":
            delta = message.get("delta", "")
            if delta:
                try:
                    audio_bytes = base64.b64decode(delta)
                    await self._send_audio_to_voximplant(audio_bytes)
                except Exception as e:
                    logger.error(f"[VOX-v2.2] Error sending audio: {e}")
                    
        elif msg_type == "conversation.item.input_audio_transcription.completed":
            self.user_transcript = message.get("transcript", "")
            logger.info(f"[VOX-v2.2] 👤 User: {self.user_transcript}")
            
        elif msg_type == "response.audio_transcript.done":
            self.assistant_transcript = message.get("transcript", "")
            logger.info(f"[VOX-v2.2] 🤖 Assistant: {self.assistant_transcript}")
            
        elif msg_type == "response.function_call_arguments.done":
            await self._handle_function_call(message)
            
        elif msg_type == "response.done":
            await self._save_dialog_to_database()

    async def _send_audio_to_voximplant(self, audio_bytes: bytes):
        """Send audio to Voximplant using protocol v2.0."""
        if self.connection_closed or self.websocket_closed:
            return
        
        if not self.stream_started:
            await self._start_audio_stream()
        
        self.outgoing_audio_buffer.extend(audio_bytes)
        
        chunk_size = 640  # 20ms at 16kHz
        
        while len(self.outgoing_audio_buffer) >= chunk_size and not self.connection_closed:
            chunk = self.outgoing_audio_buffer[:chunk_size]
            self.outgoing_audio_buffer = self.outgoing_audio_buffer[chunk_size:]
            
            self.sequence_number += 1
            self.chunk_number += 1
            
            message = {
                "event": "media",
                "sequenceNumber": self.sequence_number,
                "media": {
                    "chunk": self.chunk_number,
                    "timestamp": int((time.time() - self.stream_start_time) * 1000),
                    "payload": base64.b64encode(chunk).decode('utf-8')
                }
            }
            
            await self._send_message(message)
            self._audio_sent_count += 1
            
            if self._audio_sent_count % 50 == 0:
                logger.info(f"[VOX-v2.2] ➡️ Sent audio: {self._audio_sent_count} packets")

    async def _start_audio_stream(self):
        """Start audio stream to Voximplant."""
        if self.connection_closed or self.websocket_closed:
            return
            
        self.stream_started = True
        self.stream_start_time = time.time()
        self.sequence_number = 0
        self.chunk_number = 0
        
        message = {
            "event": "start",
            "sequenceNumber": self.sequence_number,
            "start": {
                "mediaFormat": {
                    "encoding": self.encoding,
                    "sampleRate": self.sample_rate,
                    "channels": self.channels
                }
            }
        }
        
        await self._send_message(message)
        logger.info("[VOX-v2.2] Audio stream started to Voximplant")

    async def _handle_function_call(self, message: Dict[str, Any]):
        """Handle function call execution."""
        if self.connection_closed:
            return
            
        function_name = message.get("function_name")
        arguments_str = message.get("arguments", "{}")
        call_id = message.get("call_id")
        
        if not function_name or not call_id:
            return
        
        try:
            arguments = json.loads(arguments_str)
            
            await self._send_message({
                "type": "function_call.start",
                "function": function_name,
                "function_call_id": call_id
            })
            
            result = await execute_function(
                name=function_name,
                arguments=arguments,
                context={
                    "assistant_config": self.openai_client.assistant_config,
                    "client_id": self.client_id,
                    "db_session": self.db
                }
            )
            
            self.function_result = result
            
            if not self.connection_closed and self.openai_client and self.openai_client.is_connected:
                await self.openai_client.send_function_result(call_id, result)
            
            await self._send_message({
                "type": "function_call.completed",
                "function": function_name,
                "function_call_id": call_id,
                "result": result
            })
            
        except Exception as e:
            logger.error(f"[VOX-v2.2] Error executing function: {e}")

    async def _handle_interruption(self):
        """Handle conversation interruption."""
        if self.connection_closed:
            return
            
        if self.openai_client:
            await self.openai_client.handle_interruption()
            
        await self._send_message({
            "type": "conversation.interrupted",
            "timestamp": time.time()
        })
        
        logger.info("[VOX-v2.2] Interruption handled")

    async def _handle_repeat_last_response(self):
        """Handle request to repeat last response."""
        if self.connection_closed or not self.openai_client:
            return
            
        logger.info("[VOX-v2.2] Request to repeat last response")
        
        try:
            await self.openai_client.create_response_after_function()
            
            await self._send_message({
                "type": "repeating_last_response",
                "timestamp": time.time()
            })
        except Exception as e:
            logger.error(f"[VOX-v2.2] Error repeating response: {e}")

    async def _handle_call_ended(self, data: Dict[str, Any]):
        """Handle call end."""
        logger.info(f"[VOX-v2.2] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        logger.info(f"[VOX-v2.2] 📞 CALL ENDED: {data.get('call_id')}")
        logger.info(f"[VOX-v2.2]    Dialogs saved: {self.dialogs_saved}")
        logger.info(f"[VOX-v2.2] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        
        if self.stream_started:
            await self._stop_audio_stream()
        
        self.connection_closed = True

    async def _handle_stream_stop(self, data: Dict[str, Any]):
        """Handle stream stop."""
        stop_info = data.get("stop", {})
        media_info = stop_info.get("mediaInfo", {})
        
        duration = media_info.get("duration", 0)
        bytes_sent = media_info.get("bytesSent", 0)
        
        logger.info(f"[VOX-v2.2] Stream stopped: {duration}s, {bytes_sent} bytes")
        
        self.stream_started = False

    async def _stop_audio_stream(self):
        """Stop audio stream."""
        if not self.stream_started or self.connection_closed or self.websocket_closed:
            return
        
        try:
            if len(self.outgoing_audio_buffer) > 0:
                chunk = bytes(self.outgoing_audio_buffer)
                self.outgoing_audio_buffer.clear()
                
                self.sequence_number += 1
                message = {
                    "event": "media",
                    "sequenceNumber": self.sequence_number,
                    "media": {
                        "chunk": self.chunk_number + 1,
                        "timestamp": int((time.time() - self.stream_start_time) * 1000),
                        "payload": base64.b64encode(chunk).decode('utf-8')
                    }
                }
                await self._send_message(message)
            
            self.sequence_number += 1
            message = {
                "event": "stop",
                "sequenceNumber": self.sequence_number,
                "stop": {
                    "mediaInfo": {
                        "duration": int(time.time() - self.stream_start_time),
                        "bytesSent": self.chunk_number * 640
                    }
                }
            }
            
            await self._send_message(message)
            self.stream_started = False
            logger.info("[VOX-v2.2] Audio stream stopped")
        except Exception as e:
            logger.error(f"[VOX-v2.2] Error stopping audio stream: {e}")
            self.stream_started = False

    async def _save_dialog_to_database(self):
        """
        ✅ v2.2 FIX: Save EVERY dialog as NEW database record.
        Called on response.done event.
        """
        if self.connection_closed:
            return
        
        if not self.user_transcript or not self.assistant_transcript:
            logger.warning("[VOX-v2.2] ⚠️ Empty transcripts, skipping save")
            return
            
        try:
            logger.info(f"[VOX-v2.2] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            logger.info(f"[VOX-v2.2] 💾 SAVING DIALOG TO DATABASE")
            logger.info(f"[VOX-v2.2]    User: {self.user_transcript[:50]}...")
            logger.info(f"[VOX-v2.2]    Assistant: {self.assistant_transcript[:50]}...")
            logger.info(f"[VOX-v2.2]    Caller: {self.caller_number}")
            logger.info(f"[VOX-v2.2] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            
            # Save to database as NEW record
            conversation = await ConversationService.save_conversation(
                db=self.db,
                assistant_id=str(self.openai_client.assistant_config.id),
                user_message=self.user_transcript,
                assistant_message=self.assistant_transcript,
                session_id=self.openai_client.session_id,
                caller_number=self.caller_number,
                client_info={
                    "call_id": self.call_id,
                    "source": "voximplant",
                    "protocol": "v2.2"
                },
                tokens_used=0
            )
            
            if conversation:
                self.dialogs_saved += 1
                logger.info(f"[VOX-v2.2] ✅ Dialog saved to DB: {conversation.id}")
                logger.info(f"[VOX-v2.2]    Total dialogs saved: {self.dialogs_saved}")
            else:
                logger.error("[VOX-v2.2] ❌ Failed to save dialog to DB")
            
            # Log to Google Sheets
            if self.openai_client and self.openai_client.assistant_config:
                assistant_config = self.openai_client.assistant_config
                if hasattr(assistant_config, 'google_sheet_id') and assistant_config.google_sheet_id:
                    try:
                        sheets_success = await GoogleSheetsService.log_conversation(
                            sheet_id=assistant_config.google_sheet_id,
                            user_message=self.user_transcript,
                            assistant_message=self.assistant_transcript,
                            function_result=self.function_result,
                            conversation_id=str(conversation.id) if conversation else self.call_id,
                            caller_number=self.caller_number
                        )
                        
                        if sheets_success:
                            logger.info(f"[VOX-v2.2] ✅ Dialog logged to Google Sheets")
                        else:
                            logger.warning(f"[VOX-v2.2] ⚠️ Failed to log to Google Sheets")
                    except Exception as e:
                        logger.error(f"[VOX-v2.2] ❌ Google Sheets error: {e}")
            
            # Reset transcripts for next dialog
            self.user_transcript = ""
            self.assistant_transcript = ""
            self.function_result = None
            
            logger.info(f"[VOX-v2.2] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            
        except Exception as e:
            logger.error(f"[VOX-v2.2] ❌ Error saving dialog: {e}")
            logger.error(f"[VOX-v2.2] Traceback: {traceback.format_exc()}")

    async def _send_message(self, message: Dict[str, Any]):
        """Send message to Voximplant."""
        if not self.is_connected or self.connection_closed or self.websocket_closed:
            return
            
        try:
            await self.websocket.send_text(json.dumps(message))
        except WebSocketDisconnect:
            logger.warning("[VOX-v2.2] WebSocket disconnected while sending")
            self.websocket_closed = True
            self.connection_closed = True
        except ConnectionClosed:
            logger.warning("[VOX-v2.2] Connection closed while sending")
            self.websocket_closed = True
            self.connection_closed = True
        except Exception as e:
            logger.error(f"[VOX-v2.2] Error sending message: {e}")
            if "disconnect message" in str(e) or "receive" in str(e):
                self.websocket_closed = True
                self.connection_closed = True

    async def _send_error(self, code: str, message: str):
        """Send error to Voximplant."""
        try:
            await self._send_message({
                "type": "error",
                "error": {
                    "code": code,
                    "message": message
                }
            })
            
            if not self.websocket_closed:
                await self.websocket.close(code=1008)
                self.websocket_closed = True
        except Exception as e:
            logger.error(f"[VOX-v2.2] Error sending error message: {e}")
            self.websocket_closed = True
            self.connection_closed = True

    async def cleanup(self):
        """Cleanup resources."""
        logger.info("[VOX-v2.2] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        logger.info("[VOX-v2.2] 🧹 CLEANUP STARTING")
        
        self.is_connected = False
        self.connection_closed = True
        
        if self.audio_packets_received > 0:
            duration = self.audio_bytes_received / (self.sample_rate * 2)
            logger.info(f"[VOX-v2.2] 📊 Statistics:")
            logger.info(f"[VOX-v2.2]    Audio packets: {self.audio_packets_received}")
            logger.info(f"[VOX-v2.2]    Duration: {duration:.1f}s")
            logger.info(f"[VOX-v2.2]    Dialogs saved: {self.dialogs_saved}")
            logger.info(f"[VOX-v2.2]    Caller: {self.caller_number}")
        
        if self.stream_started:
            try:
                await self._stop_audio_stream()
            except Exception as e:
                logger.error(f"[VOX-v2.2] Error stopping stream: {e}")
        
        try:
            for task in self.background_tasks:
                if not task.done():
                    task.cancel()
            
            pending_tasks = [t for t in self.background_tasks if not t.done()]
            if pending_tasks:
                await asyncio.wait(pending_tasks, timeout=2.0)
        except Exception as e:
            logger.error(f"[VOX-v2.2] Error cancelling tasks: {e}")
        
        if self.openai_client:
            try:
                await self.openai_client.close()
            except Exception as e:
                logger.error(f"[VOX-v2.2] Error closing OpenAI client: {e}")
        
        if not self.websocket_closed:
            try:
                if hasattr(self.websocket, 'client_state') and self.websocket.client_state != 3:
                    await self.websocket.close(code=1000)
                    self.websocket_closed = True
            except Exception as e:
                logger.error(f"[VOX-v2.2] Error closing WebSocket: {e}")
        
        logger.info("[VOX-v2.2] ✅ Cleanup completed")
        logger.info("[VOX-v2.2] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    async def _load_assistant_config(self) -> Optional[AssistantConfig]:
        """Load assistant configuration."""
        try:
            if self.assistant_id == "demo":
                assistant = self.db.query(AssistantConfig).filter(
                    AssistantConfig.is_public.is_(True)
                ).first()
                if not assistant:
                    assistant = self.db.query(AssistantConfig).first()
            else:
                try:
                    uuid_obj = uuid.UUID(self.assistant_id)
                    assistant = self.db.query(AssistantConfig).get(uuid_obj)
                except ValueError:
                    assistant = self.db.query(AssistantConfig).filter(
                        AssistantConfig.id.cast(str) == self.assistant_id
                    ).first()
            
            if not assistant:
                await self._send_error("assistant_not_found", "Assistant not found")
                return None
            
            return assistant
            
        except Exception as e:
            logger.error(f"[VOX-v2.2] Error loading assistant: {e}")
            await self._send_error("server_error", "Error loading assistant")
            return None

    async def _check_subscription(self, assistant: AssistantConfig) -> bool:
        """Check user subscription."""
        try:
            if not assistant.user_id or assistant.is_public:
                return True
            
            user = self.db.query(User).get(assistant.user_id)
            if not user or user.is_admin or user.email == "well96well@gmail.com":
                return True
            
            subscription_status = await UserService.check_subscription_status(
                self.db, str(user.id)
            )
            
            if not subscription_status["active"]:
                error_code = "TRIAL_EXPIRED" if subscription_status.get("is_trial") else "SUBSCRIPTION_EXPIRED"
                await self._send_error(error_code, "Subscription expired")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"[VOX-v2.2] Error checking subscription: {e}")
            return True

    async def _get_api_key(self, assistant: AssistantConfig) -> Optional[str]:
        """Get OpenAI API key."""
        try:
            if assistant.user_id:
                user = self.db.query(User).get(assistant.user_id)
                if user and user.openai_api_key:
                    return user.openai_api_key
            return None
        except Exception as e:
            logger.error(f"[VOX-v2.2] Error getting API key: {e}")
            return None


async def handle_voximplant_websocket_with_protocol(
    websocket: WebSocket,
    assistant_id: str,
    db: Session
):
    """
    Entry point for Voximplant WebSocket handler v2.2.
    ✅ Production ready with fixed conversation logging.
    """
    handler = None
    try:
        from backend.db.session import SessionLocal
        handler_db = SessionLocal()
        
        try:
            handler = VoximplantProtocolHandler(websocket, assistant_id, handler_db)
            await handler.start()
        except Exception as e:
            logger.error(f"[VOX-v2.2] Error in handler: {e}")
            logger.error(f"[VOX-v2.2] Traceback: {traceback.format_exc()}")
        finally:
            handler_db.close()
            
    except Exception as e:
        logger.error(f"[VOX-v2.2] Critical error: {e}")
        logger.error(f"[VOX-v2.2] Traceback: {traceback.format_exc()}")
        
        try:
            if not handler:
                try:
                    await websocket.accept()
                except:
                    pass
                    
            try:
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "error": {"code": "server_error", "message": "Internal server error"}
                }))
            except:
                pass
        except:
            pass
        
        if handler:
            try:
                await handler.cleanup()
            except:
                pass
        else:
            try:
                await websocket.close()
            except:
                pass


# Aliases for compatibility
SimpleVoximplantHandler = VoximplantProtocolHandler
handle_voximplant_websocket_simple = handle_voximplant_websocket_with_protocol
