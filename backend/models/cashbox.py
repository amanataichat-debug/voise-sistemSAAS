"""
Касса салона (Voksy Beauty, шаг 3.1): смены администраторов и оплаты визитов.

  CashShift    — смена кассира: кто открыл (владелец / сотрудник), сколько наличных было в кассе при
                 открытии, когда закрыл, сколько насчитал при закрытии и расхождение с ожидаемым.
                 У одного кассира не больше одной открытой смены (частичный UNIQUE по cashier_key).
  CashReceipt  — одно действие кассира: «принять оплату», «вернуть деньги», «внести», «изъять».
                 idem_key UNIQUE на владельца — двойное нажатие и повтор запроса не создают второй чек.
  CashPayment  — строки чека: способ (наличные / карта / QR / перевод), сумма со знаком (оплата +,
                 возврат −, внесение +, изъятие −) и запись, на которую легли деньги. Оплата визита из
                 нескольких услуг раскладывается по его записям — так у каждой записи и мастера своё «оплачено».

Выручка «по оплатам»: строки kind payment/refund (services/cashbox.py → booking_revenue). Наличные в
кассе смены = наличные при открытии + сумма строк method=cash этой смены.

Таблицы создаёт app.ensure_booking_tables() в начале старта.
"""

import uuid

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from .base import Base

PAY_METHODS = ("cash", "card", "qr", "transfer")
RECEIPT_KINDS = ("payment", "refund", "cash_in", "cash_out")
MONEY_KINDS = ("payment", "refund")          # деньги клиентов — выручка; cash_in / cash_out — движение кассы


class CashShift(Base):
    __tablename__ = "cash_shifts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    cashier_key = Column(String(80), nullable=False)        # owner | staff:<id> | admin:<id>
    opened_by_kind = Column(String(16), nullable=False)     # owner | staff | admin
    opened_by_id = Column(String(64), nullable=True)
    opened_by_name = Column(String(120), nullable=True)
    opened_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    opening_cash = Column(Float, nullable=False, default=0)
    status = Column(String(12), nullable=False, default="open")     # open | closed
    closed_at = Column(DateTime(timezone=True), nullable=True)
    closed_by_kind = Column(String(16), nullable=True)
    closed_by_id = Column(String(64), nullable=True)
    closed_by_name = Column(String(120), nullable=True)
    expected_cash = Column(Float, nullable=True)            # по чекам: при открытии + наличные смены
    counted_cash = Column(Float, nullable=True)             # насчитал кассир при закрытии
    diff = Column(Float, nullable=True)                     # counted − expected: «−500» — не хватает
    totals = Column(JSONB, nullable=True)                   # итоги смены при закрытии: по способам, чеки, возвраты
    note = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_cash_shifts_user_opened", "user_id", "opened_at"),
        Index("ux_cash_shifts_open", "user_id", "cashier_key", unique=True,
              postgresql_where="status = 'open'"),
    )


class CashReceipt(Base):
    __tablename__ = "cash_receipts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    shift_id = Column(UUID(as_uuid=True), ForeignKey("cash_shifts.id", ondelete="SET NULL"), nullable=True)
    idem_key = Column(String(80), nullable=False)
    kind = Column(String(12), nullable=False)               # payment | refund | cash_in | cash_out
    total = Column(Float, nullable=False)                   # всегда > 0; знак — по kind
    booking_id = Column(UUID(as_uuid=True), nullable=True)  # запись, из карточки которой платили
    visit_id = Column(UUID(as_uuid=True), nullable=True)
    contact_id = Column(UUID(as_uuid=True), nullable=True)
    client_name = Column(String(200), nullable=True)
    title = Column(String(300), nullable=True)              # «Маникюр + педикюр» / «Купили воду»
    prepay = Column(Boolean, nullable=False, default=False) # оплата до визита
    refund_of = Column(UUID(as_uuid=True), nullable=True)   # возврат — по какому чеку
    comment = Column(Text, nullable=True)
    created_by_kind = Column(String(16), nullable=False)
    created_by_id = Column(String(64), nullable=True)
    created_by_name = Column(String(120), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ux_cash_receipts_idem", "user_id", "idem_key", unique=True),
        Index("ix_cash_receipts_user_created", "user_id", "created_at"),
        Index("ix_cash_receipts_shift", "shift_id"),
        Index("ix_cash_receipts_visit", "visit_id"),
    )


class CashPayment(Base):
    __tablename__ = "cash_payments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    receipt_id = Column(UUID(as_uuid=True), ForeignKey("cash_receipts.id", ondelete="CASCADE"), nullable=False)
    shift_id = Column(UUID(as_uuid=True), nullable=True)
    kind = Column(String(12), nullable=False)
    method = Column(String(12), nullable=False)             # cash | card | qr | transfer
    amount = Column(Float, nullable=False)                  # со знаком: оплата +, возврат −, внесение +, изъятие −
    booking_id = Column(UUID(as_uuid=True), nullable=True)
    resource_id = Column(UUID(as_uuid=True), nullable=True) # мастер записи на момент оплаты (зарплата, шаг 3.3)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_cash_payments_user_created", "user_id", "created_at"),
        Index("ix_cash_payments_booking", "booking_id"),
        Index("ix_cash_payments_receipt", "receipt_id"),
        Index("ix_cash_payments_shift", "shift_id"),
    )
