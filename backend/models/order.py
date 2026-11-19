"""
Заказы (ветка 1811-niche-admin-speed): магазины, доставка еды, цветы, автозапчасти — модуль `orders`
профиля бизнеса.

  Order          — заказ клиента: номер по порядку у владельца («№ 1024»), позиции (из каталога
                   agent_knowledge_entries или свободной строкой) с размером/цветом, количеством и ценой,
                   доставка или самовывоз, адрес и время, оплата, статус new → confirmed → packed →
                   delivering → done / cancelled, откуда пришёл (бот в переписке, владелец, сотрудник).
                   Остаток позиций каталога резервируется при оформлении и возвращается при отмене.
  OrderSettings  — настройки заказов владельца: сообщать ли клиенту о статусе, стоимость доставки,
                   бесплатная доставка от суммы, адрес самовывоза.

Таблицы создаёт app.ensure_order_tables() в начале старта. Логика — services/order_service.py.
"""

import uuid

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from .base import Base

ORDER_STATUSES = ("new", "confirmed", "packed", "delivering", "done", "cancelled")
OPEN_STATUSES = ("new", "confirmed", "packed", "delivering")


class Order(Base):
    __tablename__ = "orders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    number = Column(Integer, nullable=False)
    contact_id = Column(UUID(as_uuid=True), ForeignKey("agent_contacts.id", ondelete="SET NULL"), nullable=True)
    client_name = Column(String(200), nullable=True)
    client_phone = Column(String(32), nullable=True)
    status = Column(String(16), nullable=False, default="new")
    # [{"entry_id", "title", "size", "color", "qty", "price"}]
    items = Column(JSONB, nullable=False, default=list)
    subtotal = Column(Float, nullable=False, default=0)
    delivery_fee = Column(Float, nullable=False, default=0)
    discount = Column(Float, nullable=False, default=0)
    total = Column(Float, nullable=False, default=0)
    currency = Column(String(8), nullable=False, default="KGS")
    delivery_type = Column(String(16), nullable=False, default="delivery")   # delivery | pickup
    address = Column(Text, nullable=True)
    delivery_when = Column(String(120), nullable=True)                         # «сегодня к 18:00»
    payment_method = Column(String(16), nullable=True)                         # cash | card | transfer | qr
    paid = Column(Boolean, nullable=False, default=False)
    source = Column(String(16), nullable=True)                                 # owner | staff | bot
    channel = Column(String(16), nullable=True)                                # whatsapp | telegram | instagram | call | web
    created_by = Column(String(120), nullable=True)                            # имя сотрудника
    note = Column(Text, nullable=True)
    cancel_reason = Column(String(300), nullable=True)
    stock_reserved = Column(Boolean, nullable=False, default=False)
    client_notified = Column(JSONB, nullable=True)                             # статусы, о которых клиенту уже сообщили
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    done_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ux_orders_user_number", "user_id", "number", unique=True),
        Index("ix_orders_user_created", "user_id", "created_at"),
        Index("ix_orders_user_status", "user_id", "status"),
        Index("ix_orders_contact", "contact_id"),
    )


class OrderSettings(Base):
    __tablename__ = "order_settings"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    # notify_client (bool), delivery_fee, free_delivery_from, pickup_address, payment_note
    data = Column(JSONB, nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
