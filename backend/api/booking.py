"""
Онлайн-запись (ветка 1811-niche-admin-speed).

Кабинет владельца — /api/booking:
  GET    /overview?date=&days=        — мастера, услуги, записи на дни, настройки и ссылка записи
  GET    /slots?service_id=&resource_id=&date=&days=  — свободное время
  POST   /bookings                    — записать (владелец может и вне графика)
  PATCH  /bookings/{id}               — перенос, статус (пришёл / не пришёл / подтвердил / отмена), заметка, цена
  DELETE /bookings/{id}               — отменить
  GET    /resources, POST /resources, PATCH /resources/{id}, DELETE /resources/{id} — мастера и график
  PUT    /settings                    — шаг, буфер, за сколько записываться, горизонт, напоминания, адрес, ссылка

Клиенты по ссылке — /api/public/booking/{slug} (без входа):
  GET  /{slug}                — название, адрес, услуги, мастера
  GET  /{slug}/slots          — свободное время на день
  POST /{slug}                — записаться (имя + телефон; лимит по IP)

Вся логика — services/booking_service.py (одна на кабинет, бота, голос и ссылку).
"""

import logging
import time
import uuid
from collections import defaultdict, deque
from datetime import timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.core.dependencies import get_current_user
from backend.db.session import get_db
from backend.models.booking import BookingPage, BookingResource
from backend.models.user import User
from backend.services import booking_service as bs

logger = logging.getLogger(__name__)
router = APIRouter()
public_router = APIRouter()


def _err(e: Exception):
    raise HTTPException(status_code=400, detail=str(e))


def _names(db: Session, user_id) -> Dict[str, str]:
    return {str(r.id): r.name for r in bs.resources(db, user_id, active_only=False)}


def _page_dict(page: BookingPage) -> Dict[str, Any]:
    from backend.core.config import settings
    host = (getattr(settings, "HOST_URL", "") or "").rstrip("/")
    return {"slug": page.slug, "enabled": bool(page.enabled), "settings": bs.settings_of(page),
            "url": f"{host}/b/{page.slug}" if page.slug else None}


# ───────────────────────────── кабинет ─────────────────────────────

@router.get("/overview")
def booking_overview(date: Optional[str] = None, days: int = Query(1, ge=1, le=31),
                     current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        day = bs.parse_day(date)
    except bs.BookingError as e:
        _err(e)
    page = bs.get_page(db, current_user)
    res = bs.ensure_resources(db, current_user)
    db.commit()
    start, _ = bs.day_bounds(day)
    end = start + timedelta(days=days)
    names = _names(db, current_user.id)
    rows = bs.list_bookings(db, current_user.id, start, end)
    return {
        "date": day.isoformat(), "days": days, "today": bs.to_local(bs.now_utc()).date().isoformat(),
        "resources": [bs.resource_to_dict(r) for r in res],
        "services": [bs.service_to_dict(e) for e in bs.services(db, current_user.id)],
        "bookings": [bs.booking_to_dict(b, names) for b in rows],
        "page": _page_dict(page),
        "stats": bs.stats(db, current_user.id, start, end),
    }


@router.get("/slots")
def booking_slots(service_id: Optional[str] = None, resource_id: Optional[str] = None, date: Optional[str] = None,
                  days: int = Query(1, ge=1, le=31), current_user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        service = bs.find_service(db, current_user.id, service_id) if service_id else None
        out = bs.free_slots(db, current_user, service, resource_id, date, days, ignore_lead=True)
        db.commit()
        return {"days": out}
    except bs.BookingError as e:
        _err(e)


class BookingIn(BaseModel):
    start: str
    service_id: Optional[str] = None
    resource_id: Optional[str] = None
    contact_id: Optional[str] = None
    client_name: Optional[str] = Field(None, max_length=200)
    client_phone: Optional[str] = Field(None, max_length=40)
    note: Optional[str] = Field(None, max_length=2000)
    price: Optional[float] = None
    duration_min: Optional[int] = Field(None, ge=5, le=1440)


@router.post("/bookings")
def booking_create(body: BookingIn, current_user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        b = bs.create_booking(db, current_user, start=body.start, service_id=body.service_id,
                              resource_id=body.resource_id, contact_id=body.contact_id,
                              client_name=body.client_name, client_phone=body.client_phone, note=body.note,
                              price=body.price, duration_min=body.duration_min,
                              source="staff" if getattr(current_user, "_staff_member_id", None) else "owner")
        db.commit()
    except bs.BookingError as e:
        db.rollback()
        _err(e)
    return {"ok": True, "booking": bs.booking_to_dict(b, _names(db, current_user.id))}


@router.patch("/bookings/{booking_id}")
def booking_update(booking_id: str, data: Dict[str, Any] = Body(...), current_user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        b = bs.update_booking(db, current_user, booking_id, start=data.get("start"), resource_id=data.get("resource_id"),
                              service_id=data.get("service_id"), status=data.get("status"), note=data.get("note"),
                              price=data.get("price"), cancel_reason=data.get("cancel_reason"))
        db.commit()
    except bs.BookingError as e:
        db.rollback()
        _err(e)
    return {"ok": True, "booking": bs.booking_to_dict(b, _names(db, current_user.id))}


@router.delete("/bookings/{booking_id}")
def booking_cancel(booking_id: str, reason: Optional[str] = None, current_user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        b = bs.cancel_booking(db, current_user, booking_id, reason)
        db.commit()
    except bs.BookingError as e:
        db.rollback()
        _err(e)
    return {"ok": True, "booking": bs.booking_to_dict(b, _names(db, current_user.id))}


class ResourceIn(BaseModel):
    name: Optional[str] = Field(None, max_length=120)
    role: Optional[str] = Field(None, max_length=120)
    color: Optional[str] = Field(None, max_length=16)
    phone: Optional[str] = Field(None, max_length=32)
    schedule: Optional[Dict[str, Any]] = None
    days_off: Optional[List[str]] = None
    service_ids: Optional[List[str]] = None
    is_active: Optional[bool] = None
    position: Optional[int] = None


def _apply_resource(r: BookingResource, body: ResourceIn) -> None:
    data = body.dict(exclude_unset=True)
    if "name" in data:
        name = " ".join(str(data["name"] or "").split())
        if not name:
            raise bs.BookingError("Укажите имя мастера")
        r.name = name[:120]
    for f in ("role", "color", "phone"):
        if f in data:
            setattr(r, f, (str(data[f]).strip() or None) if data[f] is not None else None)
    if "schedule" in data:
        r.schedule = bs.clean_schedule(data["schedule"])
    if "days_off" in data:
        days = []
        for d in data["days_off"] or []:
            try:
                days.append(bs.parse_day(d).isoformat())
            except bs.BookingError:
                continue
        r.days_off = sorted(set(days))[:366]
    if "service_ids" in data:
        ids = [str(x) for x in (data["service_ids"] or []) if x]
        r.service_ids = ids or None
    if "is_active" in data and data["is_active"] is not None:
        r.is_active = bool(data["is_active"])
    if "position" in data and data["position"] is not None:
        r.position = int(data["position"])


@router.get("/resources")
def booking_resources(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    bs.ensure_resources(db, current_user)
    db.commit()
    return {"resources": [bs.resource_to_dict(r) for r in bs.resources(db, current_user.id, active_only=False)]}


@router.post("/resources")
def booking_resource_create(body: ResourceIn, current_user: User = Depends(get_current_user),
                            db: Session = Depends(get_db)) -> Dict[str, Any]:
    count = len(bs.resources(db, current_user.id, active_only=False))
    if count >= 50:
        raise HTTPException(status_code=400, detail="Не больше 50 мастеров")
    r = BookingResource(user_id=current_user.id, name="Мастер", color=bs.COLORS[count % len(bs.COLORS)],
                        schedule=dict(bs.DEFAULT_SCHEDULE), days_off=[], position=count)
    try:
        _apply_resource(r, body)
    except bs.BookingError as e:
        _err(e)
    db.add(r)
    db.commit()
    return {"ok": True, "resource": bs.resource_to_dict(r)}


def _resource_or_404(db: Session, user_id, rid: str) -> BookingResource:
    try:
        r = db.query(BookingResource).filter(BookingResource.id == uuid.UUID(rid), BookingResource.user_id == user_id).first()
    except ValueError:
        r = None
    if r is None:
        raise HTTPException(status_code=404, detail="Мастер не найден")
    return r


@router.patch("/resources/{resource_id}")
def booking_resource_update(resource_id: str, body: ResourceIn, current_user: User = Depends(get_current_user),
                            db: Session = Depends(get_db)) -> Dict[str, Any]:
    r = _resource_or_404(db, current_user.id, resource_id)
    try:
        _apply_resource(r, body)
    except bs.BookingError as e:
        _err(e)
    db.commit()
    return {"ok": True, "resource": bs.resource_to_dict(r)}


@router.delete("/resources/{resource_id}")
def booking_resource_delete(resource_id: str, current_user: User = Depends(get_current_user),
                            db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Мастер с будущими записями не удаляется, а выключается (записи остаются в календаре)."""
    r = _resource_or_404(db, current_user.id, resource_id)
    upcoming = bs.list_bookings(db, current_user.id, bs.now_utc(), bs.now_utc() + timedelta(days=365), resource_id=r.id,
                                include_cancelled=False)
    if upcoming:
        r.is_active = False
        db.commit()
        return {"ok": True, "disabled": True, "upcoming": len(upcoming)}
    db.delete(r)
    db.commit()
    return {"ok": True, "deleted": True}


class SettingsIn(BaseModel):
    settings: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None
    slug: Optional[str] = Field(None, max_length=64)


@router.put("/settings")
def booking_settings(body: SettingsIn, current_user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)) -> Dict[str, Any]:
    page = bs.get_page(db, current_user)
    if body.settings is not None:
        merged = dict(page.settings or {})
        for k, v in body.settings.items():
            if k in bs.DEFAULT_SETTINGS:
                merged[k] = v
        page.settings = bs.settings_of(type("P", (), {"settings": merged})())
    if body.enabled is not None:
        page.enabled = bool(body.enabled)
    if body.slug is not None:
        slug = bs._slugify(body.slug)
        if len(slug) < 3:
            raise HTTPException(status_code=400, detail="Адрес ссылки — минимум 3 латинские буквы или цифры")
        taken = db.query(BookingPage).filter(BookingPage.slug == slug, BookingPage.user_id != current_user.id).first()
        if taken is not None:
            raise HTTPException(status_code=400, detail="Такой адрес уже занят — придумайте другой")
        page.slug = slug
    db.commit()
    return {"ok": True, "page": _page_dict(page)}


# ───────────────────────────── клиенты по ссылке ─────────────────────────────

_PUBLIC_HITS: Dict[str, deque] = defaultdict(deque)


def _public_rate(ip: str, limit: int = 8, window: float = 3600.0) -> None:
    now = time.time()
    q = _PUBLIC_HITS[ip]
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(status_code=429, detail="Слишком много записей с этого устройства. Попробуйте позже.")
    q.append(now)


def _public_page(db: Session, slug: str):
    page = db.query(BookingPage).filter(BookingPage.slug == (slug or "").lower()).first()
    if page is None or not page.enabled:
        raise HTTPException(status_code=404, detail="Онлайн-запись не найдена или выключена")
    owner = db.query(User).filter(User.id == page.user_id).first()
    if owner is None or not owner.is_active:
        raise HTTPException(status_code=404, detail="Онлайн-запись не найдена или выключена")
    return page, owner


@public_router.get("/{slug}")
def public_booking_info(slug: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    page, owner = _public_page(db, slug)
    cfg = bs.settings_of(page)
    from backend.services.niche_catalog import niche_title
    return {
        "title": cfg.get("title") or owner.company_name or owner.first_name or "Онлайн-запись",
        "niche": niche_title(owner.business_niche, owner.business_niche_other),
        "address": cfg.get("address") or "",
        "horizon_days": cfg["horizon_days"],
        "services": [bs.service_to_dict(e) for e in bs.services(db, owner.id)],
        "resources": [{"id": str(r.id), "name": r.name, "role": r.role, "color": r.color,
                       "service_ids": r.service_ids} for r in bs.resources(db, owner.id)],
    }


@public_router.get("/{slug}/slots")
def public_booking_slots(slug: str, service_id: Optional[str] = None, resource_id: Optional[str] = None,
                         date: Optional[str] = None, db: Session = Depends(get_db)) -> Dict[str, Any]:
    page, owner = _public_page(db, slug)
    try:
        service = bs.find_service(db, owner.id, service_id) if service_id else None
        days = bs.free_slots(db, owner, service, resource_id, date, 1)
    except bs.BookingError as e:
        _err(e)
    return {"days": [{"date": d["date"], "weekday": d["weekday"],
                      "slots": [{"time": s["time"], "start": s["start"]} for s in d["slots"]]} for d in days]}


class PublicBookingIn(BaseModel):
    start: str
    service_id: Optional[str] = None
    resource_id: Optional[str] = None
    name: str = Field(..., min_length=1, max_length=120)
    phone: str = Field(..., min_length=5, max_length=40)
    note: Optional[str] = Field(None, max_length=500)


@public_router.post("/{slug}")
async def public_booking_create(slug: str, body: PublicBookingIn, request: Request,
                                db: Session = Depends(get_db)) -> Dict[str, Any]:
    ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "?"))
    _public_rate(ip)
    page, owner = _public_page(db, slug)
    try:
        b = bs.create_booking(db, owner, start=body.start, service_id=body.service_id, resource_id=body.resource_id,
                              client_name=" ".join(body.name.split())[:120], client_phone=body.phone,
                              note=body.note, source="online", channel="web")
        db.commit()
    except bs.BookingError as e:
        db.rollback()
        _err(e)
    names = _names(db, owner.id)
    out = bs.booking_to_dict(b, names)
    try:
        from backend.core.spawn import spawn
        from backend.services.booking_notify import notify_owner_new_booking
        spawn(notify_owner_new_booking(owner.id, b.id))
    except Exception as e:
        logger.warning(f"[BOOKING] owner notify not scheduled: {e}")
    cfg = bs.settings_of(page)
    return {"ok": True, "booking": {"label": out["label"], "service": out["service"], "resource": out["resource"],
                                    "address": cfg.get("address") or ""}}
