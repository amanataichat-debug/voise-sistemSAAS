# backend/models/grok_assistant.py
"""
Grok Assistant models for Voksy AI application.
xAI Grok Voice Agent API integration.

Voices: Ara, Rex, Sal, Eve, Leo
Audio formats: PCM (8-48kHz), G.711 μ-law/A-law
"""

import uuid
from sqlalchemy import Column, String, Boolean, ForeignKey, DateTime, JSON, func, Integer, Float, Text, Enum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import enum

from backend.models.base import Base


class GrokVoice(str, enum.Enum):
    """Available Grok voice options."""
    ARA = "Ara"      # Female, warm, friendly - default
    REX = "Rex"      # Male, confident, clear
    SAL = "Sal"      # Neutral, smooth, balanced
    EVE = "Eve"      # Female, energetic, upbeat
    LEO = "Leo"      # Male, authoritative, strong


class GrokAssistantConfig(Base):
    """
    Configuration for a Grok voice assistant.
    Uses xAI Grok Voice Agent API (wss://api.x.ai/v1/realtime)
    
    Features:
    - 5 voices: Ara, Rex, Sal, Eve, Leo
    - Multiple audio formats (PCM, G.711)
    - Server-side VAD
    - Function calling (web_search, x_search, file_search, custom)
    - Telephony support
    """
    __tablename__ = "grok_assistant_configs"
    
    # Primary fields
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    name = Column(String(255), nullable=False)
    description = Column(String(500), nullable=True)
    system_prompt = Column(Text, nullable=True)
    
    # Voice settings (Grok voices)
    # Available: Ara (warm), Rex (confident), Sal (balanced), Eve (energetic), Leo (authoritative)
    voice = Column(String(50), default="Ara", nullable=False)
    language = Column(String(10), default="ru", nullable=False)
    
    # Audio settings
    # For web: 24000 (default), 16000, 48000
    # For telephony: 8000 (G.711)
    sample_rate = Column(Integer, default=24000, nullable=False)
    audio_format = Column(String(20), default="audio/pcm", nullable=False)  # audio/pcm, audio/pcmu, audio/pcma
    
    # Greeting and logging
    greeting_message = Column(String(500), nullable=True, default="Здравствуйте! Чем я могу вам помочь?")
    google_sheet_id = Column(String(255), nullable=True)
    
    # Status flags
    is_active = Column(Boolean, default=True, nullable=False)
    is_public = Column(Boolean, default=False, nullable=False)
    
    # Timestamps
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
    
    # Stats
    total_conversations = Column(Integer, default=0, nullable=False)
    
    # Model parameters
    temperature = Column(Float, default=0.7, nullable=False)
    max_tokens = Column(Integer, default=4096, nullable=False)
    
    # Functions configuration (JSON)
    # Format: {"enabled_functions": ["query_llm", "send_webhook", "web_search", ...]}
    # Grok-specific: web_search, x_search, file_search supported natively
    functions = Column(JSON, nullable=True)
    
    # 🆕 Grok-specific features
    enable_web_search = Column(Boolean, default=False, nullable=False)  # Native Grok web search
    enable_x_search = Column(Boolean, default=False, nullable=False)    # X (Twitter) search
    x_allowed_handles = Column(JSON, nullable=True)  # List of allowed X handles for x_search
    collection_ids = Column(JSON, nullable=True)     # Vector store IDs for file_search
    
    # Telephony settings
    is_telephony_enabled = Column(Boolean, default=False, nullable=False)

    # ── Cascade LLM fields (assistant_type = "cascade") ──────────────────────
    assistant_type    = Column(String(20),  default="grok",   nullable=False)
    openrouter_model  = Column(String(150), nullable=True)
    tts_provider      = Column(String(30),  nullable=True)
    tts_voice         = Column(String(100), nullable=True)
    tts_lang          = Column(String(10),  default="ru", nullable=False)
    asr_lang          = Column(String(10),  default="ru", nullable=False)
    # Пауза перед ответом (пресет на странице агента): сколько тишины ждёт
    # Silero, прежде чем счесть, что собеседник договорил. 300 — быстрый ответ,
    # 650 — сбалансированный, 1000 — терпеливый. Сценарий кладёт это значение в
    # VoxTurnTaking (minSilenceDurationMs), страховочный таймаут = значение+250.
    silence_duration_ms = Column(Integer, default=300, nullable=True)

    # Relationships
    user = relationship("User", back_populates="grok_assistants")
    conversations = relationship(
        "GrokConversation", 
        back_populates="assistant", 
        cascade="all, delete-orphan"
    )
    
    def __repr__(self):
        return f"<GrokAssistantConfig(id={self.id}, name='{self.name}', voice='{self.voice}')>"
    
    def get_audio_config(self):
        """Get audio configuration for API."""
        return {
            "input": {
                "format": {
                    "type": self.audio_format,
                    "rate": self.sample_rate
                }
            },
            "output": {
                "format": {
                    "type": self.audio_format,
                    "rate": self.sample_rate
                }
            }
        }
    
    def get_tools_config(self):
        """Get tools configuration for session.update."""
        tools = []
        
        # Native Grok tools
        if self.enable_web_search:
            tools.append({"type": "web_search"})
        
        if self.enable_x_search:
            x_tool = {"type": "x_search"}
            if self.x_allowed_handles:
                x_tool["allowed_x_handles"] = self.x_allowed_handles
            tools.append(x_tool)
        
        if self.collection_ids:
            tools.append({
                "type": "file_search",
                "vector_store_ids": self.collection_ids,
                "max_num_results": 10
            })
        
        return tools
    
    def to_dict(self):
        """Convert to dictionary for API response."""
        return {
            "id": str(self.id),
            "user_id": str(self.user_id) if self.user_id else None,
            "name": self.name,
            "description": self.description,
            "system_prompt": self.system_prompt,
            "voice": self.voice,
            "language": self.language,
            "sample_rate": self.sample_rate,
            "audio_format": self.audio_format,
            "greeting_message": self.greeting_message,
            "google_sheet_id": self.google_sheet_id,
            "is_active": self.is_active,
            "is_public": self.is_public,
            "total_conversations": self.total_conversations,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "functions": self.functions,
            "enable_web_search": self.enable_web_search,
            "enable_x_search": self.enable_x_search,
            "is_telephony_enabled": self.is_telephony_enabled,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class GrokConversation(Base):
    """
    Conversation log for Grok assistants.
    Stores dialog history for analytics and debugging.
    """
    __tablename__ = "grok_conversations"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assistant_id = Column(
        UUID(as_uuid=True), 
        ForeignKey("grok_assistant_configs.id", ondelete="CASCADE"), 
        nullable=False
    )
    session_id = Column(String(100), nullable=False, index=True)
    user_message = Column(Text, nullable=True)
    assistant_message = Column(Text, nullable=True)
    
    # Function call tracking
    function_name = Column(String(100), nullable=True)
    function_result = Column(JSON, nullable=True)
    
    # Telephony info
    caller_number = Column(String(50), nullable=True)
    call_direction = Column(String(20), nullable=True)  # inbound, outbound
    
    # Metrics
    tokens_used = Column(Integer, default=0, nullable=False)
    audio_duration_ms = Column(Integer, default=0, nullable=True)  # Audio duration in milliseconds
    latency_ms = Column(Integer, nullable=True)  # Response latency
    
    created_at = Column(DateTime, default=func.now(), nullable=False)
    
    # Relationship
    assistant = relationship("GrokAssistantConfig", back_populates="conversations")
    
    def __repr__(self):
        return f"<GrokConversation(id={self.id}, assistant_id={self.assistant_id}, session_id='{self.session_id}')>"
    
    def to_dict(self):
        """Convert to dictionary for API response."""
        return {
            "id": str(self.id),
            "assistant_id": str(self.assistant_id),
            "session_id": self.session_id,
            "user_message": self.user_message,
            "assistant_message": self.assistant_message,
            "function_name": self.function_name,
            "function_result": self.function_result,
            "caller_number": self.caller_number,
            "call_direction": self.call_direction,
            "tokens_used": self.tokens_used,
            "audio_duration_ms": self.audio_duration_ms,
            "latency_ms": self.latency_ms,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
