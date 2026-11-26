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

Услуги салона (шаг 1.3, services/beauty_services.py — над каталогом бота):
  GET    /services                    — услуги с категориями, мастерами и их ценами
  POST   /services, PATCH /services/{id}, DELETE /services/{id} — услуга (+ кто делает и за сколько)
  POST   /services/categories/rename  — переименовать категорию или подкатегорию
  POST   /services/categories/order   — порядок категорий

Страница салона (шаг 1.5, services/salon_page.py):
  GET    /page                        — профиль страницы, готовность, ссылки, QR, код виджета
  PUT    /page                        — тексты, контакты, соцсети, удобства, акции, цвет, порядок галереи
  POST   /page/media?kind=logo|cover|gallery, DELETE /page/media/{id} — фото страницы
  Публично: GET /api/public/booking/{slug} отдаёт ещё и `salon` (витрина), /slots — до 14 дней,
  POST /{slug}/visit-slots и /{slug}/visit — визит из нескольких услуг по ссылке.

Журнал v2 (шаг 1.4):
  POST   /blocks, DELETE /blocks/{id}  — блок времени (перерыв, обучение, отпуск, больничный; мастер или все)
  GET    /rooms, POST /rooms, PATCH /rooms/{id}, DELETE /rooms/{id} — кабинеты, кресла, аппараты
  POST   /visit-slots                 — когда помещается визит из нескольких услуг {items, date}
  POST   /visits, DELETE /visits/{id} — записать визит целиком / отменить весь визит
  GET    /stream                      — поток обновлений журнала (text/event-stream, services/booking_stream.py)
  PATCH  /bookings/{id}               — принимает и duration_min (растяжка записи в журнале)

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
from backend.services import activity
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


def _host() -> str:
    from backend.core.config import settings
    return (getattr(settings, "HOST_URL", "") or "").rstrip("/") or "https://voksyai.online"


def _page_dict(page: BookingPage, niche: Optional[str] = None) -> Dict[str, Any]:
    from backend.services.salon_page import page_path
    host = _host()
    return {"slug": page.slug, "enabled": bool(page.enabled), "settings": bs.settings_of(page),
            "url": f"{host}/b/{page.slug}" if page.slug else None,
            "page_url": f"{host}{page_path(page, niche)}" if page.slug else None}


# ───────────────────────────── кабинет ─────────────────────────────

@router.get("/overview")
def booking_overview(date: Optional[str] = None, days: int = Query(1, ge=1, le=42),
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
        "services": [bs.service_to_dict(e) for e in _sorted_services(db, current_user)],
        "bookings": [bs.booking_to_dict(b, names) for b in rows],
        "blocks": [bs.block_to_dict(k) for k in bs.list_blocks(db, current_user.id, start, end)],
        "rooms": [bs.room_to_dict(r) for r in bs.rooms(db, current_user.id, active_only=False)],
        "logins": _logins(db, current_user),
        "page": _page_dict(page, current_user.business_niche),
        "stats": bs.stats(db, current_user.id, start, end),
        "plan_limit": _plan_limit(db, current_user),
        "waitlist": _waitlist_count(db, current_user.id),
    }


def _waitlist_count(db: Session, user_id) -> int:
    try:
        from backend.services import waitlist_service
        return waitlist_service.active_count(db, user_id)
    except Exception:
        db.rollback()
        return 0


def _sorted_services(db: Session, user) -> List[Any]:
    """Активные услуги в порядке категорий владельца (как в прайсе «Услуги»)."""
    from backend.services.beauty_services import sort_services
    return sort_services(db, user, bs.services(db, user.id))


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
                              price=data.get("price"), cancel_reason=data.get("cancel_reason"),
                              prepaid=data.get("prepaid") if isinstance(data.get("prepaid"), bool) else None,
                              duration_min=data.get("duration_min"))
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


_RES_LOG = {"name": "имя", "role": "специализация", "phone": "телефон", "schedule": "график", "days_off": "выходные даты",
            "services": "услуги", "service_prices": "свои цены", "is_active": "работает", "online_booking": "онлайн-запись",
            "bio": "о себе", "experience_since": "работает с", "photos": "фото и работы"}


def _res_state(db: Session, user_id, r: BookingResource) -> Dict[str, Any]:
    """Что из карточки мастера видно в журнале действий (было → стало)."""
    titles = {str(e.id): e.title for e in bs.services(db, user_id)}
    ids = [str(x) for x in r.service_ids] if r.service_ids is not None else None
    return {"name": r.name, "role": r.role, "phone": r.phone, "schedule": bs.schedule_text(r.schedule),
            "days_off": ", ".join(r.days_off or []) or None,
            "services": "все" if ids is None else (", ".join(titles.get(x, "?") for x in ids[:8]) + (" …" if len(ids) > 8 else "") or "никакие"),
            "service_prices": {titles.get(k, "?"): v.get("price") for k, v in (r.service_prices or {}).items()} or None,
            "is_active": bool(r.is_active), "online_booking": r.online_booking is not False,
            "bio": (r.bio or "")[:80] or None, "experience_since": r.experience_since,
            "photos": len(_media_ids(r))}


def _res_fmt(k: str, v) -> str:
    if v is None or v == "" or v == {}:
        return "—"
    if isinstance(v, bool):
        return "да" if v else "нет"
    if k == "service_prices":
        return ", ".join(f"{t} {activity.money(p)}" for t, p in v.items())
    return str(v)


def _log_resource(db: Session, user: User, r: BookingResource, before: Optional[Dict[str, Any]], action: str = "") -> None:
    if before is None:
        activity.log(db, user.id, "master.create", "master", f"{r.name}" + (f" · {r.role}" if r.role else "")
                     + f" · график: {bs.schedule_text(r.schedule)}", object_id=r.id, user=user)
        return
    after = _res_state(db, user.id, r)
    ch = activity.diff(before, after, list(_RES_LOG))
    if not ch:
        return
    if set(ch) == {"photos"}:
        parts = [f"фото и работы: {before['photos']} → {after['photos']}"]
    else:
        ch.pop("photos", None)
        parts = [f"{_RES_LOG[k]}: {_res_fmt(k, a)} → {_res_fmt(k, b)}" for k, (a, b) in ch.items()]
    activity.log(db, user.id, action or "master.update", "master", f"{r.name}: " + "; ".join(parts),
                 object_id=r.id, changes=ch, user=user)


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
    db.flush()
    bs.notify_changed(db, current_user.id)
    _log_resource(db, current_user, r, None)
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
    before = _res_state(db, current_user.id, r)
    try:
        _apply_resource(r, body)
    except bs.BookingError as e:
        _err(e)
    bs.notify_changed(db, current_user.id)
    _log_resource(db, current_user, r, before)
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
        activity.log(db, current_user.id, "master.update", "master",
                     f"{r.name}: выключен (впереди {len(upcoming)} зап. — остаются в журнале)", object_id=r.id,
                     changes={"is_active": [True, False]}, user=current_user)
        db.commit()
        return {"ok": True, "disabled": True, "upcoming": len(upcoming)}
    sm.delete_many(db, current_user.id, _media_ids(r))
    activity.log(db, current_user.id, "master.delete", "master", r.name, object_id=r.id, user=current_user)
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
    activity.log(db, current_user.id, "master.login", "master",
                 f"{r.name}: " + (f"вход привязан к сотруднику {member.name} ({member.email})" if member else "вход отвязан"),
                 object_id=r.id, user=current_user)
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
    activity.log(db, current_user.id, "master.login", "master", f"{r.name}: создан вход ({member.email})",
                 object_id=r.id, user=current_user)
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
    before = dict(bs.settings_of(page), enabled=bool(page.enabled), slug=page.slug)
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
    _log_settings(db, current_user, before, dict(bs.settings_of(page), enabled=bool(page.enabled), slug=page.slug))
    db.commit()
    return {"ok": True, "page": _page_dict(page, current_user.business_niche)}


_SETTINGS_LOG = {"step_min": "шаг сетки, мин", "buffer_min": "перерыв между записями, мин",
                 "lead_min": "не раньше чем за, мин", "horizon_days": "запись вперёд, дней", "reminders": "напоминания",
                 "thanks": "«спасибо» после визита", "address": "адрес", "title": "название",
                 "master_phones": "мастера видят телефоны", "prepay_info": "как внести предоплату",
                 "cancel_hours": "отмена ботом не позже чем за, ч", "enabled": "онлайн-запись", "slug": "адрес ссылки"}


def _log_settings(db: Session, user: User, before: Dict[str, Any], after: Dict[str, Any]) -> None:
    ch = activity.diff(before, after, list(_SETTINGS_LOG))
    if not ch:
        return
    def f(v):
        return "да" if v is True else "нет" if v is False else ("—" if v in (None, "") else str(v)[:80])
    activity.log(db, user.id, "settings.update", "settings",
                 "; ".join(f"{_SETTINGS_LOG[k]}: {f(a)} → {f(b)}" for k, (a, b) in ch.items()), changes=ch, user=user)


def _qr(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    try:
        from backend.services.telegram_user_service import qr_svg_data_uri
        return qr_svg_data_uri(url)
    except Exception as e:   # нет segno — ссылка и код всё равно нужны
        logger.warning("booking qr: %s", e)
        return None


def widget_code(host: str, slug: str) -> str:
    """Код кнопки «Записаться онлайн» для сайта владельца (static/booking-widget.js)."""
    return (f'<script src="{host}/static/booking-widget.js" data-slug="{slug}" '
            f'data-text="Записаться онлайн" async></script>')


def chat_widget_code(host: str, slug: str) -> str:
    """Код кнопки чата с администратором для сайта владельца (тот же booking-widget.js, режим чата)."""
    return (f'<script src="{host}/static/booking-widget.js" data-slug="{slug}" data-chat="1" '
            f'data-text="Написать администратору" async></script>')


@router.get("/link-kit")
def booking_link_kit(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Всё, чем владелец делится с клиентами: ссылка, QR (печать на ресепшн, в Instagram), код виджета на сайт."""
    page = bs.get_page(db, current_user)
    db.commit()
    host = _host()
    info = _page_dict(page, current_user.business_niche)
    qr = _qr(info.get("url"))
    return {"ok": True, "page": info, "qr": qr, "qr_page": _qr(info.get("page_url")),
            "widget_code": widget_code(host, page.slug) if page.slug else None,
            "button_code": (f'<a href="{info["url"]}" data-voksy-booking>Записаться онлайн</a>' if info.get("url") else None)}


# ───────────────────────────── страница салона (шаг 1.5) ─────────────────────────────

def _page_payload(db: Session, user: User, page: BookingPage) -> Dict[str, Any]:
    from backend.services import salon_page as sp
    info = _page_dict(page, user.business_niche)
    prof = sp.profile_of(page)
    host = _host()
    return {
        "ok": True, "page": info,
        "profile": {**{k: prof.get(k) for k in ("tagline", "about", "phone", "whatsapp", "instagram", "telegram",
                                               "website", "map_url", "hours_note", "amenities", "promos", "accent",
                                               "show_prices")},
                    "title": bs.settings_of(page).get("title") or "", "address": bs.settings_of(page).get("address") or "",
                    "logo": {"id": prof.get("logo_id"), "url": sm.url_of(prof.get("logo_id"))} if prof.get("logo_id") else None,
                    "cover": {"id": prof.get("cover_id"), "url": sm.url_of(prof.get("cover_id"))} if prof.get("cover_id") else None,
                    "gallery": [{"id": it["id"], "url": sm.url_of(it["id"]), "caption": it.get("caption") or ""}
                                for it in prof.get("gallery") or [] if isinstance(it, dict) and it.get("id")]},
        "default_title": user.company_name or user.first_name or "",
        "readiness": sp.readiness(db, page, user),
        "hours": sp.salon_hours(bs.resources(db, user.id)),
        "amenities": [{"key": k, "title": t} for k, t in sp.AMENITIES.items()],
        "accents": list(sp.ACCENTS),
        "qr": _qr(info.get("page_url")), "qr_book": _qr(info.get("url")),
        "widget_code": widget_code(host, page.slug) if page.slug else None,
        "limits": {"gallery": sp.MAX_GALLERY, "promos": sp.MAX_PROMOS},
        "chat": _chat_cabinet(db, page, user),
        "chat_widget_code": chat_widget_code(host, page.slug) if page.slug else None,
    }


def _chat_cabinet(db: Session, page: BookingPage, user: User) -> Dict[str, Any]:
    from backend.services import webchat_service
    try:
        return webchat_service.cabinet_state(db, page, user)
    except Exception as e:
        logger.warning(f"[BOOKING] chat state failed: {e}")
        db.rollback()
        return {"enabled": False, "live": False, "reason": "", "title": "", "greeting": ""}


@router.get("/page")
def booking_page_get(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    page = bs.get_page(db, current_user)
    db.commit()
    return _page_payload(db, current_user, page)


@router.put("/page")
def booking_page_put(data: Dict[str, Any] = Body(...), current_user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)) -> Dict[str, Any]:
    from backend.services import salon_page as sp
    from backend.services import webchat_service
    page = bs.get_page(db, current_user)
    before_prof, before_cfg = dict(page.profile or {}), dict(page.settings or {})
    try:
        sp.save_profile(db, page, data or {})
        webchat_service.save_settings(page, data or {})
        _log_page(db, current_user, page, before_prof, before_cfg)
        db.commit()
    except sp.PageError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    return _page_payload(db, current_user, page)


_PAGE_LOG = {"title": "название", "address": "адрес", "tagline": "слоган", "about": "о салоне", "phone": "телефон",
             "whatsapp": "WhatsApp", "instagram": "Instagram", "telegram": "Telegram", "website": "сайт",
             "map_url": "карта", "hours_note": "примечание к часам", "amenities": "удобства", "promos": "акции",
             "accent": "цвет", "show_prices": "показывать цены", "gallery": "порядок и подписи фото"}
_CHAT_LOG = {"chat_enabled": "чат", "chat_title": "подпись чата", "chat_greeting": "первое сообщение"}


def _log_page(db: Session, user: User, page, before_prof: Dict[str, Any], before_cfg: Dict[str, Any]) -> None:
    """Страница салона и чат: какие поля изменились (тексты длинные — без «было → стало» целиком)."""
    prof, cfg = dict(page.profile or {}), dict(page.settings or {})
    b = dict(before_prof, title=before_cfg.get("title"), address=before_cfg.get("address"))
    a = dict(prof, title=cfg.get("title"), address=cfg.get("address"))
    ch = activity.diff(b, a, list(_PAGE_LOG))
    if ch:
        names = []
        for k, (x, y) in ch.items():
            if k in ("tagline", "about", "promos", "amenities", "gallery") or isinstance(y, (list, dict)):
                names.append(_PAGE_LOG[k])
            elif isinstance(y, bool):
                names.append(f"{_PAGE_LOG[k]}: {'да' if y else 'нет'}")
            else:
                names.append(f"{_PAGE_LOG[k]}: {(str(x) if x else '—')[:60]} → {(str(y) if y else '—')[:60]}")
        activity.log(db, user.id, "page.update", "page", "изменено: " + "; ".join(names),
                     changes={k: v for k, v in ch.items() if k not in ("about", "promos", "gallery")}, user=user)
    cc = activity.diff(before_prof, prof, list(_CHAT_LOG))
    if cc:
        parts = []
        for k, (x, y) in cc.items():
            parts.append(f"{_CHAT_LOG[k]}: {'включён' if y else 'выключен'}" if k == "chat_enabled"
                         else f"{_CHAT_LOG[k]} изменено")
        activity.log(db, user.id, "chat.update", "page", "; ".join(parts), changes=cc, user=user)


@router.post("/page/media")
def booking_page_media(kind: str = Query(...), file: UploadFile = File(...), caption: Optional[str] = Form(None),
                       current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Логотип и обложка заменяют прежние (старое фото удаляется), галерея — до MAX_GALLERY фото."""
    from backend.services import salon_page as sp
    if kind not in ("logo", "cover", "gallery"):
        raise HTTPException(status_code=400, detail="Вид фото: logo, cover или gallery")
    page = bs.get_page(db, current_user)
    prof = sp.profile_of(page)
    if kind == "gallery" and len(prof.get("gallery") or []) >= sp.MAX_GALLERY:
        raise HTTPException(status_code=400, detail=f"В галерее не больше {sp.MAX_GALLERY} фото — удалите лишние")
    data = _read_upload(file)
    try:
        row = sm.store(db, current_user.id, "logo" if kind == "logo" else "salon", data)
    except sm.MediaError as e:
        db.rollback()
        _err(e)
    if kind in ("logo", "cover"):
        old = prof.get(f"{kind}_id")
        prof[f"{kind}_id"] = str(row.id)
        if old:
            sm.delete(db, current_user.id, old)
    else:
        prof["gallery"] = list(prof.get("gallery") or []) + [{"id": str(row.id), "caption": " ".join(str(caption or "").split())[:120]}]
    page.profile = prof
    bs.notify_changed(db, current_user.id)
    activity.log(db, current_user.id, "page.media", "page",
                 {"logo": "новый логотип", "cover": "новая обложка"}.get(kind, "фото в галерею"), user=current_user)
    db.commit()
    return _page_payload(db, current_user, page)


@router.delete("/page/media/{media_id}")
def booking_page_media_delete(media_id: str, current_user: User = Depends(get_current_user),
                              db: Session = Depends(get_db)) -> Dict[str, Any]:
    from backend.services import salon_page as sp
    page = bs.get_page(db, current_user)
    prof = sp.profile_of(page)
    mid = str(media_id)
    found = False
    for k in ("logo_id", "cover_id"):
        if str(prof.get(k) or "") == mid:
            prof[k] = None
            found = True
    gal = [it for it in prof.get("gallery") or [] if str((it or {}).get("id")) != mid]
    if len(gal) != len(prof.get("gallery") or []):
        prof["gallery"] = gal
        found = True
    if not found:
        raise HTTPException(status_code=404, detail="Фото не найдено")
    sm.delete(db, current_user.id, mid)
    page.profile = prof
    bs.notify_changed(db, current_user.id)
    activity.log(db, current_user.id, "page.media", "page", "фото убрано со страницы", user=current_user)
    db.commit()
    return _page_payload(db, current_user, page)


# ───────────────────────────── журнал v2: блоки, кабинеты, визиты, поток ─────────────────────────────

class BlockIn(BaseModel):
    start: str
    end: str
    resource_id: Optional[str] = None
    kind: str = Field("block", max_length=16)
    note: Optional[str] = Field(None, max_length=300)


# ───────────────────────────── лист ожидания (шаг 2.4) ─────────────────────────────

class WaitlistIn(BaseModel):
    service_id: str
    resource_id: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    time_from: Optional[str] = Field(None, max_length=5)
    time_to: Optional[str] = Field(None, max_length=5)
    contact_id: Optional[str] = None
    client_name: Optional[str] = Field(None, max_length=200)
    client_phone: Optional[str] = Field(None, max_length=40)
    note: Optional[str] = Field(None, max_length=1000)


def _wl(fn, *args, **kw):
    from backend.services import waitlist_service as wl
    try:
        return fn(*args, **kw)
    except wl.WaitlistError as e:
        raise HTTPException(status_code=404 if "не найдена" in str(e) else 400, detail=str(e))


@router.get("/waitlist")
def booking_waitlist(status: Optional[str] = Query("active"), current_user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)) -> Dict[str, Any]:
    from backend.services import waitlist_service as wl
    out = wl.listing(db, current_user.id, status=status)
    db.rollback()
    return out


@router.post("/waitlist")
def booking_waitlist_add(body: WaitlistIn, current_user: User = Depends(get_current_user),
                         db: Session = Depends(get_db)) -> Dict[str, Any]:
    from backend.services import waitlist_service as wl
    try:
        e = wl.add(db, current_user, service_id=body.service_id, resource_id=body.resource_id, date_from=body.date_from,
                   date_to=body.date_to, time_from=body.time_from, time_to=body.time_to, contact_id=body.contact_id,
                   name=body.client_name, phone=body.client_phone, note=body.note,
                   source="staff" if is_staff(current_user) else "owner")
        db.commit()
    except wl.WaitlistError as ex:
        db.rollback()
        _err(ex)
    return {"ok": True, "entry": wl.entry_to_dict(e, _names(db, current_user.id))}


@router.patch("/waitlist/{entry_id}")
def booking_waitlist_update(entry_id: str, data: Dict[str, Any] = Body(...), current_user: User = Depends(get_current_user),
                            db: Session = Depends(get_db)) -> Dict[str, Any]:
    from backend.services import waitlist_service as wl
    e = _wl(wl.update, db, current_user, entry_id, data or {})
    db.commit()
    return {"ok": True, "entry": wl.entry_to_dict(e, _names(db, current_user.id))}


@router.delete("/waitlist/{entry_id}")
def booking_waitlist_cancel(entry_id: str, current_user: User = Depends(get_current_user),
                            db: Session = Depends(get_db)) -> Dict[str, Any]:
    from backend.services import waitlist_service as wl
    _wl(wl.cancel, db, current_user, entry_id)
    db.commit()
    return {"ok": True}


@router.post("/waitlist/{entry_id}/offer")
def booking_waitlist_offer(entry_id: str, current_user: User = Depends(get_current_user),
                           db: Session = Depends(get_db)) -> Dict[str, Any]:
    from backend.services import waitlist_service as wl
    out = _wl(wl.offer_now, db, current_user, entry_id)
    db.commit()
    return out


@router.post("/blocks")
def booking_block_create(body: BlockIn, current_user: User = Depends(get_current_user),
                         db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        k, clash = bs.create_block(db, current_user, start=body.start, end=body.end, resource_id=body.resource_id,
                                   kind=body.kind, note=body.note)
        db.commit()
    except (bs.BookingError, ValueError) as e:
        db.rollback()
        _err(e)
    return {"ok": True, "block": bs.block_to_dict(k), "clash": clash}


@router.delete("/blocks/{block_id}")
def booking_block_delete(block_id: str, current_user: User = Depends(get_current_user),
                         db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        bs.delete_block(db, current_user, block_id)
        db.commit()
    except bs.BookingError as e:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e))
    return {"ok": True}


class RoomIn(BaseModel):
    name: Optional[str] = Field(None, max_length=120)
    kind: Optional[str] = Field(None, max_length=16)
    is_active: Optional[bool] = None
    position: Optional[int] = None


def _room_or_404(db: Session, user_id, rid: str):
    from backend.models.booking import BookingRoom
    try:
        r = db.query(BookingRoom).filter(BookingRoom.id == uuid.UUID(rid), BookingRoom.user_id == user_id).first()
    except ValueError:
        r = None
    if r is None:
        raise HTTPException(status_code=404, detail="Кабинет не найден")
    return r


def _apply_room(r, body: RoomIn) -> None:
    data = body.dict(exclude_unset=True)
    if "name" in data:
        name = " ".join(str(data["name"] or "").split())[:120]
        if not name:
            raise HTTPException(status_code=400, detail="Укажите название кабинета")
        r.name = name
    if "kind" in data and data["kind"]:
        if data["kind"] not in bs.ROOM_KINDS:
            raise HTTPException(status_code=400, detail="Неизвестный вид: кабинет, кресло или аппарат")
        r.kind = data["kind"]
    if "is_active" in data and data["is_active"] is not None:
        r.is_active = bool(data["is_active"])
    if "position" in data and data["position"] is not None:
        r.position = int(data["position"])


@router.get("/rooms")
def booking_rooms(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    return {"rooms": [bs.room_to_dict(r) for r in bs.rooms(db, current_user.id, active_only=False)]}


@router.post("/rooms")
def booking_room_create(body: RoomIn, current_user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)) -> Dict[str, Any]:
    from backend.models.booking import BookingRoom
    count = len(bs.rooms(db, current_user.id, active_only=False))
    if count >= 50:
        raise HTTPException(status_code=400, detail="Не больше 50 кабинетов")
    r = BookingRoom(user_id=current_user.id, name="Кабинет", kind="room", position=count)
    _apply_room(r, body)
    db.add(r)
    db.flush()
    bs.notify_changed(db, current_user.id)
    activity.log(db, current_user.id, "room.create", "room", f"{bs.ROOM_KINDS.get(r.kind, 'Кабинет')} «{r.name}»",
                 object_id=r.id, user=current_user)
    db.commit()
    return {"ok": True, "room": bs.room_to_dict(r)}


@router.patch("/rooms/{room_id}")
def booking_room_update(room_id: str, body: RoomIn, current_user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)) -> Dict[str, Any]:
    r = _room_or_404(db, current_user.id, room_id)
    before = {"name": r.name, "kind": r.kind, "is_active": bool(r.is_active)}
    _apply_room(r, body)
    bs.notify_changed(db, current_user.id)
    ch = activity.diff(before, {"name": r.name, "kind": r.kind, "is_active": bool(r.is_active)})
    if ch:
        activity.log(db, current_user.id, "room.update", "room", f"«{r.name}»: " + "; ".join(
            {"name": "название", "kind": "вид", "is_active": "работает"}[k] + f": {a} → {b}" for k, (a, b) in ch.items()),
            object_id=r.id, changes=ch, user=current_user)
    db.commit()
    return {"ok": True, "room": bs.room_to_dict(r)}


@router.delete("/rooms/{room_id}")
def booking_room_delete(room_id: str, current_user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Кабинет с будущими записями выключается (записи остаются), без записей — удаляется и пропадает из услуг."""
    from backend.models.booking import Booking
    from backend.models.agent_knowledge import AgentKnowledgeEntry
    r = _room_or_404(db, current_user.id, room_id)
    upcoming = (db.query(Booking.id).filter(Booking.room_id == r.id, Booking.status.in_(("booked", "confirmed")),
                                            Booking.start_at >= bs.now_utc()).count())
    if upcoming:
        r.is_active = False
        bs.notify_changed(db, current_user.id)
        activity.log(db, current_user.id, "room.update", "room", f"«{r.name}» выключен (впереди {upcoming} зап.)",
                     object_id=r.id, changes={"is_active": [True, False]}, user=current_user)
        db.commit()
        return {"ok": True, "disabled": True, "upcoming": upcoming}
    rid = str(r.id)
    activity.log(db, current_user.id, "room.delete", "room", f"«{r.name}»", object_id=r.id, user=current_user)
    for e in db.query(AgentKnowledgeEntry).filter(AgentKnowledgeEntry.user_id == current_user.id,
                                                  AgentKnowledgeEntry.kind == "product",
                                                  AgentKnowledgeEntry.room_ids.isnot(None)).all():
        if rid in [str(x) for x in (e.room_ids or [])]:
            e.room_ids = [x for x in e.room_ids if str(x) != rid] or None
    db.delete(r)
    bs.notify_changed(db, current_user.id)
    db.commit()
    return {"ok": True, "deleted": True}


class VisitItem(BaseModel):
    service_id: Optional[str] = None
    resource_id: Optional[str] = None


class VisitSlotsIn(BaseModel):
    items: List[VisitItem] = Field(..., min_length=1, max_length=6)
    date: Optional[str] = None


@router.post("/visit-slots")
def booking_visit_slots(body: VisitSlotsIn, current_user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        out = bs.visit_slots(db, current_user, [i.dict() for i in body.items], body.date)
        db.commit()
    except bs.BookingError as e:
        db.rollback()
        _err(e)
    return {"slots": out}


class VisitIn(BaseModel):
    items: List[VisitItem] = Field(..., min_length=1, max_length=6)
    start: str
    contact_id: Optional[str] = None
    client_name: Optional[str] = Field(None, max_length=200)
    client_phone: Optional[str] = Field(None, max_length=40)
    note: Optional[str] = Field(None, max_length=2000)


@router.post("/visits")
def booking_visit_create(body: VisitIn, current_user: User = Depends(get_current_user),
                         db: Session = Depends(get_db)) -> Dict[str, Any]:
    if not (body.client_name or body.contact_id):
        raise HTTPException(status_code=400, detail="Укажите клиента")
    try:
        rows = bs.create_visit(db, current_user, [i.dict() for i in body.items], body.start,
                               client_name=body.client_name, client_phone=body.client_phone, contact_id=body.contact_id,
                               note=body.note, source="staff" if is_staff(current_user) else "owner")
        db.commit()
    except bs.BookingError as e:
        db.rollback()
        _err(e)
    names = _names(db, current_user.id)
    return {"ok": True, "bookings": [bs.booking_to_dict(b, names) for b in rows]}


@router.delete("/visits/{visit_id}")
def booking_visit_cancel(visit_id: str, reason: Optional[str] = None, current_user: User = Depends(get_current_user),
                         db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        rows = bs.cancel_visit(db, current_user, visit_id, reason)
        db.commit()
    except (bs.BookingError, ValueError) as e:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e) or "Визит не найден")
    return {"ok": True, "cancelled": len(rows)}


@router.get("/stream")
async def booking_stream(request: Request, current_user: User = Depends(get_current_user)):
    """Поток обновлений журнала: страница получает «changed» и перезапрашивает данные."""
    from fastapi.responses import StreamingResponse
    from backend.services import booking_stream as stream
    return StreamingResponse(stream.events(current_user.id, request.is_disconnected), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})


# ───────────────────────────── услуги салона ─────────────────────────────

def _svc_fail(e: Exception):
    raise HTTPException(status_code=400, detail=str(e))


@router.get("/services")
def services_list(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    from backend.services import beauty_services as bsv
    out = bsv.overview(db, current_user)
    db.commit()
    return out


@router.post("/services")
def services_create(data: Dict[str, Any] = Body(...), current_user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)) -> Dict[str, Any]:
    from backend.services import beauty_services as bsv
    try:
        e = bsv.save(db, current_user, None, data)
        db.commit()
    except (bsv.ServiceError, bs.BookingError) as ex:
        db.rollback()
        _svc_fail(ex)
    bsv.after_change(db, current_user)
    return {"ok": True, "service": bsv.service_dict(e, bs.resources(db, current_user.id, active_only=False))}


@router.patch("/services/{service_id}")
def services_update(service_id: str, data: Dict[str, Any] = Body(...), current_user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)) -> Dict[str, Any]:
    from backend.services import beauty_services as bsv
    try:
        e = bsv.save(db, current_user, service_id, data)
        db.commit()
    except (bsv.ServiceError, bs.BookingError) as ex:
        db.rollback()
        _svc_fail(ex)
    bsv.after_change(db, current_user)
    return {"ok": True, "service": bsv.service_dict(e, bs.resources(db, current_user.id, active_only=False))}


@router.delete("/services/{service_id}")
def services_delete(service_id: str, current_user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)) -> Dict[str, Any]:
    from backend.services import beauty_services as bsv
    try:
        out = bsv.remove(db, current_user, service_id)
        db.commit()
    except bsv.ServiceError as ex:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(ex))
    bsv.after_change(db, current_user, deleted_ids=[out["id"]] if out.get("deleted") else None)
    return {"ok": True, **{k: v for k, v in out.items() if k != "id"}}


class CategoryRenameIn(BaseModel):
    old: str = Field(..., max_length=120)
    new: str = Field("", max_length=60)
    sub_old: Optional[str] = Field(None, max_length=60)


@router.post("/services/categories/rename")
def services_category_rename(body: CategoryRenameIn, current_user: User = Depends(get_current_user),
                             db: Session = Depends(get_db)) -> Dict[str, Any]:
    from backend.services import beauty_services as bsv
    try:
        n = bsv.rename_category(db, current_user, body.old, body.new, body.sub_old)
        db.commit()
    except bsv.ServiceError as ex:
        db.rollback()
        _svc_fail(ex)
    bsv.after_change(db, current_user)
    return {"ok": True, "renamed": n}


class CategoryOrderIn(BaseModel):
    names: List[str] = Field(default_factory=list, max_length=100)


@router.post("/services/categories/order")
def services_category_order(body: CategoryOrderIn, current_user: User = Depends(get_current_user),
                            db: Session = Depends(get_db)) -> Dict[str, Any]:
    from backend.services import beauty_services as bsv
    try:
        bsv.set_category_order(db, current_user, body.names)
        db.commit()
    except bsv.ServiceError as ex:
        db.rollback()
        _svc_fail(ex)
    return {"ok": True}


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
    from backend.services import salon_page as sp
    from backend.services.niche_catalog import niche_title
    page, owner = _public_page(db, slug)
    cfg = bs.settings_of(page)
    salon = sp.public_view(db, page, owner)
    rows = [e for e in _sorted_services(db, owner) if bs.service_online(e)]
    photos = sp.service_photos(db, owner.id, rows)
    services = []
    for e in rows:
        d = bs.service_to_dict(e)
        d["photo_url"] = photos.get(d["id"])
        if not salon["show_prices"]:
            d.update(price=None, price_to=None, price_text="")
        services.append(d)
    masters = [public_master(r) for r in bs.resources(db, owner.id) if bs.bookable_online(r)]
    if (salon.get("reviews") or {}).get("enabled"):          # рейтинг мастеров по опубликованным отзывам (шаг 2.3)
        from backend.services import reviews_service
        mr = reviews_service.master_ratings(db, owner.id)
        for m in masters:
            if mr.get(m["id"]):
                m["rating"] = mr[m["id"]]
    if not salon["show_prices"]:
        for m in masters:
            m["service_prices"] = {k: {"duration_min": (v or {}).get("duration_min")} for k, v in (m.get("service_prices") or {}).items()}
    return {
        "title": salon["name"],
        "niche": niche_title(owner.business_niche, owner.business_niche_other),
        "address": cfg.get("address") or "",
        "horizon_days": cfg["horizon_days"],
        "services": services,
        "resources": masters,
        "prepay_info": cfg.get("prepay_info") or "",
        "salon": salon,
    }


def public_master(r: BookingResource) -> Dict[str, Any]:
    """Мастер для клиента: фото, о себе, опыт, работы, свои цены — без телефона, графика и оплаты."""
    d = bs.resource_to_dict(r)
    return {k: d[k] for k in ("id", "name", "role", "color", "initials", "service_ids", "photo_url", "bio",
                              "experience_years", "portfolio", "certificates", "service_prices")}


@public_router.get("/{slug}/reviews")
def public_booking_reviews(slug: str, master: Optional[str] = None, offset: int = 0, limit: int = 10,
                           db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Отзывы на странице салона (шаг 2.3): опубликованные с текстом, «Показать ещё», отзывы мастера."""
    from backend.services import reviews_service
    from backend.services import salon_page as sp
    page, owner = _public_page(db, slug)
    if sp.profile_of(page).get("show_reviews", True) is False:
        return {"items": [], "total": 0, "next_offset": None}
    return reviews_service.public_list(db, owner.id, master_id=master, offset=offset, limit=limit)


class PublicWaitlistIn(BaseModel):
    service_id: str
    resource_id: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    time_from: Optional[str] = Field(None, max_length=5)
    time_to: Optional[str] = Field(None, max_length=5)
    client_name: str = Field(..., min_length=1, max_length=200)
    client_phone: str = Field(..., min_length=5, max_length=40)
    note: Optional[str] = Field(None, max_length=500)


@public_router.post("/{slug}/waitlist")
def public_waitlist_add(slug: str, body: PublicWaitlistIn, request: Request, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Клиент со страницы салона встаёт в лист ожидания (шаг 2.4): освободится время — напишем в мессенджер."""
    from backend.services import waitlist_service as wl
    _public_rate(_client_ip(request))
    page, owner = _public_page(db, slug)
    try:
        e = wl.add(db, owner, service_id=body.service_id, resource_id=body.resource_id, date_from=body.date_from,
                   date_to=body.date_to, time_from=body.time_from, time_to=body.time_to, name=body.client_name.strip(),
                   phone=body.client_phone, note=body.note, source="online")
        db.commit()
    except wl.WaitlistError as ex:
        db.rollback()
        _err(ex)
    return {"ok": True, "period": wl._period_words(e), "service": e.service_title,
            "position": wl.listing(db, owner.id, status="active")["counts"]["active"]}


@public_router.get("/{slug}/slots")
def public_booking_slots(slug: str, service_id: Optional[str] = None, resource_id: Optional[str] = None,
                         date: Optional[str] = None, days: int = Query(1, ge=1, le=14),
                         db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Свободное время: один день или до 14 (страница салона отмечает дни без мест)."""
    page, owner = _public_page(db, slug)
    try:
        service = bs.find_service(db, owner.id, service_id) if service_id else None
        days = bs.free_slots(db, owner, service, resource_id, date, days, online_only=True)
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


def _client_ip(request: Request) -> str:
    return request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "?")


def _notify_owner_later(owner_id, booking_id) -> None:
    """Владельцу в Telegram — в фоновом цикле (обработчик синхронный, своего цикла у него нет)."""
    try:
        from backend.core.background_loop import submit
        from backend.services.booking_notify import notify_owner_new_booking
        submit(notify_owner_new_booking(owner_id, booking_id))
    except Exception as e:
        logger.warning(f"[BOOKING] owner notify not scheduled: {e}")


@public_router.post("/{slug}")
def public_booking_create(slug: str, body: PublicBookingIn, request: Request,
                          db: Session = Depends(get_db)) -> Dict[str, Any]:
    _public_rate(_client_ip(request))
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
    _notify_owner_later(owner.id, b.id)
    cfg = bs.settings_of(page)
    return {"ok": True, "booking": {"label": out["label"], "service": out["service"], "resource": out["resource"],
                                    "address": cfg.get("address") or "", "price": out["price"],
                                    "prepay_amount": out["prepay_amount"], "prepay_info": cfg.get("prepay_info") or "",
                                    "start": out["start"], "end": out["end"]}}


class PublicVisitSlotsIn(BaseModel):
    items: List[VisitItem] = Field(..., min_length=1, max_length=6)
    date: Optional[str] = None


@public_router.post("/{slug}/visit-slots")
def public_visit_slots(slug: str, body: PublicVisitSlotsIn, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Когда помещается визит из нескольких услуг (только онлайн-услуги и мастера с онлайн-записью)."""
    page, owner = _public_page(db, slug)
    for it in body.items:            # «только через администратора» — сразу понятный отказ, а не пустое время
        svc = bs.find_service(db, owner.id, it.service_id) if it.service_id else None
        if it.service_id and svc is None:
            raise HTTPException(status_code=400, detail="Услуга не найдена")
        if not bs.service_online(svc):
            raise HTTPException(status_code=400, detail=f"На «{svc.title}» записывает администратор — позвоните или напишите нам")
    try:
        out = bs.visit_slots(db, owner, [i.dict() for i in body.items], body.date, source="online")
        db.commit()
    except bs.BookingError as e:
        db.rollback()
        _err(e)
    return {"slots": [{"time": x["time"], "start": x["start"], "end": x["end"], "end_time": x["end_time"]} for x in out]}


class PublicVisitIn(BaseModel):
    items: List[VisitItem] = Field(..., min_length=1, max_length=6)
    start: str
    name: str = Field(..., min_length=1, max_length=120)
    phone: str = Field(..., min_length=5, max_length=40)
    note: Optional[str] = Field(None, max_length=500)


@public_router.post("/{slug}/visit")
def public_visit_create(slug: str, body: PublicVisitIn, request: Request, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Записаться по ссылке сразу на несколько услуг: всё или ничего, у салона — один визит."""
    _public_rate(_client_ip(request))
    page, owner = _public_page(db, slug)
    try:
        rows = bs.create_visit(db, owner, [i.dict() for i in body.items], body.start,
                               client_name=" ".join(body.name.split())[:120], client_phone=body.phone,
                               note=body.note, source="online", channel="web")
        db.commit()
    except bs.BookingError as e:
        db.rollback()
        _err(e)
    names = _names(db, owner.id)
    out = [bs.booking_to_dict(b, names) for b in rows]
    _notify_owner_later(owner.id, rows[0].id)
    cfg = bs.settings_of(page)
    prepay = sum(float(b.get("prepay_amount") or 0) for b in out)
    return {"ok": True, "visit": {
        "label": out[0]["label"], "start": out[0]["start"], "end": out[-1]["end"],
        "items": [{"service": b["service"], "resource": b["resource"], "start": b["start"], "price": b["price"]} for b in out],
        "price": sum(float(b.get("price") or 0) for b in out) or None,
        "prepay_amount": prepay or None, "address": cfg.get("address") or "", "prepay_info": cfg.get("prepay_info") or ""}}


# ───────────────────────────── ответ на предложение из листа ожидания (/w/<токен>) ─────────────────────────────

waitlist_public_router = APIRouter()


@waitlist_public_router.get("/{token}")
def public_waitlist_state(token: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    from backend.services import waitlist_service as wl
    try:
        st = wl.link_state(db, token)
    except wl.WaitlistError as e:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(e))
    st.pop("_entry_id", None)
    db.rollback()
    return st


@waitlist_public_router.post("/{token}")
def public_waitlist_answer(token: str, request: Request, data: Dict[str, Any] = Body(...),
                           db: Session = Depends(get_db)) -> Dict[str, Any]:
    """{action: accept | decline | stop} — записаться / не подходит / больше не нужно."""
    from backend.services import waitlist_service as wl
    _public_rate(_client_ip(request), limit=20)
    action = (data or {}).get("action")
    try:
        eid = wl.link_state(db, token)["_entry_id"]
        db.rollback()
        if action == "accept":
            return wl.accept(db, eid)
        if action in ("decline", "stop"):
            return wl.decline(db, eid, stop=action == "stop")
    except wl.WaitlistError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    raise HTTPException(status_code=400, detail="Неизвестное действие")
