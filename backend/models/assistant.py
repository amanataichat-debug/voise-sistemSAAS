"""
Assistant models for WellcomeAI application.
"""

import uuid
from sqlalchemy import Column, String, Boolean, ForeignKey, DateTime, JSON, func, Integer, Float
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from backend.models.base import Base

class AssistantConfig(Base):
    """
    Configuration for a voice assistant.
    """
    __tablename__ = "assistant_configs"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    system_prompt = Column(String, nullable=True)
    voice = Column(String, default="alloy", nullable=False)
    language = Column(String, default="ru", nullable=False)
    google_sheet_id = Column(String, nullable=True)
    greeting_message = Column(String, nullable=True, default="Здравствуйте! Чем я могу вам помочь?")  # ✅ НОВОЕ ПОЛЕ
    enable_vision = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_public = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
    total_conversations = Column(Integer, default=0, nullable=False)
    temperature = Column(Float, default=0.7, nullable=False)
    max_tokens = Column(Integer, default=1000, nullable=False)
    functions = Column(JSON, nullable=True)
    
    # Relationship with User
    user = relationship("User", back_populates="assistants")
    
    # Relationship with Conversations
    conversations = relationship("Conversation", back_populates="assistant", cascade="all, delete-orphan")
    
    # Relationship with Files
    files = relationship("File", back_populates="assistant", cascade="all, delete-orphan")
    pinecone_config = relationship("PineconeConfig", back_populates="assistant", uselist=False, cascade="all, delete-orphan")
    # Define relationship with integrations
    integrations = relationship(
        "Integration",
        primaryjoin="AssistantConfig.id == Integration.assistant_id",
        cascade="all, delete-orphan",
        back_populates="assistant"
    )
