# backend/models/pinecone_config.py
import uuid
from sqlalchemy import Column, String, ForeignKey, DateTime, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from backend.models.base import Base

class PineconeConfig(Base):
    """
    Configuration for Pinecone knowledge base connections.
    """
    __tablename__ = "pinecone_configs"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Легаси-привязка к OpenAI-ассистенту (старые базы; новые её не заполняют)
    assistant_id = Column(UUID(as_uuid=True), ForeignKey("assistant_configs.id", ondelete="CASCADE"), nullable=True)
    # Индивидуальная база ассистента: владелец-пользователь и сам ассистент
    # любого провайдера (owner_type = 'eleven' | 'fish' | 'gemini' | 'openai',
    # owner_id = UUID ассистента строкой). Одна база на ассистента.
    # Колонки добавляются при старте в app.py (check_and_fix_all_missing_columns).
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    owner_type = Column(String(20), nullable=True)
    owner_id = Column(String(64), nullable=True, index=True)
    namespace = Column(String, nullable=False)
    char_count = Column(Integer, default=0)
    content_preview = Column(Text, nullable=True)  # первые 100-200 символов для предпросмотра
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=func.now(), onupdate=func.now(), nullable=False)
    # New fields
    full_content = Column(Text, nullable=True)  # Store complete content text
    name = Column(String(100), nullable=True)  # Optional name for the knowledge base
    
    # Только связь с ассистентом
    assistant = relationship("AssistantConfig", back_populates="pinecone_config", foreign_keys=[assistant_id])
