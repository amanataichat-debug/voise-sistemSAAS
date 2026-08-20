"""
CreditPackage model — пакеты докупки кредитов оркестратора.

Таблица создана и засеяна вручную через SQL (см. ТЗ раздел 2.3), миграция не нужна.
"""

import uuid
from sqlalchemy import Column, String, Integer, Numeric, Boolean, Text, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from .base import Base, BaseModel


class CreditPackage(Base, BaseModel):
    __tablename__ = "credit_packages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(50), unique=True, nullable=False, index=True)
    # Продукт, к которому относится пакет: 'orchestrator' | 'cascade'.
    # Колонка добавляется startup-ALTER (см. app.py).
    product = Column(String(20), default="orchestrator", nullable=False)
    name = Column(String(100), nullable=False)
    credits = Column(Integer, nullable=False)
    price_rub = Column(Numeric(10, 2), nullable=False)
    sort_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<CreditPackage {self.code} ({self.credits} credits)>"

    def to_dict(self):
        return {
            "code": self.code,
            "product": self.product or "orchestrator",
            "name": self.name,
            "credits": self.credits,
            "price_rub": float(self.price_rub),
            "price_formatted": f"{int(self.price_rub):,} ₽".replace(",", " "),
            "description": self.description,
            "sort_order": self.sort_order,
        }
