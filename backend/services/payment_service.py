# backend/services/payment_service.py

"""
Payment processing service for WellcomeAI application.

Платёжный провайдер — Finik (finik.kg), QR-эквайринг Кыргызстана, валюта KGS.
Создание платежа — backend/services/finik_service.py (подпись RSA-SHA256,
302 → Location). Здесь — бизнес-логика ПОСЛЕ успешной оплаты: webhook Finik
приходит только при успехе, этот сервис зачисляет покупку по типу платежа.

Тип платежа хранится в PaymentTransaction.payment_details (JSON):
    {"type": "subscription", "plan_code": "start", "duration_months": 6, "days": 180}
    {"type": "agent_subscription"}
    {"type": "credits_package", "package_code": "credits_mini"}
    {"type": "cascade_package", "package_code": "..."}

Идемпотентность обеспечивается на два уровня выше:
  1) уникальный индекс payment_transactions.finik_transaction_id;
  2) проверка transaction.is_processed перед зачислением.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from backend.core.logging import get_logger
from backend.models.user import User
from backend.models.subscription import SubscriptionPlan, PaymentTransaction
from backend.models.credit_package import CreditPackage
from backend.services.subscription_service import SubscriptionService

logger = get_logger(__name__)


# =============================================================================
# Периоды подписки (длительность в днях; цены задаются в БД subscription_plans)
# =============================================================================

SUBSCRIPTION_PERIOD_DAYS = {
    1: 30,
    6: 180,
    12: 365,
}


def get_subscription_days_by_duration(duration_months: int) -> int:
    """Количество дней подписки по периоду (1/6/12 месяцев)."""
    if duration_months in SUBSCRIPTION_PERIOD_DAYS:
        return SUBSCRIPTION_PERIOD_DAYS[duration_months]
    logger.warning(f"⚠️ Unknown duration {duration_months}, using default 30 days")
    return 30


def parse_payment_details(transaction: PaymentTransaction) -> Dict[str, Any]:
    """payment_details хранится как JSON; при ошибке парсинга — пустой dict."""
    if not transaction.payment_details:
        return {}
    try:
        data = json.loads(transaction.payment_details)
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


class FinikPaymentService:
    """Зачисление успешных платежей Finik + партнёрские комиссии."""

    @classmethod
    async def apply_successful_payment(
        cls,
        db: Session,
        transaction: PaymentTransaction,
    ) -> bool:
        """
        Обработать УСПЕШНЫЙ платёж (вызывается из webhook'а Finik после
        проверки подписи, идемпотентности и сверки суммы).

        Возвращает True, если зачисление выполнено (или уже было выполнено).
        """
        if transaction.is_processed:
            logger.info(f"ℹ️ [FINIK] Payment {transaction.external_payment_id} already processed, skipping")
            return True

        user = db.query(User).filter(User.id == transaction.user_id).first()
        if not user:
            logger.error(f"❌ [FINIK] User {transaction.user_id} not found for payment {transaction.external_payment_id}")
            return False

        details = parse_payment_details(transaction)
        payment_type = details.get("type", "subscription")

        logger.info(
            f"💰 [FINIK] Applying payment {transaction.external_payment_id}: "
            f"type={payment_type}, amount={transaction.amount} KGS, user={user.id}"
        )

        if payment_type == "credits_package":
            return await cls._apply_credits_package(db, user, transaction, details)
        if payment_type == "cascade_package":
            return await cls._apply_cascade_package(db, user, transaction, details)
        if payment_type == "agent_subscription":
            return await cls._apply_agent_subscription(db, user, transaction)
        return await cls._apply_subscription(db, user, transaction, details)

    # -------------------------------------------------------------------------
    # Пакеты кредитов оркестратора
    # -------------------------------------------------------------------------

    @classmethod
    async def _apply_credits_package(
        cls,
        db: Session,
        user: User,
        transaction: PaymentTransaction,
        details: Dict[str, Any],
    ) -> bool:
        from backend.services.credit_service import CreditService

        package_code = details.get("package_code")
        package = db.query(CreditPackage).filter(
            CreditPackage.code == package_code,
            CreditPackage.product == "orchestrator",
        ).first()
        if not package:
            logger.error(f"❌ [FINIK] Credits package {package_code} not found for payment {transaction.external_payment_id}")
            return False

        CreditService.grant_purchase(db, user, package, transaction)
        cls._mark_processed(transaction)
        db.commit()

        logger.info(f"✅ [FINIK] Credits package {package.code} (+{package.credits}) granted to user {user.id}")
        return True

    # -------------------------------------------------------------------------
    # Пакеты кредитов каскада
    # -------------------------------------------------------------------------

    @classmethod
    async def _apply_cascade_package(
        cls,
        db: Session,
        user: User,
        transaction: PaymentTransaction,
        details: Dict[str, Any],
    ) -> bool:
        from backend.services.cascade_credit_service import CascadeCreditService

        package_code = details.get("package_code")
        package = db.query(CreditPackage).filter(
            CreditPackage.code == package_code,
            CreditPackage.product == "cascade",
        ).first()
        if not package:
            logger.error(f"❌ [FINIK] Cascade package {package_code} not found for payment {transaction.external_payment_id}")
            return False

        CascadeCreditService.grant_purchase(db, user, package, transaction)
        cls._mark_processed(transaction)
        db.commit()

        logger.info(f"✅ [FINIK] Cascade package {package.code} (+{package.credits}) granted to user {user.id}")
        return True

    # -------------------------------------------------------------------------
    # Подписка agent: 30 дней + 20 000 кредитов
    # -------------------------------------------------------------------------

    @classmethod
    async def _apply_agent_subscription(
        cls,
        db: Session,
        user: User,
        transaction: PaymentTransaction,
    ) -> bool:
        from backend.services.credit_service import CreditService

        plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.code == "agent").first()
        if not plan:
            logger.error(f"❌ [FINIK] Agent plan not found for payment {transaction.external_payment_id}")
            return False

        now = datetime.now(timezone.utc)

        # Продлеваем от текущего окончания, если подписка ещё активна
        start_date = now
        if user.subscription_end_date:
            end_date = user.subscription_end_date
            if end_date.tzinfo is None:
                end_date = end_date.replace(tzinfo=timezone.utc)
            if end_date > now:
                start_date = end_date

        user.subscription_plan_id = plan.id
        user.subscription_start_date = user.subscription_start_date or now
        user.subscription_end_date = start_date + timedelta(days=30)
        user.is_trial = False
        user.agent_subscription_blocked = False  # снятие блокировки после оплаты

        CreditService.grant_subscription(db, user, transaction)
        cls._mark_processed(transaction)
        db.commit()

        logger.info(
            f"✅ [FINIK] Agent subscription activated for user {user.id} until "
            f"{user.subscription_end_date}, +20000 credits"
        )

        await cls._process_partner_commission(db, user, transaction)
        return True

    # -------------------------------------------------------------------------
    # Обычные тарифы (ai_voice / start / profi)
    # -------------------------------------------------------------------------

    @classmethod
    async def _apply_subscription(
        cls,
        db: Session,
        user: User,
        transaction: PaymentTransaction,
        details: Dict[str, Any],
    ) -> bool:
        plan_code = details.get("plan_code", "start")
        try:
            duration_months = int(details.get("duration_months", 1))
        except (ValueError, TypeError):
            duration_months = 1
        subscription_days = int(details.get("days") or get_subscription_days_by_duration(duration_months))

        plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.code == plan_code).first()
        if not plan:
            logger.error(f"❌ [FINIK] Plan {plan_code} not found for payment {transaction.external_payment_id}")
            return False

        now = datetime.now(timezone.utc)

        # Если подписка ещё активна — продлеваем от её окончания
        start_date = now
        if user.subscription_end_date:
            end_date = user.subscription_end_date
            if end_date.tzinfo is None:
                end_date = end_date.replace(tzinfo=timezone.utc)
            if end_date > now:
                start_date = end_date
                logger.info(f"📅 [FINIK] Extending subscription from {end_date}")

        user.subscription_start_date = start_date
        user.subscription_end_date = start_date + timedelta(days=subscription_days)
        user.subscription_plan_id = plan.id
        user.is_trial = False

        cls._mark_processed(transaction)
        db.commit()

        # Тариф profi даёт доступ к агенту-оркестратору и месячный пакет
        # кредитов (идемпотентно по payment_transaction)
        if plan_code == "profi":
            try:
                from backend.services.credit_service import CreditService
                CreditService.grant_subscription(
                    db, user, transaction,
                    notes="Profi plan monthly agent credits",
                )
                db.refresh(user)
            except Exception as credit_error:
                logger.error(f"❌ [FINIK] Failed to grant profi agent credits: {credit_error}", exc_info=True)

        logger.info(
            f"✅ [FINIK] Subscription {plan_code} activated for user {user.id}: "
            f"{duration_months} months ({subscription_days} days), until {user.subscription_end_date}"
        )

        await cls._process_partner_commission(db, user, transaction)

        # Логируем успешную оплату
        try:
            await SubscriptionService.log_subscription_event(
                db=db,
                user_id=str(user.id),
                action="payment_success",
                plan_id=str(plan.id),
                plan_code=plan_code,
                details=(
                    f"Finik payment processed. "
                    f"PaymentId: {transaction.external_payment_id}, "
                    f"Amount: {transaction.amount} KGS, "
                    f"Duration: {duration_months} months ({subscription_days} days), "
                    f"Subscription until: {user.subscription_end_date.strftime('%Y-%m-%d')}"
                )
            )
        except Exception as log_error:
            logger.error(f"❌ [FINIK] Failed to log subscription event: {log_error}")

        return True

    # -------------------------------------------------------------------------
    # Общие помощники
    # -------------------------------------------------------------------------

    @staticmethod
    def _mark_processed(transaction: PaymentTransaction) -> None:
        now = datetime.now(timezone.utc)
        transaction.status = "success"
        transaction.is_processed = True
        transaction.paid_at = now
        transaction.processed_at = now

    @classmethod
    async def _process_partner_commission(
        cls,
        db: Session,
        user: User,
        transaction: PaymentTransaction,
    ) -> None:
        """Партнёрская комиссия — ошибки не прерывают основной процесс."""
        try:
            from backend.services.partner_service import PartnerService
            await PartnerService.process_referral_payment(
                db=db,
                user_id=str(user.id),
                transaction=transaction,
                amount=float(transaction.amount),
            )
            logger.info(f"✅ [FINIK] Partner commission processed for payment {transaction.external_payment_id}")
        except ImportError:
            logger.warning("⚠️ PartnerService not available, skipping commission processing")
        except Exception as partner_error:
            logger.error(f"❌ Error processing partner commission: {partner_error}")

    @staticmethod
    def get_payment_status_message(success: bool = True) -> Dict[str, Any]:
        """Сообщение о статусе платежа для страницы возврата (RedirectUrl)."""
        if success:
            return {
                "success": True,
                "title": "Спасибо за оплату!",
                "message": (
                    "Платёж обрабатывается. Подписка активируется автоматически "
                    "в течение минуты после подтверждения оплаты банком."
                ),
                "redirect_url": "/static/dashboard.html"
            }
        return {
            "success": False,
            "title": "Оплата отменена",
            "message": "Платёж не был завершён. Вы можете попробовать ещё раз.",
            "redirect_url": "/static/dashboard.html"
        }
