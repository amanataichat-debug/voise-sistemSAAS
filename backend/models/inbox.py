"""
Инбокс (ветка 0411-inbox): общий чат по WhatsApp, личному Telegram и Instagram.

Сами сообщения живут в таблицах каналов (agent_whatsapp_messages,
agent_telegram_messages, agent_instagram_messages) — инбокс их только
объединяет. Здесь два служебных хранилища:

  inbox_read_state   — до какого момента владелец прочитал диалог с клиентом
                       (счётчик непрочитанных и папка «Непрочитанные»);
  inbox_translations — перевод сообщения на язык владельца или оригинал
                       сообщения, которое владелец написал по-русски, а клиенту
                       ушёл перевод. Ключ — канал + id строки в таблице канала,
                       поэтому таблицы каналов не меняются.

Таблицы создаёт app.ensure_inbox_schema() в начале старта, в каждом воркере.
"""

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID

from .base import Base


class InboxReadState(Base):
    __tablename__ = "inbox_read_state"
    __table_args__ = (
        UniqueConstraint("user_id", "agent_contact_id", name="uq_inbox_read_user_contact"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    agent_contact_id = Column(
        UUID(as_uuid=True), ForeignKey("agent_contacts.id", ondelete="CASCADE"), nullable=False,
    )
    last_read_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class InboxTranslation(Base):
    __tablename__ = "inbox_translations"
    __table_args__ = (
        UniqueConstraint("channel", "message_id", "kind", name="uq_inbox_translation"),
        Index("ix_inbox_translations_message", "message_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # whatsapp | telegram | instagram
    channel = Column(String(16), nullable=False)
    # id строки в таблице канала (agent_*_messages.id)
    message_id = Column(UUID(as_uuid=True), nullable=False)
    # translation — входящее, переведённое на язык владельца;
    # original    — что владелец написал до перевода (клиенту ушёл перевод)
    kind = Column(String(16), nullable=False, default="translation")
    language = Column(String(8), nullable=True)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class InboxSettings(Base):
    """Настройки инбокса владельца: языки помощника, пауза после ответа, звук и т.д.
    Отдельная таблица, а не колонка users: строку users ORM выбирает в каждом запросе."""
    __tablename__ = "inbox_settings"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    data = Column(JSONB, nullable=False, default=dict)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
