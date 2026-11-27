"""
Очередь отправок и автоматизации клиентам (Voksy Beauty, шаг 2.2).

notification_queue — каждое автоматическое сообщение клиенту (напоминания, подтверждение записи, «спасибо»,
поздравление с ДР, приглашение на повтор, «давно не были», после неявки) сначала встаёт в очередь, потом
отправляется (services/notify_queue.py): повторы при сбое, запасной канал, окна времени, лимиты частоты,
отказ клиента от рассылок. idem_key уникален — одно и то же сообщение не встанет дважды, сколько бы
воркеров ни планировали отправки.

automation_rules — настройки правил владельца (services/automations.RULES): вкл/выкл, параметры времени, свои
тексты RU/KY. Нет строки — значения по умолчанию из кода. Ключ «limits» — общие ограничения салона.
Таблицы создаёт app.ensure_booking_tables().
"""

import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID

from .base import Base

QUEUE_STATUSES = ("pending", "sending", "sent", "failed", "skipped", "cancelled")


class NotificationQueue(Base):
    __tablename__ = "notification_queue"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    contact_id = Column(UUID(as_uuid=True), ForeignKey("agent_contacts.id", ondelete="CASCADE"), nullable=True)
    booking_id = Column(UUID(as_uuid=True), nullable=True)
    rule = Column(String(24), nullable=False)               # confirm | reminder_24h | reminder_2h | thanks | review | ...
    kind = Column(String(12), nullable=False, default="service")   # service (по записи) | marketing (поздравления, возврат)
    lang = Column(String(4), nullable=True)                 # ru | ky
    text = Column(Text, nullable=False)
    status = Column(String(12), nullable=False, default="pending")
    attempts = Column(Integer, nullable=False, default=0)
    send_after = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=True)   # после — смысла нет (напоминание после визита)
    lease_until = Column(DateTime(timezone=True), nullable=True)  # «отправляется» — до этого времени
    channel = Column(String(12), nullable=True)             # куда ушло (whatsapp | telegram | instagram | webchat)
    last_error = Column(Text, nullable=True)
    idem_key = Column(String(160), nullable=False)
    meta = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    sent_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("idem_key", name="uq_notification_queue_idem"),
        Index("ix_notification_queue_due", "status", "send_after"),
        Index("ix_notification_queue_user", "user_id", "created_at"),
        Index("ix_notification_queue_contact", "contact_id", "kind", "sent_at"),
    )


class AutomationRule(Base):
    __tablename__ = "automation_rules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    key = Column(String(24), nullable=False)
    enabled = Column(Boolean, nullable=True)                # NULL — как по умолчанию
    params = Column(JSONB, nullable=True)
    template_ru = Column(Text, nullable=True)
    template_ky = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_automation_rules_user_key"),)
