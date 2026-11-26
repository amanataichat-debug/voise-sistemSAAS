"""
Чат на сайте (Voksy Beauty, шаг 1.6в): новый текстовый канал агента рядом с WhatsApp,
Telegram и Instagram.

  • WebChatSession — один посетитель страницы салона (или сайта с виджетом): токен в его
    браузере (храним только sha256), карточка CRM, имя и телефон, если оставил, и метки
    «когда последний раз передали разбору» (как last_dispatched_at у чатов WhatsApp).
  • AgentWebChatMessage — сообщение переписки (обе стороны). Колонки — как у
    agent_instagram_messages, поэтому инбокс, хронология оркестратора и подтверждение записи
    читают его тем же кодом. sent_by: 'owner' — владелец/сотрудник из инбокса,
    'auto' — служебный ответ платформы (бот не смог ответить), NULL — клиент или бот.

Посетитель без телефона получает карточку с синтетическим номером "web:<hex>"; оставил номер —
карточка получает его, а если такой клиент уже есть — склеивается с ним (agent_contact_merge).
Таблицы создаёт app.ensure_booking_tables() (create_all, checkfirst).
"""

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID

from .base import Base


class WebChatSession(Base):
    __tablename__ = "webchat_sessions"
    __table_args__ = (
        Index("ix_webchat_sessions_user", "user_id", "last_seen_at"),
        Index("ix_webchat_sessions_contact", "agent_contact_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    agent_config_id = Column(UUID(as_uuid=True), ForeignKey("agent_configs.id", ondelete="CASCADE"), nullable=False)
    agent_contact_id = Column(UUID(as_uuid=True), ForeignKey("agent_contacts.id", ondelete="SET NULL"), nullable=True)
    token_hash = Column(String(64), nullable=False, unique=True)
    page_slug = Column(String(80), nullable=True)
    origin = Column(String(16), nullable=False, default="salon")   # salon | site
    name = Column(String(120), nullable=True)
    phone = Column(String(40), nullable=True)
    ip = Column(String(64), nullable=True)
    user_agent = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    # Последнее входящее, отданное разбору (created_at этого сообщения): новые — после него
    last_dispatched_at = Column(DateTime, nullable=True)
    # Посетитель видел ответы до этого момента (непрочитанное в виджете)
    visitor_read_at = Column(DateTime, nullable=True)


class AgentWebChatMessage(Base):
    __tablename__ = "agent_webchat_messages"
    __table_args__ = (
        Index("ix_agent_webchat_messages_contact", "agent_contact_id", "created_at"),
        Index("ix_agent_webchat_messages_session", "session_id", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_config_id = Column(UUID(as_uuid=True), ForeignKey("agent_configs.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    agent_contact_id = Column(UUID(as_uuid=True), ForeignKey("agent_contacts.id", ondelete="CASCADE"), nullable=True)
    session_id = Column(UUID(as_uuid=True), ForeignKey("webchat_sessions.id", ondelete="CASCADE"), nullable=True)
    direction = Column(String(10), default="inbound", nullable=False)
    sent_by = Column(String(16), nullable=True)
    body = Column(Text, nullable=False)
    attachment_id = Column(UUID(as_uuid=True), nullable=True)
    sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self) -> dict:
        ts = self.sent_at or self.created_at
        return {
            "id": str(self.id),
            "direction": self.direction or "inbound",
            "sent_by": self.sent_by,
            "body": self.body,
            "attachment_id": str(self.attachment_id) if self.attachment_id else None,
            "ts": ts.isoformat() if ts else None,
        }
