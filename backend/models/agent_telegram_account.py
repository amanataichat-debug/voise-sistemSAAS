"""
Личный Telegram-аккаунт агента (MTProto через Telethon) — коннектор Telegram.

НЕ путать с Telegram-БОТОМ агента (agent_config.telegram_* + Bot API): бот —
фронтенд чата владельца с оркестратором; здесь — личный аккаунт владельца,
с которого агент пишет клиентам и отвечает на входящие сообщения.

Три таблицы:
- AgentTelegramAccount — одна строка = один личный аккаунт, привязанный к агенту
  (у каждого агента может быть свой аккаунт). Хранит StringSession Telethon,
  зашифрованную Fernet-ключом TELEGRAM_SESSION_KEY (сессия = полный доступ к
  аккаунту, в открытом виде не храним).
- AgentTelegramDialog — состояние по каждому личному диалогу: какой последний
  message_id обработан поллером и с каким AgentContact связан peer.
- AgentTelegramMessage — переписка (вход/исход) для карточки контакта и
  контекста оркестратора; аналог sms_messages для канала Telegram.

Авторизация — трёхшаговая (телефон → код → пароль 2FA), промежуточное состояние
(частичная сессия + phone_code_hash) живёт в этой же строке между шагами.
"""

import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Boolean, DateTime, Text, ForeignKey, BigInteger,
    UniqueConstraint, Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from .base import Base


class AgentTelegramAccount(Base):
    """
    status:
      pending_code     — код отправлен, ждём ввода
      pending_password — код принят, аккаунт с 2FA, ждём облачный пароль
      connected        — авторизован, сессия рабочая
      error            — сессия отозвана/протухла, нужно переподключение
    reply_scope:
      contacts — автоответ только диалогам, привязанным к контактам агента
      all      — автоответ всем новым личным диалогам (создаём контакт)
    """
    __tablename__ = "agent_telegram_accounts"

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

    phone = Column(String(32), nullable=True)
    # StringSession Telethon, зашифрованная Fernet (TELEGRAM_SESSION_KEY)
    session_encrypted = Column(Text, nullable=True)
    status = Column(String(20), default="pending_code", nullable=False)

    # Промежуточное состояние между шагами авторизации
    phone_code_hash = Column(String(64), nullable=True)

    # Кто подключён (для UI «Подключён как …»)
    tg_user_id = Column(BigInteger, nullable=True)
    tg_username = Column(String(64), nullable=True)
    tg_first_name = Column(String(128), nullable=True)

    # Автоответ поллера на входящие личные сообщения
    auto_reply_enabled = Column(Boolean, default=False, nullable=False)
    reply_scope = Column(String(16), default="contacts", nullable=False)

    # Атомарный claim поллера при нескольких gunicorn-воркерах:
    # UPDATE ... SET last_poll_at=now() WHERE last_poll_at < cutoff
    last_poll_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    agent_config = relationship("AgentConfig", foreign_keys=[agent_config_id])
    dialogs = relationship(
        "AgentTelegramDialog", back_populates="account", cascade="all, delete-orphan"
    )

    def is_connected(self) -> bool:
        return self.status == "connected" and bool(self.session_encrypted)

    def to_dict(self) -> dict:
        phone_masked = None
        if self.phone:
            tail = self.phone[-4:]
            phone_masked = f"{self.phone[:2]}•••{tail}" if len(self.phone) > 6 else f"•••{tail}"
        return {
            "status": self.status,
            "phone_masked": phone_masked,
            "tg_username": self.tg_username,
            "tg_first_name": self.tg_first_name,
            "auto_reply_enabled": bool(self.auto_reply_enabled),
            "reply_scope": self.reply_scope or "contacts",
            "last_error": self.last_error,
        }


class AgentTelegramDialog(Base):
    """Состояние личного диалога: baseline поллера + связь peer ↔ AgentContact."""
    __tablename__ = "agent_telegram_dialogs"
    __table_args__ = (
        UniqueConstraint("account_id", "tg_peer_id", name="uq_agent_tg_dialog_peer"),
        Index("ix_agent_tg_dialogs_contact", "agent_contact_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_telegram_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_contact_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_contacts.id", ondelete="SET NULL"),
        nullable=True,
    )

    tg_peer_id = Column(BigInteger, nullable=False)
    tg_username = Column(String(64), nullable=True)
    tg_name = Column(String(255), nullable=True)

    # Последний обработанный поллером message_id (baseline при подключении —
    # текущий top_message, чтобы не отвечать на старую переписку).
    last_processed_msg_id = Column(BigInteger, default=0, nullable=False)

    # Как появился диалог: baseline (снимок при подключении) / inbound (клиент
    # написал сам) / send_username / send_phone (агент написал первым). По
    # send_phone считается почасовой лимит резолвов номеров (анти-PeerFlood).
    created_via = Column(String(16), default="baseline", nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    account = relationship("AgentTelegramAccount", back_populates="dialogs")
    contact = relationship("AgentContact", foreign_keys=[agent_contact_id])


class AgentTelegramMessage(Base):
    """
    Сообщение личной Telegram-переписки (direction: inbound/outbound).
    Используется карточкой контакта (тред) и оркестратором (контекст-блок).
    """
    __tablename__ = "agent_telegram_messages"
    __table_args__ = (
        Index("ix_agent_tg_messages_contact", "agent_contact_id"),
        Index("ix_agent_tg_messages_account", "account_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # SET NULL: при отключении/переподключении аккаунта переписка с контактами
    # должна сохраняться (тред живёт по agent_contact_id).
    account_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_telegram_accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    agent_contact_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_contacts.id", ondelete="CASCADE"),
        nullable=True,
    )

    tg_peer_id = Column(BigInteger, nullable=True)
    tg_message_id = Column(BigInteger, nullable=True)
    direction = Column(String(10), default="inbound", nullable=False)
    body = Column(Text, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "direction": self.direction or "inbound",
            "body": self.body,
            "ts": self.created_at.isoformat() if self.created_at else None,
        }
