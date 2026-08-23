"""
Instagram DM агента — диалоги и переписка (через коннектор Composio INSTAGRAM).

Зеркалит пару AgentTelegramDialog / AgentTelegramMessage личного Telegram:
  • AgentInstagramConversation — один DM-тред Instagram (по conversation_id из
    Graph API), привязка к контакту агента и watermark обработанных сообщений;
  • AgentInstagramMessage — сообщение переписки (обе стороны), источник треда
    в карточке контакта и хронологии оркестратора (build_conversation_timeline).

Идентичность собеседника — IGSID (Instagram-Scoped ID, числовая строка) +
username; телефона Instagram не даёт, поэтому контакт, созданный из входящего
DM, получает синтетический phone "ig:{igsid}" (как "tg:{peer_id}" у Telegram).

Таблицы создаются стартовым create_all / ensure-columns (см. app.py).
"""

import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from .base import Base


class AgentInstagramConversation(Base):
    """DM-тред Instagram, известный поллеру (агент ↔ один собеседник)."""
    __tablename__ = "agent_instagram_conversations"
    __table_args__ = (
        UniqueConstraint("agent_config_id", "ig_conversation_id",
                         name="uq_agent_ig_conversation"),
        Index("ix_agent_ig_conv_contact", "agent_contact_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_config_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_configs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # SET NULL: удаление контакта не должно терять сам тред (переподвяжется).
    agent_contact_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_contacts.id", ondelete="SET NULL"),
        nullable=True,
    )

    # id треда из Graph API (base64-строка вида aWdfZAG06...)
    ig_conversation_id = Column(String(255), nullable=False)
    # Собеседник: IGSID (числовая строка) и username (если известен)
    igsid = Column(String(64), nullable=True)
    ig_username = Column(String(255), nullable=True)

    # Watermark: created_time последнего ОБРАБОТАННОГО сообщения (naive UTC).
    # Сообщения новее него — «новые». Продвигается ДО запуска оркестратора
    # (идемпотентность — как last_processed_msg_id у Telegram-диалогов).
    last_processed_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "ig_conversation_id": self.ig_conversation_id,
            "igsid": self.igsid,
            "ig_username": self.ig_username,
            "agent_contact_id": str(self.agent_contact_id) if self.agent_contact_id else None,
        }


class AgentInstagramMessage(Base):
    """
    Сообщение Instagram-переписки (direction: inbound/outbound).
    Используется карточкой контакта (тред) и оркестратором (хронология).
    """
    __tablename__ = "agent_instagram_messages"
    __table_args__ = (
        Index("ix_agent_ig_messages_contact", "agent_contact_id"),
        Index("ix_agent_ig_messages_mid", "ig_message_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_config_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_configs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # CASCADE: переписка живёт по контакту (как agent_telegram_messages).
    agent_contact_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_contacts.id", ondelete="CASCADE"),
        nullable=True,
    )

    ig_conversation_id = Column(String(255), nullable=True)
    # id сообщения из Graph API (mid) — ключ дедупликации поллера.
    ig_message_id = Column(String(255), nullable=True)
    direction = Column(String(10), default="inbound", nullable=False)
    body = Column(Text, nullable=False)

    # Время сообщения по данным Instagram (naive UTC); created_at — время записи.
    sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self) -> dict:
        ts = self.sent_at or self.created_at
        return {
            "id": str(self.id),
            "direction": self.direction or "inbound",
            "body": self.body,
            "ts": ts.isoformat() if ts else None,
        }
