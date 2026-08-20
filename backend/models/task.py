# backend/models/task.py
"""
Task model для системы задач и автоматических звонков.
Привязывается к контакту (Contact) и выполняется через Scheduler.
✅ ВЕРСИЯ 2.1: Добавлено поле custom_greeting для персонализированных приветствий
✅ ВЕРСИЯ 2.0: Поддержка OpenAI + Gemini ассистентов
"""

from sqlalchemy import Column, String, Integer, Boolean, DateTime, Text, ForeignKey, Enum, Index, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
import enum

from .base import Base


class TaskStatus(str, enum.Enum):
    """Статусы выполнения задачи"""
    SCHEDULED = "scheduled"   # Запланирована (дефолт)
    PENDING = "pending"       # Ожидает выполнения
    CALLING = "calling"       # Звоним прямо сейчас
    COMPLETED = "completed"   # Выполнена успешно
    FAILED = "failed"         # Ошибка при звонке
    CANCELLED = "cancelled"   # Отменена пользователем


class Task(Base):
    """
    Задача с автоматическим звонком в указанное время.
    ✅ Поддерживает OpenAI и Gemini ассистентов
    ✅ v2.1: Персонализированные приветствия через custom_greeting
    
    Основной флоу:
    1. Создается вручную в UI или агентом во время звонка
    2. Scheduler проверяет каждую минуту
    3. Когда scheduled_time наступает → запускает звонок
    4. Обновляет статус и сохраняет результат
    """
    __tablename__ = "tasks"
    
    # ==================== Основные поля ====================
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # ==================== Связи (Foreign Keys) ====================
    # Контакт - КОМУ звонить (для обычных задач; nullable для agent tasks)
    contact_id = Column(
        UUID(as_uuid=True),
        ForeignKey("contacts.id", ondelete="CASCADE"),
        nullable=True,
        index=True
    )
    
    # ✅ НОВОЕ: Два типа ассистентов (один из них должен быть заполнен)
    # OpenAI ассистент - КТО будет звонить (nullable)
    assistant_id = Column(
        UUID(as_uuid=True), 
        ForeignKey("assistant_configs.id", ondelete="SET NULL"),
        nullable=True,  # ✅ Теперь nullable
        index=True
    )
    
    # Gemini ассистент - КТО будет звонить (nullable)
    gemini_assistant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("gemini_assistant_configs.id", ondelete="SET NULL"),
        nullable=True,  # ✅ Новое поле
        index=True
    )

    # Cartesia ассистент - КТО будет звонить (nullable)
    cartesia_assistant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("cartesia_assistant_configs.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    # Yandex ассистент - КТО будет звонить (nullable)
    yandex_assistant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("yandex_assistant_configs.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    # Cascade ассистент (grok_assistant_configs, assistant_type='cascade') - nullable
    cascade_assistant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("grok_assistant_configs.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    # Fish ассистент (OpenAI Realtime + Fish Audio TTS) - nullable
    fish_assistant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("fish_assistant_configs.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    # Пользователь - владелец задачи (обязательно)
    user_id = Column(
        UUID(as_uuid=True), 
        ForeignKey("users.id"), 
        nullable=False, 
        index=True
    )
    
    # ==================== Данные задачи ====================
    # Статус выполнения
    status = Column(
        Enum(TaskStatus, name='taskstatus'), 
        default=TaskStatus.SCHEDULED, 
        nullable=False,
        index=True
    )
    
    # Когда нужно позвонить (UTC)
    scheduled_time = Column(DateTime(timezone=True), nullable=False, index=True)
    
    # Название задачи (видит пользователь в UI)
    title = Column(String(255), nullable=False)
    
    # Дополнительное описание (опционально)
    description = Column(Text, nullable=True)
    
    # ✅ НОВОЕ v2.1: Персонализированное приветствие для задачи
    # Если указано - используется вместо config.hello из настроек ассистента
    custom_greeting = Column(Text, nullable=True)

    # ✅ НОВОЕ v2.2: Номер телефона для исходящего звонка (Caller ID)
    # Если указан — звонок идёт с этого номера
    # Если NULL — автоматически выбирается первый активный номер пользователя
    caller_id = Column(String(20), nullable=True)

    # Agent contact/call links (for agent tasks)
    agent_contact_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_contacts.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )
    agent_call_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_calls.id", ondelete="SET NULL"),
        nullable=True
    )
    is_agent_task = Column(Boolean, default=False, nullable=False)

    # Канал исполнения агентской задачи:
    #   "call"     — исходящий звонок (дефолт, весь legacy-поток)
    #   "telegram" — отложенное сообщение с личного Telegram-аккаунта агента;
    #                инструкция «что написать» хранится в description, сам текст
    #                составляет оркестратор в момент отправки
    channel = Column(String(20), default="call", nullable=False)

    # Agent orchestrator fields
    pre_call_response_id = Column(String(255), nullable=True)
    post_call_decision = Column(String(50), nullable=True)
    retry_count = Column(Integer, default=0, nullable=False)
    
    # ==================== Результат выполнения ====================
    # ID звонка в Voximplant (заполняется после звонка)
    call_session_id = Column(String(255), nullable=True)
    
    # Когда начался звонок
    call_started_at = Column(DateTime(timezone=True), nullable=True)
    
    # Когда закончился звонок
    call_completed_at = Column(DateTime(timezone=True), nullable=True)
    
    # Результат звонка (JSON: success, error, duration и т.д.)
    call_result = Column(Text, nullable=True)
    
    # ==================== Timestamps ====================
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime(timezone=True), 
        default=datetime.utcnow, 
        onupdate=datetime.utcnow, 
        nullable=False
    )
    
    # ==================== Relationships ====================
    contact = relationship("Contact", back_populates="tasks")
    assistant = relationship("AssistantConfig", foreign_keys=[assistant_id])
    gemini_assistant = relationship("GeminiAssistantConfig", foreign_keys=[gemini_assistant_id])
    cartesia_assistant = relationship("CartesiaAssistantConfig", foreign_keys=[cartesia_assistant_id])
    yandex_assistant = relationship("YandexAssistantConfig", foreign_keys=[yandex_assistant_id])
    cascade_assistant = relationship("GrokAssistantConfig", foreign_keys=[cascade_assistant_id])
    fish_assistant = relationship("FishAssistantConfig", foreign_keys=[fish_assistant_id])
    user = relationship("User")
    
    # ==================== Constraints & Indexes ====================
    __table_args__ = (
        # Составной индекс для поиска задач к выполнению
        Index('ix_tasks_scheduled_status', 'scheduled_time', 'status'),
        # Индекс для быстрого поиска по контакту
        Index('ix_tasks_contact_scheduled', 'contact_id', 'scheduled_time'),
        # Индекс для поиска по пользователю
        Index('ix_tasks_user_scheduled', 'user_id', 'scheduled_time'),
        # Индекс для agent tasks
        Index('idx_tasks_agent_contact', 'agent_contact_id'),
        Index('idx_tasks_is_agent', 'is_agent_task'),
    )
    
    def __repr__(self):
        assistant_type = self.get_assistant_type().capitalize()
        greeting_info = " (custom greeting)" if self.custom_greeting else ""
        return f"<Task {self.title} at {self.scheduled_time} ({assistant_type}, status={self.status.value}){greeting_info}>"

    def get_assistant_type(self) -> str:
        """Определить тип ассистента"""
        if self.assistant_id:
            return "openai"
        elif self.gemini_assistant_id:
            return "gemini"
        elif self.yandex_assistant_id:
            return "yandex"
        elif self.cascade_assistant_id:
            return "cascade"
        elif self.fish_assistant_id:
            return "fish"
        else:
            return "cartesia"

    def get_assistant_id(self) -> str:
        """Получить ID ассистента (универсально)"""
        if self.assistant_id:
            return str(self.assistant_id)
        elif self.gemini_assistant_id:
            return str(self.gemini_assistant_id)
        elif self.yandex_assistant_id:
            return str(self.yandex_assistant_id)
        elif self.cascade_assistant_id:
            return str(self.cascade_assistant_id)
        elif self.fish_assistant_id:
            return str(self.fish_assistant_id)
        else:
            return str(self.cartesia_assistant_id)
    
    def to_dict(self):
        """Сериализация для API"""
        return {
            "id": str(self.id),
            "contact_id": str(self.contact_id),
            "assistant_id": str(self.assistant_id) if self.assistant_id else None,
            "gemini_assistant_id": str(self.gemini_assistant_id) if self.gemini_assistant_id else None,
            "cartesia_assistant_id": str(self.cartesia_assistant_id) if self.cartesia_assistant_id else None,
            "yandex_assistant_id": str(self.yandex_assistant_id) if self.yandex_assistant_id else None,
            "cascade_assistant_id": str(self.cascade_assistant_id) if self.cascade_assistant_id else None,
            "fish_assistant_id": str(self.fish_assistant_id) if self.fish_assistant_id else None,
            "assistant_type": self.get_assistant_type(),  # ✅ Новое поле
            "user_id": str(self.user_id),
            "status": self.status.value,
            "scheduled_time": self.scheduled_time.isoformat() if self.scheduled_time else None,
            "title": self.title,
            "description": self.description,
            "custom_greeting": self.custom_greeting,  # ✅ НОВОЕ v2.1
            "caller_id": self.caller_id,  # ✅ НОВОЕ v2.2
            "agent_contact_id": str(self.agent_contact_id) if self.agent_contact_id else None,
            "agent_call_id": str(self.agent_call_id) if self.agent_call_id else None,
            "is_agent_task": self.is_agent_task or False,
            "channel": self.channel or "call",
            "call_session_id": self.call_session_id,
            "call_started_at": self.call_started_at.isoformat() if self.call_started_at else None,
            "call_completed_at": self.call_completed_at.isoformat() if self.call_completed_at else None,
            "call_result": self.call_result,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }
    
    def is_overdue(self) -> bool:
        """Проверка: задача просрочена?"""
        return (
            self.scheduled_time < datetime.utcnow() and 
            self.status in [TaskStatus.SCHEDULED, TaskStatus.PENDING]
        )
    
    def can_be_cancelled(self) -> bool:
        """Можно ли отменить задачу?"""
        return self.status in [TaskStatus.SCHEDULED, TaskStatus.PENDING]
    
    def has_custom_greeting(self) -> bool:
        """Проверка: есть ли персонализированное приветствие?"""
        return bool(self.custom_greeting and self.custom_greeting.strip())
