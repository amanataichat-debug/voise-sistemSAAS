"""
CreditService — атомарный учёт кредитов оркестратора Voicyfy Agent.

Все мутации `users.credits_balance` идут строго через SELECT ... FOR UPDATE,
чтобы исключить гонки при параллельных запросах (см. ТЗ раздел 9, edge case 1).
Каждая операция фиксируется в `credit_transactions` — источнике правды для
биллинга, поддержки и аналитики.
"""

import math
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, List
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from backend.core.logging import get_logger
from backend.models.user import User
from backend.models.subscription import PaymentTransaction, SubscriptionPlan
from backend.models.credit_transaction import CreditTransaction, CreditTransactionType
from backend.models.credit_package import CreditPackage
from backend.services.agent_models import get_model_rates, get_default_model

logger = get_logger(__name__)

AGENT_PLAN_CODE = "agent"
AGENT_TRIAL_DAYS = 3
AGENT_SUBSCRIPTION_DAYS = 30


def activate_agent_trial(db: Session, user: User) -> bool:
    """
    Активировать бесплатный тестовый период агента (3 дня + 1500 кредитов).

    ВАЖНО (v3.1): тестовый период больше НЕ переключает базовый тариф
    пользователя на `agent`. Базовый тариф (free/ai_voice/start/profi)
    сохраняется — мы только запускаем окно триала через
    agent_trial_started_at. Доступ во время триала даёт User.agent_trial_active().

    Идемпотентно: если agent_trial_used уже True — ничего не делает и
    возвращает False. Возвращает True если trial выдан.
    """
    if user.agent_trial_used:
        return False

    now = datetime.now(timezone.utc)
    user.agent_trial_started_at = now
    user.agent_subscription_blocked = False
    db.commit()

    # Начисляем кредиты (идемпотентно по trial_grant) + ставит agent_trial_used=True
    CreditService.grant_trial(db, user)
    return True


# ============================================================================
# EXCEPTIONS
# ============================================================================

class InsufficientCreditsError(Exception):
    def __init__(self, required: int, available: int):
        self.required = required
        self.available = available
        super().__init__(f"Insufficient credits: required {required}, available {available}")


class SubscriptionExpiredError(Exception):
    """Подписка agent истекла или заблокирована."""
    pass


class SubscriptionRequiredError(Exception):
    """Юзер пытается использовать оркестратор без подписки agent."""
    pass


# ============================================================================
# SERVICE
# ============================================================================

class CreditService:
    MIN_PRECHECK_BALANCE = 100  # минимум перед запуском оркестратора

    # Разовые начисления
    TRIAL_CREDITS = 1500
    SUBSCRIPTION_CREDITS = 10000

    # ------------------------------------------------------------------
    # PRE-FLIGHT
    # ------------------------------------------------------------------
    @classmethod
    def precheck(cls, db: Session, user: User) -> None:
        """
        Pre-flight проверка перед вызовом оркестратора.
        - SubscriptionRequiredError если у юзера нет плана agent.
        - SubscriptionExpiredError если подписка истекла или заблокирована.
        - InsufficientCreditsError если кредитов меньше MIN_PRECHECK_BALANCE.

        ⚠️ Доступ к оркестратору даёт тестовый период (3 дня), тариф `profi`,
        legacy-тариф `agent` или admin. Тарифы ai_voice/start после триала →
        отказ.

        Админ освобождён только от проверок ДОСТУПА (через has_agent_access →
        True), но НЕ от проверки кредитов: баланс < MIN_PRECHECK_BALANCE
        блокирует оркестратор для всех.
        """
        if not user.has_agent_access():
            # Никогда не было триала → требуется оформление; иначе доступ истёк.
            if user.agent_trial_used:
                raise SubscriptionExpiredError("Agent access expired or blocked")
            raise SubscriptionRequiredError("No agent access")

        if (user.credits_balance or 0) < cls.MIN_PRECHECK_BALANCE:
            raise InsufficientCreditsError(
                required=cls.MIN_PRECHECK_BALANCE,
                available=user.credits_balance or 0,
            )

    # ------------------------------------------------------------------
    # COST CALCULATION
    # ------------------------------------------------------------------
    @classmethod
    def calculate_cost(cls, model_slug: str, prompt_tokens: int,
                       completion_tokens: int) -> int:
        """Считает стоимость в кредитах. Минимум 1 кредит за любой вызов."""
        rates = get_model_rates(model_slug)
        if not rates:
            # Неизвестный slug — берём ставки дефолтной модели как защиту.
            logger.warning(f"[CREDITS] Unknown model slug '{model_slug}', using default rates")
            rates = get_model_rates(get_default_model())

        prompt_tokens = max(0, prompt_tokens or 0)
        completion_tokens = max(0, completion_tokens or 0)

        cost = math.ceil(
            (prompt_tokens / 1000.0) * rates["input_credits_per_1k"] +
            (completion_tokens / 1000.0) * rates["output_credits_per_1k"]
        )
        return max(1, cost)

    # ------------------------------------------------------------------
    # CHARGE (atomic spend)
    # ------------------------------------------------------------------
    @classmethod
    def charge(
        cls,
        db: Session,
        user_id: UUID,
        model_slug: str,
        prompt_tokens: int,
        completion_tokens: int,
        ref_type: str,
        ref_id: Optional[UUID] = None,
        notes: Optional[str] = None,
    ) -> CreditTransaction:
        """
        Атомарное списание. SELECT FOR UPDATE на user.
        Если баланса не хватает — списывает в ноль (НЕ в минус) и помечает
        транзакцию `partial=...`. НЕ бросает исключение — оркестратор уже
        отработал, факт расхода надо зафиксировать.
        """
        user = db.execute(
            select(User).where(User.id == user_id).with_for_update()
        ).scalar_one_or_none()
        if not user:
            raise ValueError(f"User {user_id} not found")

        cost = cls.calculate_cost(model_slug, prompt_tokens, completion_tokens)

        current = max(0, user.credits_balance or 0)
        actual_charge = min(cost, current)
        partial = actual_charge < cost
        user.credits_balance = current - actual_charge

        note_parts = []
        if partial:
            note_parts.append(f"partial: charged {actual_charge}/{cost}")
        if notes:
            note_parts.append(notes)

        tx = CreditTransaction(
            user_id=user_id,
            type=CreditTransactionType.SPEND.value,
            amount=-cost,
            balance_after=user.credits_balance,
            model_slug=model_slug,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            ref_type=ref_type,
            ref_id=ref_id,
            notes=" | ".join(note_parts) if note_parts else None,
        )
        db.add(tx)
        db.commit()
        db.refresh(tx)
        logger.info(
            f"[CREDITS] Charged user {user_id}: -{cost} (actual -{actual_charge}, "
            f"balance {user.credits_balance}, ref {ref_type})"
        )
        return tx

    # ------------------------------------------------------------------
    # GRANTS
    # ------------------------------------------------------------------
    @classmethod
    def grant_trial(cls, db: Session, user: User) -> Optional[CreditTransaction]:
        """
        Выдать 1500 кредитов при старте trial. Идемпотентно по user_id:
        проверяет наличие транзакции type=trial_grant.
        Также ставит user.agent_trial_used = True.
        """
        existing = db.query(CreditTransaction).filter(
            CreditTransaction.user_id == user.id,
            CreditTransaction.type == CreditTransactionType.TRIAL_GRANT.value,
        ).first()
        if existing:
            logger.info(f"[CREDITS] Trial already granted to user {user.id}")
            return None

        locked = db.execute(
            select(User).where(User.id == user.id).with_for_update()
        ).scalar_one_or_none()
        if not locked:
            raise ValueError(f"User {user.id} not found")

        locked.credits_balance = (locked.credits_balance or 0) + cls.TRIAL_CREDITS
        locked.agent_trial_used = True

        tx = CreditTransaction(
            user_id=locked.id,
            type=CreditTransactionType.TRIAL_GRANT.value,
            amount=cls.TRIAL_CREDITS,
            balance_after=locked.credits_balance,
            ref_type="grant",
            notes="Trial grant: 3 days, 1500 credits",
        )
        db.add(tx)
        db.commit()
        db.refresh(tx)
        # Синхронизируем переданный объект (может быть другой identity)
        try:
            user.credits_balance = locked.credits_balance
            user.agent_trial_used = True
        except Exception:
            pass
        logger.info(f"[CREDITS] Trial granted to user {user.id}: +{cls.TRIAL_CREDITS}")
        return tx

    @classmethod
    def grant_subscription(cls, db: Session, user: User,
                           payment_transaction: PaymentTransaction,
                           credits: Optional[int] = None,
                           notes: Optional[str] = None) -> Optional[CreditTransaction]:
        """
        Выдать месячные кредиты при покупке/продлении тарифа, дающего доступ
        к агенту (legacy `agent` или `profi`).

        Идемпотентно по платежу: если для этого payment_transaction уже была
        выдача SUBSCRIPTION_GRANT — повторно не начисляем (защита от повторных
        webhook-ов Finik). Возвращает None если уже было начислено.
        """
        amount = credits if credits is not None else cls.SUBSCRIPTION_CREDITS

        if payment_transaction is not None:
            existing = db.query(CreditTransaction).filter(
                CreditTransaction.payment_transaction_id == payment_transaction.id,
                CreditTransaction.type == CreditTransactionType.SUBSCRIPTION_GRANT.value,
            ).first()
            if existing:
                logger.info(
                    f"[CREDITS] Subscription grant already applied for payment "
                    f"{payment_transaction.id}, skipping"
                )
                return None

        locked = db.execute(
            select(User).where(User.id == user.id).with_for_update()
        ).scalar_one_or_none()
        if not locked:
            raise ValueError(f"User {user.id} not found")

        locked.credits_balance = (locked.credits_balance or 0) + amount

        tx = CreditTransaction(
            user_id=locked.id,
            type=CreditTransactionType.SUBSCRIPTION_GRANT.value,
            amount=amount,
            balance_after=locked.credits_balance,
            ref_type="grant",
            payment_transaction_id=payment_transaction.id if payment_transaction else None,
            notes=notes or f"Subscription grant: {amount} credits",
        )
        db.add(tx)
        db.commit()
        db.refresh(tx)
        try:
            user.credits_balance = locked.credits_balance
        except Exception:
            pass
        logger.info(f"[CREDITS] Subscription credits granted to user {user.id}: +{amount}")
        return tx

    @classmethod
    def grant_purchase(cls, db: Session, user: User, package: CreditPackage,
                       payment_transaction: PaymentTransaction) -> CreditTransaction:
        """Начислить кредиты при покупке пакета."""
        locked = db.execute(
            select(User).where(User.id == user.id).with_for_update()
        ).scalar_one_or_none()
        if not locked:
            raise ValueError(f"User {user.id} not found")

        locked.credits_balance = (locked.credits_balance or 0) + package.credits

        tx = CreditTransaction(
            user_id=locked.id,
            type=CreditTransactionType.PURCHASE.value,
            amount=package.credits,
            balance_after=locked.credits_balance,
            ref_type="purchase",
            payment_transaction_id=payment_transaction.id if payment_transaction else None,
            credits_package_code=package.code,
            notes=f"Purchase package {package.code}: +{package.credits} credits",
        )
        db.add(tx)
        db.commit()
        db.refresh(tx)
        try:
            user.credits_balance = locked.credits_balance
        except Exception:
            pass
        logger.info(f"[CREDITS] Package {package.code} granted to user {user.id}: +{package.credits}")
        return tx

    # ------------------------------------------------------------------
    # REFUND / SYSTEM EVENTS
    # ------------------------------------------------------------------
    @classmethod
    def refund(cls, db: Session, original_tx: CreditTransaction,
               reason: str) -> CreditTransaction:
        """Возврат кредитов при ошибке оркестратора (компенсация SPEND)."""
        refund_amount = abs(original_tx.amount or 0)

        locked = db.execute(
            select(User).where(User.id == original_tx.user_id).with_for_update()
        ).scalar_one_or_none()
        if not locked:
            raise ValueError(f"User {original_tx.user_id} not found")

        locked.credits_balance = (locked.credits_balance or 0) + refund_amount

        tx = CreditTransaction(
            user_id=locked.id,
            type=CreditTransactionType.REFUND.value,
            amount=refund_amount,
            balance_after=locked.credits_balance,
            model_slug=original_tx.model_slug,
            ref_type=original_tx.ref_type,
            ref_id=original_tx.ref_id,
            notes=f"Refund of tx {original_tx.id}: {reason}",
        )
        db.add(tx)
        db.commit()
        db.refresh(tx)
        logger.info(f"[CREDITS] Refunded user {locked.id}: +{refund_amount} ({reason})")
        return tx

    @classmethod
    def log_system_event(cls, db: Session, user_id: UUID, ref_type: str,
                         ref_id: Optional[UUID] = None, notes: str = "") -> CreditTransaction:
        """
        Системная транзакция с amount=0 (без изменения баланса).
        Логирование событий: удаление агента, ручная пометка и т.п.
        """
        balance = cls.get_balance(db, user_id)
        tx = CreditTransaction(
            user_id=user_id,
            type=CreditTransactionType.MANUAL_ADJUST.value,
            amount=0,
            balance_after=balance,
            ref_type=ref_type,
            ref_id=ref_id,
            notes=notes or None,
        )
        db.add(tx)
        db.commit()
        db.refresh(tx)
        logger.info(f"[CREDITS] System event for user {user_id}: {ref_type} ({notes[:80]})")
        return tx

    @classmethod
    def manual_adjust(cls, db: Session, user_id: UUID, amount: int,
                      notes: str) -> CreditTransaction:
        """
        Ручная корректировка баланса админом (edge case 10).
        amount может быть положительным (начисление) или отрицательным (списание).
        notes обязателен — кто и почему.
        """
        if not notes or not notes.strip():
            raise ValueError("manual_adjust requires non-empty notes")

        locked = db.execute(
            select(User).where(User.id == user_id).with_for_update()
        ).scalar_one_or_none()
        if not locked:
            raise ValueError(f"User {user_id} not found")

        locked.credits_balance = max(0, (locked.credits_balance or 0) + amount)

        tx = CreditTransaction(
            user_id=user_id,
            type=CreditTransactionType.MANUAL_ADJUST.value,
            amount=amount,
            balance_after=locked.credits_balance,
            ref_type="grant",
            notes=notes,
        )
        db.add(tx)
        db.commit()
        db.refresh(tx)
        logger.info(f"[CREDITS] Manual adjust for user {user_id}: {amount:+d} ({notes[:80]})")
        return tx

    # ------------------------------------------------------------------
    # READ
    # ------------------------------------------------------------------
    @classmethod
    def get_balance(cls, db: Session, user_id: UUID) -> int:
        """Без FOR UPDATE — просто чтение для UI."""
        balance = db.execute(
            select(User.credits_balance).where(User.id == user_id)
        ).scalar_one_or_none()
        return int(balance or 0)

    @classmethod
    def get_transactions(
        cls, db: Session, user_id: UUID,
        limit: int = 50, offset: int = 0,
        type_filter: Optional[str] = None,
    ) -> Tuple[List[CreditTransaction], int]:
        """История транзакций для UI. Возвращает (список, total).

        Только транзакции оркестратора (product='orchestrator'). Кредиты каскада
        учитываются отдельно (CascadeCreditService.get_transactions)."""
        q = db.query(CreditTransaction).filter(
            CreditTransaction.user_id == user_id,
            CreditTransaction.product == "orchestrator",
        )
        if type_filter:
            q = q.filter(CreditTransaction.type == type_filter)

        total = q.count()
        rows = (
            q.order_by(CreditTransaction.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return rows, total
