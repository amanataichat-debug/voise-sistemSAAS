"""
CreditTransaction model — источник правды для биллинга кредитов оркестратора.

Каждое начисление/списание кредитов (trial, подписка, покупка пакета, расход
на вызов оркестратора, возврат, ручная коррекция) фиксируется здесь.
Таблица создана вручную через SQL (см. ТЗ раздел 2.2), миграция не нужна.
"""

import uuid
import enum
from sqlalchemy import Column, String, Integer, Text, DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .base import Base, BaseModel


class CreditTransactionType(str, enum.Enum):
    TRIAL_GRANT = "trial_grant"
    SUBSCRIPTION_GRANT = "subscription_grant"
    PURCHASE = "purchase"
    SPEND = "spend"
    REFUND = "refund"
    MANUAL_ADJUST = "manual_adjust"


class CreditTransaction(Base, BaseModel):
    __tablename__ = "credit_transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    # Дискриминатор продукта: 'orchestrator' (кредиты агента) | 'cascade'
    # (кредиты каскад-ассистентов). Разделяет два независимых кошелька в одной
    # таблице транзакций. Колонка добавляется startup-ALTER (см. app.py).
    product = Column(String(20), default="orchestrator", nullable=False)
    type = Column(String(30), nullable=False)
    amount = Column(Integer, nullable=False)
    balance_after = Column(Integer, nullable=False)
    model_slug = Column(String(100), nullable=True)
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    ref_type = Column(String(50), nullable=True)
    ref_id = Column(UUID(as_uuid=True), nullable=True)
    payment_transaction_id = Column(UUID(as_uuid=True),
                                    ForeignKey("payment_transactions.id"), nullable=True)
    credits_package_code = Column(String(50), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User")
    payment_transaction = relationship("PaymentTransaction")

    __table_args__ = (
        Index("idx_credit_tx_user_created", "user_id", "created_at"),
    )

    def __repr__(self):
        return f"<CreditTransaction {self.type} {self.amount} user={self.user_id}>"

    def to_dict(self):
        return {
            "id": str(self.id),
            "product": self.product or "orchestrator",
            "type": self.type,
            "amount": self.amount,
            "balance_after": self.balance_after,
            "model_slug": self.model_slug,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "ref_type": self.ref_type,
            "ref_id": str(self.ref_id) if self.ref_id else None,
            "payment_transaction_id": str(self.payment_transaction_id) if self.payment_transaction_id else None,
            "credits_package_code": self.credits_package_code,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
