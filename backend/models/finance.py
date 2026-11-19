"""
Доходы и расходы владельца (ветка 1811-niche-admin-speed, 19.11.2026): аренда, зарплаты, материалы,
реклама — чтобы видеть не только выручку, но и прибыль.

  BusinessExpense — расход: категория, сумма в сомах, дата. repeat="month" — ежемесячный (аренда,
                    оклад администратора): в отчёт за период попадает доля по дням (неделя аренды =
                    сумма × 7 / дней в месяце), начиная с даты starts_on и до ends_on (если задано).
                    repeat="once" — разовый (закупка материалов, реклама) — в день spent_on.

Зарплата мастеров считается сама: booking_resources.pay_percent (% с выручки его записей «пришёл»)
+ pay_fixed (оклад в месяц, доля по дням). Колонки добавляет app.ensure_finance_tables().

Логика — services/finance_service.py; API — api/finance.py (/api/finance/*, только владелец).
"""

import uuid

from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from .base import Base

EXPENSE_CATEGORIES = {
    "rent": "Аренда",
    "salary": "Зарплаты",
    "supplies": "Материалы и закупки",
    "ads": "Реклама",
    "utilities": "Коммунальные и связь",
    "taxes": "Налоги и банк",
    "other": "Другое",
}


class BusinessExpense(Base):
    __tablename__ = "business_expenses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    category = Column(String(20), nullable=False, default="other")
    title = Column(String(200), nullable=False)
    amount = Column(Float, nullable=False)                 # сом: разовый — сумма, ежемесячный — сумма в месяц
    repeat = Column(String(10), nullable=False, default="once")   # once | month
    spent_on = Column(Date, nullable=False)                # разовый — дата; ежемесячный — с какого дня
    ends_on = Column(Date, nullable=True)                  # ежемесячный — по какой день (пусто — бессрочно)
    resource_id = Column(UUID(as_uuid=True), ForeignKey("booking_resources.id", ondelete="SET NULL"), nullable=True)
    note = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (Index("ix_business_expenses_user_date", "user_id", "spent_on"),)
