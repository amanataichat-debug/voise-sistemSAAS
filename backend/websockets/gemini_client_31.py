# backend/websockets/gemini_client.py
"""
🚀 PRODUCTION VERSION 1.0 - Google Gemini 3.1 Flash Live API Client
Model: gemini-3.1-flash-live-preview

CRITICAL FIX in v1.6:
✅ Added auto-greeting on connect using greeting_message from config
✅ Correct toolResponse format for function results
   - Changed from client_content to toolResponse
   - Proper BidiGenerateContentToolResponse structure
   - ID matching for function calls (from toolCall event)
   
Features:
✅ PURE GEMINI VAD - automatic voice activity detection
✅ Continuous audio streaming - no manual commit needed
✅ Native audio I/O (PCM 16kHz input, 24kHz output)
✅ Manual function calling support
✅ Thinking mode support (configurable)
✅ Screen context support (silent mode)
✅ Audio transcription support (input + output)
✅ Interruption handling
✅ Reconnection logic
✅ Performance monitoring
✅ Production-ready stability
✅ Auto-greeting on connect
✅ FIXED: Correct WebSocket endpoint for Live API
✅ FIXED: toolResponse format per official documentation
"""

import asyncio
import json
import uuid
import base64
import time
import websockets
import re
import traceback
from websockets.exceptions import ConnectionClosed
from typing import Optional, List, Dict, Any, Union, AsyncGenerator

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.models.gemini_assistant import GeminiAssistantConfig, GeminiConversation
from backend.functions import get_function_definitions, get_enabled_functions, normalize_function_name, execute_function

logger = get_logger(__name__)

DEFAULT_VOICE = "Aoede"
DEFAULT_SYSTEM_MESSAGE = "Ты мой умный помощник. Ты веселый и приятный. Отвечай по существу и с энергией."


def normalize_functions(assistant_functions):
    """Convert UI function list to full definitions with parameters."""
    if not assistant_functions:
        return []
    
    enabled_names = []
    
    if isinstance(assistant_functions, dict) and "enabled_functions" in assistant_functions:
        enabled_names = [normalize_function_name(name) for name in assistant_functions.get("enabled_functions", [])]
    else:
        enabled_names = [normalize_function_name(func.get("name")) for func in assistant_functions if func.get("name")]
        
    return get_enabled_functions(enabled_names)


def extract_webhook_url_from_prompt(prompt: str) -> Optional[str]:
    """Extract webhook URL from assistant system prompt."""
    if not prompt:
        return None
        
    pattern1 = r'URL\s+(?:вебхука|webhook):\s*(https?://[^\s"\'<>]+)'
    pattern2 = r'(?:вебхука|webhook)\s+URL:\s*(https?://[^\s"\'<>]+)'
    pattern3 = r'https?://[^\s"\'<>]+'
    
    for pattern in [pattern1, pattern2, pattern3]:
        matches = re.findall(pattern, prompt, re.IGNORECASE)
        if matches:
            return matches[0]
            
    return None


def generate_short_id(prefix: str = "") -> str:
    """Generate short unique identifier."""
    raw_id = str(uuid.uuid4()).replace("-", "")
    max_id_len = 32 - len(prefix)
    return f"{prefix}{raw_id[:max_id_len]}"


class GeminiLiveClient31:
    """
    🚀 PRODUCTION v1.0 - Client for Google Gemini 3.1 Flash Live API
    
    Key features:
    - Pure Gemini VAD (automatic voice activity detection)
    - Continuous audio streaming (no manual commit needed)
    - Native audio processing (16kHz input, 24kHz output)
    - Audio transcription (input + output)
    - Manual function calling (handler controls execution)
    - Thinking mode support
    - Screen context support
    - Auto-greeting on connect
    - Reliable error handling
    - Performance monitoring
    """
    
    def __init__(
        self,
        api_key: str,
        assistant_config: GeminiAssistantConfig,
        client_id: str,
        db_session: Any = None,
        user_agent: str = ""
    ):
        """Initialize the Gemini Live API client."""
        self.api_key = api_key
        self.assistant_config = assistant_config
        self.client_id = client_id
        self.db_session = db_session
        self.user_agent = user_agent
        self.ws = None
        self.is_connected = False
        
        # ✅ FIXED: Correct WebSocket endpoint for Gemini Live API
        self.model = "gemini-3.1-flash-live-preview"
        self.base_url = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
        self.gemini_url = f"{self.base_url}?key={self.api_key}"
        
        self.session_id = str(uuid.uuid4())
        self.conversation_record_id: Optional[str] = None
        self.webhook_url = None
        self.last_function_name = None
        self.enabled_functions = []
        
        # Interruption states
        self.is_assistant_speaking = False
        self.current_response_id: Optional[str] = None
        self.current_audio_samples = 0
        self.interruption_occurred = False
        self.last_interruption_time = 0
        
        # ✅ NEW: Greeting state
        self.greeting_sent = False
        
        # Device detection
        self.is_ios = "iphone" in user_agent.lower() or "ipad" in user_agent.lower()
        self.is_android = "android" in user_agent.lower()
        self.is_mobile = self.is_ios or self.is_android
        
        if self.is_ios:
            logger.info(f"[GEMINI-31-CLIENT] iOS device detected")
        
        # Extract functions
        if hasattr(assistant_config, "functions"):
            functions = assistant_config.functions
            if isinstance(functions, list):
                self.enabled_functions = [normalize_function_name(f.get("name")) for f in functions if f.get("name")]
            elif isinstance(functions, dict) and "enabled_functions" in functions:
                self.enabled_functions = [normalize_function_name(name) for name in functions.get("enabled_functions", [])]
            
            logger.info(f"[GEMINI-31-CLIENT] Enabled functions: {self.enabled_functions}")
        
        # Webhook URL
        if "send_webhook" in self.enabled_functions and hasattr(assistant_config, "system_prompt") and assistant_config.system_prompt:
            self.webhook_url = extract_webhook_url_from_prompt(assistant_config.system_prompt)
            if self.webhook_url:
                logger.info(f"[GEMINI-31-CLIENT] Webhook configured")

    async def connect(self) -> bool:
        """Establish WebSocket connection to Gemini Live API."""
        if not self.api_key:
            logger.error("[GEMINI-31-CLIENT] ❌ Google API key not provided")
            return False

        logger.info(f"[GEMINI-31-CLIENT] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        logger.info(f"[GEMINI-31-CLIENT] 🔌 CONNECTION ATTEMPT")
        logger.info(f"[GEMINI-31-CLIENT] Model: {self.model}")
        logger.info(f"[GEMINI-31-CLIENT] Endpoint: {self.base_url}")
        logger.info(f"[GEMINI-31-CLIENT] API Key: {self.api_key[:15]}...{self.api_key[-8:]}")
        logger.info(f"[GEMINI-31-CLIENT] VAD Mode: Pure Gemini (automatic)")
        logger.info(f"[GEMINI-31-CLIENT] Transcription: ENABLED (input + output)")
        logger.info(f"[GEMINI-31-CLIENT] Auto-greeting: {bool(getattr(self.assistant_config, 'greeting_message', None))}")
        logger.info(f"[GEMINI-31-CLIENT] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        try:
            logger.info(f"[GEMINI-31-CLIENT] Opening WebSocket connection (timeout: 30s)...")
            
            self.ws = await asyncio.wait_for(
                websockets.connect(
                    self.gemini_url,
                    max_size=15*1024*1024,
                    ping_interval=30,
                    ping_timeout=120,
                    close_timeout=15,
                    extra_headers={
                        'User-Agent': 'Voicyfy/3.1'
                    }
                ),
                timeout=30
            )
            
            self.is_connected = True
            logger.info(f"[GEMINI-31-CLIENT] ✅ WebSocket CONNECTED successfully")
            logger.info(f"[GEMINI-31-CLIENT] Model: {self.model}")

            # Get settings
            voice = self.assistant_config.voice or DEFAULT_VOICE
            system_message = getattr(self.assistant_config, "system_prompt", None) or DEFAULT_SYSTEM_MESSAGE
            functions = getattr(self.assistant_config, "functions", None)

            # Динамически домешиваем функции подключённых коннекторов (Composio):
            # голос не полагается на разовый снимок в конфиге, читает connected-
            # коннекторы агента из БД — так же, как оркестратор берёт tools.get.
            try:
                from backend.services import composio_service as _composio
                functions = _composio.merge_voice_connector_functions(
                    self.db_session, self.assistant_config, functions
                )
                _note = _composio.connector_voice_prompt_note(self.db_session, self.assistant_config)
                if _note:
                    system_message = (system_message or "") + _note
            except Exception as _e:
                logger.warning(f"[GEMINI-31-CLIENT] connector merge failed: {_e}")

            # Update functions
            if functions:
                if isinstance(functions, list):
                    self.enabled_functions = [normalize_function_name(f.get("name")) for f in functions if f.get("name")]
                elif isinstance(functions, dict) and "enabled_functions" in functions:
                    self.enabled_functions = [normalize_function_name(name) for name in functions.get("enabled_functions", [])]
                
                logger.info(f"[GEMINI-31-CLIENT] Functions loaded: {self.enabled_functions}")

            # Webhook URL
            if "send_webhook" in self.enabled_functions:
                self.webhook_url = extract_webhook_url_from_prompt(system_message)

            # Send setup message
            if not await self.setup_session(
                voice=voice,
                system_message=system_message,
                functions=functions
            ):
                logger.error("[GEMINI-31-CLIENT] ❌ Failed to setup session")
                await self.close()
                return False

            logger.info(f"[GEMINI-31-CLIENT] ✅ Session initialized successfully")

            return True
            
        except asyncio.TimeoutError:
            logger.error(f"[GEMINI-31-CLIENT] ❌ CONNECTION TIMEOUT (30s)")
            logger.error(f"[GEMINI-31-CLIENT] Возможные причины:")
            logger.error(f"[GEMINI-31-CLIENT]   1. Google API недоступен из вашей страны/сервера")
            logger.error(f"[GEMINI-31-CLIENT]   2. Требуется VPN/proxy")
            logger.error(f"[GEMINI-31-CLIENT]   3. Проблемы с сетью")
            logger.error(f"[GEMINI-31-CLIENT]   4. Firewall блокирует WebSocket")
            return False
            
        except websockets.exceptions.InvalidStatusCode as e:
            logger.error(f"[GEMINI-31-CLIENT] ❌ INVALID HTTP STATUS: {e.status_code}")
            
            if hasattr(e, 'headers'):
                logger.error(f"[GEMINI-31-CLIENT] Response headers: {dict(e.headers)}")
            
            if e.status_code == 400:
                logger.error(f"[GEMINI-31-CLIENT] ⚠️ BAD REQUEST - проверьте формат запроса")
            elif e.status_code == 401:
                logger.error(f"[GEMINI-31-CLIENT] ⚠️ UNAUTHORIZED - проверьте API ключ")
                logger.error(f"[GEMINI-31-CLIENT]   Ключ: {self.api_key[:20]}...")
            elif e.status_code == 403:
                logger.error(f"[GEMINI-31-CLIENT] ⚠️ FORBIDDEN - нет доступа к Live API")
                logger.error(f"[GEMINI-31-CLIENT]   Проверьте, что API ключ имеет доступ к Gemini Live API")
            elif e.status_code == 404:
                logger.error(f"[GEMINI-31-CLIENT] ⚠️ NOT FOUND - неверный endpoint или модель")
                logger.error(f"[GEMINI-31-CLIENT]   Endpoint: {self.base_url}")
                logger.error(f"[GEMINI-31-CLIENT]   Model: {self.model}")
            elif e.status_code >= 500:
                logger.error(f"[GEMINI-31-CLIENT] ⚠️ SERVER ERROR на стороне Google")
            
            return False
            
        except OSError as e:
            logger.error(f"[GEMINI-31-CLIENT] ❌ NETWORK ERROR: {e}")
            if hasattr(e, 'errno'):
                logger.error(f"[GEMINI-31-CLIENT] Error code: {e.errno}")
            logger.error(f"[GEMINI-31-CLIENT] Возможно требуется VPN/proxy для доступа к Google API")
            return False
            
        except Exception as e:
            logger.error(f"[GEMINI-31-CLIENT] ❌ UNEXPECTED ERROR: {type(e).__name__}")
            logger.error(f"[GEMINI-31-CLIENT] Message: {str(e)}")
            logger.error(f"[GEMINI-31-CLIENT] Traceback:")
            logger.error(traceback.format_exc())
            return False

    async def reconnect(self) -> bool:
        """Reconnect to Gemini Live API."""
        logger.info(f"[GEMINI-31-CLIENT] Attempting reconnection...")
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
            self.greeting_sent = False  # Reset greeting state for reconnection
            
            result = await self.connect()
            if result:
                logger.info(f"[GEMINI-31-CLIENT] ✅ Reconnection successful")
            return result
        except Exception as e:
            logger.error(f"[GEMINI-31-CLIENT] Reconnection error: {e}")
            return False

    async def setup_session(
        self,
        voice: str = DEFAULT_VOICE,
        system_message: str = DEFAULT_SYSTEM_MESSAGE,
        functions: Optional[Union[List[Dict[str, Any]], Dict[str, Any]]] = None
    ) -> bool:
        """
        Send BidiGenerateContentSetup to configure session.

        Gemini 3.1 Live API: uses "config" key instead of "setup",
        thinkingConfig inside config with thinkingBudget=0 for voice latency.
        """
        if not self.is_connected or not self.ws:
            logger.error("[GEMINI-31-CLIENT] Cannot setup session: not connected")
            return False

        # Build tools
        normalized_functions = normalize_functions(functions)
        tools = []

        for func_def in normalized_functions:
            tools.append({
                "function_declarations": [{
                    "name": func_def["name"],
                    "description": func_def["description"],
                    "parameters": func_def["parameters"]
                }]
            })

        self.enabled_functions = [normalize_function_name(tool["function_declarations"][0]["name"]) for tool in tools]
        logger.info(f"[GEMINI-31-CLIENT] Functions activated: {self.enabled_functions}")

        # Gemini 3.1 Live: same "setup" key as 2.5, but with thinkingConfig in generationConfig
        setup_payload = {
            "setup": {
                "model": f"models/{self.model}",
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {
                        "voiceConfig": {
                            "prebuiltVoiceConfig": {
                                "voiceName": voice
                            }
                        }
                    },
                    "thinkingConfig": {
                        "thinkingLevel": "minimal"
                    }
                },
                "systemInstruction": {
                    "parts": [{"text": system_message}]
                },
                "outputAudioTranscription": {},
                "inputAudioTranscription": {}
            }
        }

        # Add tools if any
        if tools:
            setup_payload["setup"]["tools"] = tools

        try:
            logger.info(f"[GEMINI-31-CLIENT] Sending setup message (3.1 model)...")
            logger.info(f"[GEMINI-31-CLIENT] Setup payload keys: {list(setup_payload['setup'].keys())}")
            logger.info(f"[GEMINI-31-CLIENT] Gemini will use automatic VAD (no manual commit needed)")
            logger.info(f"[GEMINI-31-CLIENT] Transcription enabled for both input and output audio")
            logger.info(f"[GEMINI-31-CLIENT] thinkingLevel=minimal (optimized for voice latency)")

            await self.ws.send(json.dumps(setup_payload))

            logger.info(f"[GEMINI-31-CLIENT] Session setup sent successfully")
            logger.info(f"[GEMINI-31-CLIENT]   Model: {self.model}")
            logger.info(f"[GEMINI-31-CLIENT]   Voice: {voice}")
            logger.info(f"[GEMINI-31-CLIENT]   Tools: {len(tools)}")
            logger.info(f"[GEMINI-31-CLIENT]   Thinking: level=minimal")
            logger.info(f"[GEMINI-31-CLIENT]   Transcription: ENABLED")
        except Exception as e:
            logger.error(f"[GEMINI-31-CLIENT] ❌ Error sending setup: {e}")
            logger.error(traceback.format_exc())
            return False

        # Create conversation record
        if self.db_session:
            try:
                conv = GeminiConversation(
                    assistant_id=self.assistant_config.id,
                    session_id=self.session_id,
                    user_message="",
                    assistant_message="",
                )
                self.db_session.add(conv)
                self.db_session.commit()
                self.db_session.refresh(conv)
                self.conversation_record_id = str(conv.id)
                logger.info(f"[GEMINI-31-CLIENT] Conversation record created: {self.conversation_record_id}")
            except Exception as e:
                logger.error(f"[GEMINI-31-CLIENT] Error creating conversation: {e}")

        return True

    async def send_initial_greeting(self) -> bool:
        """
        Send initial greeting prompt to trigger voice greeting from Gemini.
        Uses greeting_message from assistant config.
        
        Returns:
            True if sent successfully
        """
        if not self.is_connected or not self.ws:
            logger.error("[GEMINI-31-CLIENT] Cannot send greeting: not connected")
            return False
        
        if self.greeting_sent:
            logger.info("[GEMINI-31-CLIENT] Greeting already sent, skipping")
            return True
        
        # Get greeting from config
        greeting_message = getattr(self.assistant_config, "greeting_message", None)
        
        if not greeting_message:
            logger.info("[GEMINI-31-CLIENT] No greeting_message configured, skipping auto-greet")
            return False
        
        try:
            logger.info(f"[GEMINI-31-CLIENT] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            logger.info(f"[GEMINI-31-CLIENT] 👋 SENDING INITIAL GREETING")
            logger.info(f"[GEMINI-31-CLIENT]    Message: {greeting_message[:100]}...")
            logger.info(f"[GEMINI-31-CLIENT] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            
            # Gemini 3.1: clientContent запрещён во время разговора,
            # используем realtimeInput с текстом
            payload = {
                "realtimeInput": {
                    "text": f"Поприветствуй пользователя голосом. Скажи именно это: \"{greeting_message}\""
                }
            }
            
            await self.ws.send(json.dumps(payload))
            self.greeting_sent = True
            
            logger.info(f"[GEMINI-31-CLIENT] ✅ Greeting prompt sent successfully")
            logger.info(f"[GEMINI-31-CLIENT]    Gemini will now speak the greeting")
            
            return True
            
        except Exception as e:
            logger.error(f"[GEMINI-31-CLIENT] ❌ Error sending greeting: {e}")
            logger.error(traceback.format_exc())
            return False

    async def send_text_message(self, text: str) -> bool:
        """
        Send a text message to Gemini (triggers voice response).
        
        Args:
            text: Text message to send
            
        Returns:
            True if sent successfully
        """
        if not self.is_connected or not self.ws:
            logger.error("[GEMINI-31-CLIENT] Cannot send message: not connected")
            return False
        
        try:
            logger.info(f"[GEMINI-31-CLIENT] 💬 Sending text message: {text[:100]}...")
            
            # Gemini 3.1: clientContent запрещён, используем realtimeInput
            payload = {
                "realtimeInput": {
                    "text": text
                }
            }

            await self.ws.send(json.dumps(payload))
            logger.info(f"[GEMINI-31-CLIENT] ✅ Text message sent via realtimeInput")
            return True
            
        except Exception as e:
            logger.error(f"[GEMINI-31-CLIENT] ❌ Error sending text message: {e}")
            return False

    async def handle_interruption(self) -> bool:
        """Handle interruption events."""
        try:
            current_time = time.time()
            
            # Debounce protection
            protection_time = 0.15 if self.is_ios else 0.2
            
            if current_time - self.last_interruption_time < protection_time:
                logger.info(f"[GEMINI-31-CLIENT] Ignoring duplicate interruption (debounce: {protection_time}s)")
                return True
                
            self.last_interruption_time = current_time
            self.interruption_occurred = True
            
            logger.info(f"[GEMINI-31-CLIENT] Handling interruption")
            
            # Gemini handles interruption automatically via VAD
            # Just update our state
            self.is_assistant_speaking = False
            self.current_response_id = None
            self.current_audio_samples = 0
            
            logger.info("[GEMINI-31-CLIENT] Interruption handled successfully")
            return True
            
        except Exception as e:
            logger.error(f"[GEMINI-31-CLIENT] Error handling interruption: {e}")
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

    async def send_function_result(self, function_call_id: str, result: Dict[str, Any]) -> Dict[str, bool]:
        """
        Send function result back to Gemini via toolResponse.
        
        According to Gemini Live API documentation:
        "BidiGenerateContentClientContent shouldn't be used to provide a response 
        to the function calls issued by the model. BidiGenerateContentToolResponse 
        should be used instead."
        
        Args:
            function_call_id: ID from the toolCall event
            result: Function execution result
            
        Returns:
            Dict with success status
        """
        if not self.is_connected or not self.ws:
            error_msg = "Cannot send function result: not connected"
            logger.error(f"[GEMINI-31-CLIENT] {error_msg}")
            return {
                "success": False,
                "error": error_msg
            }
        
        try:
            logger.info(f"[GEMINI-31-CLIENT] 📤 Sending toolResponse for call ID: {function_call_id}")
            logger.info(f"[GEMINI-31-CLIENT]    Function: {self.last_function_name}")
            
            # ✅ CORRECT FORMAT per Live API WebSocket documentation
            # https://ai.google.dev/api/live#BidiGenerateContentToolResponse
            result_payload = {
                "toolResponse": {  # NOT client_content!
                    "functionResponses": [{  # Plural!
                        "id": function_call_id,  # CRITICAL: ID from toolCall event
                        "response": result  # Your result as-is
                    }]
                }
            }
            
            # Log payload preview
            payload_preview = json.dumps(result_payload, ensure_ascii=False)[:300]
            logger.info(f"[GEMINI-31-CLIENT] Payload: {payload_preview}...")
            
            await self.ws.send(json.dumps(result_payload))
            logger.info(f"[GEMINI-31-CLIENT] ✅ toolResponse sent successfully")
            
            return {
                "success": True,
                "error": None
            }
            
        except Exception as e:
            error_msg = f"Error sending toolResponse: {e}"
            logger.error(f"[GEMINI-31-CLIENT] {error_msg}")
            return {
                "success": False,
                "error": error_msg
            }

    async def send_screen_context(self, image_base64: str, silent: bool = True) -> bool:
        """
        Send screen capture to conversation context WITHOUT triggering a response.
        
        Args:
            image_base64: Base64-encoded image (data:image/jpeg;base64,...)
            silent: If True, don't trigger response (default: True)
        
        Returns:
            True if sent successfully
        """
        if not self.is_connected or not self.ws:
            logger.error("[GEMINI-31-CLIENT] Cannot send context: not connected")
            return False
        
        try:
            image_size_kb = len(image_base64) // 1024
            logger.info(f"[GEMINI-31-CLIENT] Sending screen context silently ({image_size_kb}KB)")
            
            # Extract base64 data (remove data:image/jpeg;base64, prefix if present)
            if "base64," in image_base64:
                image_base64 = image_base64.split("base64,")[1]
            
            # Send as realtime_input with inline_data
            payload = {
                "realtimeInput": {
                    "video": {
                        "mimeType": "image/jpeg",
                        "data": image_base64
                    }
                }
            }
            
            await self.ws.send(json.dumps(payload))
            logger.info("[GEMINI-31-CLIENT] ✅ Context image added to conversation")
            
            if not silent:
                logger.info("[GEMINI-31-CLIENT] Non-silent mode - model will process image")
            else:
                logger.info("[GEMINI-31-CLIENT] ⏸️ Silent mode - no response requested")
            
            return True
            
        except Exception as e:
            logger.error(f"[GEMINI-31-CLIENT] Error sending screen context: {e}")
            return False

    async def process_audio(self, audio_buffer: bytes) -> bool:
        """
        Process and send audio data to Gemini API.
        
        ✅ Pure Gemini VAD mode:
        - Just sends audio continuously
        - Gemini decides when to respond
        - No manual commit needed
        """
        if not self.is_connected or not self.ws or not audio_buffer:
            return False
        try:
            # Gemini expects base64-encoded PCM16 audio at 16kHz
            data_b64 = base64.b64encode(audio_buffer).decode("utf-8")
            
            payload = {
                "realtimeInput": {
                    "audio": {
                        "mimeType": "audio/pcm;rate=16000",
                        "data": data_b64
                    }
                }
            }
            
            await self.ws.send(json.dumps(payload))
            return True
        except ConnectionClosed:
            logger.error("[GEMINI-31-CLIENT] Connection closed while sending audio")
            self.is_connected = False
            return False
        except Exception as e:
            logger.error(f"[GEMINI-31-CLIENT] Error processing audio: {e}")
            return False

    async def close(self) -> None:
        """Close WebSocket connection."""
        if self.ws:
            try:
                await self.ws.close()
                device_info = "iOS" if self.is_ios else ("Android" if self.is_android else "Desktop")
                logger.info(f"[GEMINI-31-CLIENT] WebSocket closed ({device_info})")
            except Exception as e:
                logger.error(f"[GEMINI-31-CLIENT] Error closing WebSocket: {e}")
        self.is_connected = False
        
        self.is_assistant_speaking = False
        self.current_response_id = None
        self.current_audio_samples = 0
        self.interruption_occurred = False
        self.greeting_sent = False

    async def receive_messages(self) -> AsyncGenerator[Dict[str, Any], None]:
        """Receive and yield messages from Gemini WebSocket."""
        if not self.is_connected or not self.ws:
            return
            
        try:
            async for message in self.ws:
                try:
                    data = json.loads(message)
                    yield data
                except json.JSONDecodeError:
                    logger.error(f"[GEMINI-31-CLIENT] Failed to decode: {message[:100]}...")
        except ConnectionClosed:
            device_info = "iOS" if self.is_ios else ("Android" if self.is_android else "Desktop")
            logger.info(f"[GEMINI-31-CLIENT] WebSocket closed ({device_info})")
            self.is_connected = False
        except Exception as e:
            logger.error(f"[GEMINI-31-CLIENT] Error receiving messages: {e}")
            self.is_connected = False
