# backend/models/fish_assistant.py
"""
Fish Assistant model for Voksy AI application.

Fish-ассистент — «половинный каскад» на серверных ключах:

    клиент (виджет / SIP-шлюз через HandlerSocket)
        │  PCM16 24 кГц, протокол виджета
        ▼
    backend/websockets/handler_fish.py
        ├── OpenAI Realtime (gpt-realtime-2, output_modalities=["text"]):
        │     распознаёт речь, детектирует конец реплики, ведёт диалог, зовёт функции
        └── Fish Audio TTS (wss://api.fish.audio/v1/tts/live, MessagePack):
              озвучивает текст модели голосом reference_id → PCM16 24 кГц клиенту

Ключи серверные: settings.OPENAI_API_KEY (диалог) и settings.FISH_API_KEY
(синтез). Пользовательские ключи не используются.

Телефония и виджет проходят через один и тот же хендлер, поэтому функции,
приветствие, транскрипты и запись диалогов у них общие. Диалоги пишутся в
fish_conversations (у conversations FK на assistant_configs).
"""

import uuid
from sqlalchemy import Column, String, Boolean, ForeignKey, DateTime, JSON, func, Float, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from backend.models.base import Base

# Модель синтеза Fish Audio по умолчанию.
DEFAULT_FISH_MODEL = "s2.1-pro-free"

# Модель OpenAI Realtime, которая ведёт диалог и транскрибирует речь.
DEFAULT_FISH_LLM_MODEL = "gpt-realtime-2"
FISH_LLM_MODELS = ["gpt-realtime-2", "gpt-realtime-2.1-mini"]

# Частота PCM, которую хендлер запрашивает у Fish. 24000 — частота выхода
# браузерных хендлеров (виджет играет 24 кГц, SIP-адаптер сам ресемплирует в 8).
# Колонка sample_rate у старых записей может хранить 8000 — хендлер её не читает.
DEFAULT_FISH_SAMPLE_RATE = 24000

# Режим латентности Fish: balanced — минимальное время до первого аудио
# (то, что нужно голосовому агенту), normal — выше качество, выше задержка,
# low — ещё быстрее старт ценой качества.
DEFAULT_FISH_LATENCY = "balanced"

# Пределы параметров синтеза Fish (из их API).
FISH_SPEED_MIN, FISH_SPEED_MAX = 0.5, 2.0
FISH_TEMPERATURE_MIN, FISH_TEMPERATURE_MAX = 0.0, 1.0

# Модели, которые принимает API. Список шире того, что предлагается в UI:
# у части агентов в базе уже стоит s2.1-pro или s1, и их правки не должны
# отваливаться с 400 при сохранении.
FISH_MODELS = ["s1", "s2-pro", "s2.1-pro", "s2.1-pro-free"]

# То, что показываем в селекторе на fish-agents.html. Пока обкатываем
# только бесплатную модель — остальные убраны из выбора намеренно.
FISH_SELECTABLE_MODELS = ["s2.1-pro-free"]

# low — быстрее всего начинает говорить, normal — лучшее качество.
FISH_LATENCY_MODES = ["low", "balanced", "normal"]


class FishAssistantConfig(Base):
    """
    Configuration for a Fish Audio voice assistant.

    Обслуживается хендлером backend/websockets/handler_fish.py
    (маршрут /ws/fish/{assistant_id}; по телефону — через SIP-шлюз).
    """
    __tablename__ = "fish_assistant_configs"

    # Primary fields
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    name = Column(String(255), nullable=False)
    description = Column(String(500), nullable=True)
    system_prompt = Column(Text, nullable=True)

    # Fish voice settings
    # fish_voice_id — reference_id голоса из библиотеки fish.audio (в т.ч. клон).
    fish_voice_id = Column(String(255), nullable=True)
    fish_model = Column(String(50), default=DEFAULT_FISH_MODEL, nullable=False)
    fish_latency = Column(String(20), default=DEFAULT_FISH_LATENCY, nullable=False)
    sample_rate = Column(Integer, default=DEFAULT_FISH_SAMPLE_RATE, nullable=False)

    # Скорость речи Fish (prosody.speed, 0.5–2.0). 1.0 — обычный темп.
    voice_speed = Column(Float, default=1.0, nullable=True)

    # LLM settings (OpenAI Realtime на серверном ключе)
    llm_model = Column(String(100), default=DEFAULT_FISH_LLM_MODEL, nullable=False)
    language = Column(String(10), default="ru", nullable=False)

    # Живость интонации Fish (их temperature, 0–1). К модели OpenAI отношения
    # не имеет: диалогом правит system_prompt, а это про манеру речи.
    temperature = Column(Float, default=0.7, nullable=True)

    # Greeting and logging
    greeting_message = Column(
        String(500),
        nullable=True,
        default="Здравствуйте! Чем я могу вам помочь?",
    )
    google_sheet_id = Column(String(255), nullable=True)

    # Functions configuration (JSON)
    functions = Column(JSON, nullable=True)

    # Status
    is_active = Column(Boolean, default=True, nullable=False)

    # Timestamps
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    user = relationship("User", back_populates="fish_assistants")
    conversations = relationship(
        "FishConversation", back_populates="assistant", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<FishAssistantConfig(id={self.id}, name='{self.name}', voice='{self.fish_voice_id}')>"

    def get_fish_start_request(self, sample_rate: int = None):
        """
        Тело StartEvent для wss://api.fish.audio/v1/tts/live.

        Клиент Fish шлёт его первым сообщением после подключения; format
        всегда pcm. sample_rate — частота, которую ждёт получатель аудио
        (хендлер передаёт 24000); без аргумента берётся колонка.
        """
        def clamp(value, low, high, default):
            if value is None:
                return default
            return max(low, min(high, float(value)))

        request = {
            "text": "",
            "format": "pcm",
            "sample_rate": sample_rate or self.sample_rate or DEFAULT_FISH_SAMPLE_RATE,
            "latency": self.fish_latency or DEFAULT_FISH_LATENCY,
            # Живость интонации. Ограничиваем на всякий случай: у старых
            # записей temperature могла быть до 2 (когда поле трактовалось
            # как параметр LLM), а Fish принимает только 0–1.
            "temperature": clamp(
                self.temperature, FISH_TEMPERATURE_MIN, FISH_TEMPERATURE_MAX, 0.7
            ),
            "prosody": {
                "speed": clamp(
                    self.voice_speed, FISH_SPEED_MIN, FISH_SPEED_MAX, 1.0
                ),
            },
        }
        if self.fish_voice_id:
            request["reference_id"] = self.fish_voice_id
        return request

    def to_dict(self):
        """Convert to dictionary for API response."""
        return {
            "id": str(self.id),
            "user_id": str(self.user_id) if self.user_id else None,
            "name": self.name,
            "description": self.description,
            "system_prompt": self.system_prompt,
            "fish_voice_id": self.fish_voice_id,
            "fish_model": self.fish_model,
            "fish_latency": self.fish_latency,
            "sample_rate": self.sample_rate,
            "voice_speed": self.voice_speed,
            "llm_model": self.llm_model,
            "language": self.language,
            "temperature": self.temperature,
            "greeting_message": self.greeting_message,
            "google_sheet_id": self.google_sheet_id,
            "functions": self.functions,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class FishConversation(Base):
    """
    Журнал диалогов Fish-ассистентов (таблица fish_conversations).

    Отдельная таблица по той же причине, что и gemini_conversations:
    conversations.assistant_id ссылается на assistant_configs (OpenAI), и
    запись Fish-диалога туда падает по внешнему ключу. Набор колонок совпадает
    с gemini_conversations плюс call_direction — страница «Диалоги» объединяет
    все три таблицы через UNION ALL, а SIP-сервис проставляет номер и
    направление после звонка (tag_conversations).
    """
    __tablename__ = "fish_conversations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assistant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("fish_assistant_configs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    session_id = Column(String, nullable=False, index=True)
    user_message = Column(Text, nullable=True)
    assistant_message = Column(Text, nullable=True)
    caller_number = Column(String, nullable=True)   # номер абонента (SIP-звонок)
    call_direction = Column(String(20), nullable=True)  # INBOUND / OUTBOUND
    tokens_used = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)

    assistant = relationship("FishAssistantConfig", back_populates="conversations")

    def __repr__(self):
        return f"<FishConversation(id={self.id}, assistant_id={self.assistant_id}, session_id='{self.session_id}')>"
