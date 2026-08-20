# backend/services/partner_service.py
"""
Partner service for WellcomeAI application.
✅ УЛУЧШЕННАЯ ВЕРСИЯ v2.0 с детальным логированием для диагностики
"""

import secrets
import string
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from decimal import Decimal
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.core.logging import get_logger
from backend.core.config import settings
from backend.models.user import User
from backend.models.partner import Partner, ReferralRelationship, PartnerCommission
from backend.models.subscription import PaymentTransaction
from backend.services.subscription_service import SubscriptionService

logger = get_logger(__name__)

class PartnerService:
    """Сервис для работы с партнерской программой"""
    
    COMMISSION_RATE = 30.00  # 30% комиссии
    
    @staticmethod
    async def activate_partnership(db: Session, user_id: str) -> Dict[str, Any]:
        """
        Активация партнерства для пользователя
        """
        try:
            # Проверяем существование пользователя
            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                raise HTTPException(404, "User not found")
            
            # Проверяем, уже ли партнер
            existing_partner = db.query(Partner).filter(Partner.user_id == user.id).first()
            if existing_partner:
                return {
                    "success": True,
                    "already_partner": True,
                    "referral_code": existing_partner.referral_code,
                    "referral_link": PartnerService._generate_referral_link(existing_partner.referral_code),
                    "commission_rate": float(existing_partner.commission_rate)
                }
            
            # Генерируем уникальный код
            referral_code = PartnerService._generate_unique_referral_code(db)
            
            # Создаем партнера
            partner = Partner(
                user_id=user.id,
                referral_code=referral_code,
                commission_rate=PartnerService.COMMISSION_RATE,
                is_active=True
            )
            
            db.add(partner)
            db.commit()
            db.refresh(partner)
            
            logger.info(f"✅ Partnership activated for user {user_id} with code {referral_code}")
            
            return {
                "success": True,
                "referral_code": referral_code,
                "referral_link": PartnerService._generate_referral_link(referral_code),
                "commission_rate": float(partner.commission_rate),
                "activated_at": partner.activated_at
            }
            
        except Exception as e:
            db.rollback()
            logger.error(f"❌ Error activating partnership: {str(e)}")
            raise HTTPException(500, f"Failed to activate partnership: {str(e)}")
    
    @staticmethod
    def _generate_unique_referral_code(db: Session) -> str:
        """Генерация уникального реферального кода"""
        max_attempts = 100
        
        for _ in range(max_attempts):
            # Генерируем код: 2 буквы + 6 цифр
            letters = ''.join(secrets.choice(string.ascii_uppercase) for _ in range(2))
            numbers = ''.join(secrets.choice(string.digits) for _ in range(6))
            code = letters + numbers
            
            # Проверяем уникальность
            existing = db.query(Partner).filter(Partner.referral_code == code).first()
            if not existing:
                return code
        
        raise Exception("Failed to generate unique referral code")
    
    @staticmethod
    def _generate_referral_link(referral_code: str) -> str:
        """
        Генерация полной реферальной ссылки с UTM метками
        ✅ ИСПРАВЛЕНО: Ссылка на главную страницу вместо /register
        """
        base_url = settings.HOST_URL.rstrip('/')
        utm_params = [
            f"utm_source=partner",
            f"utm_medium=referral", 
            f"utm_campaign={referral_code}",
            f"utm_content=registration"
        ]
        return f"{base_url}/?{'&'.join(utm_params)}"
    
    @staticmethod
    async def process_referral_registration(
        db: Session, 
        new_user_id: str, 
        referral_code: str,
        utm_data: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Обработка регистрации по реферальной ссылке
        ✅ ОБНОВЛЕНО: Возвращает dict с информацией о партнере
        """
        try:
            # Находим партнера по коду
            partner = db.query(Partner).filter(
                Partner.referral_code == referral_code.upper(),
                Partner.is_active == True
            ).first()
            
            if not partner:
                logger.warning(f"❌ Invalid referral code: {referral_code}")
                return {
                    "success": False,
                    "message": "Invalid referral code"
                }
            
            # Проверяем, что пользователь еще не привязан
            existing_relationship = db.query(ReferralRelationship).filter(
                ReferralRelationship.referral_user_id == new_user_id
            ).first()
            
            if existing_relationship:
                logger.warning(f"❌ User {new_user_id} already has referral relationship")
                return {
                    "success": False,
                    "message": "User already has referral relationship"
                }
            
            # Создаем связь реферер-реферал
            relationship = ReferralRelationship(
                partner_id=partner.id,
                referral_user_id=new_user_id,
                utm_source=utm_data.get('utm_source') if utm_data else 'partner',
                utm_medium=utm_data.get('utm_medium') if utm_data else 'referral',
                utm_campaign=utm_data.get('utm_campaign') if utm_data else referral_code,
                utm_content=utm_data.get('utm_content') if utm_data else 'registration'
            )
            
            # Обновляем счетчик рефералов
            partner.total_referrals += 1
            
            db.add(relationship)
            db.commit()
            
            logger.info(f"✅ Referral relationship created: partner {partner.id} -> user {new_user_id}")
            
            # Получаем информацию о пользователе-партнере
            partner_user = db.query(User).filter(User.id == partner.user_id).first()
            
            return {
                "success": True,
                "message": "Referral registration processed successfully",
                "referrer_info": {
                    "name": f"{partner_user.first_name or ''} {partner_user.last_name or ''}".strip() or "Партнер",
                    "email": partner_user.email
                } if partner_user else None,
                "commission_rate": float(partner.commission_rate)
            }
            
        except Exception as e:
            db.rollback()
            logger.error(f"❌ Error processing referral registration: {str(e)}")
            return {
                "success": False,
                "message": f"Error processing referral: {str(e)}"
            }
    
    @staticmethod
    async def process_referral_payment(
        db: Session,
        user_id: str,
        transaction: PaymentTransaction,
        amount: float
    ):
        """
        Обработка платежа реферала и начисление комиссии
        ✅ УЛУЧШЕННАЯ ВЕРСИЯ v2.0 с детальным логированием
        """
        logger.info("="*80)
        logger.info(f"🚀 STARTING REFERRAL PAYMENT PROCESSING")
        logger.info(f"   user_id: {user_id}")
        logger.info(f"   transaction_id: {transaction.id if transaction else 'None'}")
        logger.info(f"   amount: {amount}")
        logger.info("="*80)
        
        try:
            # ШАГ 1: Проверка входных данных
            logger.info(f"📋 STEP 1: Validating input parameters")
            
            if not user_id:
                logger.error(f"❌ STEP 1 FAILED: user_id is empty!")
                return
            
            if not transaction:
                logger.error(f"❌ STEP 1 FAILED: transaction is None!")
                return
            
            if not amount or amount <= 0:
                logger.error(f"❌ STEP 1 FAILED: invalid amount {amount}")
                return
            
            logger.info(f"✅ STEP 1 PASSED: All input parameters are valid")
            
            # ШАГ 2: Поиск реферальной связи
            logger.info(f"📋 STEP 2: Searching for referral relationship")
            logger.info(f"   Querying: ReferralRelationship.referral_user_id = {user_id}")
            
            referral_relationship = db.query(ReferralRelationship).filter(
                ReferralRelationship.referral_user_id == user_id,
                ReferralRelationship.is_active == True
            ).first()
            
            if not referral_relationship:
                logger.warning(f"⚠️ STEP 2: No referral relationship found for user {user_id}")
                logger.info(f"   This user was not referred by anyone - skipping commission")
                return
            
            logger.info(f"✅ STEP 2 PASSED: Referral relationship found!")
            logger.info(f"   relationship_id: {referral_relationship.id}")
            logger.info(f"   partner_id: {referral_relationship.partner_id}")
            logger.info(f"   utm_campaign: {referral_relationship.utm_campaign}")
            logger.info(f"   first_payment_made: {referral_relationship.first_payment_made}")
            
            # ШАГ 3: Получение партнера
            logger.info(f"📋 STEP 3: Getting partner information")
            
            partner = referral_relationship.partner
            
            if not partner:
                logger.error(f"❌ STEP 3 FAILED: Partner not found for relationship {referral_relationship.id}")
                return
            
            logger.info(f"✅ STEP 3 PASSED: Partner found!")
            logger.info(f"   partner_id: {partner.id}")
            logger.info(f"   partner_user_id: {partner.user_id}")
            logger.info(f"   referral_code: {partner.referral_code}")
            logger.info(f"   commission_rate: {partner.commission_rate}%")
            logger.info(f"   is_active: {partner.is_active}")
            
            if not partner.is_active:
                logger.warning(f"⚠️ STEP 3: Partner {partner.id} is not active - skipping commission")
                return
            
            # ШАГ 4: Проверка дублирования комиссии
            logger.info(f"📋 STEP 4: Checking for duplicate commission")
            logger.info(f"   Querying: PartnerCommission.payment_transaction_id = {transaction.id}")
            
            existing_commission = db.query(PartnerCommission).filter(
                PartnerCommission.payment_transaction_id == transaction.id
            ).first()
            
            if existing_commission:
                logger.warning(f"⚠️ STEP 4: Commission already exists for transaction {transaction.id}")
                logger.info(f"   Existing commission_id: {existing_commission.id}")
                logger.info(f"   Existing commission_amount: {existing_commission.commission_amount}")
                return
            
            logger.info(f"✅ STEP 4 PASSED: No duplicate commission found")
            
            # ШАГ 5: Вычисление комиссии
            logger.info(f"📋 STEP 5: Calculating commission")
            logger.info(f"   Formula: {amount} * {partner.commission_rate}% / 100")
            
            # ✅ ИСПРАВЛЕНО: Используем Decimal для корректных вычислений
            amount_decimal = Decimal(str(amount))
            commission_amount = (amount_decimal * partner.commission_rate) / Decimal('100')
            
            logger.info(f"✅ STEP 5 PASSED: Commission calculated")
            logger.info(f"   Original amount: {amount}₽")
            logger.info(f"   Commission rate: {partner.commission_rate}%")
            logger.info(f"   Commission amount: {commission_amount}₽")
            
            # ШАГ 6: Создание записи о комиссии
            logger.info(f"📋 STEP 6: Creating commission record")
            
            now = datetime.now(timezone.utc)
            
            # ✅ ИСПРАВЛЕНО: Используем Decimal для всех денежных значений
            commission = PartnerCommission(
                partner_id=partner.id,
                referral_relationship_id=referral_relationship.id,
                payment_transaction_id=transaction.id,
                original_amount=amount_decimal,  # ✅ Decimal вместо float
                commission_rate=partner.commission_rate,
                commission_amount=commission_amount,  # ✅ Уже Decimal
                status="confirmed",
                confirmed_at=now
            )
            
            logger.info(f"   Commission object created:")
            logger.info(f"   - partner_id: {commission.partner_id}")
            logger.info(f"   - referral_relationship_id: {commission.referral_relationship_id}")
            logger.info(f"   - payment_transaction_id: {commission.payment_transaction_id}")
            logger.info(f"   - original_amount: {commission.original_amount}")
            logger.info(f"   - commission_rate: {commission.commission_rate}")
            logger.info(f"   - commission_amount: {commission.commission_amount}")
            logger.info(f"   - status: {commission.status}")
            
            # ШАГ 7: Обновление статистики партнера
            logger.info(f"📋 STEP 7: Updating partner statistics")
            logger.info(f"   Current total_earnings: {partner.total_earnings}")
            logger.info(f"   Adding: {commission_amount}")
            
            # ✅ ИСПРАВЛЕНО: Decimal += Decimal (без ошибок типов)
            partner.total_earnings += commission_amount
            
            logger.info(f"   New total_earnings: {partner.total_earnings}")
            
            # ШАГ 8: Отметка первого платежа реферала
            logger.info(f"📋 STEP 8: Updating first payment flag")
            
            if not referral_relationship.first_payment_made:
                logger.info(f"   Setting first_payment_made = True")
                logger.info(f"   Setting first_payment_at = {now}")
                
                referral_relationship.first_payment_made = True
                referral_relationship.first_payment_at = now
            else:
                logger.info(f"   First payment already made on {referral_relationship.first_payment_at}")
            
            # ШАГ 9: Сохранение в БД
            logger.info(f"📋 STEP 9: Saving to database")
            logger.info(f"   Adding commission to session...")
            
            db.add(commission)
            
            logger.info(f"   Calling db.commit()...")
            db.commit()
            
            logger.info(f"✅ STEP 9 PASSED: Successfully committed to database!")
            
            # ШАГ 10: Логирование события
            logger.info(f"📋 STEP 10: Logging subscription event")
            
            await SubscriptionService.log_subscription_event(
                db=db,
                user_id=str(partner.user_id),
                action="partner_commission_earned",
                plan_id=None,
                plan_code="commission",
                details=f"Earned {float(commission_amount):.2f}₽ commission from referral payment {float(amount_decimal):.2f}₽ (transaction: {transaction.id})"
            )
            
            logger.info(f"✅ STEP 10 PASSED: Event logged")
            
            # ФИНАЛ
            logger.info("="*80)
            logger.info(f"🎉 REFERRAL PAYMENT PROCESSING COMPLETED SUCCESSFULLY!")
            logger.info(f"   Commission ID: {commission.id}")
            logger.info(f"   Partner earned: {float(commission_amount):.2f}₽")
            logger.info(f"   Partner total earnings: {float(partner.total_earnings):.2f}₽")
            logger.info("="*80)
            
        except Exception as e:
            logger.error("="*80)
            logger.error(f"💥 CRITICAL ERROR IN REFERRAL PAYMENT PROCESSING!")
            logger.error(f"   Error type: {type(e).__name__}")
            logger.error(f"   Error message: {str(e)}")
            logger.error(f"   User ID: {user_id}")
            logger.error(f"   Transaction ID: {transaction.id if transaction else 'None'}")
            logger.error(f"   Amount: {amount}")
            
            # Детальная трассировка
            import traceback
            logger.error(f"   Full traceback:")
            logger.error(traceback.format_exc())
            
            logger.error("="*80)
            
            # Откатываем транзакцию
            db.rollback()
            
            # Пробрасываем ошибку дальше
            raise
    
    @staticmethod
    async def get_partner_stats(db: Session, user_id: str) -> Dict[str, Any]:
        """
        Получение статистики партнера
        """
        try:
            # Получаем партнера
            partner = db.query(Partner).filter(Partner.user_id == user_id).first()
            if not partner:
                raise HTTPException(404, "Partnership not found")
            
            # Базовая статистика
            total_referrals = db.query(ReferralRelationship).filter(
                ReferralRelationship.partner_id == partner.id,
                ReferralRelationship.is_active == True
            ).count()
            
            # Статистика по платежам
            paid_referrals = db.query(ReferralRelationship).filter(
                ReferralRelationship.partner_id == partner.id,
                ReferralRelationship.first_payment_made == True
            ).count()
            
            # Общие заработки
            total_earnings = db.query(func.sum(PartnerCommission.commission_amount)).filter(
                PartnerCommission.partner_id == partner.id,
                PartnerCommission.status.in_(["confirmed", "paid"])
            ).scalar() or 0
            
            # Заработки в этом месяце
            from datetime import datetime
            current_month_start = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            this_month_earnings = db.query(func.sum(PartnerCommission.commission_amount)).filter(
                PartnerCommission.partner_id == partner.id,
                PartnerCommission.earned_at >= current_month_start,
                PartnerCommission.status.in_(["confirmed", "paid"])
            ).scalar() or 0
            
            # Последние комиссии
            recent_commissions = db.query(PartnerCommission).filter(
                PartnerCommission.partner_id == partner.id
            ).order_by(PartnerCommission.earned_at.desc()).limit(10).all()
            
            return {
                "partner_info": {
                    "referral_code": partner.referral_code,
                    "referral_link": PartnerService._generate_referral_link(partner.referral_code),
                    "commission_rate": float(partner.commission_rate),
                    "is_active": partner.is_active,
                    "activated_at": partner.activated_at
                },
                "statistics": {
                    "total_referrals": total_referrals,
                    "paid_referrals": paid_referrals,
                    "conversion_rate": round((paid_referrals / total_referrals * 100) if total_referrals > 0 else 0, 2),
                    "total_earnings": float(total_earnings),
                    "this_month_earnings": float(this_month_earnings)
                },
                "recent_commissions": [
                    {
                        "id": str(comm.id),
                        "amount": float(comm.commission_amount),
                        "original_payment": float(comm.original_amount),
                        "status": comm.status,
                        "earned_at": comm.earned_at,
                        "referral_user": {
                            "email": comm.referral_relationship.referral_user.email
                        } if comm.referral_relationship and comm.referral_relationship.referral_user else None
                    }
                    for comm in recent_commissions
                ]
            }
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"❌ Error getting partner stats: {str(e)}")
            raise HTTPException(500, f"Failed to get partner stats: {str(e)}")
