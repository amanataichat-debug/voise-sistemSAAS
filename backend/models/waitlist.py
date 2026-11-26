"""
Лист ожидания салона (Voksy Beauty, шаг 2.4).

waitlist_entries — клиент хочет записаться, а подходящего времени нет: услуга, мастер (или любой), дни
«с — по», желаемое время суток. Освободилось подходящее окно (отмена, перенос, новый график) — система сама
предлагает его ПЕРВОМУ в очереди (решение владельца 26.11.2026): сообщение со ссылкой /w/<токен>, на ответ
30 минут; не ответил или «не подходит» — окно уходит следующему, а этому предложат другое (то же окно второй
раз не предлагается — offered_slots). Ответить можно по ссылке («Записаться» / «Не подходит» / «Больше не
нужно») или боту в переписке («да» → запись). Логика — services/waitlist_service.py; таблицу создаёт
app.ensure_booking_tables().

Статусы: waiting (ждёт окна) → offered (предложено, ждём ответа до offer_expires_at) → booked (записался,
booking_id) | cancelled (убрали / «больше не нужно») | expired (прошёл последний желаемый день).
"""

import uuid

from sqlalchemy import Column, Date, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID

from .base import Base

WAITLIST_STATUSES = ("waiting", "offered", "booked", "cancelled", "expired")
WAITLIST_ACTIVE = ("waiting", "offered")


class WaitlistEntry(Base):
    __tablename__ = "waitlist_entries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    contact_id = Column(UUID(as_uuid=True), ForeignKey("agent_contacts.id", ondelete="SET NULL"), nullable=True)
    client_name = Column(String(200), nullable=True)
    client_phone = Column(String(40), nullable=True)
    service_entry_id = Column(UUID(as_uuid=True), ForeignKey("agent_knowledge_entries.id", ondelete="SET NULL"), nullable=True)
    service_title = Column(String(300), nullable=True)
    resource_id = Column(UUID(as_uuid=True), ForeignKey("booking_resources.id", ondelete="SET NULL"), nullable=True)  # NULL — любой
    date_from = Column(Date, nullable=False)
    date_to = Column(Date, nullable=False)
    time_from = Column(String(5), nullable=True)      # «18:00» — не раньше (по Бишкеку)
    time_to = Column(String(5), nullable=True)        # «12:00» — не позже (начало визита)
    note = Column(Text, nullable=True)
    source = Column(String(12), nullable=False, default="owner")   # owner | staff | bot | online | call
    status = Column(String(12), nullable=False, default="waiting")
    offer_start = Column(DateTime(timezone=True), nullable=True)
    offer_resource_id = Column(UUID(as_uuid=True), nullable=True)
    offered_at = Column(DateTime(timezone=True), nullable=True)
    offer_expires_at = Column(DateTime(timezone=True), nullable=True)
    offers_count = Column(Integer, nullable=False, default=0)
    offered_slots = Column(JSONB, nullable=True)      # ["<мастер>|<ISO начала>", …] — что уже предлагали
    booking_id = Column(UUID(as_uuid=True), nullable=True)
    last_note = Column(String(300), nullable=True)    # почему не предлагаем сами («нет мессенджера») и т.п.
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    closed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_waitlist_user_status", "user_id", "status", "created_at"),
        Index("ix_waitlist_contact", "contact_id", "status"),
        Index("ix_waitlist_offer_due", "status", "offer_expires_at"),
    )
