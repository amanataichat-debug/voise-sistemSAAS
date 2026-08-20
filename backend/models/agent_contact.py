"""
AgentContact — контакт в базе автономного агента Voicyfy.
Отдельная таблица от CRM Contact. Управляется агентом через tools.
"""

import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, Text, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from .base import Base
from backend.core.timezone_utils import iso_utc


class AgentContact(Base):
    """
    Контакт автономного агента.
    Статусы: new / calling / active / success / rejected / do_not_call
    memory — JSONB с историей взаимодействия:
      {summary, attempts, best_time, tone_history, key_facts, last_call}
    """
    __tablename__ = "agent_contacts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_config_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_configs.id", ondelete="CASCADE"),
        nullable=True,
        index=True
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True
    )

    name = Column(String(255), nullable=True)
    phone = Column(String(50), nullable=False)
    company = Column(String(255), nullable=True)
    position = Column(String(255), nullable=True)
    notes = Column(Text, nullable=True)

    status = Column(String(50), default="new", nullable=False)
    memory = Column(JSONB, default=dict, nullable=False)

    attempts_count = Column(Integer, default=0, nullable=False)
    last_called_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    agent_config = relationship("AgentConfig", foreign_keys=[agent_config_id])
    calls = relationship("AgentCall", back_populates="contact", cascade="all, delete-orphan",
                         order_by="desc(AgentCall.created_at)")
    tasks = relationship("Task", foreign_keys="Task.agent_contact_id",
                         order_by="desc(Task.scheduled_time)")

    __table_args__ = (
        Index("idx_agent_contacts_status", "status"),
        Index("idx_agent_contacts_user", "user_id"),
        Index("idx_agent_contacts_config", "agent_config_id"),
    )

    def __repr__(self):
        return f"<AgentContact {self.name or self.phone} status={self.status}>"

    def to_dict(self):
        return {
            "id": str(self.id),
            "agent_config_id": str(self.agent_config_id) if self.agent_config_id else None,
            "user_id": str(self.user_id) if self.user_id else None,
            "name": self.name,
            "phone": self.phone,
            "company": self.company,
            "position": self.position,
            "notes": self.notes,
            "status": self.status,
            "memory": self.memory or {},
            "attempts_count": self.attempts_count or 0,
            # ✅ Колонки naive (UTC по факту) — сериализуем с явным UTC-маркером,
            #    чтобы фронт корректно конвертировал в МСК.
            "last_called_at": iso_utc(self.last_called_at),
            "created_at": iso_utc(self.created_at),
            "updated_at": iso_utc(self.updated_at),
        }
