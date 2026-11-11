# backend/websockets/__init__.py
"""
WebSocket module for Voksy AI application.
Handles real-time communication with clients.

OpenAI voice runs on GPT-Live (gpt-live-1, full-duplex); Gemini Live on gemini-3.8-live;
Eleven cards (Voksy Voice / Voksy Live / Voksy Live Go) — handler_eleven. Legacy Realtime handlers and
Voximplant bridges were removed 11.11.2026 (branch 1111-clean-system).
"""

# OpenAI GPT-Live (gpt-live-1) — актуальный голосовой транспорт OpenAI
from .handler_live import handle_live_websocket_connection
from .live_client import OpenAILiveClient

# Google Gemini Live (gemini-3.8-live)
from .gemini_client import GeminiLiveClient
from .handler_gemini import handle_gemini_websocket_connection

# 🆕 xAI Grok Voice Agent API
from .grok_client import GrokVoiceClient, map_voice_to_grok
from .handler_grok import handle_grok_websocket_connection

# 🐟 Fish Audio
from .handler_fish import handle_fish_websocket_connection  # Fish: OpenAI Realtime (текст) + Fish Audio
from .fish_llm_client import FishLLMClient
from .fish_tts_client import FishTTSClient


__all__ = [
    # OpenAI GPT-Live (актуальный транспорт)
    "handle_live_websocket_connection",
    "OpenAILiveClient",

    # OpenAI GA API (легаси)

    
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

    "handle_fish_websocket_connection",
    "FishLLMClient",
    "FishTTSClient",

    # Voximplant

    
    # 🆕 v3.3: Voximplant ↔ Gemini bridge

]
