"""
Онлайн-запись (ветка 1811-niche-admin-speed): мастера/специалисты, записи, страница записи.

Как Altegio, только проще. Принадлежит аккаунту владельца (user_id), а не конкретному агенту:
бот любого агента владельца, сам владелец, сотрудники и клиент по ссылке пишут в один календарь.

  BookingResource — кто принимает: мастер, врач, бокс СТО, столик. График по дням недели
                    (несколько интервалов = перерыв), выходные даты, какие услуги делает.
  Booking         — запись: услуга (позиция каталога agent_knowledge_entries), мастер, время,
                    клиент (карточка CRM agent_contacts + имя/телефон на случай без карточки),
                    статус booked → confirmed → arrived / no_show / cancelled, откуда пришла.
  BookingPage     — настройки записи владельца: шаг сетки, буфер, за сколько можно записаться,
                    горизонт, напоминания, адрес; публичная ссылка /b/<slug>.
  SalonMedia      — фото салона и мастеров (Voksy Beauty, шаг 1.2): аватар, портфолио, сертификаты,
                    фото помещения. Байты — в R2 (storage_key), без R2 — прямо в базе (data), уже
                    уменьшенные. Отдаются без входа /media/salon/<id> — их показывает страница салона.

Карточка мастера (Voksy Beauty, шаг 1.2): фото, специализация (role), о себе, опыт (с какого года),
портфолио и сертификаты (id из salon_media), свои цена и длительность услуг (service_prices),
привязка ко входу сотрудника (team_member_id — мастер видит только свой журнал), показывать ли в
онлайн-записи (online_booking). Колонки добавляет app.ensure_booking_tables().

Таблицы создаёт app.ensure_booking_tables() в начале старта.
"""

import uuid

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, LargeBinary, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from .base import Base

BOOKING_STATUSES = ("booked", "confirmed", "arrived", "no_show", "cancelled")
ACTIVE_STATUSES = ("booked", "confirmed", "arrived")   # занимают время в календаре


class BookingResource(Base):
    __tablename__ = "booking_resources"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    role = Column(String(120), nullable=True)                   # «Мастер маникюра», «Врач-терапевт», «Бокс 1»
    color = Column(String(16), nullable=True)
    phone = Column(String(32), nullable=True)
    # {"mon": [["09:00","13:00"],["14:00","19:00"]], ..., "sun": []} — время по Бишкеку
    schedule = Column(JSONB, nullable=True)
    days_off = Column(JSONB, nullable=True)                     # ["2026-12-31", ...]
    service_ids = Column(JSONB, nullable=True)                  # null — делает все услуги
    is_active = Column(Boolean, nullable=False, default=True)
    position = Column(Integer, nullable=False, default=0)
    pay_percent = Column(Float, nullable=True)                  # % мастеру с выручки его записей «пришёл»
    pay_fixed = Column(Float, nullable=True)                    # оклад в месяц, сом
    # — карточка мастера (Voksy Beauty) —
    photo_id = Column(UUID(as_uuid=True), nullable=True)        # salon_media.id
    bio = Column(Text, nullable=True)                           # «о себе» для страницы салона
    experience_since = Column(Integer, nullable=True)           # работает с такого-то года → «опыт 7 лет»
    portfolio = Column(JSONB, nullable=True)                    # [{"id": media_id, "caption": "..."}]
    certificates = Column(JSONB, nullable=True)                 # [{"id": media_id, "title": "..."}]
    service_prices = Column(JSONB, nullable=True)               # {service_id: {"price": 1500, "duration_min": 90}}
    team_member_id = Column(UUID(as_uuid=True), nullable=True, index=True)   # вход мастера (team_members.id)
    online_booking = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class SalonMedia(Base):
    __tablename__ = "salon_media"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = Column(String(16), nullable=False)                   # avatar | portfolio | certificate | salon
    resource_id = Column(UUID(as_uuid=True), nullable=True, index=True)   # чей (мастер), для salon — пусто
    mime = Column(String(40), nullable=False, default="image/jpeg")
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    size_bytes = Column(Integer, nullable=True)
    storage_key = Column(String(300), nullable=True)            # R2
    data = Column(LargeBinary, nullable=True)                   # без R2: байты прямо здесь
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Booking(Base):
    __tablename__ = "bookings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    resource_id = Column(UUID(as_uuid=True), ForeignKey("booking_resources.id", ondelete="SET NULL"), nullable=True)
    service_entry_id = Column(UUID(as_uuid=True), ForeignKey("agent_knowledge_entries.id", ondelete="SET NULL"), nullable=True)
    service_title = Column(String(300), nullable=True)
    contact_id = Column(UUID(as_uuid=True), ForeignKey("agent_contacts.id", ondelete="SET NULL"), nullable=True)
    client_name = Column(String(200), nullable=True)
    client_phone = Column(String(32), nullable=True)
    start_at = Column(DateTime(timezone=True), nullable=False)
    end_at = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(16), nullable=False, default="booked")
    price = Column(Float, nullable=True)
    # Услуги салона (шаг 1.3): подготовка до и перерыв после — на момент записи (правка услуги
    # старые записи не двигает); предоплата по правилу услуги и когда её отметили полученной
    prep_min = Column(Integer, nullable=True)
    after_min = Column(Integer, nullable=True)
    prepay_amount = Column(Float, nullable=True)
    prepaid_at = Column(DateTime(timezone=True), nullable=True)
    source = Column(String(16), nullable=True)                  # owner | bot | online | call | staff
    channel = Column(String(16), nullable=True)                 # whatsapp | telegram | instagram | call | web
    note = Column(Text, nullable=True)
    cancel_reason = Column(String(300), nullable=True)
    reminder_24h_at = Column(DateTime(timezone=True), nullable=True)
    reminder_2h_at = Column(DateTime(timezone=True), nullable=True)
    followup_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_bookings_user_start", "user_id", "start_at"),
        Index("ix_bookings_resource_start", "resource_id", "start_at"),
        Index("ix_bookings_contact", "contact_id"),
    )


class BookingPage(Base):
    __tablename__ = "booking_pages"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    slug = Column(String(64), nullable=True, unique=True, index=True)
    enabled = Column(Boolean, nullable=False, default=True)      # онлайн-запись по ссылке
    # step_min, buffer_min, lead_min, horizon_days, reminders (bool), address, title, thanks (bool)
    settings = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
