"""
🤖 Browser Agent Task Model
Модель для хранения задач браузерного агента.

Задача проходит следующие состояния:
PENDING → RUNNING → WAITING_DOM → RUNNING → ... → COMPLETED/FAILED
"""

from sqlalchemy import Column, String, Integer, DateTime, Text, JSON, Enum as SQLEnum, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import enum
import uuid

from .base import Base


class BrowserTaskStatus(str, enum.Enum):
    """Статусы задачи браузерного агента"""
    PENDING = "pending"           # Создана, ожидает выполнения
    RUNNING = "running"           # Выполняется
    WAITING_DOM = "waiting_dom"   # Ждёт ответ от виджета (DOM/результат)
    COMPLETED = "completed"       # Успешно завершена
    FAILED = "failed"             # Ошибка
    CANCELLED = "cancelled"       # Отменена пользователем


class BrowserTask(Base):
    """
    Задача для Browser Agent.
    
    Пример использования:
    - Пользователь говорит: "Найди iPhone 15 и добавь в корзину"
    - Voice Agent создаёт BrowserTask с goal="Найди iPhone 15 и добавь в корзину"
    - Browser Agent выполняет задачу пошагово
    - Результат сообщается пользователю голосом
    """
    __tablename__ = "browser_tasks"
    
    # Идентификаторы
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), nullable=False, index=True)
    assistant_id = Column(String(36), nullable=False, index=True)
    session_id = Column(String(36), nullable=False, index=True)  # Связь с WebSocket сессией
    
    # Задача
    goal = Column(Text, nullable=False)  # "Найди iPhone 15 и добавь в корзину"
    status = Column(SQLEnum(BrowserTaskStatus), default=BrowserTaskStatus.PENDING, index=True)
    
    # Контекст страницы
    initial_url = Column(String(2048))   # URL при создании задачи
    current_url = Column(String(2048))   # Текущий URL
    page_title = Column(String(512))     # Заголовок страницы
    
    # План и прогресс (JSON)
    plan = Column(JSON, default=list)           # Список запланированных шагов
    current_step = Column(Integer, default=0)   # Текущий шаг
    history = Column(JSON, default=list)        # История выполненных действий
    
    # Последний DOM snapshot (для отладки)
    last_dom_snapshot = Column(JSON, default=dict)
    
    # Результат
    result = Column(Text)           # Успешный результат
    error = Column(Text)            # Сообщение об ошибке
    
    # Метрики
    iterations = Column(Integer, default=0)     # Количество итераций
    total_actions = Column(Integer, default=0)  # Всего действий выполнено
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    started_at = Column(DateTime(timezone=True))   # Когда начали выполнять
    completed_at = Column(DateTime(timezone=True)) # Когда завершили
    
    def __repr__(self):
        return f"<BrowserTask(id={self.id[:8]}, status={self.status}, goal='{self.goal[:30]}...')>"
    
    def to_dict(self):
        """Конвертация в словарь для API"""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "assistant_id": self.assistant_id,
            "session_id": self.session_id,
            "goal": self.goal,
            "status": self.status.value if self.status else None,
            "initial_url": self.initial_url,
            "current_url": self.current_url,
            "page_title": self.page_title,
            "plan": self.plan,
            "current_step": self.current_step,
            "history": self.history,
            "result": self.result,
            "error": self.error,
            "iterations": self.iterations,
            "total_actions": self.total_actions,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }
    
    def add_to_history(self, action: dict, result: dict):
        """Добавить запись в историю"""
        import time
        
        entry = {
            "step": self.current_step,
            "action": action,
            "result": result,
            "timestamp": time.time()
        }
        
        if self.history is None:
            self.history = []
        
        # SQLAlchemy JSON trick - нужно присвоить новый список
        self.history = self.history + [entry]
        self.total_actions = (self.total_actions or 0) + 1
