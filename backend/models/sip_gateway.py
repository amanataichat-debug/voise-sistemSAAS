"""
Модели собственной SIP-телефонии (шлюз Asterisk + мост, см. infra/sip-gateway/).

Две таблицы:
  * sip_phone_numbers — номера, выделенные оператором, и их привязка к ассистенту.
    Входящий звонок на номер → ассистент; исходящий от ассистента → caller ID.
  * sip_calls — журнал звонков через шлюз. Строка создаётся до начала звонка
    (исходящие ставятся в очередь со статусом "queued" и забираются тем воркером,
    который держит управляющий сокет шлюза) и обновляется событиями от моста.
"""

import uuid
from datetime import datetime

from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Float, Integer, JSON, Index, Text
from sqlalchemy.dialects.postgresql import UUID

from backend.models.base import Base


# Типы ассистентов, которые умеет обслуживать телефонный тракт: у каждого есть
# браузерный хендлер с протоколом виджета, который заворачивается в HandlerSocket.
SIP_SUPPORTED_ASSISTANT_TYPES = ("openai", "gemini", "fish")


class SipPhoneNumber(Base):
    __tablename__ = "sip_phone_numbers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Номер в цифровом виде без "+": 996705579977
    phone_number = Column(String(20), nullable=False, unique=True)
    label = Column(String(100), nullable=True)

    # Какой шлюз обслуживает номер (gateway_id из /etc/voksy-bridge/bridge.env)
    gateway_id = Column(String(50), nullable=False, default="sip-gw-1")

    # Привязка к ассистенту: "openai" → assistant_configs, "gemini" → gemini_assistant_configs,
    # "fish" → fish_assistant_configs
    assistant_type = Column(String(20), nullable=True)
    assistant_id = Column(UUID(as_uuid=True), nullable=True)

    # Первая фраза при входящем звонке (переопределяет greeting_message ассистента)
    first_phrase = Column(Text, nullable=True)

    # Разрешать исходящие с этим номером в качестве caller ID
    allow_outbound = Column(Boolean, default=True, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "phone_number": self.phone_number,
            "label": self.label,
            "gateway_id": self.gateway_id,
            "assistant_type": self.assistant_type,
            "assistant_id": str(self.assistant_id) if self.assistant_id else None,
            "first_phrase": self.first_phrase,
            "allow_outbound": self.allow_outbound,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class SipCallStatus:
    QUEUED = "queued"        # исходящий ждёт отправки на шлюз
    DIALING = "dialing"      # шлюз набирает номер
    RINGING = "ringing"      # (зарезервировано)
    ANSWERED = "answered"    # разговор идёт
    COMPLETED = "completed"  # завершён нормально
    FAILED = "failed"        # не дозвонились / ошибка


class SipCall(Base):
    __tablename__ = "sip_calls"

    # call_id моста = первичный ключ
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    gateway_id = Column(String(50), nullable=False, default="sip-gw-1", index=True)
    direction = Column(String(10), nullable=False)  # inbound | outbound
    status = Column(String(20), nullable=False, default=SipCallStatus.QUEUED, index=True)

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    phone_number_id = Column(UUID(as_uuid=True), ForeignKey("sip_phone_numbers.id", ondelete="SET NULL"), nullable=True)
    task_id = Column(UUID(as_uuid=True), nullable=True, index=True)  # tasks.id для исходящих из CRM/планировщика

    assistant_type = Column(String(20), nullable=True)
    assistant_id = Column(UUID(as_uuid=True), nullable=True)

    # Номера в цифровом виде без "+"
    did = Column(String(30), nullable=True)        # наш номер (набранный для входящих / caller ID для исходящих)
    caller = Column(String(30), nullable=True)     # кто звонил (входящие)
    to_number = Column(String(30), nullable=True)  # кому звоним (исходящие)

    # Контекст для ассистента: имя контакта, заголовок задачи, кастомное приветствие и т.п.
    call_metadata = Column(JSON, nullable=True)
    trunk_host = Column(String(50), nullable=True)
    conversation_session_id = Column(String(100), nullable=True)

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False, index=True)
    dialed_at = Column(DateTime(timezone=True), nullable=True)
    answered_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    duration_sec = Column(Float, nullable=True)
    end_reason = Column(String(50), nullable=True)
    error = Column(Text, nullable=True)
    attempts = Column(Integer, default=0, nullable=False)

    __table_args__ = (
        Index("ix_sip_calls_gateway_status", "gateway_id", "status"),
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "gateway_id": self.gateway_id,
            "direction": self.direction,
            "status": self.status,
            "user_id": str(self.user_id) if self.user_id else None,
            "task_id": str(self.task_id) if self.task_id else None,
            "assistant_type": self.assistant_type,
            "assistant_id": str(self.assistant_id) if self.assistant_id else None,
            "did": self.did,
            "caller": self.caller,
            "to_number": self.to_number,
            "metadata": self.call_metadata,
            "trunk_host": self.trunk_host,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "answered_at": self.answered_at.isoformat() if self.answered_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "duration_sec": self.duration_sec,
            "end_reason": self.end_reason,
            "error": self.error,
        }


def normalize_sip_number(value: str) -> str:
    """Оставить только цифры: '+996 705 57-99-77' → '996705579977', '0705579977' → '996705579977'."""
    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    if len(digits) == 10 and digits.startswith("0"):
        digits = "996" + digits[1:]
    elif len(digits) == 9:
        digits = "996" + digits
    return digits
