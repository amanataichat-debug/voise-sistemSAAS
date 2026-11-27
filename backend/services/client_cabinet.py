"""
Кабинет клиента салона «Мои записи» (Voksy Beauty, шаг 2.5).

Клиент без пароля и без кода открывает подписанную ссылку /m/<токен> из сообщения салона (вставка {мои_записи}
в «Подтверждение записи» и «Напоминание накануне») или с экрана «Вы записаны» на странице салона. Решение
владельца 27.11.2026: вход только по ссылке; одна ссылка = один салон (аккаунт клиента на несколько салонов —
позже, план 2.7).

Токен: «c» + 32 hex карточки клиента (agent_contacts) + 12 hex HMAC (JWT_SECRET_KEY, своя соль), или «b» + 32 hex
записи + подпись — для записей без карточки (у салона ещё нет бота: записи по ссылке карточку не заводят). Чьи
записи видны: этого салона (user_id) и этой карточки, плюс записи без карточки с тем же номером телефона.

Что клиент может: подтвердить визит, перенести (те же свободные окна, что на странице салона; своё время при
подборе не мешает), отменить, записаться снова на те же услуги к тому же мастеру, убрать себя из листа ожидания,
оставить отзыв (ссылка /r/), написать в чат салона. Правило салона «отменить или перенести не позже чем за N ч»
(cancel_hours) действует и здесь — позже показываем телефон салона. Всё пишется источником online (журнал
действий: «клиент по ссылке» и его имя), владельцу — сообщение в Telegram. Освободившееся время подхватит лист
ожидания сам (он сканирует журнал раз в минуту).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from backend.services import booking_service as bs

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 20
HISTORY_DAYS = 365
SLOT_DAYS_MAX = 14
VISIT_SLOT_DAYS_MAX = 7
CANCEL_REASON = "Клиент отменил сам в «Мои записи»"
CANCEL_REASON_CALL = "Клиент отменил на звонке"
_SERVICE_PREFIXES = ("web:", "tg:", "ig:", "wa:")


class ClientError(ValueError):
    """Понятный клиенту отказ (400)."""


class NotFound(ClientError):
    """Ссылка неверная или запись не этого клиента (404)."""


# ───────────────────────────── ссылка /m/<токен> ─────────────────────────────

def _secret() -> bytes:
    from backend.core.config import settings
    return ("voksy-client-cabinet:" + str(getattr(settings, "JWT_SECRET_KEY", "") or "")).encode()


def _sig(kind: str, hx: str) -> str:
    return hmac.new(_secret(), (kind + hx).encode(), hashlib.sha256).hexdigest()[:12]


def _uuid(v) -> Optional[uuid.UUID]:
    if v is None or v == "":
        return None
    if isinstance(v, uuid.UUID):
        return v
    try:
        return uuid.UUID(str(v))
    except (ValueError, TypeError):
        return None


def token_for_contact(contact_id) -> str:
    hx = _uuid(contact_id).hex
    return "c" + hx + _sig("c", hx)


def token_for_booking(booking_id) -> str:
    hx = _uuid(booking_id).hex
    return "b" + hx + _sig("b", hx)


def parse_token(token: str):
    """(kind, uuid) или None — подпись проверяется постоянным временем."""
    t = (token or "").strip().lower()
    if not re.fullmatch(r"[cb][0-9a-f]{44}", t):
        return None
    kind, hx, sig = t[0], t[1:33], t[33:]
    if not hmac.compare_digest(sig, _sig(kind, hx)):
        return None
    return kind, uuid.UUID(hx)


def _host(host: Optional[str] = None) -> str:
    if host is None:
        from backend.core.config import settings
        host = getattr(settings, "HOST_URL", "") or "https://voksyai.online"
    return host.rstrip("/")


def link_for_contact(contact_id, host: Optional[str] = None) -> str:
    return f"{_host(host)}/m/{token_for_contact(contact_id)}"


def link_for_booking(b, host: Optional[str] = None) -> str:
    """Ссылка «Мои записи» для клиента этой записи: по карточке, а без карточки — по самой записи."""
    if getattr(b, "contact_id", None):
        return link_for_contact(b.contact_id, host)
    return f"{_host(host)}/m/{token_for_booking(b.id)}"


def path_for_booking(b) -> str:
    """Относительная ссылка (страница салона открывает её на том же домене)."""
    if getattr(b, "contact_id", None):
        return f"/m/{token_for_contact(b.contact_id)}"
    return f"/m/{token_for_booking(b.id)}"


# ───────────────────────────── кто это ─────────────────────────────

def _real_phone(p: Optional[str]) -> Optional[str]:
    p = (p or "").strip()
    if not p or p.startswith(_SERVICE_PREFIXES):
        return None
    return p if p.startswith("+") else "+" + p if p.isdigit() else p


class Client:
    """Клиент салона по ссылке: владелец, страница записи, карточка (если есть), его номера."""

    def __init__(self, owner, page, contact=None, phones: Iterable[str] = (), name: Optional[str] = None):
        self.owner = owner
        self.page = page
        self.contact = contact
        self.contact_id = contact.id if contact is not None else None
        self.phones = sorted({p for p in phones if p})
        self.name = name or (contact.name if contact is not None else None) or ""


def _phones_of_contact(c) -> List[str]:
    out = [_real_phone(c.phone)]
    for p in (getattr(c, "alt_phones", None) or "").split(","):
        out.append(_real_phone(p.strip()))
    if getattr(c, "call_phone", None):
        out.append(_real_phone(c.call_phone))
    return [p for p in out if p]


def resolve(db: Session, token: str) -> Client:
    from backend.models.agent_contact import AgentContact
    from backend.models.booking import Booking, BookingPage
    from backend.models.user import User
    parsed = parse_token(token)
    if parsed is None:
        raise NotFound("Ссылка неверная или устарела")
    kind, oid = parsed
    contact = None
    phones: List[str] = []
    name = None
    if kind == "c":
        contact = db.query(AgentContact).filter(AgentContact.id == oid).first()
        if contact is None:
            raise NotFound("Ссылка неверная или устарела")
        owner_id = contact.user_id
        phones = _phones_of_contact(contact)
    else:
        b = db.query(Booking).filter(Booking.id == oid).first()
        if b is None:
            raise NotFound("Ссылка неверная или устарела")
        owner_id = b.user_id
        name = b.client_name
        if b.contact_id:
            contact = db.query(AgentContact).filter(AgentContact.id == b.contact_id).first()
        phones = _phones_of_contact(contact) if contact is not None else [p for p in [_real_phone(b.client_phone)] if p]
    owner = db.query(User).filter(User.id == owner_id).first()
    page = db.query(BookingPage).filter(BookingPage.user_id == owner_id).first()
    if owner is None or not owner.is_active or page is None:
        raise NotFound("Салон сейчас не принимает записи")
    return Client(owner, page, contact, phones, name)


def client_for_phone(db: Session, owner, phone: Optional[str]) -> Optional[Client]:
    """Клиент салона по номеру звонка (шаг 2.6: голосовой агент переносит и отменяет записи на звонке):
    карточка CRM по любому её номеру + записи без карточки с этим номером."""
    from backend.models.booking import BookingPage
    p = _real_phone(phone)
    if owner is None or not p:
        return None
    page = db.query(BookingPage).filter(BookingPage.user_id == owner.id).first()
    if page is None:
        return None
    contact = None
    try:
        agent = bs._first_agent(db, owner.id)
        if agent is not None:
            from backend.services.agent_contact_phones import find_contact_by_phone
            contact = find_contact_by_phone(db, agent.id, p)
    except Exception as e:
        logger.warning(f"[CLIENT-CABINET] contact by phone failed: {e}")
        db.rollback()
    phones = _phones_of_contact(contact) if contact is not None else []
    return Client(owner, page, contact, phones + [p])


def upcoming_for(db: Session, cl: Client) -> List[Dict[str, Any]]:
    """Ближайшие записи клиента (как в «Моих записях»): визит — одной строкой, с тем, можно ли менять."""
    from backend.models.booking import Booking
    q = _own_bookings_q(db, cl)
    if q is None:
        return []
    cfg = bs.settings_of(cl.page)
    masters = _masters(db, cl.owner.id)
    rows = (q.filter(Booking.status.in_(("booked", "confirmed")), Booking.end_at > bs.now_utc())
            .order_by(Booking.start_at).limit(30).all())
    out, seen = [], set()
    for b in rows:
        key = str(b.visit_id or b.id)
        if key in seen:
            continue
        seen.add(key)
        parts = [x for x in rows if x.visit_id == b.visit_id] if b.visit_id else [b]
        v = _visit_out(db, cl, parts, masters, cfg, upcoming=True)
        v["say"] = client_label(parts)
        out.append(v)
    return out


def _own_filter(cl: Client):
    from backend.models.booking import Booking
    conds = []
    if cl.contact_id is not None:
        conds.append(Booking.contact_id == cl.contact_id)
    if cl.phones:
        conds.append(and_(Booking.contact_id.is_(None), Booking.client_phone.in_(cl.phones)))
    if not conds:
        return None
    return and_(Booking.user_id == cl.owner.id, or_(*conds))


def _own_bookings_q(db: Session, cl: Client):
    from backend.models.booking import Booking
    flt = _own_filter(cl)
    if flt is None:
        return None
    return db.query(Booking).filter(flt)


def own_booking(db: Session, cl: Client, booking_id):
    """Запись этого клиента в этом салоне — иначе 404 (чужую не показываем и не трогаем)."""
    from backend.models.booking import Booking
    bid = _uuid(booking_id)
    q = _own_bookings_q(db, cl)
    b = q.filter(Booking.id == bid).first() if (q is not None and bid is not None) else None
    if b is None:
        raise NotFound("Запись не найдена")
    return b


def _parts(db: Session, cl: Client, b) -> List[Any]:
    if not b.visit_id:
        return [b]
    rows = [x for x in bs.visit_bookings(db, cl.owner.id, b.visit_id) if x.status != "cancelled"]
    return rows or [b]


# ───────────────────────────── что видит клиент ─────────────────────────────

def _masters(db: Session, owner_id) -> Dict[str, Any]:
    from backend.models.booking import BookingResource
    return {str(r.id): r for r in db.query(BookingResource).filter(BookingResource.user_id == owner_id).all()}


def _master_out(r) -> Optional[Dict[str, Any]]:
    if r is None:
        return None
    from backend.services import salon_media as sm
    return {"id": str(r.id), "name": r.name, "initials": bs.initials(r.name), "color": r.color,
            "photo_url": sm.url_of(r.photo_id) if getattr(r, "photo_id", None) else None}


def _salon_out(db: Session, cl: Client) -> Dict[str, Any]:
    from backend.services import salon_media as sm
    from backend.services import salon_page as sp
    prof, cfg = sp.profile_of(cl.page), bs.settings_of(cl.page)
    address = cfg.get("address") or ""
    chat = {"enabled": False}
    try:
        from backend.services import webchat_service
        chat = webchat_service.public_state(db, cl.page, cl.owner)
    except Exception as e:
        logger.warning(f"[CLIENT-CABINET] chat state failed: {e}")
        db.rollback()
    return {
        "name": cfg.get("title") or cl.owner.company_name or cl.owner.first_name or "Салон",
        "slug": cl.page.slug,
        "logo_url": sm.url_of(prof.get("logo_id")),
        "accent": prof.get("accent") or sp.DEFAULT_ACCENT,
        "address": address,
        "map_url": sp.map_link(prof, address),
        "phone": prof.get("phone") or "",
        "whatsapp": prof.get("whatsapp") or "",
        "instagram": prof.get("instagram") or "",
        "telegram": prof.get("telegram") or "",
        "page_url": sp.page_path(cl.page, cl.owner.business_niche),
        "book_url": f"/b/{cl.page.slug}" if cl.page.enabled else None,
        "booking_enabled": bool(cl.page.enabled),
        "cancel_hours": int(cfg.get("cancel_hours") or 0),
        "prepay_info": cfg.get("prepay_info") or "",
        "chat": chat,
    }


def _first_name(name: Optional[str]) -> str:
    try:
        from backend.services.automations import _first_name as fn
        return fn(name)
    except Exception:
        parts = (name or "").strip().split()
        return parts[0] if parts else ""


def change_block(b, cfg: Dict[str, Any]) -> Optional[str]:
    """Почему клиент не может сам перенести или отменить (None — может)."""
    st = bs._aw(b.start_at)
    if b.status not in ("booked", "confirmed") or st <= bs.now_utc():
        return "Эту запись уже нельзя изменить"
    hours = int(cfg.get("cancel_hours") or 0)
    if hours and st - bs.now_utc() < timedelta(hours=hours):
        return f"Перенести или отменить можно не позже чем за {hours} ч до визита — позвоните или напишите салону"
    return None


def _online_ok(db: Session, cl: Client, parts) -> bool:
    """Перенос сам — только если услуги и мастера открыты для онлайн-записи (иначе записывает администратор)."""
    masters = _masters(db, cl.owner.id)
    for p in parts:
        svc = bs.find_service(db, cl.owner.id, p.service_entry_id) if p.service_entry_id else None
        if svc is not None and not bs.service_online(svc):
            return False
        r = masters.get(str(p.resource_id)) if p.resource_id else None
        if r is not None and not bs.bookable_online(r):
            return False
    return True


def _visit_out(db: Session, cl: Client, parts, masters, cfg, *, upcoming: bool) -> Dict[str, Any]:
    first, last = parts[0], parts[-1]
    st, en = bs._aw(first.start_at), bs._aw(last.end_at)
    loc = bs.to_local(st)
    items = [{"id": str(p.id), "service": p.service_title or "Услуга",
              "service_id": str(p.service_entry_id) if p.service_entry_id else None,
              "master": _master_out(masters.get(str(p.resource_id))) if p.resource_id else None,
              "start": bs._aw(p.start_at).isoformat(), "time": bs.to_local(p.start_at).strftime("%H:%M"),
              "price": p.price} for p in parts]
    total = sum(float(p.price or 0) for p in parts) or None
    prepay = sum(float(p.prepay_amount or 0) for p in parts) or None
    out = {"id": str(first.id), "visit_id": str(first.visit_id) if first.visit_id else None,
           "date": loc.date().isoformat(), "time": loc.strftime("%H:%M"),
           "end_time": bs.to_local(en).strftime("%H:%M"), "start": st.isoformat(), "label": bs.fmt_local(st),
           "status": first.status, "status_title": bs.STATUS_TITLES.get(first.status, first.status),
           "items": items, "price": total, "prepay_amount": prepay,
           "prepaid": all(p.prepaid_at is not None for p in parts) if prepay else False}
    if upcoming:
        block = change_block(first, cfg)
        online = _online_ok(db, cl, parts)
        out.update(can_cancel=block is None, can_move=block is None and online and bool(cl.page.enabled),
                   can_confirm=first.status == "booked" and bs._aw(first.start_at) > bs.now_utc(),
                   change_note=block or (None if online else "Перенести эту запись может только администратор — "
                                                            "позвоните или напишите салону"))
    return out


def _review_out(db: Session, b) -> Optional[Dict[str, Any]]:
    from backend.services import reviews_service as rv
    if b.status not in rv.VISITED:
        return None
    r = rv.find_review(db, b)
    fresh = bs._aw(b.end_at) > bs.now_utc() - timedelta(days=rv.LINK_DAYS)
    return {"score": r.score if r is not None else None,
            "url": rv.review_link(b.id, host="") if fresh and (r is None or not r.reply_text) else None}


def state(db: Session, token: str) -> Dict[str, Any]:
    from backend.models.booking import Booking
    cl = resolve(db, token)
    cfg = bs.settings_of(cl.page)
    masters = _masters(db, cl.owner.id)
    q = _own_bookings_q(db, cl)
    now = bs.now_utc()
    upcoming: List[Dict[str, Any]] = []
    history: List[Dict[str, Any]] = []
    if q is not None:
        rows = (q.filter(Booking.status.in_(("booked", "confirmed")), Booking.end_at > now)
                .order_by(Booking.start_at).limit(60).all())
        seen = set()
        for b in rows:
            key = str(b.visit_id or b.id)
            if key in seen:
                continue
            seen.add(key)
            parts = [x for x in rows if x.visit_id == b.visit_id] if b.visit_id else [b]
            upcoming.append(_visit_out(db, cl, parts, masters, cfg, upcoming=True))
        past = (q.filter(Booking.status.in_(("arrived", "completed", "no_show")),
                         Booking.start_at > now - timedelta(days=HISTORY_DAYS), Booking.start_at <= now)
                .order_by(Booking.start_at.desc()).limit(HISTORY_LIMIT * 3).all())
        seen = set()
        for b in past:
            key = str(b.visit_id or b.id)
            if key in seen:
                continue
            seen.add(key)
            parts = sorted([x for x in past if x.visit_id == b.visit_id], key=lambda x: x.start_at) if b.visit_id else [b]
            item = _visit_out(db, cl, parts, masters, cfg, upcoming=False)
            item["review"] = _review_out(db, parts[0])
            item["can_repeat"] = bool(cl.page.enabled) and parts[0].status != "no_show" and any(
                p.service_entry_id for p in parts)
            history.append(item)
            if len(history) >= HISTORY_LIMIT:
                break
    return {"salon": _salon_out(db, cl),
            "client": {"name": _first_name(cl.name), "full_name": cl.name or ""},
            "upcoming": upcoming, "history": history, "waitlist": _waitlist_out(db, cl)}


def _waitlist_q(db: Session, cl: Client):
    from backend.models.waitlist import WaitlistEntry
    conds = []
    if cl.contact_id is not None:
        conds.append(WaitlistEntry.contact_id == cl.contact_id)
    if cl.phones:
        conds.append(and_(WaitlistEntry.contact_id.is_(None), WaitlistEntry.client_phone.in_(cl.phones)))
    if not conds:
        return None
    return db.query(WaitlistEntry).filter(WaitlistEntry.user_id == cl.owner.id, or_(*conds))


def _waitlist_out(db: Session, cl: Client) -> List[Dict[str, Any]]:
    from backend.models.waitlist import WaitlistEntry
    from backend.services import waitlist_service as wl
    q = _waitlist_q(db, cl)
    if q is None:
        return []
    out = []
    for e in q.filter(WaitlistEntry.status.in_(("waiting", "offered"))).order_by(WaitlistEntry.created_at).all():
        offer = None
        if e.status == "offered" and e.offer_start and e.offer_expires_at and bs._aw(e.offer_expires_at) > bs.now_utc():
            offer = {"label": bs.fmt_local(e.offer_start), "url": f"/w/{wl.link_token(e.id)}"}
        out.append({"id": str(e.id), "service": e.service_title or "", "period": wl._period_words(e),
                    "status": e.status, "status_title": wl.STATUS_TITLES.get(e.status, e.status), "offer": offer})
    return out


# ───────────────────────────── свободное время ─────────────────────────────

@contextmanager
def _without(db: Session, rows):
    """Свои записи клиента на время подбора окон не мешают: «отменяем» их в точке сохранения и откатываем."""
    sp = db.begin_nested()
    try:
        for b in rows:
            b.status = "cancelled"
        db.flush()
        yield
    finally:
        sp.rollback()


def _items_of(db: Session, cl: Client, parts, keep_master: bool = True) -> List[Dict[str, Any]]:
    masters = _masters(db, cl.owner.id)
    out = []
    for p in parts:
        r = masters.get(str(p.resource_id)) if p.resource_id else None
        rid = str(p.resource_id) if (keep_master and r is not None and r.is_active and bs.bookable_online(r)) else None
        out.append({"service_id": str(p.service_entry_id) if p.service_entry_id else None, "resource_id": rid})
    return out


def _slots_single(db: Session, cl: Client, b, day, days: int, any_master: bool) -> List[Dict[str, Any]]:
    service = bs.find_service(db, cl.owner.id, b.service_entry_id) if b.service_entry_id else None
    rid = None if any_master else (str(b.resource_id) if b.resource_id else None)
    found = bs.free_slots(db, cl.owner, service, rid, day, days, online_only=True)
    return [{"date": d["date"], "weekday": d["weekday"],
             "slots": [{"time": s["time"], "start": s["start"]} for s in d["slots"]]} for d in found]


def _slots_visit(db: Session, cl: Client, items, day, days: int) -> List[Dict[str, Any]]:
    start_day = bs.parse_day(day) if day else bs.to_local(bs.now_utc()).date()
    out = []
    for i in range(max(1, min(days, VISIT_SLOT_DAYS_MAX))):
        d = start_day + timedelta(days=i)
        found = bs.visit_slots(db, cl.owner, items, d, source="online")
        out.append({"date": d.isoformat(), "weekday": bs.DAY_TITLES[d.weekday()],
                    "slots": [{"time": s["time"], "start": s["start"], "end_time": s["end_time"]} for s in found]})
    return out


def slots(db: Session, token: str, booking_id, mode: str = "move", day=None, days: int = 1,
          any_master: bool = False) -> Dict[str, Any]:
    """Свободное время для переноса (mode=move, своя запись не мешает) или «записаться снова» (mode=repeat)."""
    cl = resolve(db, token)
    b = own_booking(db, cl, booking_id)
    if not cl.page.enabled:
        raise ClientError("Онлайн-запись в салоне сейчас выключена — позвоните или напишите салону")
    days = max(1, min(int(days or 1), SLOT_DAYS_MAX))
    parts = _parts(db, cl, b)
    if mode == "repeat":
        if not any(p.service_entry_id for p in parts):
            raise ClientError("Эту запись повторить нельзя — выберите услугу на странице салона")
        items = _items_of(db, cl, parts, keep_master=not any_master)
        if len(items) == 1:
            p = parts[0]
            service = bs.find_service(db, cl.owner.id, p.service_entry_id)
            found = bs.free_slots(db, cl.owner, service, items[0]["resource_id"], day, days, online_only=True)
            out = [{"date": d["date"], "weekday": d["weekday"],
                    "slots": [{"time": s["time"], "start": s["start"]} for s in d["slots"]]} for d in found]
        else:
            out = _slots_visit(db, cl, items, day, days)
        db.rollback()
        return {"days": out, "visit": len(items) > 1}
    block = change_block(parts[0], bs.settings_of(cl.page))
    if block:
        raise ClientError(block)
    with _without(db, parts):
        if len(parts) == 1:
            out = _slots_single(db, cl, parts[0], day, days, any_master)
        else:
            out = _slots_visit(db, cl, _items_of(db, cl, parts), day, days)
    db.rollback()
    return {"days": out, "visit": len(parts) > 1}


# ───────────────────────────── действия ─────────────────────────────

def _check_start(cfg: Dict[str, Any], start) -> datetime:
    st = bs.parse_start(start)
    if st < bs.now_utc() + timedelta(minutes=min(int(cfg.get("lead_min") or 0), 30)):
        raise ClientError("Это время уже прошло или слишком близко — выберите другое")
    horizon = bs.to_local(bs.now_utc()).date() + timedelta(days=int(cfg.get("horizon_days") or 30))
    if bs.to_local(st).date() > horizon:
        raise ClientError("Так далеко вперёд салон пока не записывает — выберите время пораньше")
    return st


def _label(db: Session, cl: Client, parts) -> str:
    masters = _masters(db, cl.owner.id)
    svc = ", ".join(p.service_title or "услуга" for p in parts)
    m = masters.get(str(parts[0].resource_id)) if parts[0].resource_id else None
    return f"{bs.fmt_local(parts[0].start_at)} — {svc}" + (f" · {m.name}" if m else "")


_MON = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября",
        "декабря")
_WDL = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")


def client_label(parts) -> str:
    """Для клиента: «завтра в 15:00 — Маникюр · Айгуль» / «пятница, 28 ноября в 15:00 — …»."""
    st = bs.to_local(parts[0].start_at)
    today = bs.to_local(bs.now_utc()).date()
    hm = st.strftime("%H:%M")
    if st.date() == today:
        when = f"сегодня в {hm}"
    elif st.date() == today + timedelta(days=1):
        when = f"завтра в {hm}"
    else:
        when = f"{_WDL[st.weekday()]}, {st.day} {_MON[st.month - 1]} в {hm}"
    return when + " — " + ", ".join(p.service_title or "услуга" for p in parts)


def _who(cl: Client, b) -> str:
    phone = b.client_phone or (cl.phones[0] if cl.phones else "")
    return (b.client_name or cl.name or "Клиент") + (f" · {phone}" if phone else "")


_BY = {"online": "", "call": " (на звонке)"}


def reschedule(db: Session, token: str, booking_id, start, any_master: bool = False) -> Dict[str, Any]:
    return move(db, resolve(db, token), booking_id, start, any_master=any_master)


def move(db: Session, cl: Client, booking_id, start, any_master: bool = False, source: str = "online") -> Dict[str, Any]:
    """Перенос записи клиентом: по ссылке «Мои записи» (online) или голосовым агентом на звонке (call)."""
    b = own_booking(db, cl, booking_id)
    cfg = bs.settings_of(cl.page)
    parts = _parts(db, cl, b)
    first = parts[0]
    block = change_block(first, cfg)
    if block:
        raise ClientError(block)
    if not cl.page.enabled or not _online_ok(db, cl, parts):
        raise ClientError("Перенести эту запись может только администратор — позвоните или напишите салону")
    new_start = _check_start(cfg, start)
    was = _label(db, cl, parts)
    old_ids = [p.id for p in parts]
    try:
        _apply_move(db, cl, parts, new_start, any_master, source)
        db.commit()
    except bs.BookingError as e:
        db.rollback()
        raise ClientError(str(e))
    moved = sorted([bs.get_booking(db, cl.owner.id, i) for i in old_ids], key=lambda x: x.start_at)
    _notify(cl.owner.id, f"🔁 **Клиент перенёс запись{_BY.get(source, '')}**\n{_who(cl, moved[0])}\n{was}\n→ {_label(db, cl, moved)}")
    return {"ok": True, "label": client_label(moved), "booking_id": str(moved[0].id)}


def _apply_move(db: Session, cl: Client, parts, new_start: datetime, any_master: bool, source: str) -> None:
    """Сдвинуть запись или визит целиком (без commit; BookingError — время не подходит)."""
    first = parts[0]
    if len(parts) == 1:
        _move_single(db, cl, first, new_start, any_master, source)
        return
    delta = new_start - bs._aw(first.start_at)
    if not delta:
        raise ClientError("Это то же самое время")
    ordered = list(reversed(parts)) if delta > timedelta(0) else list(parts)
    for p in ordered:               # двигаем так, чтобы части визита не мешали друг другу
        bs.update_booking(db, cl.owner, p.id, start=bs._aw(p.start_at) + delta, source=source)


def try_move(db: Session, cl: Client, booking_id, start, source: str = "call") -> Dict[str, Any]:
    """Проверка переноса без изменений (голос: итог клиенту до его согласия). ClientError — нельзя."""
    b = own_booking(db, cl, booking_id)
    cfg = bs.settings_of(cl.page)
    parts = _parts(db, cl, b)
    block = change_block(parts[0], cfg)
    if block:
        raise ClientError(block)
    if not cl.page.enabled or not _online_ok(db, cl, parts):
        raise ClientError("Перенести эту запись может только администратор")
    new_start = _check_start(cfg, start)
    was = client_label(parts)
    sp = db.begin_nested()
    try:
        _apply_move(db, cl, parts, new_start, False, source)
        now = client_label(sorted(parts, key=lambda x: x.start_at))
    except bs.BookingError as e:
        raise ClientError(str(e))
    finally:
        sp.rollback()
    db.rollback()
    return {"was": was, "now": now, "start": new_start.isoformat()}


def _move_single(db: Session, cl: Client, b, new_start: datetime, any_master: bool, source: str = "online") -> None:
    if bs._aw(b.start_at) == new_start and not any_master:
        raise ClientError("Это то же самое время")
    try:
        sp = db.begin_nested()
        bs.update_booking(db, cl.owner, b.id, start=new_start, source=source)
        sp.commit()
        return
    except bs.BookingError as e:
        sp.rollback()
        if not any_master:
            raise
        last = e
    service = bs.find_service(db, cl.owner.id, b.service_entry_id) if b.service_entry_id else None
    for r in bs.resources(db, cl.owner.id):
        if str(r.id) == str(b.resource_id) or not bs.bookable_online(r) or not bs._resource_does(r, service):
            continue
        sp = db.begin_nested()
        try:
            bs.update_booking(db, cl.owner, b.id, start=new_start, resource_id=r.id, source=source)
            sp.commit()
            return
        except bs.BookingError as e:
            sp.rollback()
            last = e
    raise last


def cancel(db: Session, token: str, booking_id, reason: Optional[str] = None) -> Dict[str, Any]:
    return cancel_for(db, resolve(db, token), booking_id, reason)


def cancel_for(db: Session, cl: Client, booking_id, reason: Optional[str] = None, source: str = "online") -> Dict[str, Any]:
    b = own_booking(db, cl, booking_id)
    parts = _parts(db, cl, b)
    block = change_block(parts[0], bs.settings_of(cl.page))
    if block:
        raise ClientError(block)
    was = _label(db, cl, parts)
    why = (CANCEL_REASON_CALL if source == "call" else CANCEL_REASON) + (
        f": {' '.join(str(reason).split())[:200]}" if reason and str(reason).strip() else "")
    try:
        if b.visit_id:
            bs.cancel_visit(db, cl.owner, b.visit_id, why, source=source)
        else:
            bs.cancel_booking(db, cl.owner, b.id, why, source=source)
        db.commit()
    except bs.BookingError as e:
        db.rollback()
        raise ClientError(str(e))
    _notify(cl.owner.id, f"❌ **Клиент отменил запись{_BY.get(source, '')}**\n{_who(cl, b)}\n{was}"
                         + (f"\nПричина: {' '.join(str(reason).split())[:200]}" if reason and str(reason).strip() else ""))
    return {"ok": True, "label": client_label(parts)}


def confirm(db: Session, token: str, booking_id) -> Dict[str, Any]:
    return confirm_for(db, resolve(db, token), booking_id)


def confirm_for(db: Session, cl: Client, booking_id, source: str = "online") -> Dict[str, Any]:
    b = own_booking(db, cl, booking_id)
    parts = _parts(db, cl, b)
    if parts[0].status not in ("booked", "confirmed") or bs._aw(parts[0].start_at) <= bs.now_utc():
        raise ClientError("Эту запись уже нельзя подтвердить")
    try:
        for p in parts:
            if p.status == "booked":
                bs.update_booking(db, cl.owner, p.id, status="confirmed", source=source)
        db.commit()
    except bs.BookingError as e:
        db.rollback()
        raise ClientError(str(e))
    return {"ok": True, "label": client_label(parts)}


def repeat(db: Session, token: str, booking_id, start, any_master: bool = False) -> Dict[str, Any]:
    """«Записаться снова»: те же услуги к тем же мастерам (или к любому) на выбранное время."""
    cl = resolve(db, token)
    b = own_booking(db, cl, booking_id)
    if not cl.page.enabled:
        raise ClientError("Онлайн-запись в салоне сейчас выключена — позвоните или напишите салону")
    cfg = bs.settings_of(cl.page)
    st = _check_start(cfg, start)
    parts = [p for p in _parts(db, cl, b) if p.service_entry_id] if b.visit_id else ([b] if b.service_entry_id else [])
    if not parts:
        raise ClientError("Эту запись повторить нельзя — выберите услугу на странице салона")
    items = _items_of(db, cl, parts, keep_master=not any_master)
    name = b.client_name or cl.name or "Клиент"
    phone = b.client_phone or (cl.phones[0] if cl.phones else None)
    try:
        if len(items) == 1:
            rows = [bs.create_booking(db, cl.owner, start=st, service_id=items[0]["service_id"],
                                      resource_id=items[0]["resource_id"], client_name=name, client_phone=phone,
                                      contact_id=str(cl.contact_id) if cl.contact_id else None,
                                      source="online", channel="cabinet")]
        else:
            rows = bs.create_visit(db, cl.owner, items, st, client_name=name, client_phone=phone,
                                   contact_id=str(cl.contact_id) if cl.contact_id else None,
                                   source="online", channel="cabinet")
        db.commit()
    except bs.BookingError as e:
        db.rollback()
        raise ClientError(str(e))
    _notify_new(cl.owner.id, rows[0].id)
    return {"ok": True, "label": client_label(rows), "booking_id": str(rows[0].id)}


def waitlist_stop(db: Session, token: str, entry_id) -> Dict[str, Any]:
    from backend.models.waitlist import WaitlistEntry
    from backend.services import waitlist_service as wl
    cl = resolve(db, token)
    q = _waitlist_q(db, cl)
    eid = _uuid(entry_id)
    e = q.filter(WaitlistEntry.id == eid).first() if (q is not None and eid is not None) else None
    if e is None:
        raise NotFound("Запись в листе ожидания не найдена")
    db.rollback()
    try:
        return wl.decline(db, e.id, stop=True)
    except wl.WaitlistError as ex:
        db.rollback()
        raise ClientError(str(ex))


# ───────────────────────────── владельцу ─────────────────────────────

async def _send_owner(owner_id, text: str) -> bool:
    from backend.db.session import SessionLocal
    from backend.models.agent_config import AgentConfig
    from backend.services.owner_notifications import _send
    db = SessionLocal()
    try:
        agent = db.query(AgentConfig).filter(AgentConfig.user_id == owner_id).order_by(AgentConfig.created_at).first()
        if agent is None:
            return False
        return await _send(agent, db, text)
    except Exception as e:
        logger.warning(f"[CLIENT-CABINET] owner notify failed: {e}")
        return False
    finally:
        db.close()


def _notify_owner(owner_id, text: str) -> None:
    try:
        from backend.core.background_loop import submit
        submit(_send_owner(owner_id, text))
    except Exception as e:
        logger.warning(f"[CLIENT-CABINET] owner notify not scheduled: {e}")


def _notify_new_owner(owner_id, booking_id) -> None:
    try:
        from backend.core.background_loop import submit
        from backend.services.booking_notify import notify_owner_new_booking
        submit(notify_owner_new_booking(owner_id, booking_id))
    except Exception as e:
        logger.warning(f"[CLIENT-CABINET] owner notify not scheduled: {e}")


NOTIFY = _notify_owner          # тесты подменяют
NOTIFY_NEW = _notify_new_owner


def _notify(owner_id, text: str) -> None:
    NOTIFY(owner_id, text)


def _notify_new(owner_id, booking_id) -> None:
    NOTIFY_NEW(owner_id, booking_id)
