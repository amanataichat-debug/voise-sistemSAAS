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
  GET    /link-kit                    — ссылка, QR-код (SVG) и код кнопки-виджета для сайта (static/booking-widget.js)
  POST   /resources/{id}/media?kind=avatar|portfolio|certificate — фото мастера, работа, сертификат (файл)
  DELETE /resources/{id}/media/{media_id}                         — убрать фото
  PUT    /resources/{id}/login        — привязать вход сотрудника к мастеру {team_member_id|null} (владелец)
  POST   /resources/{id}/login        — создать мастеру вход {email, password?} (владелец; место по тарифу)

Кабинет мастера (сотрудник с разделом «Свой журнал» — только свои записи; владелец — ?resource_id=):
  GET    /me?date=&days=              — мастер, записи, услуги, заработок, статистика, новое
  GET    /me/clients                  — клиенты мастера с историей визитов
  GET    /me/slots?service_id=&date=  — своё свободное время
  POST   /me/bookings                 — записать клиента к себе
  PATCH  /me/bookings/{id}            — статус (подтвердил / пришёл / не пришёл), заметка, перенос своей записи
  PATCH  /me/profile                  — «о себе», подписи к работам
  POST   /me/media, DELETE /me/media/{id} — свои работы в портфолио

Фото — GET /media/salon/{id} без входа (services/salon_media.py).

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

from fastapi import File, Form, UploadFile
from fastapi.responses import Response

from backend.core.dependencies import ensure_owner, get_current_user, is_staff
from backend.db.session import get_db
from backend.models.booking import BookingPage, BookingResource
from backend.models.user import User
from backend.services import booking_service as bs
from backend.services import master_cabinet as mc
from backend.services import salon_media as sm

logger = logging.getLogger(__name__)
router = APIRouter()
public_router = APIRouter()
media_router = APIRouter()


def _staff_perms(user) -> Optional[set]:
    """Разделы сотрудника (None — запрос владельца)."""
    if not is_staff(user):
        return None
    snap = getattr(user, "_staff", None) or {}
    return set(snap.get("permissions") or [])


def _sees_money(user) -> bool:
    perms = _staff_perms(user)
    return perms is None or "finance" in perms


def _resource_out(user, r: BookingResource) -> Dict[str, Any]:
    d = bs.resource_to_dict(r)
    if _sees_money(user):   # оплата мастера — владельцу и бухгалтеру
        d["pay_percent"], d["pay_fixed"] = r.pay_percent or 0, r.pay_fixed or 0
    return d


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
        "resources": [_resource_out(current_user, r) for r in res],
        "services": [bs.service_to_dict(e) for e in bs.services(db, current_user.id)],
        "bookings": [bs.booking_to_dict(b, names) for b in rows],
        "logins": _logins(db, current_user),
        "page": _page_dict(page),
        "stats": bs.stats(db, current_user.id, start, end),
        "plan_limit": _plan_limit(db, current_user),
    }


def _plan_limit(db: Session, user) -> Optional[Dict[str, Any]]:
    """Voksy Beauty: сколько мастеров даёт тариф (для «4 из 10» на экране мастеров)."""
    try:
        from backend.services.plan_catalog import staff_terms
        t = staff_terms(db, user)
    except Exception:
        return None
    if not t or not t.get("limit"):
        return None
    return {"limit": int(t["limit"]), "plan_name": t.get("plan_name"), "trial": bool(t.get("trial"))}


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
    bio: Optional[str] = Field(None, max_length=1500)
    experience_since: Optional[int] = None
    service_prices: Optional[Dict[str, Any]] = None
    online_booking: Optional[bool] = None
    portfolio: Optional[List[Dict[str, Any]]] = None       # порядок и подписи уже загруженных работ
    certificates: Optional[List[Dict[str, Any]]] = None


def _reorder_media(current: Any, wanted: List[Dict[str, Any]], title_key: str) -> List[Dict[str, Any]]:
    """Новый порядок и подписи — только для уже загруженных фото (чужие id и новые не принимаются)."""
    have = {str(it.get("id")): it for it in (current or []) if isinstance(it, dict) and it.get("id")}
    out, seen = [], set()
    for it in wanted or []:
        mid = str((it or {}).get("id") or "")
        if mid in have and mid not in seen:
            seen.add(mid)
            out.append({"id": mid, title_key: " ".join(str(it.get(title_key) or "").split())[:200]})
    out += [have[k] for k in have if k not in seen]      # не упомянутые не теряются
    return out


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
    if "bio" in data:
        r.bio = (str(data["bio"] or "").strip() or None)
    if "experience_since" in data:
        y = data["experience_since"]
        if y in (None, "", 0):
            r.experience_since = None
        elif bs.experience_years(y) is None:
            raise bs.BookingError("Год начала работы — например, 2018")
        else:
            r.experience_since = int(y)
    if "service_prices" in data:
        r.service_prices = bs.clean_service_prices(data["service_prices"]) or None
    if "online_booking" in data and data["online_booking"] is not None:
        r.online_booking = bool(data["online_booking"])
    if "portfolio" in data and data["portfolio"] is not None:
        r.portfolio = _reorder_media(r.portfolio, data["portfolio"], "caption")
    if "certificates" in data and data["certificates"] is not None:
        r.certificates = _reorder_media(r.certificates, data["certificates"], "title")


@router.get("/resources")
def booking_resources(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    bs.ensure_resources(db, current_user)
    db.commit()
    return {"resources": [_resource_out(current_user, r) for r in bs.resources(db, current_user.id, active_only=False)]}


@router.post("/resources")
def booking_resource_create(body: ResourceIn, current_user: User = Depends(get_current_user),
                            db: Session = Depends(get_db)) -> Dict[str, Any]:
    count = len(bs.resources(db, current_user.id, active_only=False))
    if count >= 50:
        raise HTTPException(status_code=400, detail="Не больше 50 мастеров")
    if body.is_active is not False:
        _check_master_limit(db, current_user)
    r = BookingResource(user_id=current_user.id, name="Мастер", color=bs.COLORS[count % len(bs.COLORS)],
                        schedule=dict(bs.DEFAULT_SCHEDULE), days_off=[], position=count)
    try:
        _apply_resource(r, body)
    except bs.BookingError as e:
        _err(e)
    db.add(r)
    db.commit()
    return {"ok": True, "resource": _resource_out(current_user, r)}


def _check_master_limit(db: Session, user: User) -> None:
    """Voksy Beauty: мастеров в журнале — не больше, чем даёт тариф (plan_catalog.master_limit_error)."""
    from backend.services.plan_catalog import master_limit_error
    msg = master_limit_error(db, user)
    if msg:
        raise HTTPException(status_code=402, detail=msg)


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
    if body.is_active is True and not r.is_active:
        _check_master_limit(db, current_user)   # снова включить мастера — в пределах тарифа
    try:
        _apply_resource(r, body)
    except bs.BookingError as e:
        _err(e)
    db.commit()
    return {"ok": True, "resource": _resource_out(current_user, r)}


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
    sm.delete_many(db, current_user.id, _media_ids(r))
    db.delete(r)
    db.commit()
    return {"ok": True, "deleted": True}


# ───────────────────────────── фото мастера ─────────────────────────────

def _media_ids(r: BookingResource) -> List[str]:
    ids = [str(r.photo_id)] if r.photo_id else []
    ids += [str(it.get("id")) for it in (r.portfolio or []) if isinstance(it, dict) and it.get("id")]
    ids += [str(it.get("id")) for it in (r.certificates or []) if isinstance(it, dict) and it.get("id")]
    return ids


def _attach_media(db: Session, owner_id, r: BookingResource, kind: str, data: bytes, title: str = "") -> Dict[str, Any]:
    """Сохранить фото и прикрепить к мастеру. Аватар заменяет прежний (старый файл удаляется)."""
    key = {"portfolio": "portfolio", "certificate": "certificates"}.get(kind)
    if kind not in ("avatar", "portfolio", "certificate"):
        raise sm.MediaError("Неизвестный вид фото")
    if key:
        items = list(getattr(r, key) or [])
        if len(items) >= sm.LIMITS[kind]:
            raise sm.MediaError(f"Не больше {sm.LIMITS[kind]} фото — удалите старые")
    row = sm.store(db, owner_id, kind, data, resource_id=r.id)
    if kind == "avatar":
        old = r.photo_id
        r.photo_id = row.id
        if old:
            sm.delete(db, owner_id, old)
    else:
        label = "caption" if kind == "portfolio" else "title"
        items.append({"id": str(row.id), label: " ".join((title or "").split())[:200]})
        setattr(r, key, items)
    return {"id": str(row.id), "url": sm.url_of(row.id), "width": row.width, "height": row.height}


def _detach_media(db: Session, owner_id, r: BookingResource, media_id: str) -> bool:
    mid = str(media_id)
    found = False
    if r.photo_id and str(r.photo_id) == mid:
        r.photo_id, found = None, True
    for key in ("portfolio", "certificates"):
        items = list(getattr(r, key) or [])
        left = [it for it in items if not (isinstance(it, dict) and str(it.get("id")) == mid)]
        if len(left) != len(items):
            setattr(r, key, left or None)
            found = True
    if found:
        sm.delete(db, owner_id, mid)
    return found


def _read_upload(file: UploadFile) -> bytes:
    data = file.file.read(sm.MAX_UPLOAD_BYTES + 1)
    if len(data) > sm.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Фото больше 15 МБ — выберите поменьше")
    return data


@router.post("/resources/{resource_id}/media")
def booking_resource_media(resource_id: str, kind: str = Query("portfolio"), file: UploadFile = File(...),
                           title: Optional[str] = Form(None), current_user: User = Depends(get_current_user),
                           db: Session = Depends(get_db)) -> Dict[str, Any]:
    r = _resource_or_404(db, current_user.id, resource_id)
    data = _read_upload(file)
    try:
        media = _attach_media(db, current_user.id, r, kind, data, title or "")
        db.commit()
    except sm.MediaError as e:
        db.rollback()
        _err(e)
    return {"ok": True, "media": media, "resource": _resource_out(current_user, r)}


@router.delete("/resources/{resource_id}/media/{media_id}")
def booking_resource_media_delete(resource_id: str, media_id: str, current_user: User = Depends(get_current_user),
                                  db: Session = Depends(get_db)) -> Dict[str, Any]:
    r = _resource_or_404(db, current_user.id, resource_id)
    if not _detach_media(db, current_user.id, r, media_id):
        raise HTTPException(status_code=404, detail="Фото не найдено")
    db.commit()
    return {"ok": True, "resource": _resource_out(current_user, r)}


# ───────────────────────────── вход мастера ─────────────────────────────

def _logins(db: Session, user) -> List[Dict[str, Any]]:
    """Входы сотрудников для привязки к мастерам (только владельцу)."""
    if is_staff(user):
        return []
    from backend.services import team_service as ts
    return [{"id": str(m.id), "name": m.name, "email": m.email, "is_active": bool(m.is_active),
             "role_title": m.role_title, "permissions": ts.clean_permissions(m.permissions)}
            for m in ts.list_members(db, user.id)]


def _link(db: Session, owner_id, r: BookingResource, member_id) -> None:
    """Один вход — один мастер: у других мастеров этот вход снимается."""
    if member_id is None:
        r.team_member_id = None
        return
    db.query(BookingResource).filter(BookingResource.user_id == owner_id,
                                     BookingResource.team_member_id == member_id,
                                     BookingResource.id != r.id).update({"team_member_id": None},
                                                                        synchronize_session=False)
    r.team_member_id = member_id


class LoginLinkIn(BaseModel):
    team_member_id: Optional[str] = None


@router.put("/resources/{resource_id}/login")
def booking_resource_login_link(resource_id: str, body: LoginLinkIn, current_user: User = Depends(get_current_user),
                                db: Session = Depends(get_db)) -> Dict[str, Any]:
    ensure_owner(current_user, "Привязка входа мастера")
    from backend.services import team_service as ts
    r = _resource_or_404(db, current_user.id, resource_id)
    member = None
    if body.team_member_id:
        try:
            member = ts._get_member(db, current_user.id, body.team_member_id)
        except ts.TeamError as e:
            raise HTTPException(status_code=e.status, detail={"error": e.code, "message": e.message, **e.extra})
    _link(db, current_user.id, r, member.id if member else None)
    db.commit()
    return {"ok": True, "resource": _resource_out(current_user, r), "logins": _logins(db, current_user)}


class LoginCreateIn(BaseModel):
    email: str = Field(..., max_length=255)
    password: Optional[str] = Field(None, max_length=128)


@router.post("/resources/{resource_id}/login")
def booking_resource_login_create(resource_id: str, body: LoginCreateIn, current_user: User = Depends(get_current_user),
                                  db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Вход мастера одной кнопкой: сотрудник «Мастер» с разделом «Свой журнал», сразу привязан."""
    ensure_owner(current_user, "Вход для мастера")
    from backend.core.dependencies import ensure_not_impersonating
    ensure_not_impersonating(current_user, "Оплата места сотрудника")
    from backend.services import team_service as ts
    r = _resource_or_404(db, current_user.id, resource_id)
    rid = r.id
    try:
        member, password = ts.create_member(db, current_user, name=r.name, email=body.email, password=body.password,
                                            phone=r.phone, role_title="Мастер", permissions=["booking_own"])
    except ts.TeamError as e:
        raise HTTPException(status_code=e.status, detail={"error": e.code, "message": e.message, **e.extra})
    r = _resource_or_404(db, current_user.id, str(rid))
    _link(db, current_user.id, r, member.id)
    db.commit()
    return {"ok": True, "resource": _resource_out(current_user, r), "logins": _logins(db, current_user),
            "member": ts.member_dict(member), "password": password}


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


def widget_code(host: str, slug: str) -> str:
    """Код кнопки «Записаться онлайн» для сайта владельца (static/booking-widget.js)."""
    return (f'<script src="{host}/static/booking-widget.js" data-slug="{slug}" '
            f'data-text="Записаться онлайн" async></script>')


@router.get("/link-kit")
def booking_link_kit(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Всё, чем владелец делится с клиентами: ссылка, QR (печать на ресепшн, в Instagram), код виджета на сайт."""
    from backend.core.config import settings
    page = bs.get_page(db, current_user)
    db.commit()
    host = (getattr(settings, "HOST_URL", "") or "").rstrip("/") or "https://voksyai.online"
    info = _page_dict(page)
    qr = None
    if info.get("url"):
        try:
            from backend.services.telegram_user_service import qr_svg_data_uri
            qr = qr_svg_data_uri(info["url"])
        except Exception as e:   # нет segno — ссылка и код всё равно нужны
            logger.warning("booking qr: %s", e)
    return {"ok": True, "page": info, "qr": qr,
            "widget_code": widget_code(host, page.slug) if page.slug else None,
            "button_code": (f'<a href="{info["url"]}" data-voksy-booking>Записаться онлайн</a>' if info.get("url") else None)}


# ───────────────────────────── кабинет мастера ─────────────────────────────

def _me_resource(db: Session, user: User, resource_id: Optional[str] = None) -> BookingResource:
    """Чей кабинет. Мастер (сотрудник) — только свой, по привязке входа; владелец и сотрудник
    с полным журналом — любой мастер по resource_id («как видит мастер»)."""
    perms = _staff_perms(user)
    full = perms is None or bool(perms & {"booking", "booking_setup"})
    if resource_id and full:
        return _resource_or_404(db, user.id, resource_id)
    if perms is None:
        rows = bs.resources(db, user.id)
        if not rows:
            raise HTTPException(status_code=404, detail="Сначала добавьте мастера в журнале записи")
        return rows[0]
    r = mc.find_for_member(db, user.id, user._staff_member_id)
    if r is None:
        raise HTTPException(status_code=404, detail={
            "error": "master_not_linked",
            "message": "Ваш вход ещё не привязан к мастеру в журнале. Попросите администратора: "
                       "Записи → Мастера → ваша карточка → «Вход в кабинет»."})
    if not r.is_active:
        raise HTTPException(status_code=403, detail={"error": "master_disabled",
                                                     "message": "Ваша карточка мастера выключена. Обратитесь к администратору."})
    return r


@router.get("/me")
def master_overview(date: Optional[str] = None, days: int = Query(7, ge=1, le=31), resource_id: Optional[str] = None,
                    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    r = _me_resource(db, current_user, resource_id)
    try:
        out = mc.overview(db, current_user, r, date, days)
    except bs.BookingError as e:
        _err(e)
    db.commit()
    out["preview"] = not is_staff(current_user) or bool(resource_id)
    return out


@router.get("/me/clients")
def master_clients(resource_id: Optional[str] = None, current_user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)) -> Dict[str, Any]:
    r = _me_resource(db, current_user, resource_id)
    cfg = bs.settings_of(bs.get_page(db, current_user, create=False))
    return {"clients": mc.clients(db, r, bool(cfg.get("master_phones")))}


@router.get("/me/slots")
def master_slots(service_id: Optional[str] = None, date: Optional[str] = None, days: int = Query(1, ge=1, le=14),
                 resource_id: Optional[str] = None, current_user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)) -> Dict[str, Any]:
    r = _me_resource(db, current_user, resource_id)
    try:
        service = bs.find_service(db, current_user.id, service_id) if service_id else None
        out = bs.free_slots(db, current_user, service, r.id, date, days, ignore_lead=True)
        db.commit()
    except bs.BookingError as e:
        _err(e)
    return {"days": out}


class MasterBookingIn(BaseModel):
    start: str
    service_id: Optional[str] = None
    client_name: Optional[str] = Field(None, max_length=200)
    client_phone: Optional[str] = Field(None, max_length=40)
    contact_id: Optional[str] = None
    note: Optional[str] = Field(None, max_length=2000)


@router.post("/me/bookings")
def master_booking_create(body: MasterBookingIn, resource_id: Optional[str] = None,
                          current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    r = _me_resource(db, current_user, resource_id)
    if not (body.client_name or body.contact_id):
        raise HTTPException(status_code=400, detail="Укажите имя клиента")
    try:
        b = bs.create_booking(db, current_user, start=body.start, service_id=body.service_id, resource_id=r.id,
                              contact_id=body.contact_id, client_name=body.client_name, client_phone=body.client_phone,
                              note=body.note, source="staff" if is_staff(current_user) else "owner")
        db.commit()
    except bs.BookingError as e:
        db.rollback()
        _err(e)
    cfg = bs.settings_of(bs.get_page(db, current_user, create=False))
    return {"ok": True, "booking": mc.booking_view(b, bool(cfg.get("master_phones")))}


@router.patch("/me/bookings/{booking_id}")
def master_booking_update(booking_id: str, data: Dict[str, Any] = Body(...), resource_id: Optional[str] = None,
                          current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Мастер: статус (кроме отмены), заметка, перенос СВОЕЙ записи (к себе же)."""
    r = _me_resource(db, current_user, resource_id)
    status = data.get("status")
    if status is not None and status not in mc.MASTER_STATUSES:
        raise HTTPException(status_code=403, detail="Отменить запись может администратор")
    try:
        mc.check_own_booking(db, current_user.id, r, booking_id)
        b = bs.update_booking(db, current_user, booking_id, start=data.get("start"), status=status,
                              note=data.get("note"))
        db.commit()
    except bs.BookingError as e:
        db.rollback()
        _err(e)
    cfg = bs.settings_of(bs.get_page(db, current_user, create=False))
    return {"ok": True, "booking": mc.booking_view(b, bool(cfg.get("master_phones")))}


class MasterProfileIn(BaseModel):
    bio: Optional[str] = Field(None, max_length=1500)
    portfolio: Optional[List[Dict[str, Any]]] = None


@router.patch("/me/profile")
def master_profile(body: MasterProfileIn, resource_id: Optional[str] = None,
                   current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    r = _me_resource(db, current_user, resource_id)
    if body.bio is not None:
        r.bio = body.bio.strip() or None
    if body.portfolio is not None:
        r.portfolio = _reorder_media(r.portfolio, body.portfolio, "caption")
    db.commit()
    return {"ok": True, "master": bs.resource_to_dict(r)}


@router.post("/me/media")
def master_media(file: UploadFile = File(...), title: Optional[str] = Form(None), resource_id: Optional[str] = None,
                 current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Мастер сам добавляет фото своих работ — они появятся на странице салона."""
    r = _me_resource(db, current_user, resource_id)
    data = _read_upload(file)
    try:
        media = _attach_media(db, current_user.id, r, "portfolio", data, title or "")
        db.commit()
    except sm.MediaError as e:
        db.rollback()
        _err(e)
    return {"ok": True, "media": media, "master": bs.resource_to_dict(r)}


@router.delete("/me/media/{media_id}")
def master_media_delete(media_id: str, resource_id: Optional[str] = None, current_user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)) -> Dict[str, Any]:
    r = _me_resource(db, current_user, resource_id)
    ids = {str(it.get("id")) for it in (r.portfolio or []) if isinstance(it, dict)}
    if str(media_id) not in ids or not _detach_media(db, current_user.id, r, media_id):
        raise HTTPException(status_code=404, detail="Фото не найдено")
    db.commit()
    return {"ok": True, "master": bs.resource_to_dict(r)}


# ───────────────────────────── фото без входа ─────────────────────────────

@media_router.get("/media/salon/{media_id}")
def salon_media_file(media_id: str, db: Session = Depends(get_db)):
    """Фото мастера / работы / салона. Новое фото = новый id, поэтому кэш на год."""
    data = sm.read_bytes(db, media_id)
    if not data:
        raise HTTPException(status_code=404, detail="Фото не найдено")
    return Response(content=data, media_type="image/jpeg", headers={
        "Cache-Control": "public, max-age=31536000, immutable", "ETag": f'"{media_id}"',
        "X-Content-Type-Options": "nosniff"})


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
        "resources": [public_master(r) for r in bs.resources(db, owner.id) if bs.bookable_online(r)],
    }


def public_master(r: BookingResource) -> Dict[str, Any]:
    """Мастер для клиента: фото, о себе, опыт, работы, свои цены — без телефона, графика и оплаты."""
    d = bs.resource_to_dict(r)
    return {k: d[k] for k in ("id", "name", "role", "color", "initials", "service_ids", "photo_url", "bio",
                              "experience_years", "portfolio", "certificates", "service_prices")}


@public_router.get("/{slug}/slots")
def public_booking_slots(slug: str, service_id: Optional[str] = None, resource_id: Optional[str] = None,
                         date: Optional[str] = None, db: Session = Depends(get_db)) -> Dict[str, Any]:
    page, owner = _public_page(db, slug)
    try:
        service = bs.find_service(db, owner.id, service_id) if service_id else None
        days = bs.free_slots(db, owner, service, resource_id, date, 1, online_only=True)
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
