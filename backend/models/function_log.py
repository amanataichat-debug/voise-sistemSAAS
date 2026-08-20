import uuid
from sqlalchemy import Column, String, Float, JSON, ForeignKey, DateTime, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from backend.models.base import Base, BaseModel

class FunctionLog(Base, BaseModel):
    """
    Модель для логирования вызовов функций.
    
    Универсальная таблица для всех типов ассистентов:
    - OpenAI (assistant_configs, conversations)
    - Gemini (gemini_assistant_configs, gemini_conversations)
    - Grok (grok_assistant_configs, grok_conversations)
    
    ForeignKey убраны для assistant_id и conversation_id чтобы поддерживать
    все типы ассистентов. user_id остается с FK т.к. таблица users единая.
    """
    __tablename__ = "function_logs"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    assistant_id = Column(UUID(as_uuid=True), nullable=True)  # Universal - any assistant type
    conversation_id = Column(UUID(as_uuid=True), nullable=True)  # Universal - any conversation type
    
    function_name = Column(String, nullable=False)
    function_version = Column(String, nullable=True)
    arguments = Column(JSON, nullable=True)
    result = Column(JSON, nullable=True)
    
    execution_time_ms = Column(Float, nullable=True)
    status = Column(String, nullable=False)  # success, error, timeout
    error_message = Column(Text, nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
