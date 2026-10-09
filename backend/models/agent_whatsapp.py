"""
WhatsApp агента обзвона — неофициальное подключение номера через Evolution API
(VPS wa-gateway-1, infra/whatsapp-gateway/). Только переписка: звонков нет.

Три таблицы (зеркалят личный Telegram, agent_telegram_account.py):
- AgentWhatsAppAccount — номер WhatsApp, привязанный к агенту (один на агента).
  Одна строка = один instance Evolution (instance_name). Подключение по QR как
  у WhatsApp Web; входящие приходят webhook'ом с секретом webhook_secret.
- AgentWhatsAppChat — личный чат с собеседником (remote_jid) и связь с
  AgentContact; watermark last_dispatched_at — до какого входящего сообщения
  чат уже передан оркестратору (склейка серии сообщений в один прогон).
- AgentWhatsAppMessage — переписка (вход/исход) для карточки контакта и
  хронологии оркестратора.

Таблицы создаются стартовым ensure_agent_whatsapp_tables (app.py).
"""

import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Boolean, DateTime, Text, Integer, ForeignKey,
    UniqueConstraint, Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from .base import Base


class AgentWhatsAppAccount(Base):
    """
    status:
      pending_qr   — instance создан, ждём сканирования QR
      connected    — номер подключён (Evolution state=open)
      disconnected — сессию завершили с телефона / выход, нужен новый QR
    reply_scope:
      contacts — автоответ только чатам, привязанным к контактам агента
      all      — автоответ всем новым личным чатам (контакт создаётся)
    """
    __tablename__ = "agent_whatsapp_accounts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_config_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_configs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Имя instance в Evolution API (уникально на шлюзе)
    instance_name = Column(String(64), nullable=False, unique=True)
    # Секрет в URL webhook'а (у Evolution нет подписи запросов)
    webhook_secret = Column(String(64), nullable=False)
    status = Column(String(20), default="pending_qr", nullable=False)

    # Кто подключён (после сканирования QR)
    wa_jid = Column(String(64), nullable=True)
    wa_phone = Column(String(32), nullable=True)
    wa_name = Column(String(255), nullable=True)

    auto_reply_enabled = Column(Boolean, default=True, nullable=False)
    reply_scope = Column(String(16), default="contacts", nullable=False)
    # Анти-бан: сколько НОВЫХ чатов (агент пишет первым) в сутки
    daily_new_chats_limit = Column(Integer, default=20, nullable=False)

    last_error = Column(Text, nullable=True)
    connected_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    chats = relationship(
        "AgentWhatsAppChat", back_populates="account", cascade="all, delete-orphan"
    )

    def is_connected(self) -> bool:
        return self.status == "connected"

    def to_dict(self) -> dict:
        phone_masked = None
        if self.wa_phone:
            p = self.wa_phone
            phone_masked = f"+{p[:3]}•••{p[-4:]}" if len(p) > 7 else f"•••{p[-4:]}"
        return {
            "status": self.status,
            "phone_masked": phone_masked,
            "wa_name": self.wa_name,
            "auto_reply_enabled": bool(self.auto_reply_enabled),
            "reply_scope": self.reply_scope or "contacts",
            "daily_new_chats_limit": int(self.daily_new_chats_limit or 0),
            "last_error": self.last_error,
        }


class AgentWhatsAppChat(Base):
    """Личный чат WhatsApp (агент ↔ один собеседник) и его связь с AgentContact."""
    __tablename__ = "agent_whatsapp_chats"
    __table_args__ = (
        UniqueConstraint("account_id", "remote_jid", name="uq_agent_wa_chat_jid"),
        Index("ix_agent_wa_chats_contact", "agent_contact_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_whatsapp_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_contact_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_contacts.id", ondelete="SET NULL"),
        nullable=True,
    )

    # 996700123456@s.whatsapp.net (или ...@lid, если номер скрыт)
    remote_jid = Column(String(128), nullable=False)
    phone = Column(String(32), nullable=True)          # только цифры, без +
    push_name = Column(String(255), nullable=True)
    # inbound (клиент написал сам) / send (агент написал первым)
    created_via = Column(String(16), default="inbound", nullable=False)

    # Время последнего входящего, уже переданного оркестратору
    last_dispatched_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    account = relationship("AgentWhatsAppAccount", back_populates="chats")


class AgentWhatsAppMessage(Base):
    """Сообщение WhatsApp-переписки (direction: inbound/outbound)."""
    __tablename__ = "agent_whatsapp_messages"
    __table_args__ = (
        Index("ix_agent_wa_messages_contact", "agent_contact_id"),
        Index("ix_agent_wa_messages_wamid", "wa_message_id"),
        Index("ix_agent_wa_messages_chat", "chat_id", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_config_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_configs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # SET NULL: переписка остаётся после отключения номера
    account_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_whatsapp_accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    chat_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_whatsapp_chats.id", ondelete="SET NULL"),
        nullable=True,
    )
    agent_contact_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_contacts.id", ondelete="CASCADE"),
        nullable=True,
    )

    remote_jid = Column(String(128), nullable=True)
    # key.id из WhatsApp — ключ дедупликации webhook'ов
    wa_message_id = Column(String(128), nullable=True)
    direction = Column(String(10), default="inbound", nullable=False)
    body = Column(Text, nullable=False)
    attachment_id = Column(UUID(as_uuid=True), nullable=True)

    sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self) -> dict:
        ts = self.sent_at or self.created_at
        return {
            "id": str(self.id),
            "direction": self.direction or "inbound",
            "body": self.body,
            "attachment_id": str(self.attachment_id) if self.attachment_id else None,
            "ts": ts.isoformat() if ts else None,
        }
