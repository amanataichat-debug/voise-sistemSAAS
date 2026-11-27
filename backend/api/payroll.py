"""
Зарплата мастеров v2 (Voksy Beauty, шаг 3.3) — /api/payroll/*.

  GET  /sheet?from=&to=                 — ведомость за период (по умолчанию с 1-го числа по сегодня)
  GET  /masters/{id}?from=&to=          — строка мастера: разбор по категориям, выходы, ручные строки, выплаты,
                                          схема оплаты и категории услуг салона
  PUT  /masters/{id}/scheme             — {percent, categories: {«Окрашивание»: 40}, shift, fixed}
  POST /adjustments                     — аванс / штраф / премия {resource_id, kind, amount, day?, comment?}
  DELETE /adjustments/{id}
  POST /payouts                         — выплатить {resource_id, from, to, amount?, method: cash|transfer, idem_key, comment?}
  GET  /attendance?date=                — «Кто сегодня работает»: мастера дня, выход, подсказка (есть записи)
  PUT  /attendance                      — {resource_id, date, worked}

Ведомость, ручные строки, выплаты и схема — владелец и бухгалтер (раздел «Финансы»); выход отмечает ещё и
администратор (раздел «Онлайн-запись»). Права сотрудника — team_service.STAFF_RULES. Обработчики def.
"""

from datetime import date
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.core.dependencies import get_current_user
from backend.db.session import get_db
from backend.models.user import User
from backend.services import booking_service as bs
from backend.services import cashbox, payroll

router = APIRouter()


def _fail(db: Session, e: Exception):
    db.rollback()
    if isinstance(e, cashbox.NotFound):
        raise HTTPException(status_code=404, detail=str(e))
    if isinstance(e, cashbox.Forbidden):
        raise HTTPException(status_code=403, detail=str(e))
    code = getattr(e, "code", "error")
    raise HTTPException(status_code=409 if code in ("paid",) else 400, detail={"message": str(e), "code": code})


class AdjustmentBody(BaseModel):
    resource_id: str
    kind: str
    amount: Any
    day: Optional[str] = None
    comment: Optional[str] = Field(None, max_length=300)


class PayoutBody(BaseModel):
    resource_id: str
    date_from: str = Field(..., alias="from")
    date_to: str = Field(..., alias="to")
    amount: Optional[Any] = None
    method: str
    idem_key: str = Field(..., min_length=8, max_length=80)
    comment: Optional[str] = Field(None, max_length=300)


class AttendanceBody(BaseModel):
    resource_id: str
    date: Optional[str] = None
    worked: bool


def _day(v: Optional[str]) -> date:
    if not v:
        return bs.to_local(bs.now_utc()).date()
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        raise HTTPException(status_code=400, detail="Неверная дата")


@router.get("/sheet")
def sheet(date_from: Optional[str] = Query(None, alias="from"), date_to: Optional[str] = Query(None, alias="to"),
          current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        a, b = payroll.parse_period(date_from, date_to)
        out = payroll.sheet(db, current_user, a, b)
        db.rollback()
        return out
    except cashbox.CashError as e:
        _fail(db, e)


@router.get("/masters/{resource_id}")
def master(resource_id: str, date_from: Optional[str] = Query(None, alias="from"), date_to: Optional[str] = Query(None, alias="to"),
           current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        a, b = payroll.parse_period(date_from, date_to)
        rows = payroll.sheet(db, current_user, a, b, resource_id=resource_id)["masters"]
        out = {"from": a.isoformat(), "to": b.isoformat(), "label": payroll.period_label(a, b),
               "line": rows[0] if rows else None, "categories": payroll.categories(db, current_user.id),
               "can_cash": cashbox.can_take(current_user)}
        db.rollback()
        return out
    except cashbox.CashError as e:
        _fail(db, e)


@router.put("/masters/{resource_id}/scheme")
def scheme(resource_id: str, data: Dict[str, Any] = Body(...), current_user: User = Depends(get_current_user),
           db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        out = payroll.save_scheme(db, current_user, resource_id, data)
        db.commit()
        return out
    except cashbox.CashError as e:
        _fail(db, e)


@router.post("/adjustments")
def add_adjustment(body: AdjustmentBody, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        out = payroll.add_adjustment(db, current_user, body.resource_id, body.kind, body.amount, body.day, body.comment)
        db.commit()
        return out
    except cashbox.CashError as e:
        _fail(db, e)


@router.delete("/adjustments/{adj_id}")
def delete_adjustment(adj_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        out = payroll.delete_adjustment(db, current_user, adj_id)
        db.commit()
        return out
    except cashbox.CashError as e:
        _fail(db, e)


@router.post("/payouts")
def payout(body: PayoutBody, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        a, b = payroll.parse_period(body.date_from, body.date_to)
        out, created = payroll.payout(db, current_user, body.resource_id, a, b, body.amount, body.method, body.idem_key,
                                      body.comment)
        db.commit()
        return {"ok": True, "created": created, "payout": out}
    except cashbox.CashError as e:
        _fail(db, e)


@router.get("/attendance")
def attendance(date_: Optional[str] = Query(None, alias="date"), current_user: User = Depends(get_current_user),
               db: Session = Depends(get_db)) -> Dict[str, Any]:
    out = payroll.attendance(db, current_user, _day(date_))
    db.rollback()
    return out


@router.put("/attendance")
def set_attendance(body: AttendanceBody, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        out = payroll.set_attendance(db, current_user, body.resource_id, _day(body.date), body.worked)
        db.commit()
        return out
    except cashbox.CashError as e:
        _fail(db, e)
