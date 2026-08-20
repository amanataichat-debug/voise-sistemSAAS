"""
Gemini Assistant models for WellcomeAI application.
Google Gemini Live API integration.
"""

import uuid
from sqlalchemy import Column, String, Boolean, ForeignKey, DateTime, JSON, func, Integer, Float, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from backend.models.base import Base


class GeminiAssistantConfig(Base):
    """
    Configuration for a Gemini voice assistant.
    Uses Google Gemini Live API (gemini-2.5-flash-native-audio-preview-09-2025)
    """
    __tablename__ = "gemini_assistant_configs"
    
    # Primary fields
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    system_prompt = Column(Text, nullable=True)
    
    # Voice settings (Gemini voices)
    # Available: Aoede, Charon, Kore, Fenrir, Puck, etc. (30 voices, 24 languages)
    voice = Column(String, default="Aoede", nullable=False)
    language = Column(String, default="ru", nullable=False)
    
    # Greeting and logging
    greeting_message = Column(String, nullable=True, default="Здравствуйте! Чем я могу вам помочь?")
    google_sheet_id = Column(String, nullable=True)
    
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
    max_tokens = Column(Integer, default=4000, nullable=False)  # Gemini supports up to 8192
    
    # Functions configuration (JSON)
    # Format: {"enabled_functions": ["query_llm", "send_webhook", ...]}
    functions = Column(JSON, nullable=True)
    
    # 🆕 Gemini-specific features
    enable_thinking = Column(Boolean, default=False, nullable=False)
    thinking_budget = Column(Integer, default=1024, nullable=True)  # Token budget for thinking
    enable_screen_context = Column(Boolean, default=False, nullable=False)  # For future UI
    model = Column(String(100), default="models/gemini-2.5-flash-native-audio-preview-12-2025", nullable=False)
    
    # Relationships
    user = relationship("User", back_populates="gemini_assistants")
    conversations = relationship(
        "GeminiConversation", 
        back_populates="assistant", 
        cascade="all, delete-orphan"
    )
    
    def __repr__(self):
        return f"<GeminiAssistantConfig(id={self.id}, name='{self.name}', voice='{self.voice}')>"


class GeminiConversation(Base):
    """
    Conversation log for Gemini assistants.
    Stores dialog history for analytics and debugging.
    """
    __tablename__ = "gemini_conversations"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assistant_id = Column(
        UUID(as_uuid=True), 
        ForeignKey("gemini_assistant_configs.id", ondelete="CASCADE"), 
        nullable=False
    )
    session_id = Column(String, nullable=False)
    user_message = Column(Text, nullable=True)
    assistant_message = Column(Text, nullable=True)
    caller_number = Column(String, nullable=True)  # For phone integration
    tokens_used = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    
    # Relationship
    assistant = relationship("GeminiAssistantConfig", back_populates="conversations")
    
    def __repr__(self):
        return f"<GeminiConversation(id={self.id}, assistant_id={self.assistant_id}, session_id='{self.session_id}')>"
