# backend/websockets/grok_client.py
"""
🚀 PRODUCTION VERSION 1.1 - xAI Grok Voice Agent API Client
WebSocket endpoint: wss://api.x.ai/v1/realtime

✨ Features:
✅ Full xAI Grok Voice Agent API support
✅ Multiple voices: Ara, Rex, Sal, Eve, Leo
✅ Audio formats: PCM (8-48kHz), G.711 μ-law, G.711 A-law
✅ Server-side VAD support
✅ Function calling (web_search, x_search, file_search, custom)
✅ Async function execution (non-blocking)
✅ Telephony support (pcmu/pcma for Voximplant)

CHANGELOG v1.1:
- Fixed: session.update structure matches xAI API docs
- Fixed: VAD config is simple {"type": "server_vad"} (no threshold/silence params)
- Fixed: Correct audio format nesting

Based on OpenAI Realtime client architecture for Voicyfy platform.
"""

import asyncio
import json
import uuid
import base64
import time
import websockets
import re
from websockets.exceptions import ConnectionClosed
from typing import Optional, List, Dict, Any, Union, AsyncGenerator

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.models.grok_assistant import GrokAssistantConfig
from backend.models.conversation import Conversation
from backend.functions import get_function_definitions, get_enabled_functions, normalize_function_name, execute_function

logger = get_logger(__name__)

# Grok Voice defaults
DEFAULT_VOICE = "Ara"  # Options: Ara, Rex, Sal, Eve, Leo
DEFAULT_SAMPLE_RATE = 24000  # 24kHz default (8000, 16000, 21050, 24000, 32000, 44100, 48000)
DEFAULT_SYSTEM_MESSAGE = "You are a helpful voice assistant. Keep your responses conversational and concise."

# Voice mapping (OpenAI -> Grok)
VOICE_MAPPING = {
    "alloy": "Ara",
    "echo": "Rex",
    "fable": "Sal",
    "onyx": "Eve",
    "nova": "Leo",
    "shimmer": "Ara",
    # Grok native voices
    "ara": "Ara",
    "rex": "Rex",
    "sal": "Sal",
    "eve": "Eve",
    "leo": "Leo",
}


def map_voice_to_grok(voice: str) -> str:
    """Map OpenAI or custom voice name to Grok voice."""
    if not voice:
        return DEFAULT_VOICE
    return VOICE_MAPPING.get(voice.lower(), DEFAULT_VOICE)


def normalize_functions_for_grok(assistant_functions):
    """Convert UI function list to Grok tool definitions."""
    if not assistant_functions:
        return []
    
    enabled_names = []
    
    if isinstance(assistant_functions, dict) and "enabled_functions" in assistant_functions:
        enabled_names = [normalize_function_name(name) for name in assistant_functions.get("enabled_functions", [])]
    else:
        enabled_names = [normalize_function_name(func.get("name")) for func in assistant_functions if func.get("name")]
        
    return get_enabled_functions(enabled_names)


def generate_short_id(prefix: str = "") -> str:
    """Generate short unique identifier (max 32 chars)."""
    raw_id = str(uuid.uuid4()).replace("-", "")
    max_id_len = 32 - len(prefix)
    return f"{prefix}{raw_id[:max_id_len]}"


def get_audio_format_config(
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    format_type: str = "audio/pcm",
    is_telephony: bool = False
) -> Dict[str, Any]:
    """
    Get audio format configuration for Grok API.
    
    For telephony (Voximplant): use audio/pcmu or audio/pcma at 8000Hz
    For web: use audio/pcm at 24000Hz or 48000Hz
    
    Structure matches xAI API docs:
    {
        "input": {"format": {"type": "audio/pcm", "rate": 24000}},
        "output": {"format": {"type": "audio/pcm", "rate": 24000}}
    }
    """
    if is_telephony:
        return {
            "input": {
                "format": {
                    "type": "audio/pcmu",  # G.711 μ-law for telephony
                    "rate": 8000
                }
            },
            "output": {
                "format": {
                    "type": "audio/pcmu",
                    "rate": 8000
                }
            }
        }
    
    return {
        "input": {
            "format": {
                "type": format_type,
                "rate": sample_rate
            }
        },
        "output": {
            "format": {
                "type": format_type,
                "rate": sample_rate
            }
        }
    }


class GrokVoiceClient:
    """
    🚀 PRODUCTION v1.1 - Client for xAI Grok Voice Agent API
    
    WebSocket endpoint: wss://api.x.ai/v1/realtime
    
    Key features:
    - Multiple voices (Ara, Rex, Sal, Eve, Leo)
    - Flexible audio formats (PCM, G.711)
    - Server-side VAD
    - Function calling support
    - Async function execution
    - Telephony support (Voximplant)
    """
    
    def __init__(
        self,
        api_key: str,
        assistant_config: GrokAssistantConfig,
        client_id: str,
        db_session: Any = None,
        user_agent: str = "",
        is_telephony: bool = False,
        sample_rate: int = DEFAULT_SAMPLE_RATE
    ):
        """
        Initialize the Grok Voice Agent client.
        
        Args:
            api_key: xAI API key
            assistant_config: Assistant configuration from DB
            client_id: Unique client identifier
            db_session: Database session for conversation logging
            user_agent: Client user agent string
            is_telephony: True for Voximplant telephony (uses G.711)
            sample_rate: Audio sample rate (default 24000Hz)
        """
        self.api_key = api_key
        self.assistant_config = assistant_config
        self.client_id = client_id
        self.db_session = db_session
        self.user_agent = user_agent
        self.is_telephony = is_telephony
        self.sample_rate = sample_rate
        self.ws = None
        self.is_connected = False
        
        # Grok API URL
        self.grok_url = "wss://api.x.ai/v1/realtime"
        
        self.session_id = str(uuid.uuid4())
        self.conversation_record_id: Optional[str] = None
        self.last_function_name = None
        self.enabled_functions = []
        
        # Speaking states
        self.is_assistant_speaking = False
        self.current_response_id: Optional[str] = None
        self.current_audio_samples = 0
        self.interruption_occurred = False
        self.last_interruption_time = 0
        
        # Device detection
        user_agent_lower = user_agent.lower()
        self.is_ios = "iphone" in user_agent_lower or "ipad" in user_agent_lower
        self.is_android = "android" in user_agent_lower
        self.is_mobile = self.is_ios or self.is_android
        
        # Extract functions
        if hasattr(assistant_config, "functions"):
            functions = assistant_config.functions
            if isinstance(functions, list):
                self.enabled_functions = [normalize_function_name(f.get("name")) for f in functions if f.get("name")]
            elif isinstance(functions, dict) and "enabled_functions" in functions:
                self.enabled_functions = [normalize_function_name(name) for name in functions.get("enabled_functions", [])]
            
            logger.info(f"[GROK-CLIENT v1.1] Enabled functions: {self.enabled_functions}")
        
        logger.info(f"[GROK-CLIENT v1.1] Initialized (telephony: {is_telephony}, sample_rate: {sample_rate})")

    async def connect(self) -> bool:
        """Establish WebSocket connection to Grok Voice Agent API."""
        if not self.api_key:
            logger.error("[GROK-CLIENT v1.1] xAI API key not provided")
            return False

        headers = [
            ("Authorization", f"Bearer {self.api_key}"),
            ("Content-Type", "application/json"),
            ("User-Agent", "Voicyfy-Production/1.1")
        ]
        
        try:
            self.ws = await asyncio.wait_for(
                websockets.connect(
                    self.grok_url,
                    extra_headers=headers,
                    max_size=15*1024*1024,
                    ping_interval=30,
                    ping_timeout=120,
                    close_timeout=15
                ),
                timeout=30
            )
            self.is_connected = True
            logger.info(f"[GROK-CLIENT v1.1] ✅ Connected to Grok Voice Agent API")

            # Wait for conversation.created event
            try:
                init_message = await asyncio.wait_for(self.ws.recv(), timeout=10)
                init_data = json.loads(init_message)
                if init_data.get("type") == "conversation.created":
                    logger.info(f"[GROK-CLIENT v1.1] Conversation created: {init_data.get('conversation', {}).get('id')}")
            except asyncio.TimeoutError:
                logger.warning("[GROK-CLIENT v1.1] No conversation.created event received")

            # Get settings
            voice = map_voice_to_grok(self.assistant_config.voice)
            system_message = getattr(self.assistant_config, "system_prompt", None) or DEFAULT_SYSTEM_MESSAGE
            functions = getattr(self.assistant_config, "functions", None)

            # Send session.update
            if not await self.update_session(
                voice=voice,
                system_message=system_message,
                functions=functions
            ):
                logger.error("[GROK-CLIENT v1.1] Failed to update session settings")
                await self.close()
                return False

            logger.info(f"[GROK-CLIENT v1.1] Session initialized (voice: {voice})")
            return True
            
        except asyncio.TimeoutError:
            logger.error(f"[GROK-CLIENT v1.1] Connection timeout")
            return False
        except Exception as e:
            logger.error(f"[GROK-CLIENT v1.1] Failed to connect: {e}")
            return False

    async def reconnect(self) -> bool:
        """Reconnect to Grok Voice Agent API."""
        logger.info(f"[GROK-CLIENT v1.1] Attempting reconnection...")
        try:
            if self.ws:
                try:
                    await self.ws.close()
                except:
                    pass
            
            self.is_connected = False
            self.ws = None
            
            # Reset states
            self.is_assistant_speaking = False
            self.current_response_id = None
            self.current_audio_samples = 0
            self.interruption_occurred = False
            
            result = await self.connect()
            if result:
                logger.info(f"[GROK-CLIENT v1.1] ✅ Reconnection successful")
            return result
        except Exception as e:
            logger.error(f"[GROK-CLIENT v1.1] Reconnection error: {e}")
            return False

    async def update_session(
        self,
        voice: str = DEFAULT_VOICE,
        system_message: str = DEFAULT_SYSTEM_MESSAGE,
        functions: Optional[Union[List[Dict[str, Any]], Dict[str, Any]]] = None
    ) -> bool:
        """
        Update session settings for Grok Voice Agent API.
        
        v1.1: Fixed structure to match xAI API documentation exactly.
        
        Configures:
        - Voice selection
        - Audio format (PCM or G.711 for telephony)
        - VAD settings (simple server_vad, no extra params)
        - Function tools
        """
        if not self.is_connected or not self.ws:
            logger.error("[GROK-CLIENT v1.1] Cannot update session: not connected")
            return False
        
        # Audio format configuration
        audio_config = get_audio_format_config(
            sample_rate=self.sample_rate,
            is_telephony=self.is_telephony
        )
        
        # Build tools array
        tools = []
        
        # Normalize functions
        normalized_functions = normalize_functions_for_grok(functions)
        
        for func_def in normalized_functions:
            tools.append({
                "type": "function",
                "name": func_def["name"],
                "description": func_def["description"],
                "parameters": func_def["parameters"]
            })
        
        self.enabled_functions = [normalize_function_name(tool["name"]) for tool in tools if tool["type"] == "function"]
        logger.info(f"[GROK-CLIENT v1.1] Functions activated: {self.enabled_functions}")
        
        # v1.1: Session payload exactly matching xAI API docs
        # Reference: https://docs.x.ai/docs/guides/grok-voice-agent-api
        payload = {
            "type": "session.update",
            "session": {
                "voice": voice,
                "instructions": system_message,
                "turn_detection": {
                    "type": "server_vad"  # Simple server VAD - no threshold/silence params!
                },
                "audio": audio_config
            }
        }
        
        # Add tools only if we have any
        if tools:
            payload["session"]["tools"] = tools
        
        try:
            logger.info(f"[GROK-CLIENT v1.1] Sending session.update: voice={voice}, tools={len(tools)}")
            logger.info(f"[GROK-CLIENT v1.1] Audio config: {audio_config}")
            
            await self.ws.send(json.dumps(payload))
            logger.info(f"[GROK-CLIENT v1.1] ✅ Session configured")
            
            # Wait for session.updated confirmation
            try:
                response = await asyncio.wait_for(self.ws.recv(), timeout=5)
                response_data = json.loads(response)
                if response_data.get("type") == "session.updated":
                    logger.info(f"[GROK-CLIENT v1.1] Session update confirmed by server")
                else:
                    logger.info(f"[GROK-CLIENT v1.1] Received: {response_data.get('type')}")
            except asyncio.TimeoutError:
                logger.warning("[GROK-CLIENT v1.1] No session.updated confirmation received")
            
        except Exception as e:
            logger.error(f"[GROK-CLIENT v1.1] Error sending session.update: {e}")
            return False

        # Create conversation record
        if self.db_session:
            try:
                conv = Conversation(
                    assistant_id=self.assistant_config.id,
                    session_id=self.session_id,
                    user_message="",
                    assistant_message="",
                )
                self.db_session.add(conv)
                self.db_session.commit()
                self.db_session.refresh(conv)
                self.conversation_record_id = str(conv.id)
                logger.info(f"[GROK-CLIENT v1.1] Conversation record created: {self.conversation_record_id}")
            except Exception as e:
                logger.error(f"[GROK-CLIENT v1.1] Error creating conversation: {e}")

        return True

    async def handle_interruption(self) -> bool:
        """Handle interruption events."""
        try:
            current_time = time.time()
            
            protection_time = 0.15 if self.is_ios else 0.2
            
            if current_time - self.last_interruption_time < protection_time:
                logger.info(f"[GROK-CLIENT v1.1] Ignoring duplicate interruption")
                return True
                
            self.last_interruption_time = current_time
            self.interruption_occurred = True
            
            logger.info(f"[GROK-CLIENT v1.1] Handling interruption")
            
            # Clear input buffer
            await self.clear_audio_buffer()
            
            self.is_assistant_speaking = False
            self.current_response_id = None
            self.current_audio_samples = 0
            
            logger.info("[GROK-CLIENT v1.1] Interruption handled successfully")
            return True
            
        except Exception as e:
            logger.error(f"[GROK-CLIENT v1.1] Error handling interruption: {e}")
            return False

    def set_assistant_speaking(self, speaking: bool, response_id: str = None) -> None:
        """Set assistant speaking state."""
        self.is_assistant_speaking = speaking
        if speaking:
            self.current_response_id = response_id
            self.current_audio_samples = 0
        else:
            self.current_response_id = None
            self.current_audio_samples = 0

    def increment_audio_samples(self, sample_count: int) -> None:
        """Increment audio sample count."""
        self.current_audio_samples += sample_count

    async def send_function_result(self, call_id: str, result: Dict[str, Any]) -> Dict[str, bool]:
        """
        Send function result back to Grok.
        
        After sending function output, automatically triggers response.create
        for the assistant to continue with the result.
        
        Returns:
            Dict with success status
        """
        if not self.is_connected or not self.ws:
            error_msg = "Cannot send function result: not connected"
            logger.error(f"[GROK-CLIENT v1.1] {error_msg}")
            return {"success": False, "error": error_msg}
        
        try:
            logger.info(f"[GROK-CLIENT v1.1] Sending function result: {call_id}")
            
            # Step 1: Send function_call_output
            result_payload = {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(result)
                }
            }
            
            await self.ws.send(json.dumps(result_payload))
            logger.info(f"[GROK-CLIENT v1.1] ✅ Function result sent")
            
            # Step 2: Trigger response.create
            response_payload = {
                "type": "response.create",
                "response": {
                    "modalities": ["text", "audio"]
                }
            }
            
            await self.ws.send(json.dumps(response_payload))
            logger.info(f"[GROK-CLIENT v1.1] ✅ Response.create sent after function")
            
            return {"success": True, "error": None}
            
        except Exception as e:
            error_msg = f"Error sending function result: {e}"
            logger.error(f"[GROK-CLIENT v1.1] {error_msg}")
            return {"success": False, "error": error_msg}

    async def send_text_message(self, text: str, trigger_response: bool = True) -> bool:
        """
        Send a text message to Grok.
        
        Args:
            text: Text message to send
            trigger_response: Whether to trigger response.create after
        """
        if not self.is_connected or not self.ws:
            logger.error("[GROK-CLIENT v1.1] Cannot send text: not connected")
            return False
        
        try:
            # Create conversation item with text
            payload = {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": text
                        }
                    ]
                }
            }
            
            await self.ws.send(json.dumps(payload))
            logger.info(f"[GROK-CLIENT v1.1] Text message sent: {text[:50]}...")
            
            if trigger_response:
                response_payload = {
                    "type": "response.create",
                    "response": {
                        "modalities": ["text", "audio"]
                    }
                }
                await self.ws.send(json.dumps(response_payload))
                logger.info(f"[GROK-CLIENT v1.1] Response.create sent")
            
            return True
            
        except Exception as e:
            logger.error(f"[GROK-CLIENT v1.1] Error sending text: {e}")
            return False

    async def process_audio(self, audio_buffer: bytes) -> bool:
        """
        Process and send audio data to Grok API.
        
        Audio must be in the format configured during session.update:
        - PCM16 for web (default)
        - G.711 μ-law for telephony
        """
        if not self.is_connected or not self.ws or not audio_buffer:
            return False
        try:
            data_b64 = base64.b64encode(audio_buffer).decode("utf-8")
            await self.ws.send(json.dumps({
                "type": "input_audio_buffer.append",
                "audio": data_b64
            }))
            return True
        except ConnectionClosed:
            logger.error("[GROK-CLIENT v1.1] Connection closed while sending audio")
            self.is_connected = False
            return False
        except Exception as e:
            logger.error(f"[GROK-CLIENT v1.1] Error processing audio: {e}")
            return False

    async def commit_audio(self) -> bool:
        """
        Commit audio buffer (manual VAD mode only).
        
        Note: When using server_vad, this is handled automatically.
        """
        if not self.is_connected or not self.ws:
            return False
        try:
            await self.ws.send(json.dumps({
                "type": "input_audio_buffer.commit"
            }))
            return True
        except ConnectionClosed:
            logger.error("[GROK-CLIENT v1.1] Connection closed while committing audio")
            self.is_connected = False
            return False
        except Exception as e:
            logger.error(f"[GROK-CLIENT v1.1] Error committing audio: {e}")
            return False

    async def clear_audio_buffer(self) -> bool:
        """Clear input audio buffer."""
        if not self.is_connected or not self.ws:
            return False
        try:
            await self.ws.send(json.dumps({
                "type": "input_audio_buffer.clear"
            }))
            return True
        except ConnectionClosed:
            logger.error("[GROK-CLIENT v1.1] Connection closed while clearing buffer")
            self.is_connected = False
            return False
        except Exception as e:
            logger.error(f"[GROK-CLIENT v1.1] Error clearing buffer: {e}")
            return False

    async def create_response(self, modalities: List[str] = None) -> bool:
        """
        Request a response from Grok.
        
        Args:
            modalities: Response modalities ["text", "audio"] (default: both)
        """
        if not self.is_connected or not self.ws:
            return False
        
        if modalities is None:
            modalities = ["text", "audio"]
        
        try:
            payload = {
                "type": "response.create",
                "response": {
                    "modalities": modalities
                }
            }
            await self.ws.send(json.dumps(payload))
            return True
        except Exception as e:
            logger.error(f"[GROK-CLIENT v1.1] Error creating response: {e}")
            return False

    async def close(self) -> None:
        """Close WebSocket connection."""
        if self.ws:
            try:
                await self.ws.close()
                logger.info(f"[GROK-CLIENT v1.1] WebSocket closed")
            except Exception as e:
                logger.error(f"[GROK-CLIENT v1.1] Error closing WebSocket: {e}")
        self.is_connected = False
        
        self.is_assistant_speaking = False
        self.current_response_id = None
        self.current_audio_samples = 0
        self.interruption_occurred = False

    async def receive_messages(self) -> AsyncGenerator[Dict[str, Any], None]:
        """Receive and yield messages from Grok WebSocket."""
        if not self.is_connected or not self.ws:
            return
            
        try:
            async for message in self.ws:
                try:
                    data = json.loads(message)
                    yield data
                except json.JSONDecodeError:
                    logger.error(f"[GROK-CLIENT v1.1] Failed to decode: {message[:100]}...")
        except ConnectionClosed:
            logger.info(f"[GROK-CLIENT v1.1] WebSocket closed")
            self.is_connected = False
        except Exception as e:
            logger.error(f"[GROK-CLIENT v1.1] Error receiving messages: {e}")
            self.is_connected = False
