# backend/models/contact.py
"""
Contact model для CRM функциональности.
Хранит информацию о контактах (клиентах) с их телефонами и именами.
✅ ОБНОВЛЕНО: Добавлена модель ContactNote для ленты заметок
✅ ОБНОВЛЕНО: Добавлен relationship для Task (задачи с автозвонками)
"""

from sqlalchemy import Column, String, DateTime, Text, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from .base import Base


class Contact(Base):
    """
    Контакт (клиент) в CRM системе.
    Автоматически создается при первом звонке с нового номера.
    Связан с диалогами через Conversation.contact_id
    """
    __tablename__ = "contacts"
    
    # Основные поля
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    
    # Информация о контакте
    phone = Column(String(50), nullable=False, index=True)  # +79123456789
    name = Column(String(255), nullable=True)  # "Иван Петров" - задается вручную
    
    # Статус контакта
    status = Column(
        String(50), 
        default="new",
        nullable=False
    )  # new, active, client, archived
    
    # Заметки (оставлено для обратной совместимости)
    notes = Column(Text, nullable=True)

    # Долгосрочная память агента по контакту
    agent_memory = Column(JSONB, default=dict, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    last_interaction = Column(DateTime, nullable=True)  # дата последнего диалога (обновляется автоматически)
    
    # Relationships
    user = relationship("User", backref="contacts")
    conversations = relationship("Conversation", back_populates="contact", cascade="all, delete-orphan")
    contact_notes = relationship("ContactNote", back_populates="contact", cascade="all, delete-orphan", order_by="desc(ContactNote.created_at)")
    # ✅ НОВОЕ: Задачи с автозвонками
    tasks = relationship("Task", back_populates="contact", cascade="all, delete-orphan", order_by="desc(Task.scheduled_time)")
    
    # Уникальность: один номер телефона у одного пользователя
    __table_args__ = (
        UniqueConstraint('user_id', 'phone', name='uq_user_phone'),
        Index('ix_contacts_user_phone', 'user_id', 'phone'),
        Index('ix_contacts_status', 'status'),
        Index('ix_contacts_last_interaction', 'last_interaction'),
    )
    
    def __repr__(self):
        return f"<Contact {self.name or self.phone} (user_id={self.user_id})>"
    
    def to_dict(self):
        """Сериализация для API"""
        return {
            "id": str(self.id),
            "phone": self.phone,
            "name": self.name,
            "status": self.status,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "last_interaction": self.last_interaction.isoformat() if self.last_interaction else None
        }


class ContactNote(Base):
    """
    Отдельная заметка по контакту.
    Позволяет создавать ленту заметок с историей (как в AmoCRM).
    Каждая заметка - отдельная запись с датой создания.
    """
    __tablename__ = "contact_notes"
    
    # Основные поля
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    contact_id = Column(UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    
    # Текст заметки
    note_text = Column(Text, nullable=False)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    contact = relationship("Contact", back_populates="contact_notes")
    user = relationship("User")
    
    # Индексы
    __table_args__ = (
        Index('ix_contact_notes_contact_created', 'contact_id', 'created_at'),
    )
    
    def __repr__(self):
        return f"<ContactNote {self.id} for contact {self.contact_id}>"
    
    def to_dict(self):
        """Сериализация для API"""
        return {
            "id": str(self.id),
            "contact_id": str(self.contact_id),
            "note_text": self.note_text,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }
