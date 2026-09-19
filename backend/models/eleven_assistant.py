# backend/models/eleven_assistant.py
"""
Eleven-ассистент — «половинный каскад» на серверных ключах (как Fish):

    клиент (виджет / SIP-шлюз через HandlerSocket)
        │  PCM16 24 кГц, протокол виджета
        ▼
    backend/websockets/handler_eleven.py
        ├── OpenAI Realtime (gpt-realtime-2, output_modalities=["text"]):
        │     распознаёт речь, детектирует конец реплики, ведёт диалог, зовёт функции
        └── ElevenLabs Text-to-Dialogue WebSocket (eleven_v3_conversational / eleven_v3):
              озвучивает текст модели голосом voice_id → PCM16 24 кГц клиенту

Ключи серверные: settings.OPENAI_API_KEY (диалог) и settings.ELEVENLABS_API_KEY
(синтез). Пользовательские ключи не используются.

Язык по умолчанию — кыргызский (ky): у ElevenLabs его умеет только семейство
Eleven v3, поэтому другие модели синтеза здесь не предлагаются. Телефония и
виджет проходят через один хендлер; диалоги пишутся в eleven_conversations
(у conversations FK на assistant_configs).
"""

import uuid

from sqlalchemy import Column, String, Boolean, ForeignKey, DateTime, JSON, func, Float, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from backend.models.base import Base

# Модели синтеза ElevenLabs, которые умеют кыргызский и работают по
# Text-to-Dialogue WebSocket. Цены — прайс ElevenLabs API (сентябрь 2026).
ELEVEN_TTS_MODELS = [
    {
        "id": "eleven_v3_conversational",
        "title": "Eleven v3 Conversational",
        "description": "Для живого диалога: первый звук ≈ 280 мс. Один голос на соединение.",
        "price": "$0.05 за 1000 символов",
        "price_per_1k_chars_usd": 0.05,
    },
    {
        "id": "eleven_v3",
        "title": "Eleven v3",
        "description": "Максимальное качество и выразительность, задержка выше.",
        "price": "$0.10 за 1000 символов",
        "price_per_1k_chars_usd": 0.10,
    },
]
ELEVEN_TTS_MODEL_IDS = [m["id"] for m in ELEVEN_TTS_MODELS]
DEFAULT_ELEVEN_TTS_MODEL = "eleven_v3_conversational"

# Языки, которые предлагаем в карточке (все есть у Eleven v3). Код — ISO 639-1,
# он же уходит в language_code ElevenLabs и в инструкцию диалоговой модели.
ELEVEN_LANGUAGES = [
    {"code": "ky", "title": "Кыргызский"},
    {"code": "ru", "title": "Русский"},
    {"code": "kk", "title": "Казахский"},
    {"code": "uz", "title": "Узбекский"},
    {"code": "en", "title": "Английский"},
    {"code": "tr", "title": "Турецкий"},
]
DEFAULT_ELEVEN_LANGUAGE = "ky"

# Стабильность голоса у v3 дискретная: 0 — Creative (эмоциональнее), 0.5 — Natural, 1 — Robust.
ELEVEN_STABILITY_LEVELS = [
    {"value": 0.0, "title": "Creative — эмоциональнее, интонация «гуляет»"},
    {"value": 0.5, "title": "Natural — естественно (рекомендуется)"},
    {"value": 1.0, "title": "Robust — ровно и предсказуемо"},
]
DEFAULT_ELEVEN_STABILITY = 0.5

# Модель OpenAI Realtime, которая ведёт диалог и транскрибирует речь (как у Fish).
DEFAULT_ELEVEN_LLM_MODEL = "gpt-realtime-2"
ELEVEN_LLM_MODELS = ["gpt-realtime-2", "gpt-realtime-2.1-mini"]

DEFAULT_ELEVEN_GREETING = "Саламатсызбы! Мен сизге кантип жардам бере алам?"
ELEVEN_SAMPLE_RATE = 24000  # частота выхода браузерных хендлеров


class ElevenAssistantConfig(Base):
    """
    Конфигурация Eleven-ассистента (OpenAI Realtime текстом + озвучка ElevenLabs).
    Обслуживается хендлером backend/websockets/handler_eleven.py
    (маршрут /ws/eleven/{assistant_id}; по телефону — через SIP-шлюз).
    """
    __tablename__ = "eleven_assistant_configs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    name = Column(String(255), nullable=False)
    description = Column(String(500), nullable=True)
    system_prompt = Column(Text, nullable=True)

    # Голос ElevenLabs (voice_id из аккаунта; библиотечный голос сначала добавляется в аккаунт)
    voice_id = Column(String(255), nullable=True)
    voice_name = Column(String(255), nullable=True)
    tts_model = Column(String(50), default=DEFAULT_ELEVEN_TTS_MODEL, nullable=False)
    stability = Column(Float, default=DEFAULT_ELEVEN_STABILITY, nullable=True)

    # LLM settings (OpenAI Realtime на серверном ключе)
    llm_model = Column(String(100), default=DEFAULT_ELEVEN_LLM_MODEL, nullable=False)
    language = Column(String(10), default=DEFAULT_ELEVEN_LANGUAGE, nullable=False)

    greeting_message = Column(String(500), nullable=True, default=DEFAULT_ELEVEN_GREETING)
    google_sheet_id = Column(String(255), nullable=True)
    functions = Column(JSON, nullable=True)

    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    user = relationship("User", back_populates="eleven_assistants")
    conversations = relationship(
        "ElevenConversation", back_populates="assistant", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<ElevenAssistantConfig(id={self.id}, name='{self.name}', voice='{self.voice_id}')>"

    def to_dict(self):
        return {
            "id": str(self.id),
            "user_id": str(self.user_id) if self.user_id else None,
            "name": self.name,
            "description": self.description,
            "system_prompt": self.system_prompt,
            "voice_id": self.voice_id,
            "voice_name": self.voice_name,
            "tts_model": self.tts_model,
            "stability": self.stability,
            "llm_model": self.llm_model,
            "language": self.language,
            "greeting_message": self.greeting_message,
            "google_sheet_id": self.google_sheet_id,
            "functions": self.functions,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ElevenConversation(Base):
    """
    Журнал диалогов Eleven-ассистентов (таблица eleven_conversations).
    Колонки те же, что у fish_conversations: страница «Диалоги» объединяет
    таблицы через UNION ALL, SIP-сервис проставляет номер и направление.
    """
    __tablename__ = "eleven_conversations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assistant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("eleven_assistant_configs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    session_id = Column(String, nullable=False, index=True)
    user_message = Column(Text, nullable=True)
    assistant_message = Column(Text, nullable=True)
    caller_number = Column(String, nullable=True)
    call_direction = Column(String(20), nullable=True)
    tokens_used = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)

    assistant = relationship("ElevenAssistantConfig", back_populates="conversations")

    def __repr__(self):
        return f"<ElevenConversation(id={self.id}, assistant_id={self.assistant_id}, session_id='{self.session_id}')>"
