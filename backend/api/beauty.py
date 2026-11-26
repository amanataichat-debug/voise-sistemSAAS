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

Шаг 2.1 — клиенты салона (services/salon_clients.py):
  GET  /api/beauty/clients          — список с цифрами из журнала: ?segment=all|regular|new|vip|sleeping|lost|no_show|
                                     birthday|upcoming|never, q, master, service, source, gender, tag,
                                     sort=last_visit|spent|visits|next|name|created|birthday, limit, offset, counts=0|1.
                                     Владелец и сотрудник с разделом «Клиенты»
  GET  /api/beauty/clients/export.csv — те же фильтры, CSV для Excel; только владелец
  GET  /api/beauty/clients/{id}     — салонная часть карточки: цифры, любимые, ДР, источник, сегменты, история визитов
"""

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from backend.core.dependencies import ensure_owner, get_current_user, is_staff
from backend.db.session import get_db
from backend.models.booking import Booking
from backend.models.user import User
from backend.services import activity
from backend.services import booking_service as bs
from backend.services import salon_clients, salon_home

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


# ───────────────────────────── клиенты салона (шаг 2.1) ─────────────────────────────

def _client_filters(segment, q, master, service, source, gender, tag, sort) -> Dict[str, Any]:
    return {"segment": segment or "all", "q": q, "master": master, "service": service, "source": source,
            "gender": gender, "tag": tag, "sort": sort or ""}     # пусто — сервис выберет (у «ДР скоро» — по ДР)


@router.get("/clients")
def beauty_clients(segment: Optional[str] = Query("all"), q: Optional[str] = Query(None),
                   master: Optional[str] = Query(None), service: Optional[str] = Query(None),
                   source: Optional[str] = Query(None), gender: Optional[str] = Query(None),
                   tag: Optional[str] = Query(None), sort: Optional[str] = Query(None),
                   limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), counts: int = Query(1),
                   current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    flt = _client_filters(segment, q, master, service, source, gender, tag, sort)
    out = salon_clients.list_clients(db, current_user, limit=limit, offset=offset, with_counts=bool(counts), **flt)
    out["can_export"] = not is_staff(current_user)
    db.rollback()
    return out


@router.get("/clients/export.csv")
def beauty_clients_export(segment: Optional[str] = Query("all"), q: Optional[str] = Query(None),
                          master: Optional[str] = Query(None), service: Optional[str] = Query(None),
                          source: Optional[str] = Query(None), gender: Optional[str] = Query(None),
                          tag: Optional[str] = Query(None), sort: Optional[str] = Query(None),
                          current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    ensure_owner(current_user, "Выгрузка клиентов")
    body = salon_clients.export_csv(db, current_user, **_client_filters(segment, q, master, service, source, gender,
                                                                        tag, sort))
    db.rollback()
    name = f"clients_{bs.to_local(bs.now_utc()).strftime('%Y-%m-%d')}.csv"
    return Response(content=body.encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


@router.get("/clients/{contact_id}")
def beauty_client_card(contact_id: str, current_user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)) -> Dict[str, Any]:
    card = salon_clients.client_card(db, current_user, contact_id)
    db.rollback()
    if card is None:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    return card
