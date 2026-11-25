# backend/models/subscription.py
"""
Subscription models for WellcomeAI application.
ИСПРАВЛЕННАЯ ВЕРСИЯ - убраны поля amount и payment_id из SubscriptionLog
"""

import uuid
from sqlalchemy import Column, String, Boolean, Numeric, Integer, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from .base import Base, BaseModel

class SubscriptionPlan(Base, BaseModel):
    """
    Model representing subscription plans.
    """
    __tablename__ = "subscription_plans"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(50), nullable=False)
    code = Column(String(20), nullable=False, unique=True)
    price = Column(Numeric(10, 2), nullable=False)
    max_assistants = Column(Integer, nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Каталог тарифов (ветка 2511-beauty, services/plan_catalog.py). Колонки добавляет
    # app.ensure_plan_catalog() в начале старта. Тарифы product="beauty" живут ТОЛЬКО в базе
    # (цену, лимиты и текст правит суперадмин), тарифы "voksy" — по-прежнему из
    # SUBSCRIPTION_PLANS_CONFIG (backend/api/payments.py).
    product = Column(String(20), nullable=False, default="voksy", server_default="voksy")
    # {"staff": 3, "agent_mode": "reactive", "credits_month": 0, "trial": false}
    limits = Column(JSONB, nullable=True)
    features = Column(JSONB, nullable=True)        # строки «что входит» для окна оплаты
    badge = Column(String(40), nullable=True)      # «Хит», «Выгодно»
    sort_order = Column(Integer, nullable=False, default=0, server_default="0")
    is_public = Column(Boolean, nullable=False, default=True, server_default="true")
    # Индивидуальный тариф одного клиента (без внешнего ключа: users уже ссылается сюда через
    # subscription_plan_id, взаимные ключи мешали бы порядку создания таблиц).
    for_user_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    admin_updated_at = Column(DateTime(timezone=True), nullable=True)

    def __repr__(self):
        """String representation of SubscriptionPlan"""
        return f"<SubscriptionPlan {self.name} (code={self.code})>"


class SubscriptionLog(Base, BaseModel):
    """
    Model representing subscription action logs.
    ИСПРАВЛЕНО: убраны поля amount и payment_id
    """
    __tablename__ = "subscription_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    action = Column(String(50), nullable=False)  # payment_success, trial_start, subscription_expired, etc.
    plan_id = Column(UUID(as_uuid=True), ForeignKey("subscription_plans.id"), nullable=True)
    plan_code = Column(String(20), nullable=True)
    # ✅ УБРАНЫ: amount и payment_id - вся информация сохраняется в details
    details = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    plan = relationship("SubscriptionPlan", foreign_keys=[plan_id])

    def __repr__(self):
        """String representation of SubscriptionLog"""
        return f"<SubscriptionLog {self.action} for user {self.user_id}>"


class PaymentTransaction(Base, BaseModel):
    """
    Model for tracking payment transactions.
    КРИТИЧЕСКИ ВАЖНО для отчетности и поддержки!
    """
    __tablename__ = "payment_transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    plan_id = Column(UUID(as_uuid=True), ForeignKey("subscription_plans.id"), nullable=True)
    
    # Данные от платежной системы
    external_payment_id = Column(String(100), nullable=True)  # наш PaymentId (UUID) для Finik; InvId у старых записей Robokassa
    payment_system = Column(String(50), default="finik")  # 'finik'; у исторических записей — 'robokassa'
    amount = Column(Numeric(10, 2), nullable=False)
    currency = Column(String(3), default="KGS")

    # Finik: transactionId из webhook'а (уникальный — идемпотентность) и URL платёжной страницы
    finik_transaction_id = Column(String(100), nullable=True, unique=True, index=True)
    payment_url = Column(String(1000), nullable=True)
    
    # Статус транзакции
    status = Column(String(20), default="pending")  # pending, success, failed, cancelled
    is_processed = Column(Boolean, default=False)
    
    # Даты
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    paid_at = Column(DateTime(timezone=True), nullable=True)
    processed_at = Column(DateTime(timezone=True), nullable=True)
    
    # Дополнительные данные
    payment_details = Column(String(500), nullable=True)  # JSON с деталями
    error_message = Column(String(500), nullable=True)

    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    plan = relationship("SubscriptionPlan", foreign_keys=[plan_id])

    def __repr__(self):
        """String representation of PaymentTransaction"""
        return f"<PaymentTransaction {self.external_payment_id} ({self.status})>"
