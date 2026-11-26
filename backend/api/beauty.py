"""
Voksy Beauty, шаг 1.7 — /api/beauty: журнал действий и главная салона. Обработчики без await — def (в потоках).

  GET  /api/beauty/activity        — журнал действий: ?period=today|week|month|all, actor=owner|ai|call|client|admin|
                                     system|staff:<id>, group=bookings|schedule|services|page|team|money|ai|admin,
                                     object_type=&object_id= (история одного объекта; у записи — вместе с её визитом),
                                     before=<ISO> (следующая страница), limit. Владелец и сотрудник с разделом
                                     «Журнал действий» (activity, в роли «Управляющий»); мастер — нет. IP — только владельцу.
  GET  /api/beauty/home?since=     — главная салона (services/salon_home.py), только владелец
  POST /api/beauty/home/offer-gaps — {resource_id, date?}: ИИ-управляющий предлагает окна мастера давним клиентам —
                                     на одобрение владельца (Beauty Pro / Profi)
"""

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.core.dependencies import ensure_owner, get_current_user, is_staff
from backend.db.session import get_db
from backend.models.booking import Booking
from backend.models.user import User
from backend.services import activity
from backend.services import booking_service as bs
from backend.services import salon_home

router = APIRouter(prefix="/api/beauty", tags=["Voksy Beauty"])


@router.get("/activity")
def beauty_activity(period: Optional[str] = Query("week"), actor: Optional[str] = Query(None),
                    group: Optional[str] = Query(None), object_type: Optional[str] = Query(None),
                    object_id: Optional[str] = Query(None), before: Optional[str] = Query(None),
                    limit: int = Query(50, ge=1, le=200), current_user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)) -> Dict[str, Any]:
    if is_staff(current_user):
        perms = (getattr(current_user, "_staff", None) or {}).get("permissions") or []
        if "activity" not in perms:      # middleware уже проверил; вторая проверка — на случай прямого вызова
            raise HTTPException(status_code=403, detail={"error": "staff_forbidden",
                                                         "message": "Журнал действий вам не открыт"})
    if period not in activity.PERIODS:
        period = "week"
    visit_id = None
    if object_type == "booking" and object_id:
        try:
            visit_id = db.query(Booking.visit_id).filter(Booking.id == uuid.UUID(object_id),
                                                         Booking.user_id == current_user.id).scalar()
        except ValueError:
            raise HTTPException(status_code=404, detail="Запись не найдена")
        period = "all"
    res = activity.query(db, current_user.id, period=period, actor=actor, group=group, object_type=object_type,
                         object_id=object_id, visit_id=str(visit_id) if visit_id else None, before=before,
                         limit=limit, show_ip=not is_staff(current_user))
    if not before and not object_id:
        res["people"] = activity.people(db, current_user.id)
        res["groups"] = activity.groups()
        res["keep_days"] = activity.ACTIVITY_KEEP_DAYS
    return res


@router.get("/home")
def beauty_home(since: Optional[str] = Query(None), current_user: User = Depends(get_current_user),
                db: Session = Depends(get_db)) -> Dict[str, Any]:
    ensure_owner(current_user, "Главная салона")
    s = None
    if since:
        try:
            s = datetime.fromisoformat(since.replace("Z", "+00:00"))
            if s.tzinfo is None:
                s = s.replace(tzinfo=bs.BISHKEK)
        except ValueError:
            s = None
    out = salon_home.build(db, current_user, s)
    db.rollback()
    return out


@router.post("/home/offer-gaps")
def beauty_offer_gaps(data: Dict[str, Any] = Body(...), current_user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)) -> Dict[str, Any]:
    ensure_owner(current_user, "ИИ-управляющий")
    if not data.get("resource_id"):
        raise HTTPException(status_code=400, detail="Не выбран мастер")
    try:
        return salon_home.offer_gaps(db, current_user, str(data["resource_id"]), data.get("date"))
    except bs.BookingError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
