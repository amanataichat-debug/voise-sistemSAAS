# backend/websockets/__init__.py
"""
WebSocket module for Voksy AI application.
Handles real-time communication with clients.

🆕 OpenAI voice runs on GPT-Live (gpt-live-1, full-duplex); Realtime GA kept as legacy
🆕 Google Gemini Live on gemini-3.8-live (Browser Agent and 3.1/2.5 variants removed)
🆕 Now includes xAI Grok Voice Agent API support
🆕 v3.3: Voximplant ↔ Gemini bridge (fallback for Vox Gemini module)
🧪 Experimental: Streaming TTS with sentence detection + ElevenLabs
"""

# 📌 OpenAI - Старые обработчики (Beta API)
from .handler import handle_websocket_connection
from .openai_client import OpenAIRealtimeClient

# OpenAI GPT-Live (gpt-live-1) — актуальный голосовой транспорт OpenAI
from .handler_live import handle_live_websocket_connection
from .live_client import OpenAILiveClient

# OpenAI Realtime GA (легаси, оставлен для отката)
from .handler_realtime_new import handle_websocket_connection_new
from .openai_client_new import OpenAIRealtimeClientNew

# Google Gemini Live (gemini-3.8-live)
from .gemini_client import GeminiLiveClient
from .handler_gemini import handle_gemini_websocket_connection

# 🆕 xAI Grok Voice Agent API
from .grok_client import GrokVoiceClient, map_voice_to_grok
from .handler_grok import handle_grok_websocket_connection

# 🐟 Fish Audio — прокси синтеза речи для сценариев Voximplant
from .handler_fish_tts import handle_fish_tts_connection  # legacy: прокси для сценариев Voximplant (не используется)
from .handler_fish import handle_fish_websocket_connection  # Fish: OpenAI Realtime (текст) + Fish Audio
from .fish_llm_client import FishLLMClient
from .fish_tts_client import FishTTSClient

# 📞 Voximplant интеграция
from .voximplant_adapter import VoximplantAdapter, handle_voximplant_websocket
from .voximplant_handler import (
    VoximplantProtocolHandler, 
    SimpleVoximplantHandler,
    handle_voximplant_websocket_with_protocol,
    handle_voximplant_websocket_simple
)

# 🆕 v3.3: Voximplant ↔ Gemini bridge (fallback)
from .handler_vox_gemini import handle_vox_gemini_websocket

__all__ = [
    # OpenAI Beta API (старая версия)
    "handle_websocket_connection", 
    "OpenAIRealtimeClient",
    
    # OpenAI GPT-Live (актуальный транспорт)
    "handle_live_websocket_connection",
    "OpenAILiveClient",

    # OpenAI GA API (легаси)
    "handle_websocket_connection_new",
    "OpenAIRealtimeClientNew",
    
    # Google Gemini Live (gemini-3.8-live)
    "GeminiLiveClient",
    "handle_gemini_websocket_connection",
    
    # 🆕 xAI Grok Voice Agent API
    "GrokVoiceClient",
    "handle_grok_websocket_connection",
    "map_voice_to_grok",
    
    # 🧪 Streaming TTS (экспериментальная версия)
    "handle_websocket_connection_streaming",
    "handle_websocket_connection_streaming_openai_tts",
    "handle_websocket_connection_streaming_elevenlabs_tts",
    "OpenAIRealtimeClientStreaming",
    "StreamingSentenceDetector",
    
    # 🐟 Fish Audio TTS proxy
    "handle_fish_tts_connection",
    "handle_fish_websocket_connection",
    "FishLLMClient",
    "FishTTSClient",

    # Voximplant
    "VoximplantAdapter",
    "handle_voximplant_websocket",
    "VoximplantProtocolHandler", 
    "SimpleVoximplantHandler",
    "handle_voximplant_websocket_with_protocol",
    "handle_voximplant_websocket_simple",
    
    # 🆕 v3.3: Voximplant ↔ Gemini bridge
    "handle_vox_gemini_websocket",
]
