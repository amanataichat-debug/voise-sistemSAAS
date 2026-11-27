"""
«Мои записи» клиента салона по подписанной ссылке /m/<токен> (Voksy Beauty, шаг 2.5).

Без входа: токен в адресе (services/client_cabinet.py) открывает только записи этой карточки клиента в этом салоне.
Обработчики синхронные (FastAPI выполняет их в потоках, цикл звонков не замирает). Лимиты — с одного IP, в памяти
воркера: чтение 120 за 10 минут, действия 30 в час.
"""

import time
from collections import defaultdict, deque
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from backend.db.session import get_db
from backend.services import booking_service as bs
from backend.services import client_cabinet as cc

router = APIRouter()

_HITS: Dict[str, deque] = defaultdict(deque)


def _ip(request: Request) -> str:
    return request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "?")


def _rate(request: Request, kind: str, limit: int, window: float) -> None:
    now = time.time()
    q = _HITS[f"{kind}:{_ip(request)}"]
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(status_code=429, detail="Слишком много запросов с этого устройства. Попробуйте чуть позже.")
    q.append(now)


def _read(request: Request) -> None:
    _rate(request, "r", 120, 600.0)


def _act(request: Request) -> None:
    _rate(request, "a", 30, 3600.0)


def _fail(db: Session, e: Exception):
    db.rollback()
    if isinstance(e, cc.NotFound):
        raise HTTPException(status_code=404, detail=str(e))
    raise HTTPException(status_code=400, detail=str(e))


@router.get("/{token}")
def client_state(token: str, request: Request, db: Session = Depends(get_db)) -> Dict[str, Any]:
    _read(request)
    try:
        out = cc.state(db, token)
    except cc.ClientError as e:
        _fail(db, e)
    db.rollback()
    return out


@router.get("/{token}/slots")
def client_slots(token: str, request: Request, booking_id: str = Query(...), mode: str = Query("move"),
                 date: Optional[str] = None, days: int = Query(1, ge=1, le=14), any_master: bool = False,
                 db: Session = Depends(get_db)) -> Dict[str, Any]:
    _read(request)
    try:
        return cc.slots(db, token, booking_id, mode="repeat" if mode == "repeat" else "move", day=date, days=days,
                        any_master=any_master)
    except cc.ClientError as e:
        _fail(db, e)
    except bs.BookingError as e:                 # неверная дата и т.п.
        _fail(db, cc.ClientError(str(e)))


@router.post("/{token}/bookings/{booking_id}/reschedule")
def client_reschedule(token: str, booking_id: str, request: Request, data: Dict[str, Any] = Body(...),
                      db: Session = Depends(get_db)) -> Dict[str, Any]:
    _act(request)
    if not (data or {}).get("start"):
        raise HTTPException(status_code=400, detail="Выберите новое время")
    try:
        return cc.reschedule(db, token, booking_id, data["start"], any_master=bool(data.get("any_master")))
    except cc.ClientError as e:
        _fail(db, e)


@router.post("/{token}/bookings/{booking_id}/cancel")
def client_cancel(token: str, booking_id: str, request: Request, data: Optional[Dict[str, Any]] = Body(None),
                  db: Session = Depends(get_db)) -> Dict[str, Any]:
    _act(request)
    try:
        return cc.cancel(db, token, booking_id, (data or {}).get("reason"))
    except cc.ClientError as e:
        _fail(db, e)


@router.post("/{token}/bookings/{booking_id}/confirm")
def client_confirm(token: str, booking_id: str, request: Request, db: Session = Depends(get_db)) -> Dict[str, Any]:
    _act(request)
    try:
        return cc.confirm(db, token, booking_id)
    except cc.ClientError as e:
        _fail(db, e)


@router.post("/{token}/repeat")
def client_repeat(token: str, request: Request, data: Dict[str, Any] = Body(...),
                  db: Session = Depends(get_db)) -> Dict[str, Any]:
    _act(request)
    if not (data or {}).get("booking_id") or not data.get("start"):
        raise HTTPException(status_code=400, detail="Выберите время")
    try:
        return cc.repeat(db, token, data["booking_id"], data["start"], any_master=bool(data.get("any_master")))
    except cc.ClientError as e:
        _fail(db, e)


@router.post("/{token}/waitlist/{entry_id}/stop")
def client_waitlist_stop(token: str, entry_id: str, request: Request, db: Session = Depends(get_db)) -> Dict[str, Any]:
    _act(request)
    try:
        return cc.waitlist_stop(db, token, entry_id)
    except cc.ClientError as e:
        _fail(db, e)
