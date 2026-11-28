"""
Отзывы клиентов салона (Voksy Beauty, шаг 2.3).

salon_reviews — оценка визита 1–5 и текст клиента. Откуда приходит: ответ на «оцените визит» в переписке
(бот вызывает save_review), страница «Оцените визит» по ссылке из сообщения (/r/<токен>). Один отзыв на
запись (визит из нескольких услуг — один отзыв на его первую запись).

Публикация (решение владельца 26.11.2026): оценки 4–5 появляются на странице салона сразу, 1–3 ждут
проверки владельца (status=pending): он отвечает от салона и решает — показать или скрыть. Рейтинг салона
и мастеров считается только по опубликованным. Оценка дублируется в bookings.review_score/review_text —
по ним считают карточка клиента, отчёты ИИ-управляющего и главная салона.
Таблицу создаёт app.ensure_booking_tables(); старые оценки из bookings переносятся туда один раз.
"""

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID

from .base import Base

REVIEW_STATUSES = ("published", "pending", "hidden")
REVIEW_SOURCES = ("chat", "link")


class SalonReview(Base):
    __tablename__ = "salon_reviews"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    booking_id = Column(UUID(as_uuid=True), ForeignKey("bookings.id", ondelete="SET NULL"), nullable=True)
    visit_id = Column(UUID(as_uuid=True), nullable=True)
    contact_id = Column(UUID(as_uuid=True), ForeignKey("agent_contacts.id", ondelete="SET NULL"), nullable=True)
    resource_id = Column(UUID(as_uuid=True), ForeignKey("booking_resources.id", ondelete="SET NULL"), nullable=True)
    service_title = Column(String(300), nullable=True)
    visit_at = Column(DateTime(timezone=True), nullable=True)
    score = Column(Integer, nullable=False)
    text = Column(Text, nullable=True)
    author_name = Column(String(80), nullable=True)      # как подписан на странице: «Айгерим К.», пусто — «Клиент салона»
    source = Column(String(12), nullable=False, default="chat")      # chat | link
    status = Column(String(12), nullable=False, default="published")  # published | pending | hidden
    reply_text = Column(Text, nullable=True)              # ответ салона (виден на странице под отзывом)
    reply_at = Column(DateTime(timezone=True), nullable=True)
    reply_by = Column(String(120), nullable=True)
    checked_at = Column(DateTime(timezone=True), nullable=True)      # владелец решил: показать / скрыть
    published_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("booking_id", name="uq_salon_reviews_booking"),
        Index("ix_salon_reviews_user_status", "user_id", "status", "created_at"),
        Index("ix_salon_reviews_master", "user_id", "resource_id", "status"),
    )
