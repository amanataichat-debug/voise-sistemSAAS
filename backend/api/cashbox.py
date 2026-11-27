"""
Касса салона (Voksy Beauty, шаг 3.1) — /api/cash/*.

  GET  /state                         — моя смена с итогами, «в кассе оставили», чужие открытые смены и день (владельцу)
  POST /shifts/open                   — открыть свою смену {opening_cash?}
  POST /shifts/{id}/close             — закрыть {counted_cash, note?}: свою — кассир, любую — владелец/бухгалтер
  GET  /shifts?before=&status=        — смены (владельцу и бухгалтеру — все, кассиру — свои)
  GET  /shifts/{id}                   — смена: итоги и чеки
  GET  /receipts?date=&shift_id=      — чеки дня или смены
  GET  /booking/{id}                  — «Оплата» записи: весь визит, оплачено, осталось, чеки
  POST /pay                           — принять оплату {booking_id, lines: [{method, amount}], idem_key, prepay?, comment?}
  POST /receipts/{id}/refund          — вернуть {amount, reason, method?, idem_key}
  POST /moves                         — внести / изъять наличные {kind: cash_in|cash_out, amount, comment, idem_key}

Права сотрудника — team_service.STAFF_RULES (+ services/cashbox.can_take / can_manage). Обработчики def
(FastAPI выполняет их в потоках — синхронная база не держит цикл звонков).
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.core.dependencies import get_current_user
from backend.db.session import get_db
from backend.models.user import User
from backend.services import cashbox

router = APIRouter()


def _fail(db: Session, e: Exception):
    db.rollback()
    if isinstance(e, cashbox.NotFound):
        raise HTTPException(status_code=404, detail=str(e))
    if isinstance(e, cashbox.Forbidden):
        raise HTTPException(status_code=403, detail=str(e))
    code = getattr(e, "code", "error")
    raise HTTPException(status_code=409 if code in ("shift_required", "paid", "closed", "refunded") else 400,
                        detail={"message": str(e), "code": code})


class OpenBody(BaseModel):
    opening_cash: Optional[Any] = None
    note: Optional[str] = Field(None, max_length=500)


class CloseBody(BaseModel):
    counted_cash: Any
    note: Optional[str] = Field(None, max_length=500)


class Line(BaseModel):
    method: str
    amount: Any


class PayBody(BaseModel):
    booking_id: str
    lines: List[Line]
    idem_key: str = Field(..., min_length=8, max_length=80)
    prepay: Optional[bool] = None
    comment: Optional[str] = Field(None, max_length=500)


class RefundBody(BaseModel):
    amount: Any
    reason: str = Field("", max_length=500)
    method: Optional[str] = None
    idem_key: str = Field(..., min_length=8, max_length=80)


class MoveBody(BaseModel):
    kind: str
    amount: Any
    comment: Optional[str] = Field(None, max_length=500)
    idem_key: str = Field(..., min_length=8, max_length=80)


@router.get("/state")
def cash_state(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    return cashbox.state(db, current_user)


@router.post("/shifts/open")
def open_shift(body: OpenBody, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        sh, created = cashbox.open_shift(db, current_user, body.opening_cash, body.note)
        db.commit()
    except cashbox.CashError as e:
        _fail(db, e)
    return {"ok": True, "created": created, "shift": cashbox.shift_to_dict(db, sh)}


@router.post("/shifts/{shift_id}/close")
def close_shift(shift_id: str, body: CloseBody, current_user: User = Depends(get_current_user),
                db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        sh = cashbox.close_shift(db, current_user, shift_id, body.counted_cash, body.note)
        db.commit()
    except cashbox.CashError as e:
        _fail(db, e)
    return {"ok": True, "shift": cashbox.shift_to_dict(db, sh)}


@router.get("/shifts")
def shifts(before: Optional[str] = None, status: Optional[str] = None, limit: int = Query(30, ge=1, le=100),
           current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    return cashbox.list_shifts(db, current_user, before=before, limit=limit, status=status)


@router.get("/shifts/{shift_id}")
def shift_detail(shift_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        sh = cashbox._get_shift(db, current_user.id, shift_id)
        if not cashbox._own(current_user, sh) and not cashbox.can_manage(current_user):
            raise cashbox.Forbidden("Это чужая смена")
        return {"shift": cashbox.shift_to_dict(db, sh),
                "receipts": cashbox.list_receipts(db, current_user, shift_id=shift_id, limit=300)["items"]}
    except cashbox.CashError as e:
        _fail(db, e)


@router.get("/receipts")
def receipts(date: Optional[str] = None, shift_id: Optional[str] = None, before: Optional[str] = None,
             limit: int = Query(100, ge=1, le=300), current_user: User = Depends(get_current_user),
             db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        out = cashbox.list_receipts(db, current_user, shift_id=shift_id, day=date, before=before, limit=limit)
        if not shift_id and cashbox.can_manage(current_user):
            out["day"] = cashbox.day_numbers(db, current_user.id, date)
        return out
    except cashbox.CashError as e:
        _fail(db, e)


@router.get("/booking/{booking_id}")
def booking_money(booking_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        return cashbox.booking_money(db, current_user, booking_id)
    except cashbox.CashError as e:
        _fail(db, e)


@router.post("/pay")
def pay(body: PayBody, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        receipt, created = cashbox.pay(db, current_user, body.booking_id, [ln.dict() for ln in body.lines],
                                       body.idem_key, prepay=body.prepay, comment=body.comment)
        db.commit()
    except cashbox.CashError as e:
        _fail(db, e)
    return {"ok": True, "created": created, "receipt": receipt,
            "money": cashbox.booking_money(db, current_user, body.booking_id)}


@router.post("/receipts/{receipt_id}/refund")
def refund(receipt_id: str, body: RefundBody, current_user: User = Depends(get_current_user),
           db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        receipt, created = cashbox.refund(db, current_user, receipt_id, body.amount, body.idem_key,
                                          reason=body.reason, method=body.method)
        db.commit()
    except cashbox.CashError as e:
        _fail(db, e)
    return {"ok": True, "created": created, "receipt": receipt}


@router.post("/moves")
def moves(body: MoveBody, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        receipt, created = cashbox.cash_move(db, current_user, body.kind, body.amount, body.idem_key, body.comment)
        db.commit()
    except cashbox.CashError as e:
        _fail(db, e)
    return {"ok": True, "created": created, "receipt": receipt}
