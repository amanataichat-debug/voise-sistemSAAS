# backend/models/cartesia_assistant.py
"""
Cartesia Assistant model for Voicyfy application.
Cartesia TTS provider integration — config only, call logic lives in Voximplant.
"""

import uuid
from sqlalchemy import Column, String, Boolean, ForeignKey, DateTime, JSON, func, Float, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from backend.models.base import Base


class CartesiaAssistantConfig(Base):
    """
    Configuration for a Cartesia voice assistant.
    Cartesia is a TTS provider — all call logic lives in Voximplant scripts.
    This model stores the agent config that is served via /config and /outbound-config.
    """
    __tablename__ = "cartesia_assistant_configs"

    # Primary fields
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    system_prompt = Column(Text, nullable=True)

    # Cartesia voice settings
    cartesia_voice_id = Column(String, nullable=True)
    voice_speed = Column(Float, default=1.0)

    # Greeting
    greeting_message = Column(String, default="Здравствуйте! Чем я могу вам помочь?")

    # Functions configuration (JSON)
    functions = Column(JSON, nullable=True)

    # Status
    is_active = Column(Boolean, default=True)

    # Timestamps
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    user = relationship("User", back_populates="cartesia_assistants")

    def __repr__(self):
        return f"<CartesiaAssistantConfig(id={self.id}, name='{self.name}')>"

    def to_dict(self):
        """Convert to dictionary for API response."""
        return {
            "id": str(self.id),
            "user_id": str(self.user_id) if self.user_id else None,
            "name": self.name,
            "description": self.description,
            "system_prompt": self.system_prompt,
            "cartesia_voice_id": self.cartesia_voice_id,
            "voice_speed": self.voice_speed,
            "greeting_message": self.greeting_message,
            "functions": self.functions,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
