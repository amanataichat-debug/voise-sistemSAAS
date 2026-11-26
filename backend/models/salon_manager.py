"""
ИИ-управляющий салона (Voksy Beauty, шаг 1.6б): действия агента, отчёты владельцу, push на телефон.

  ManagerAction    — одно действие сценария: кому (клиент), какой сценарий (winback / fill_gaps / repeat),
                     как (сообщение или звонок), что предложить (payload: окна, услуга, мастер, причина).
                     Статус: proposed (ждёт одобрения владельца) → approved → running → done / failed;
                     rejected — владелец отказал, skipped — не выполнено (клиент записался сам, пауза…).
                     В режиме «агент решает сам» действие сразу approved. Журнал «что сделал агент» и
                     строка отчёта «вчера агент…» читаются отсюда же.
  OwnerReport      — утренний / недельный отчёт: текст (то же, что ушло в Telegram) + данные для карточек
                     приложения; read_at — открыл ли владелец.
  PushSubscription — подписка браузера / установленного приложения на push (Web Push, VAPID).
  WebPushKey       — пара ключей VAPID сервера (одна строка; создаётся сама при первом запросе,
                     если ключей нет в переменных окружения).

Таблицы создаёт app.ensure_booking_tables() в начале старта.
"""

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from .base import Base


class ManagerAction(Base):
    __tablename__ = "manager_actions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    agent_config_id = Column(UUID(as_uuid=True), ForeignKey("agent_configs.id", ondelete="CASCADE"), nullable=False)
    contact_id = Column(UUID(as_uuid=True), ForeignKey("agent_contacts.id", ondelete="CASCADE"), nullable=True)
    scenario = Column(String(24), nullable=False)        # winback | fill_gaps | repeat
    kind = Column(String(12), nullable=False, default="message")   # message | call
    status = Column(String(12), nullable=False, default="proposed")
    payload = Column(JSONB, nullable=True)               # {reason, slots[], service, master, ...}
    result = Column(Text, nullable=True)                 # что получилось (для журнала)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    done_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_manager_actions_user_status", "user_id", "status", "created_at"),
        Index("ix_manager_actions_contact", "contact_id", "created_at"),
    )


class OwnerReport(Base):
    __tablename__ = "owner_reports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind = Column(String(12), nullable=False)            # morning | weekly
    day = Column(String(10), nullable=False)             # дата Бишкека YYYY-MM-DD
    title = Column(String(200), nullable=False)
    text = Column(Text, nullable=False)
    data = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    read_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_owner_reports_user_day", "user_id", "kind", "day", unique=True),)


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    endpoint = Column(Text, nullable=False, unique=True)
    p256dh = Column(String(200), nullable=False)
    auth = Column(String(100), nullable=False)
    user_agent = Column(String(300), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_ok_at = Column(DateTime(timezone=True), nullable=True)


class WebPushKey(Base):
    __tablename__ = "web_push_keys"

    id = Column(String(16), primary_key=True, default="main")
    private_pem = Column(Text, nullable=False)
    public_b64 = Column(String(200), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
