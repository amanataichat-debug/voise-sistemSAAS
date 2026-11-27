"""
Журнал действий кабинета (Voksy Beauty, шаг 1.7): кто что сделал.

  ActivityLog — одна строка = одно действие: запись создана / перенесена / отменена, предоплата получена,
                блок времени, услуга и цена, мастер и его график, страница салона и чат, сотрудник и права,
                расход и зарплата, решение по ИИ-управляющему, правила записи.
                Кто: владелец, сотрудник (мастер — тоже сотрудник), поддержка Voksy (вход админа),
                ИИ-администратор (бот в переписке), голосовой агент, клиент (по ссылке / в чате на сайте),
                система. Было → стало — в changes ({поле: [было, стало]}), строка для человека — summary.
                Только добавление, без правки. Хранится ACTIVITY_KEEP_DAYS (180) дней — чистку делает
                services/activity.cleanup из фонового цикла.

Пишет services/activity.log (из сервисов, а не со страниц — чтобы ловить все входы). Читает
GET /api/beauty/activity (владелец и сотрудник с разделом «Журнал действий»).
Таблицу создаёт app.ensure_booking_tables() в начале старта.
"""

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from .base import Base


class ActivityLog(Base):
    __tablename__ = "activity_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)   # владелец кабинета
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    actor_kind = Column(String(16), nullable=False)      # owner | staff | admin | ai | call | client | system
    actor_id = Column(String(64), nullable=True)          # id сотрудника / админа
    actor_name = Column(String(120), nullable=True)
    action = Column(String(40), nullable=False)           # booking.create, booking.move, service.update, …
    object_type = Column(String(24), nullable=False)      # booking | visit | block | service | master | …
    object_id = Column(String(64), nullable=True)
    summary = Column(Text, nullable=False)                # «перенос записи Нурай (маникюр): ср 11:00 → чт 15:00»
    changes = Column(JSONB, nullable=True)                # {поле: [было, стало]}
    meta = Column(JSONB, nullable=True)                   # {visit_id, booking_ids, client, …}
    ip = Column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_activity_log_user_time", "user_id", "created_at"),
        Index("ix_activity_log_object", "user_id", "object_type", "object_id"),
    )
