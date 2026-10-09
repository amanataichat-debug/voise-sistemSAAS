"""
WalletService — кошелёк пользователя в сомах: минуты голосовых моделей.

Баланс — users.wallet_balance в тыйынах (1 сом = 100 тыйын), история —
wallet_transactions. Все изменения баланса идут через SELECT ... FOR UPDATE.

Тариф минуты (сом) = себестоимость минуты в USD × WALLET_MARKUP / WALLET_KGS_USD_RATE
(курс — сколько долларов стоит 1 сом). Сейчас оба движка по $0,05/мин, наценка 25 %:
0,05 × 1,25 / 0,01143392 ≈ 5,47 сом/мин. Списание посекундное, вверх до тыйына.

Кто платит: владелец ассистента (у агента обзвона — владелец агента). Админ и
служебный аккаунт не платят (как с подпиской). Новый пользователь один раз получает
подарок WALLET_WELCOME_BONUS сом на тест (выдаётся при первом обращении к кошельку).

Подписка остаётся доступом к кабинету; минуты связи оператора пока не списываются.
"""

import math
import uuid
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.models.user import User
from backend.models.wallet_transaction import WalletTransaction, WalletTransactionType

logger = get_logger(__name__)

FREE_EMAILS = {"amanat.aichat@gmail.com"}

# Себестоимость минуты по движку голоса карточки (eleven_assistant_configs.voice_engine)
ENGINE_TITLES = {"eleven": "ElevenLabs", "gpt_live": "OpenAI GPT-Live"}


def _engine_cost_usd(engine: str) -> float:
    if engine == "gpt_live":
        return settings.VOICE_COST_USD_PER_MIN_GPT_LIVE
    return settings.VOICE_COST_USD_PER_MIN_ELEVEN


def tyiyn_per_minute(engine: str) -> float:
    """Цена минуты в тыйынах (дробная — округляем только итог списания)."""
    rate = settings.WALLET_KGS_USD_RATE or 0.01143392
    return _engine_cost_usd(engine) * settings.WALLET_MARKUP / rate * 100


def som_per_minute(engine: str) -> float:
    return round(tyiyn_per_minute(engine) / 100, 2)


def cost_tyiyn(engine: str, seconds: float) -> int:
    if seconds <= 0:
        return 0
    return int(math.ceil(seconds * tyiyn_per_minute(engine) / 60 - 1e-9))


def tariffs() -> List[Dict[str, Any]]:
    return [{"engine": e, "title": t, "som_per_minute": som_per_minute(e)} for e, t in ENGINE_TITLES.items()]


def is_free_user(user: Optional[User]) -> bool:
    return bool(user and (user.is_admin or (user.email or "").lower() in FREE_EMAILS))


def to_som(tyiyn: int) -> float:
    return round((tyiyn or 0) / 100, 2)


class WalletService:

    @staticmethod
    def _lock_user(db: Session, user_id) -> Optional[User]:
        uid = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
        return db.query(User).filter(User.id == uid).with_for_update().first()

    @staticmethod
    def _add(db: Session, user: User, tx_type: str, amount: int, **fields) -> WalletTransaction:
        user.wallet_balance = int(user.wallet_balance or 0) + int(amount)
        tx = WalletTransaction(user_id=user.id, type=tx_type, amount=int(amount),
                               balance_after=user.wallet_balance, **fields)
        db.add(tx)
        return tx

    # ------------------------------------------------------------------ подарок
    @classmethod
    def ensure_welcome_bonus(cls, db: Session, user_id) -> bool:
        """Один раз зачислить подарок на тест. True — зачислено сейчас."""
        bonus = int(settings.WALLET_WELCOME_BONUS or 0) * 100
        user = cls._lock_user(db, user_id)
        if user is None or user.wallet_bonus_granted:
            db.rollback()
            return False
        user.wallet_bonus_granted = True
        if bonus > 0:
            cls._add(db, user, WalletTransactionType.BONUS, bonus, note="Подарок на тест")
        db.commit()
        logger.info(f"[WALLET] welcome bonus {bonus / 100} KGS → user {user.id}")
        return bonus > 0

    # ------------------------------------------------------------------ баланс
    @classmethod
    def get_balance(cls, db: Session, user_id) -> int:
        uid = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
        user = db.query(User).filter(User.id == uid).first()
        if user is None:
            return 0
        if not user.wallet_bonus_granted:  # блокировка строки — только пока подарок не выдан
            cls.ensure_welcome_bonus(db, uid)
            db.refresh(user)
        return int(user.wallet_balance or 0)

    @classmethod
    def can_start_call(cls, db: Session, user_id) -> bool:
        """Есть ли чем платить за разговор (бесплатным пользователям — всегда)."""
        uid = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
        user = db.query(User).filter(User.id == uid).first()
        if user is None or is_free_user(user):
            return True
        return cls.get_balance(db, uid) > 0

    # ------------------------------------------------------------------ зачисления
    @classmethod
    def credit(cls, db: Session, user_id, amount_tyiyn: int, tx_type: str, *, note: Optional[str] = None,
               admin_id=None, payment_transaction_id=None) -> WalletTransaction:
        """Зачисление (или корректировка со знаком минус от админа). Коммитит сам."""
        user = cls._lock_user(db, user_id)
        if user is None:
            db.rollback()
            raise ValueError("user_not_found")
        tx = cls._add(db, user, tx_type, amount_tyiyn, note=note, admin_id=admin_id,
                      payment_transaction_id=payment_transaction_id)
        db.commit()
        db.refresh(tx)
        logger.info(f"[WALLET] {tx_type} {amount_tyiyn / 100:+.2f} KGS → user {user.id}, balance {user.wallet_balance / 100:.2f}")
        return tx

    # ------------------------------------------------------------------ списание
    @classmethod
    def charge_call(cls, db: Session, user_id, *, engine: str, seconds: float, session_id: Optional[str] = None,
                    assistant_id: Optional[str] = None, channel: Optional[str] = None) -> Tuple[int, int]:
        """
        Списать разговор (посекундно). Возвращает (списано тыйын, баланс после).
        Бесплатным пользователям — (0, баланс) без записи. Баланс может уйти в небольшой минус:
        звонок уже состоялся, следующий не начнётся, пока кошелёк не пополнен.
        """
        user = cls._lock_user(db, user_id)
        if user is None:
            db.rollback()
            return 0, 0
        amount = cost_tyiyn(engine, seconds)
        if is_free_user(user) or amount <= 0:
            balance = int(user.wallet_balance or 0)
            db.rollback()
            return 0, balance
        cls._add(db, user, WalletTransactionType.CHARGE, -amount, engine=engine, seconds=int(round(seconds)),
                 session_id=(session_id or None) and str(session_id)[:100],
                 assistant_id=assistant_id and str(assistant_id), channel=channel)
        db.commit()
        logger.info(f"[WALLET] charge {amount / 100:.2f} KGS ({engine}, {seconds:.0f}s) → user {user.id}, "
                    f"balance {user.wallet_balance / 100:.2f}")
        return amount, int(user.wallet_balance)

    # ------------------------------------------------------------------ история
    @staticmethod
    def get_transactions(db: Session, user_id, limit: int = 50, offset: int = 0):
        q = db.query(WalletTransaction).filter(WalletTransaction.user_id == user_id)
        total = q.count()
        rows = q.order_by(WalletTransaction.created_at.desc()).offset(offset).limit(limit).all()
        return rows, total
