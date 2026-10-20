"""
AgentContact — контакт в базе автономного агента Voksy AI.
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

    # Пауза агента в переписке с этим клиентом (во всех мессенджерах сразу):
    # NULL — агент отвечает; дата в будущем — молчит до неё; AI_PAUSE_FOREVER —
    # навсегда. Причина: owner_message (владелец написал сам) | manual (кнопка
    # в карточке) | command (команда владельца в чате агента). Звонков не касается.
    # Колонки создаёт app.ensure_chat_v2_columns() в начале старта.
    ai_paused_until = Column(DateTime, nullable=True)
    ai_pause_reason = Column(String(16), nullable=True)

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

    def is_ai_paused(self, now=None) -> bool:
        """Агент молчит в переписке с клиентом (пауза ещё не истекла)."""
        until = self.ai_paused_until
        return until is not None and until > (now or datetime.utcnow())

    def call_phone(self) -> str:
        """Номер, на который агент ЗВОНИТ.

        Клиент часто пишет с WhatsApp на другом операторе (Мегаком), а звонить мы
        можем только на О!. Номер О!, который он дал в переписке, хранится в
        memory["call_phone"] (тулзы trigger_immediate_call / create_agent_task,
        параметр phone) — основной phone карточки при этом не трогаем, чтобы не
        сломать привязку переписки. Нет отдельного номера — звоним на phone.
        """
        mem = self.memory if isinstance(self.memory, dict) else {}
        return (mem.get("call_phone") or self.phone or "").strip()

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
            #    чтобы фронт корректно конвертировал в Бишкек.
            "last_called_at": iso_utc(self.last_called_at),
            "ai_paused_until": iso_utc(self.ai_paused_until) if self.is_ai_paused() else None,
            "ai_pause_reason": self.ai_pause_reason if self.is_ai_paused() else None,
            "ai_paused_forever": self.is_ai_paused() and self.ai_paused_until.year >= 9999,
            "created_at": iso_utc(self.created_at),
            "updated_at": iso_utc(self.updated_at),
        }
