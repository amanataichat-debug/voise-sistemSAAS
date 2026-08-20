# backend/models/translate_assistant.py
"""
Translate Assistant models for Voicyfy application.
OpenAI Realtime Translation API integration (gpt-realtime-translate).

✅ v1.0: Initial Translate Assistant implementation

Особенности модели перевода (НЕ путать с обычным Realtime API):
- Endpoint: wss://api.openai.com/v1/realtime/translations?model=gpt-realtime-translate
- Авторизация тем же ключом, что и Realtime-2 (User.openai_api_key)
- Конфигурируется только output language через session.update
- Нет instructions / voice / tools / conversation lifecycle
- Аудио: PCM16, 24kHz, base64
- Выходные языки (13): en, es, fr, de, it, pt, nl, pl, ru, ja, ko, zh, ar
- Цена: $0.034/минута (фикс)
"""

import uuid
from sqlalchemy import Column, String, Boolean, ForeignKey, DateTime, func, Integer, Float, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from backend.models.base import Base


class TranslateAssistantConfig(Base):
    """
    Конфигурация ассистента-переводчика.
    Использует OpenAI Realtime Translation API (gpt-realtime-translate).

    Ассистент ПЕРЕВОДИТ речь, а не отвечает на неё.
    Входной язык определяется автоматически (70+ языков),
    выходной язык задаётся через output_language.
    """
    __tablename__ = "translate_assistant_configs"

    # Primary fields
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    name = Column(String(255), nullable=False)
    description = Column(String(500), nullable=True)

    # Язык перевода (target). Один из 13 поддерживаемых выходных языков.
    output_language = Column(String(10), default="ru", nullable=False)
    # Подсказка для исходного языка (необязательно, обычно автодетект)
    source_language_hint = Column(String(10), nullable=True)
    # Показывать ли транскрипт исходной речи (субтитры сверху)
    enable_source_transcript = Column(Boolean, default=True, nullable=False)

    # Приветственное сообщение виджета
    greeting_message = Column(String(500), nullable=True)

    # Status flags
    is_active = Column(Boolean, default=True, nullable=False)
    is_public = Column(Boolean, default=False, nullable=False)

    # Stats
    total_conversations = Column(Integer, default=0, nullable=False)

    # Timestamps
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    user = relationship("User", back_populates="translate_assistants")
    conversations = relationship(
        "TranslateConversation",
        back_populates="assistant",
        cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<TranslateAssistantConfig(id={self.id}, name='{self.name}', output_language='{self.output_language}')>"

    def to_dict(self):
        """Преобразование в словарь для ответа API."""
        return {
            "id": str(self.id),
            "user_id": str(self.user_id) if self.user_id else None,
            "name": self.name,
            "description": self.description,
            "output_language": self.output_language,
            "source_language_hint": self.source_language_hint,
            "enable_source_transcript": self.enable_source_transcript,
            "greeting_message": self.greeting_message,
            "is_active": self.is_active,
            "is_public": self.is_public,
            "total_conversations": self.total_conversations,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class TranslateConversation(Base):
    """
    Лог сессии перевода.
    Сохраняет исходный текст, перевод и метрики для аналитики.
    """
    __tablename__ = "translate_conversations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assistant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("translate_assistant_configs.id", ondelete="CASCADE"),
        nullable=False
    )
    session_id = Column(String(100), nullable=False, index=True)

    # Тексты
    source_text = Column(Text, nullable=True)       # Распознанный исходник
    translated_text = Column(Text, nullable=True)   # Переведённый текст

    # Языки
    source_language = Column(String(10), nullable=True)  # Автоопределённый исходный язык
    target_language = Column(String(10), nullable=True)  # Язык перевода

    # Телефония (для будущей интеграции с Voximplant)
    caller_number = Column(String(50), nullable=True)
    call_direction = Column(String(20), nullable=True)  # inbound, outbound

    # Метрики
    duration_seconds = Column(Float, nullable=True)
    audio_duration_ms = Column(Integer, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    call_cost = Column(Float, nullable=True)  # $0.034/min

    created_at = Column(DateTime, default=func.now(), nullable=False)

    # Relationship
    assistant = relationship("TranslateAssistantConfig", back_populates="conversations")

    def __repr__(self):
        return f"<TranslateConversation(id={self.id}, assistant_id={self.assistant_id}, session_id='{self.session_id}')>"

    def to_dict(self):
        """Преобразование в словарь для ответа API."""
        return {
            "id": str(self.id),
            "assistant_id": str(self.assistant_id),
            "session_id": self.session_id,
            "source_text": self.source_text,
            "translated_text": self.translated_text,
            "source_language": self.source_language,
            "target_language": self.target_language,
            "caller_number": self.caller_number,
            "call_direction": self.call_direction,
            "duration_seconds": self.duration_seconds,
            "audio_duration_ms": self.audio_duration_ms,
            "latency_ms": self.latency_ms,
            "call_cost": self.call_cost,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
