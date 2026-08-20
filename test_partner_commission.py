#!/usr/bin/env python3
"""
Тестовый скрипт для проверки начисления партнерских комиссий.
Симулирует оплату от реферала и выводит детальные логи.

Использование:
    python test_partner_commission.py
"""

import asyncio
import sys
import os
from datetime import datetime, timezone
from decimal import Decimal

# Добавляем корневую директорию в PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.db.session import SessionLocal
from backend.models.user import User
from backend.models.subscription import PaymentTransaction, SubscriptionPlan
from backend.models.partner import Partner, ReferralRelationship, PartnerCommission
from backend.services.partner_service import PartnerService
from backend.core.logging import get_logger

logger = get_logger(__name__)

async def test_partner_commission():
    """
    Тестирование начисления партнерской комиссии
    """
    print("\n" + "="*80)
    print("🧪 ТЕСТОВЫЙ СКРИПТ НАЧИСЛЕНИЯ ПАРТНЕРСКИХ КОМИССИЙ")
    print("="*80 + "\n")
    
    db = SessionLocal()
    
    try:
        # ШАГ 1: Находим тестового пользователя (реферала)
        print("📋 ШАГ 1: Поиск тестового пользователя...")
        
        test_user = db.query(User).filter(User.email == "19wellai96@gmail.com").first()
        
        if not test_user:
            print("❌ ОШИБКА: Пользователь 19wellai96@gmail.com не найден!")
            print("   Сначала создайте этого пользователя.")
            return
        
        print(f"✅ Пользователь найден:")
        print(f"   ID: {test_user.id}")
        print(f"   Email: {test_user.email}")
        print(f"   Created: {test_user.created_at}")
        
        # ШАГ 2: Проверяем реферальную связь
        print("\n📋 ШАГ 2: Проверка реферальной связи...")
        
        referral_relationship = db.query(ReferralRelationship).filter(
            ReferralRelationship.referral_user_id == test_user.id
        ).first()
        
        if not referral_relationship:
            print("❌ ОШИБКА: Реферальная связь не найдена!")
            print("   Этот пользователь не был зарегистрирован по реферальной ссылке.")
            print("   Создайте реферальную связь вручную или зарегистрируйте нового пользователя.")
            return
        
        print(f"✅ Реферальная связь найдена:")
        print(f"   Relationship ID: {referral_relationship.id}")
        print(f"   Partner ID: {referral_relationship.partner_id}")
        print(f"   UTM Campaign: {referral_relationship.utm_campaign}")
        print(f"   First Payment Made: {referral_relationship.first_payment_made}")
        
        # ШАГ 3: Получаем партнера
        print("\n📋 ШАГ 3: Получение информации о партнере...")
        
        partner = referral_relationship.partner
        
        if not partner:
            print("❌ ОШИБКА: Партнер не найден!")
            return
        
        partner_user = db.query(User).filter(User.id == partner.user_id).first()
        
        print(f"✅ Партнер найден:")
        print(f"   Partner ID: {partner.id}")
        print(f"   User ID: {partner.user_id}")
        print(f"   Email: {partner_user.email if partner_user else 'N/A'}")
        print(f"   Referral Code: {partner.referral_code}")
        print(f"   Commission Rate: {partner.commission_rate}%")
        print(f"   Total Earnings (before): {partner.total_earnings}₽")
        print(f"   Total Referrals: {partner.total_referrals}")
        
        # ШАГ 4: Получаем или создаем план подписки
        print("\n📋 ШАГ 4: Получение плана подписки...")
        
        plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.code == "start").first()
        
        if not plan:
            print("   План 'start' не найден, создаем...")
            plan = SubscriptionPlan(
                code="start",
                name="Тариф Старт",
                price=1490.00,
                max_assistants=3,
                description="Стартовый тариф",
                is_active=True
            )
            db.add(plan)
            db.flush()
            print(f"   ✅ План создан: {plan.id}")
        else:
            print(f"   ✅ План найден: {plan.id}")
        
        # ШАГ 5: Создаем тестовую транзакцию
        print("\n📋 ШАГ 5: Создание тестовой транзакции...")
        
        test_amount = 1.00  # Тестовая сумма 1 рубль
        test_inv_id = f"TEST_{int(datetime.now().timestamp())}"
        
        transaction = PaymentTransaction(
            user_id=test_user.id,
            plan_id=plan.id,
            external_payment_id=test_inv_id,
            payment_system="finik",
            amount=test_amount,
            currency="KGS",
            status="success",
            is_processed=True,
            paid_at=datetime.now(timezone.utc),
            payment_details="TEST PAYMENT - Simulated for commission testing"
        )
        
        db.add(transaction)
        db.commit()
        db.refresh(transaction)
        
        print(f"✅ Транзакция создана:")
        print(f"   Transaction ID: {transaction.id}")
        print(f"   External ID: {test_inv_id}")
        print(f"   Amount: {test_amount}₽")
        print(f"   Status: {transaction.status}")
        
        # ШАГ 6: Проверяем существующие комиссии для этой транзакции
        print("\n📋 ШАГ 6: Проверка существующих комиссий...")
        
        existing_commission = db.query(PartnerCommission).filter(
            PartnerCommission.payment_transaction_id == transaction.id
        ).first()
        
        if existing_commission:
            print(f"⚠️ Комиссия уже существует для этой транзакции:")
            print(f"   Commission ID: {existing_commission.id}")
            print(f"   Amount: {existing_commission.commission_amount}₽")
        else:
            print(f"✅ Дубликатов комиссий нет")
        
        # ШАГ 7: Запускаем обработку партнерской комиссии
        print("\n" + "="*80)
        print("🚀 ШАГ 7: ЗАПУСК ОБРАБОТКИ ПАРТНЕРСКОЙ КОМИССИИ")
        print("="*80 + "\n")
        
        # Вызываем метод с детальным логированием
        await PartnerService.process_referral_payment(
            db=db,
            user_id=str(test_user.id),
            transaction=transaction,
            amount=float(test_amount)
        )
        
        print("\n" + "="*80)
        print("✅ ОБРАБОТКА ЗАВЕРШЕНА")
        print("="*80 + "\n")
        
        # ШАГ 8: Проверяем результаты
        print("📋 ШАГ 8: Проверка результатов...\n")
        
        # Обновляем данные из БД
        db.refresh(partner)
        db.refresh(referral_relationship)
        
        # Проверяем созданную комиссию
        commission = db.query(PartnerCommission).filter(
            PartnerCommission.payment_transaction_id == transaction.id
        ).first()
        
        if commission:
            print("✅ КОМИССИЯ УСПЕШНО СОЗДАНА!")
            print(f"   Commission ID: {commission.id}")
            print(f"   Original Amount: {commission.original_amount}₽")
            print(f"   Commission Rate: {commission.commission_rate}%")
            print(f"   Commission Amount: {commission.commission_amount}₽")
            print(f"   Status: {commission.status}")
            print(f"   Earned At: {commission.earned_at}")
            print(f"   Confirmed At: {commission.confirmed_at}")
        else:
            print("❌ КОМИССИЯ НЕ СОЗДАНА!")
            print("   Проверьте логи выше для определения причины.")
        
        print(f"\n✅ ПАРТНЕР:")
        print(f"   Total Earnings (after): {partner.total_earnings}₽")
        print(f"   Change: +{float(partner.total_earnings) - float(partner.total_earnings or 0)}₽")
        
        print(f"\n✅ РЕФЕРАЛЬНАЯ СВЯЗЬ:")
        print(f"   First Payment Made: {referral_relationship.first_payment_made}")
        print(f"   First Payment At: {referral_relationship.first_payment_at}")
        
        # ШАГ 9: Показываем все комиссии партнера
        print("\n📋 ШАГ 9: Все комиссии партнера...")
        
        all_commissions = db.query(PartnerCommission).filter(
            PartnerCommission.partner_id == partner.id
        ).order_by(PartnerCommission.earned_at.desc()).all()
        
        if all_commissions:
            print(f"\n✅ Найдено комиссий: {len(all_commissions)}")
            for i, comm in enumerate(all_commissions, 1):
                print(f"\n   Комиссия #{i}:")
                print(f"      ID: {comm.id}")
                print(f"      Amount: {comm.commission_amount}₽")
                print(f"      From: {comm.original_amount}₽")
                print(f"      Status: {comm.status}")
                print(f"      Date: {comm.earned_at}")
        else:
            print("   ⚠️ Комиссий не найдено")
        
        print("\n" + "="*80)
        print("🎉 ТЕСТ ЗАВЕРШЕН")
        print("="*80 + "\n")
        
    except Exception as e:
        print("\n" + "="*80)
        print("💥 КРИТИЧЕСКАЯ ОШИБКА В ТЕСТЕ!")
        print("="*80)
        print(f"Error type: {type(e).__name__}")
        print(f"Error message: {str(e)}")
        
        import traceback
        print("\nFull traceback:")
        print(traceback.format_exc())
        
        db.rollback()
        
    finally:
        db.close()

if __name__ == "__main__":
    print("\n🚀 Запуск тестового скрипта...\n")
    asyncio.run(test_partner_commission())
    print("\n✅ Скрипт завершен.\n")
