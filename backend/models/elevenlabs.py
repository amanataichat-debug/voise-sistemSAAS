"""
Упрощенная модель ElevenLabs - минимальное хранение
СОХРАНЯЕМ существующую структуру БД, но используем только нужные поля
"""

import uuid
from sqlalchemy import Column, String, Boolean, ForeignKey, DateTime, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from backend.models.base import Base

class ElevenLabsAgent(Base):
    """
    ✅ ОПТИМИЗИРОВАННАЯ модель ElevenLabs агента
    Сохраняем структуру таблицы, но логически используем минимум полей
    """
    __tablename__ = "elevenlabs_agents"
    
    # ✅ ОСНОВНЫЕ поля - используем активно
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    elevenlabs_agent_id = Column(String, nullable=True)  # ✅ ГЛАВНОЕ: ID в ElevenLabs
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
    
    # ✅ УСТАРЕВШИЕ поля - сохраняем для совместимости, но НЕ используем
    # Данные получаем из ElevenLabs API, эти поля могут быть пустыми/устаревшими
    name = Column(String, nullable=True)              # ❌ НЕ используем - получаем из API
    system_prompt = Column(Text, nullable=True)       # ❌ НЕ используем - получаем из API
    voice_id = Column(String, nullable=True)          # ❌ НЕ используем - получаем из API
    voice_name = Column(String, nullable=True)        # ❌ НЕ используем - получаем из API
    is_active = Column(Boolean, default=True, nullable=False)  # ❌ НЕ используем - получаем из API
    
    # Relationship with User (сохраняем)
    user = relationship("User", back_populates="elevenlabs_agents")
    
    def __repr__(self):
        return f"<ElevenLabsAgent(id={self.id}, elevenlabs_agent_id={self.elevenlabs_agent_id})>"
    
    @property
    def is_synced_with_elevenlabs(self):
        """Проверить, синхронизирован ли агент с ElevenLabs"""
        return bool(self.elevenlabs_agent_id)
    
    def to_dict_minimal(self):
        """
        ✅ Возвращает только базовую информацию
        Остальные данные должны быть получены из ElevenLabs API
        """
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "elevenlabs_agent_id": self.elevenlabs_agent_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "is_synced": self.is_synced_with_elevenlabs
        }

class ElevenLabsConversation(Base):
    """
    ✅ УПРОЩЕННАЯ модель разговоров
    Тоже минимальное хранение - основную информацию получаем из ElevenLabs
    """
    __tablename__ = "elevenlabs_conversations"
    
    # ✅ ОСНОВНЫЕ поля
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id = Column(UUID(as_uuid=True), ForeignKey("elevenlabs_agents.id", ondelete="CASCADE"), nullable=False)
    elevenlabs_conversation_id = Column(String, nullable=True)  # ✅ ID в ElevenLabs
    created_at = Column(DateTime, default=func.now(), nullable=False)
    
    # ✅ ОПЦИОНАЛЬНЫЕ поля - для локального кэширования (необязательны)
    user_message = Column(Text, nullable=True)        # Можем кэшировать последнее сообщение
    agent_response = Column(Text, nullable=True)      # Можем кэшировать последний ответ
    
    # Relationship with ElevenLabsAgent
    agent = relationship("ElevenLabsAgent")
    
    def __repr__(self):
        return f"<ElevenLabsConversation(id={self.id}, elevenlabs_conversation_id={self.elevenlabs_conversation_id})>"

# =============== ПРИМЕР ИСПОЛЬЗОВАНИЯ ===============

"""
✅ ПРАВИЛЬНОЕ использование модели:

# 1. Создание агента - сохраняем только связь
agent = ElevenLabsAgent(
    user_id=user_id,
    elevenlabs_agent_id="elvenlabs_agent_123"  # Только это важно!
    # Остальные поля НЕ заполняем - получаем через API
)

# 2. Получение данных агента
agent_config = await get_agent_from_elevenlabs_api(
    api_key, 
    agent.elevenlabs_agent_id
)

# 3. Отображение пользователю
agent_display = {
    "id": agent.id,
    "name": agent_config["name"],                    # ✅ Из ElevenLabs API
    "system_prompt": agent_config["prompt"],        # ✅ Из ElevenLabs API  
    "voice_id": agent_config["voice_id"],           # ✅ Из ElevenLabs API
    "created_at": agent.created_at                  # ✅ Из локальной БД
}

❌ НЕПРАВИЛЬНО:
agent_display = {
    "name": agent.name,           # Может быть устаревшим!
    "voice_id": agent.voice_id    # Может быть устаревшим!
}
"""

# =============== HELPER FUNCTIONS ===============

def get_minimal_agent_data(agent: ElevenLabsAgent) -> dict:
    """
    ✅ Получить минимальные данные агента (без обращения к API)
    Используется для списков, где не нужны детали
    """
    return {
        "id": str(agent.id),
        "elevenlabs_agent_id": agent.elevenlabs_agent_id,
        "created_at": agent.created_at,
        "updated_at": agent.updated_at,
        "is_synced": bool(agent.elevenlabs_agent_id),
        "user_id": str(agent.user_id)
    }

async def get_full_agent_data(agent: ElevenLabsAgent, api_key: str) -> dict:
    """
    ✅ Получить полные данные агента (с обращением к ElevenLabs API)
    Используется для детального просмотра и редактирования
    """
    if not agent.elevenlabs_agent_id:
        raise ValueError("Agent not synced with ElevenLabs")
    
    # Получаем актуальные данные из ElevenLabs
    config = await get_agent_config_from_elevenlabs(api_key, agent.elevenlabs_agent_id)
    
    return {
        # Локальные данные
        "id": str(agent.id),
        "user_id": str(agent.user_id),
        "elevenlabs_agent_id": agent.elevenlabs_agent_id,
        "created_at": agent.created_at,
        "updated_at": agent.updated_at,
        
        # Актуальные данные из ElevenLabs API
        "name": config.get("name", "Unnamed Agent"),
        "system_prompt": config.get("conversation_config", {}).get("agent", {}).get("prompt", {}).get("prompt", ""),
        "voice_id": config.get("conversation_config", {}).get("tts", {}).get("voice_id", ""),
        "is_active": True  # Если агент существует в ElevenLabs, он активен
    }
