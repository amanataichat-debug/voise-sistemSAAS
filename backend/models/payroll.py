"""
Зарплата мастеров v2 (Voksy Beauty, шаг 3.3; решения владельца 27.11.2026 — docs/voksy_beauty_plan.md, разд. 9, 12).

Схема мастера — в booking_resources: pay_percent (общий % с оплаченных услуг), pay_categories ({категория: %},
«Окрашивание — 40%», остальное — общий), pay_shift (ставка за выход), pay_fixed (оклад в месяц). Здесь —
то, что ведёт салон руками:

  master_shifts        — выход мастера на день (отмечает администратор в журнале записи; сам не ставится);
  payroll_adjustments  — аванс / штраф / премия: дата, сумма, комментарий (правил и автоматики нет);
  payroll_payouts      — выплата за период (наличными — изъятием в смене кассира, или переводом), снимок строки
                         ведомости на момент выплаты; idem_key — одно нажатие = одна выплата.

Расчёт ведомости — services/payroll.py.
"""

import uuid

from sqlalchemy import Column, Date, DateTime, Float, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from .base import Base

ADJUSTMENT_KINDS = ("advance", "fine", "bonus")
PAYOUT_METHODS = ("cash", "transfer")


class MasterShift(Base):
    __tablename__ = "master_shifts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    resource_id = Column(UUID(as_uuid=True), ForeignKey("booking_resources.id", ondelete="CASCADE"), nullable=False)
    day = Column(Date, nullable=False)                          # дата Бишкека
    marked_by_name = Column(String(120), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ux_master_shifts_day", "resource_id", "day", unique=True),
        Index("ix_master_shifts_user_day", "user_id", "day"),
    )


class PayrollAdjustment(Base):
    __tablename__ = "payroll_adjustments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    resource_id = Column(UUID(as_uuid=True), ForeignKey("booking_resources.id", ondelete="CASCADE"), nullable=False)
    kind = Column(String(12), nullable=False)                   # advance | fine | bonus
    day = Column(Date, nullable=False)
    amount = Column(Float, nullable=False)                      # > 0; знак даёт вид
    comment = Column(String(300), nullable=True)
    created_by_name = Column(String(120), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (Index("ix_payroll_adjustments_master_day", "resource_id", "day"),)


class PayrollPayout(Base):
    __tablename__ = "payroll_payouts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    resource_id = Column(UUID(as_uuid=True), ForeignKey("booking_resources.id", ondelete="CASCADE"), nullable=False)
    date_from = Column(Date, nullable=False)
    date_to = Column(Date, nullable=False)
    amount = Column(Float, nullable=False)
    method = Column(String(12), nullable=False)                 # cash | transfer
    receipt_id = Column(UUID(as_uuid=True), nullable=True)      # чек изъятия из кассы (наличными)
    comment = Column(String(300), nullable=True)
    idem_key = Column(String(80), nullable=False)
    snapshot = Column(JSONB, nullable=True)                     # строка ведомости на момент выплаты
    created_by_name = Column(String(120), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ux_payroll_payouts_idem", "user_id", "idem_key", unique=True),
        Index("ix_payroll_payouts_master", "resource_id", "date_from"),
    )
