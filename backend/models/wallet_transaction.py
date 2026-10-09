"""
WalletTransaction — история кошелька в сомах (минуты голосовых моделей).

Каждое изменение users.wallet_balance пишется сюда: пополнение через Finik,
подарок на тест, начисление/корректировка админом, списание за разговор.
Суммы в тыйынах (1 сом = 100 тыйын): amount > 0 — зачисление, < 0 — списание.
"""

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from .base import Base


class WalletTransactionType:
    TOPUP = "topup"          # пополнение через Finik
    BONUS = "bonus"          # подарок на тест
    ADMIN = "admin"          # начисление / корректировка админом
    CHARGE = "charge"        # минуты голосовой модели


class WalletTransaction(Base):
    __tablename__ = "wallet_transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    type = Column(String(20), nullable=False)
    amount = Column(Integer, nullable=False)          # тыйын, со знаком
    balance_after = Column(Integer, nullable=False)   # тыйын
    # Списание за разговор: движок (eleven | gpt_live), секунды, сессия диалога, ассистент, канал
    engine = Column(String(20), nullable=True)
    seconds = Column(Integer, nullable=True)
    session_id = Column(String(100), nullable=True)
    assistant_id = Column(String(64), nullable=True)
    channel = Column(String(20), nullable=True)
    payment_transaction_id = Column(UUID(as_uuid=True), nullable=True)
    admin_id = Column(UUID(as_uuid=True), nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (Index("ix_wallet_tx_user_created", "user_id", "created_at"),)

    def to_dict(self):
        return {
            "id": str(self.id),
            "type": self.type,
            "amount": round(self.amount / 100, 2),
            "balance_after": round(self.balance_after / 100, 2),
            "engine": self.engine,
            "seconds": self.seconds,
            "session_id": self.session_id,
            "assistant_id": self.assistant_id,
            "channel": self.channel,
            "note": self.note,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
