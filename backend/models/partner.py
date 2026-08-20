# backend/models/partner.py - НОВЫЙ файл
"""
Partner models for WellcomeAI application.
Партнерская система без изменения существующих таблиц.
"""

import uuid
from sqlalchemy import Column, String, Boolean, Numeric, DateTime, ForeignKey, Text, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from .base import Base, BaseModel

class Partner(Base, BaseModel):
    """
    Модель партнера - отдельная таблица для партнерской информации
    """
    __tablename__ = "partners"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    
    # Партнерская информация
    referral_code = Column(String(10), unique=True, nullable=False, index=True)
    commission_rate = Column(Numeric(5, 2), default=30.00)  # 30%
    is_active = Column(Boolean, default=True)
    
    # Статистика
    total_referrals = Column(Integer, default=0)
    total_earnings = Column(Numeric(10, 2), default=0.00)
    
    # Даты
    activated_at = Column(DateTime(timezone=True), server_default=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Отношения
    user = relationship("User", backref="partner_profile")

class ReferralRelationship(Base, BaseModel):
    """
    Модель связи реферер-реферал
    """
    __tablename__ = "referral_relationships"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Кто привел (партнер)
    partner_id = Column(UUID(as_uuid=True), ForeignKey("partners.id", ondelete="CASCADE"), nullable=False)
    
    # Кого привели (новый пользователь)
    referral_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    
    # UTM данные
    utm_source = Column(String(50), nullable=True)
    utm_medium = Column(String(50), nullable=True)  
    utm_campaign = Column(String(50), nullable=True)  # referral_code
    utm_content = Column(String(100), nullable=True)
    
    # Статус
    is_active = Column(Boolean, default=True)
    first_payment_made = Column(Boolean, default=False)
    
    # Даты
    referred_at = Column(DateTime(timezone=True), server_default=func.now())
    first_payment_at = Column(DateTime(timezone=True), nullable=True)
    
    # Отношения
    partner = relationship("Partner", backref="referrals")
    referral_user = relationship("User", backref="referral_info")

class PartnerCommission(Base, BaseModel):
    """
    Модель партнерских комиссий
    """
    __tablename__ = "partner_commissions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Связи
    partner_id = Column(UUID(as_uuid=True), ForeignKey("partners.id", ondelete="CASCADE"), nullable=False)
    referral_relationship_id = Column(UUID(as_uuid=True), ForeignKey("referral_relationships.id"), nullable=False)
    payment_transaction_id = Column(UUID(as_uuid=True), ForeignKey("payment_transactions.id"), nullable=False)
    
    # Суммы
    original_amount = Column(Numeric(10, 2), nullable=False)
    commission_rate = Column(Numeric(5, 2), nullable=False)
    commission_amount = Column(Numeric(10, 2), nullable=False)
    
    # Статус
    status = Column(String(20), default="confirmed")  # pending, confirmed, paid, cancelled
    
    # Даты
    earned_at = Column(DateTime(timezone=True), server_default=func.now())
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    paid_at = Column(DateTime(timezone=True), nullable=True)
    
    # Отношения
    partner = relationship("Partner", backref="commissions")
    referral_relationship = relationship("ReferralRelationship", backref="commissions")
    payment_transaction = relationship("PaymentTransaction")

    def __repr__(self):
        return f"<PartnerCommission {self.commission_amount}₽ for partner {self.partner_id}>"
