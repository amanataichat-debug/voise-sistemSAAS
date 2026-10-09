"""
Wallet API — кошелёк в сомах для минут голосовых моделей.

Префикс: /api/wallet
  GET  /balance       — баланс (+ подарок на тест при первом обращении), лимиты пополнения, тарифы
  GET  /tariffs       — цены минуты по движкам (без авторизации: лендинг, карточки моделей)
  GET  /transactions  — история операций
  POST /topup         — платёж Finik на пополнение (зачисление — в webhook, тип wallet_topup)
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.api.credits import create_finik_payment_transaction
from backend.core.config import settings
from backend.core.dependencies import get_current_user
from backend.core.logging import get_logger
from backend.db.session import get_db
from backend.models.user import User
from backend.services.wallet_service import WalletService, is_free_user, tariffs, to_som

logger = get_logger(__name__)
router = APIRouter(prefix="/api/wallet", tags=["Wallet"])


class TopupRequest(BaseModel):
    amount: float  # сом


@router.get("/tariffs")
async def get_tariffs():
    return {"currency": "KGS", "tariffs": tariffs(), "markup": settings.WALLET_MARKUP}


@router.get("/balance")
async def get_balance(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    balance = WalletService.get_balance(db, current_user.id)
    return {
        "balance": to_som(balance),
        "balance_tyiyn": balance,
        "currency": "KGS",
        "free": is_free_user(current_user),
        "min_topup": settings.WALLET_MIN_TOPUP,
        "max_topup": settings.WALLET_MAX_TOPUP,
        "tariffs": tariffs(),
    }


@router.get("/transactions")
async def get_transactions(limit: int = Query(30, ge=1, le=200), offset: int = Query(0, ge=0),
                           current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows, total = WalletService.get_transactions(db, current_user.id, limit=limit, offset=offset)
    return {"total": total, "limit": limit, "offset": offset, "transactions": [r.to_dict() for r in rows]}


@router.post("/topup")
async def topup(body: TopupRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    amount = round(float(body.amount or 0), 2)
    if amount < settings.WALLET_MIN_TOPUP or amount > settings.WALLET_MAX_TOPUP:
        raise HTTPException(status_code=400, detail=f"Сумма пополнения — от {settings.WALLET_MIN_TOPUP} "
                                                    f"до {settings.WALLET_MAX_TOPUP} сом")
    payment = await create_finik_payment_transaction(
        db=db, user=current_user, amount=amount,
        description=f"Пополнение кошелька VoksiAI на {amount:g} сом",
        details={"type": "wallet_topup", "amount_tyiyn": int(round(amount * 100))},
    )
    logger.info(f"[WALLET] topup payment {payment['payment_id']} for user {current_user.id}: {amount} KGS")
    return payment
