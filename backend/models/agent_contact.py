"""
AgentContact — контакт в базе автономного агента Voksy AI.
Отдельная таблица от CRM Contact. Управляется агентом через tools.
"""

import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, Text, ForeignKey, Index, Numeric
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

    # Дополнительные номера клиента (ветка 2110-client-memory): второй номер,
    # номер для звонков (WhatsApp на Beeline, звонить — на O!), номер склеенной
    # карточки. Канонические цифры через запятую с запятыми по краям
    # (",996700111222,996555333444,") — поиск по последним 9 цифрам одним LIKE.
    # call_phone — на какой номер звонить (NULL — основной, а если он не O!,
    # первый дополнительный на O!). Работа с ними — services/agent_contact_phones.py.
    # Колонки создаёт app.ensure_chat_v2_columns() в начале старта.
    alt_phones = Column(Text, nullable=True)
    call_phone = Column(String(50), nullable=True)

    # ── Поля CRM v2 (ветка 2510-crm-v2) ─────────────────────────────────
    # Теги: произвольные метки (VIP, Hot, по продукту). Хранятся как
    # comma-separated TEXT: "vip,повтор,розница". Разбирать через split(',').
    tags = Column(Text, nullable=True)
    # Сделка: сумма в сомах (сомов), следующий шаг и когда, причина потери.
    # Используется для аналитики выручки, фильтров воронки и расчёта среднего
    # чека. Колонки создаёт app.ensure_crm_v2_columns() в начале старта.
    deal_amount = Column(Numeric(14, 2), nullable=True)       # KGS
    deal_currency = Column(String(8), nullable=True, default="KGS")
    next_step_text = Column(Text, nullable=True)
    next_step_at = Column(DateTime, nullable=True)
    lost_reason = Column(Text, nullable=True)

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

    def to_dict(self):
        return {
            "id": str(self.id),
            "agent_config_id": str(self.agent_config_id) if self.agent_config_id else None,
            "user_id": str(self.user_id) if self.user_id else None,
            "name": self.name,
            "phone": self.phone,
            "alt_phones": ["+" + p for p in (self.alt_phones or "").split(",") if p],
            "call_phone": ("+" + self.call_phone) if self.call_phone else None,
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
            # CRM v2 поля: теги + сделка
            "tags": [t.strip() for t in (self.tags or "").split(",") if t.strip()],
            "deal_amount": float(self.deal_amount) if self.deal_amount is not None else None,
            "deal_currency": self.deal_currency or "KGS",
            "next_step_text": self.next_step_text,
            "next_step_at": iso_utc(self.next_step_at),
            "lost_reason": self.lost_reason,
            "created_at": iso_utc(self.created_at),
            "updated_at": iso_utc(self.updated_at),
        }
