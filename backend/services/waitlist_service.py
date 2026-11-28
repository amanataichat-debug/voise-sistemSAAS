"""
Лист ожидания салона (Voksy Beauty, шаг 2.4).

Клиент хочет к мастеру (или к любому) на услугу в дни «с — по» и, может быть, в своё время суток, а свободного
окна нет — он встаёт в лист ожидания: из журнала записи (администратор), со страницы салона (сам), ботом в
переписке (add_to_waitlist). Раз в минуту (booking_notify.run_once → tick) система:
  1. закрывает просроченные предложения (30 минут без ответа — окно уходит следующему, этому предложат другое);
  2. закрывает записи листа, у которых прошёл последний желаемый день;
  3. в рабочее окно отправок (08:00–21:00 по Бишкеку) ищет каждому ждущему окно — ПО ОЧЕРЕДИ, первым тем, кто
     встал раньше (решение владельца 26.11.2026: «автоматически первому»): free_slots той же логикой, что у
     записи по ссылке, без окон, предложенных другим или уже предложенных ему же; нашлось → сообщение в мессенджер
     через очередь отправок (правило waitlist_offer) со ссылкой /w/<токен>, ответ — 30 минут.
Ответ: по ссылке («Записаться» → запись источником online, «Не подходит», «Больше не нужно») или боту в переписке
(answer_waitlist_offer). Окно заняли, пока клиент думал, — честно говорим и ждём следующего. Без мессенджера
(некуда написать) сами не предлагаем — в кабинете видно «предложите сами» и есть кнопка «Предложить сейчас».
Окно не держим: пока клиент думает, его может занять кто-то по ссылке или администратор.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import time as _time
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.services import activity
from backend.services import booking_service as bs

logger = logging.getLogger(__name__)

OFFER_MINUTES = 30
MAX_SPAN_DAYS = 31
SCAN_EVERY = 90                 # секунд: один салон сканируется не чаще (на воркер)
STATUS_TITLES = {"waiting": "Ждёт окна", "offered": "Предложено", "booked": "Записался", "cancelled": "Убран",
                 "expired": "Срок прошёл"}
SOURCE_TITLES = {"owner": "Администратор", "staff": "Администратор", "bot": "Бот", "online": "Сам по ссылке",
                 "call": "Звонок"}
_last_scan: Dict[str, float] = {}

OFFER_TEXT = {
    "ru": "Здравствуйте[, {имя}]! Освободилось время: {когда}[ на {услуга}][ к мастеру {мастер}]. Записать вас? "
          "Ответьте «да» в течение 30 минут или нажмите: {ссылка}",
    "ky": "Саламатсызбы[, {имя}]! Убакыт бошоду: {когда}[ {услуга} кызматына][ {мастер} чеберге]. Жазып коёлубу? "
          "30 мүнөттүн ичинде «ооба» деп жооп бериңиз же басыңыз: {ссылка}",
}


class WaitlistError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid(v) -> Optional[uuid.UUID]:
    if v is None or v == "":
        return None
    if isinstance(v, uuid.UUID):
        return v
    try:
        return uuid.UUID(str(v))
    except (ValueError, TypeError):
        return None


def _hm(v, field: str) -> Optional[str]:
    if v in (None, ""):
        return None
    t = bs.parse_hm(str(v))
    if t is None:
        raise WaitlistError(f"{field}: время в формате ЧЧ:ММ")
    return t.strftime("%H:%M")


def _today() -> date:
    return bs.to_local(bs.now_utc()).date()


def _day(v, field: str) -> date:
    try:
        return bs.parse_day(v)
    except Exception:
        raise WaitlistError(f"{field}: дата в формате ГГГГ-ММ-ДД")


# ───────────────────────────── ссылка /w/<токен> ─────────────────────────────

def _secret() -> bytes:
    from backend.core.config import settings
    return ("voksy-waitlist:" + str(getattr(settings, "JWT_SECRET_KEY", "") or "")).encode()


def link_token(entry_id) -> str:
    hx = _uuid(entry_id).hex
    return hx + hmac.new(_secret(), hx.encode(), hashlib.sha256).hexdigest()[:12]


def entry_id_from_token(token: str) -> Optional[uuid.UUID]:
    t = (token or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{44}", t):
        return None
    hx, sig = t[:32], t[32:]
    if not hmac.compare_digest(sig, hmac.new(_secret(), hx.encode(), hashlib.sha256).hexdigest()[:12]):
        return None
    return uuid.UUID(hx)


def link(entry_id, host: Optional[str] = None) -> str:
    if host is None:
        from backend.core.config import settings
        host = (getattr(settings, "HOST_URL", "") or "https://voksyai.online").rstrip("/")
    return f"{host}/w/{link_token(entry_id)}"


# ───────────────────────────── запись в лист ─────────────────────────────

def _period_words(e) -> str:
    a, b = e.date_from, e.date_to
    if a == b:
        out = f"{bs.DAY_TITLES[a.weekday()]} {a.day:02d}.{a.month:02d}"
    else:
        out = f"{a.day:02d}.{a.month:02d}–{b.day:02d}.{b.month:02d}"
    if e.time_from and e.time_to:
        out += f", {e.time_from}–{e.time_to}"
    elif e.time_from:
        out += f", после {e.time_from}"
    elif e.time_to:
        out += f", до {e.time_to}"
    return out


def add(db: Session, user, *, service_id=None, service_query: Optional[str] = None, resource_id=None,
        date_from=None, date_to=None, time_from=None, time_to=None, contact_id=None, name: Optional[str] = None,
        phone: Optional[str] = None, note: Optional[str] = None, source: str = "owner"):
    """Встать в лист ожидания (без коммита). Тот же клиент на ту же услугу — обновляем его запись, а не дублируем."""
    from backend.models.waitlist import WaitlistEntry
    from backend.utils.phone import normalize_phone_e164
    service = bs.find_service(db, user.id, service_id, service_query)
    if service is None:
        raise WaitlistError("Выберите услугу из прайса")
    if source in ("online", "bot", "call") and not bs.service_online(service):
        raise WaitlistError("На эту услугу записывает администратор")
    res = None
    if resource_id:
        res = next((r for r in bs.resources(db, user.id) if str(r.id) == str(resource_id)), None)
        if res is None or not bs._resource_does(res, service):
            raise WaitlistError("Этот мастер не делает такую услугу")
    today = _today()
    d1 = _day(date_from, "С какого дня") if date_from else today
    d2 = _day(date_to, "По какой день") if date_to else d1 + timedelta(days=13)
    if d1 < today:
        d1 = today
    if d2 < d1:
        raise WaitlistError("«По какой день» раньше, чем «с какого»")
    if (d2 - d1).days >= MAX_SPAN_DAYS:
        raise WaitlistError(f"Лист ожидания — не дольше {MAX_SPAN_DAYS} дней")
    t1, t2 = _hm(time_from, "С какого времени"), _hm(time_to, "До какого времени")
    if t1 and t2 and t2 <= t1:
        raise WaitlistError("Время «до» должно быть позже «с»")
    ph = normalize_phone_e164(phone) if phone else None
    if phone and not ph:
        raise WaitlistError("Проверьте номер телефона")
    contact = bs.resolve_contact(db, user.id, contact_id, name, ph, source=source)
    if contact is None and not (name and ph):
        raise WaitlistError("Нужны имя и телефон клиента")
    e = None
    if contact is not None:
        e = (db.query(WaitlistEntry).filter(WaitlistEntry.user_id == user.id, WaitlistEntry.contact_id == contact.id,
                                            WaitlistEntry.service_entry_id == service.id,
                                            WaitlistEntry.status.in_(("waiting", "offered")))
             .order_by(WaitlistEntry.created_at).first())
    created = e is None
    if e is None:
        e = WaitlistEntry(id=uuid.uuid4(), user_id=user.id, contact_id=contact.id if contact else None,
                          service_entry_id=service.id, source=source if source in SOURCE_TITLES else "owner",
                          status="waiting", offers_count=0, offered_slots=[], created_at=_now())
        db.add(e)
    e.client_name = (name or (contact.name if contact else None) or "")[:200] or None
    from backend.services.salon_clients import real_phone
    e.client_phone = ph or (real_phone(contact.phone) if contact else None)
    e.service_title = (service.title or "")[:300]
    e.resource_id = res.id if res else None
    e.date_from, e.date_to, e.time_from, e.time_to = d1, d2, t1, t2
    if note is not None:
        e.note = (str(note).strip()[:1000] or None)
    e.updated_at = _now()
    e.last_note = None
    db.flush()
    who = res.name if res else "любой мастер"
    activity.log(db, user.id, "waitlist.add" if created else "waitlist.update", "waitlist",
                 f"{e.client_name or 'Клиент'} · {e.service_title} · {who} · {_period_words(e)}",
                 object_id=e.id, user=user, source=source, client_name=e.client_name)
    bs.notify_changed(db, user.id)
    _last_scan.pop(str(user.id), None)       # новый в листе — проверим окна на ближайшем тике
    return e


def update(db: Session, user, entry_id, data: Dict[str, Any]):
    e = get_owned(db, user.id, entry_id)
    if e.status not in ("waiting", "offered"):
        raise WaitlistError("Запись в листе уже закрыта")
    return add(db, user, service_id=e.service_entry_id, resource_id=data.get("resource_id", e.resource_id),
               date_from=data.get("date_from", e.date_from), date_to=data.get("date_to", e.date_to),
               time_from=data.get("time_from", e.time_from), time_to=data.get("time_to", e.time_to),
               contact_id=e.contact_id, name=e.client_name, note=data.get("note", e.note), source="owner")


def get_owned(db: Session, user_id, entry_id):
    from backend.models.waitlist import WaitlistEntry
    eid = _uuid(entry_id)
    e = db.query(WaitlistEntry).filter(WaitlistEntry.id == eid, WaitlistEntry.user_id == user_id).first() if eid else None
    if e is None:
        raise WaitlistError("Запись в листе ожидания не найдена")
    return e


def _clear_offer(e) -> None:
    e.offer_start = e.offer_resource_id = e.offered_at = e.offer_expires_at = None


def cancel(db: Session, user, entry_id, by_client: bool = False):
    e = get_owned(db, user.id, entry_id)
    if e.status in ("booked", "cancelled", "expired"):
        return e
    e.status, e.closed_at, e.updated_at = "cancelled", _now(), _now()
    _clear_offer(e)
    activity.log(db, user.id, "waitlist.cancel", "waitlist",
                 f"{e.client_name or 'Клиент'} · {e.service_title}" + (" — клиенту больше не нужно" if by_client else ""),
                 object_id=e.id, user=None if by_client else user, source="online" if by_client else None,
                 client_name=e.client_name)
    bs.notify_changed(db, user.id)
    return e


def entry_to_dict(e, names: Dict[str, str]) -> Dict[str, Any]:
    left = None
    if e.status == "offered" and e.offer_expires_at:
        left = max(0, int((bs._aw(e.offer_expires_at) - bs.now_utc()).total_seconds() // 60))
    return {
        "id": str(e.id), "status": e.status, "status_title": STATUS_TITLES.get(e.status, e.status),
        "contact_id": str(e.contact_id) if e.contact_id else None, "client_name": e.client_name or "",
        "client_phone": e.client_phone or "", "service_id": str(e.service_entry_id) if e.service_entry_id else None,
        "service": e.service_title or "", "resource_id": str(e.resource_id) if e.resource_id else None,
        "resource": names.get(str(e.resource_id)) if e.resource_id else None,
        "date_from": e.date_from.isoformat(), "date_to": e.date_to.isoformat(),
        "time_from": e.time_from, "time_to": e.time_to, "period": _period_words(e), "note": e.note or "",
        "source": e.source, "source_title": SOURCE_TITLES.get(e.source, e.source),
        "offer": ({"start": e.offer_start.isoformat(), "label": bs.fmt_local(e.offer_start),
                   "resource": names.get(str(e.offer_resource_id)), "minutes_left": left}
                  if e.status == "offered" and e.offer_start else None),
        "offers_count": e.offers_count or 0, "booking_id": str(e.booking_id) if e.booking_id else None,
        "hint": e.last_note or "", "created_at": e.created_at.isoformat() if e.created_at else None,
    }


def listing(db: Session, user_id, status: Optional[str] = None, limit: int = 200) -> Dict[str, Any]:
    from backend.models.waitlist import WaitlistEntry
    q = db.query(WaitlistEntry).filter(WaitlistEntry.user_id == user_id)
    if status == "active":
        q = q.filter(WaitlistEntry.status.in_(("waiting", "offered")))
    elif status in STATUS_TITLES:
        q = q.filter(WaitlistEntry.status == status)
    elif status == "closed":
        q = q.filter(WaitlistEntry.status.in_(("booked", "cancelled", "expired")))
    rows = q.order_by(WaitlistEntry.created_at.asc() if status in (None, "", "active", "waiting", "offered")
                      else WaitlistEntry.updated_at.desc()).limit(max(1, min(limit, 500))).all()
    names = {str(r.id): r.name for r in bs.resources(db, user_id, active_only=False)}
    cnt = dict(db.execute(text("SELECT status, count(*) FROM waitlist_entries WHERE user_id = :u GROUP BY status"),
                          {"u": str(user_id)}).fetchall())
    counts = {s: int(cnt.get(s, 0)) for s in STATUS_TITLES}
    counts["active"] = counts["waiting"] + counts["offered"]
    items = [entry_to_dict(e, names) for e in rows]
    pos = 0
    for it in items:            # место в очереди — среди ждущих по времени постановки
        if it["status"] in ("waiting", "offered"):
            pos += 1
            it["position"] = pos
    return {"items": items, "counts": counts}


def active_count(db: Session, user_id) -> int:
    return int(db.execute(text("SELECT count(*) FROM waitlist_entries WHERE user_id = :u AND status IN "
                               "('waiting', 'offered')"), {"u": str(user_id)}).scalar() or 0)


# ───────────────────────────── поиск окна и предложение ─────────────────────────────

def _key(rid, start: datetime) -> str:
    return f"{rid}|{bs._aw(start).astimezone(timezone.utc).isoformat()}"


def find_slot(db: Session, user, e, skip: set) -> Optional[Tuple[datetime, str]]:
    """Первое свободное окно для записи листа: дни, время суток, мастер; без skip и уже предложенных ему."""
    service = bs.find_service(db, user.id, e.service_entry_id) if e.service_entry_id else None
    if service is None:
        return None
    start_day = max(e.date_from, _today())
    if start_day > e.date_to:
        return None
    days = min(MAX_SPAN_DAYS, (e.date_to - start_day).days + 1)
    seen = set(e.offered_slots or [])
    earliest = bs.now_utc() + timedelta(minutes=OFFER_MINUTES + 30)   # клиенту — полчаса подумать и полчаса доехать
    for d in bs.free_slots(db, user, service, e.resource_id, start_day, days=days, online_only=True):
        for s in d["slots"]:
            if (e.time_from and s["time"] < e.time_from) or (e.time_to and s["time"] > e.time_to):
                continue
            st = bs.parse_start(s["start"])
            if st < earliest:
                continue
            for rid in s["resource_ids"]:
                k = _key(rid, st)
                if k in skip or k in seen:
                    continue
                return st, rid
    return None


def _can_message(db: Session, contact) -> bool:
    from backend.models.agent_config import AgentConfig
    from backend.services import inbox_service
    agent = db.query(AgentConfig).filter(AgentConfig.id == contact.agent_config_id).first()
    if agent is None:
        return False
    try:
        return any((v or {}).get("can_send") for v in inbox_service.send_channels(db, contact, agent).values())
    except Exception:
        db.rollback()
        return False


def _offer_text(db: Session, user, e, contact, start: datetime, rid, names: Dict[str, str]) -> Tuple[str, str]:
    from backend.services import automations
    lang = automations.client_lang(db, contact.id, automations.limits_for(db, user.id).get("lang", "auto"))
    vals = {"имя": automations._first_name(e.client_name or contact.name), "когда": automations.when_words(start, bs.now_utc(), lang),
            "услуга": e.service_title or "", "мастер": names.get(str(rid), ""), "ссылка": link(e.id)}
    return automations.render(OFFER_TEXT["ky" if lang == "ky" else "ru"], vals), lang


def make_offer(db: Session, user, e, start: datetime, rid, now: Optional[datetime] = None) -> bool:
    """Предложить окно (без коммита): статус, срок 30 минут, сообщение в очередь."""
    from backend.models.agent_contact import AgentContact
    from backend.services import notify_queue as nq
    now = now or bs.now_utc()
    contact = db.query(AgentContact).filter(AgentContact.id == e.contact_id).first() if e.contact_id else None
    if contact is None:
        return False
    names = {str(r.id): r.name for r in bs.resources(db, user.id, active_only=False)}
    e.status, e.offer_start, e.offer_resource_id = "offered", start, _uuid(rid)
    e.offered_at, e.offer_expires_at = now, now + timedelta(minutes=OFFER_MINUTES)
    e.offers_count = (e.offers_count or 0) + 1
    e.offered_slots = list(e.offered_slots or []) + [_key(rid, start)]
    e.updated_at, e.last_note = now, None
    msg, lang = _offer_text(db, user, e, contact, start, rid, names)
    nq.enqueue(db, user_id=user.id, contact_id=contact.id, rule="waitlist_offer", text_=msg,
               idem_key=f"waitlist:{e.id}:{e.offers_count}", kind="service", lang=lang,
               send_after=now, expires_at=e.offer_expires_at, meta={"waitlist_id": str(e.id)})
    activity.log(db, user.id, "waitlist.offer", "waitlist",
                 f"Предложено {e.client_name or 'клиенту'}: {bs.fmt_local(start)}, {e.service_title}"
                 + (f" · {names.get(str(rid))}" if names.get(str(rid)) else "") + " — ждём ответа 30 минут",
                 object_id=e.id, actor=activity._actor("system"))
    bs.notify_changed(db, user.id)
    return True


def _expire(db: Session, now: datetime) -> int:
    rows = db.execute(text("""
        UPDATE waitlist_entries SET status = 'waiting', offer_start = NULL, offer_resource_id = NULL, offered_at = NULL,
               offer_expires_at = NULL, updated_at = :now, last_note = 'Не ответил на предложение за 30 минут'
        WHERE status = 'offered' AND offer_expires_at < :now RETURNING user_id
    """), {"now": now}).fetchall()
    past = db.execute(text("""
        UPDATE waitlist_entries SET status = 'expired', closed_at = :now, updated_at = :now,
               offer_start = NULL, offer_resource_id = NULL, offered_at = NULL, offer_expires_at = NULL
        WHERE status IN ('waiting', 'offered') AND date_to < :today RETURNING user_id
    """), {"now": now, "today": bs.to_local(now).date()}).fetchall()
    db.commit()
    for (uid,) in rows:
        _last_scan.pop(str(uid), None)
    return len(rows) + len(past)


def scan_user(db: Session, user, now: Optional[datetime] = None, only_entry=None) -> int:
    """Предложить окна ждущим одного салона — по очереди, под блокировкой строк (несколько воркеров)."""
    from backend.models.agent_contact import AgentContact
    from backend.models.waitlist import WaitlistEntry
    now = now or bs.now_utc()
    q = (db.query(WaitlistEntry).filter(WaitlistEntry.user_id == user.id, WaitlistEntry.status == "waiting",
                                        WaitlistEntry.date_to >= bs.to_local(now).date())
         .order_by(WaitlistEntry.created_at).with_for_update(skip_locked=True))
    entries = q.all()
    if not entries:
        db.rollback()
        return 0
    taken = {_key(r[0], r[1]) for r in db.execute(text(
        "SELECT offer_resource_id, offer_start FROM waitlist_entries WHERE user_id = :u AND status = 'offered'"),
        {"u": str(user.id)}).fetchall() if r[0] and r[1]}
    n = 0
    for e in entries:
        if only_entry is not None and str(e.id) != str(only_entry):
            continue
        contact = db.query(AgentContact).filter(AgentContact.id == e.contact_id).first() if e.contact_id else None
        if contact is None or not _can_message(db, contact):
            e.last_note = "Нет мессенджера, чтобы написать клиенту — предложите время сами"
            continue
        slot = find_slot(db, user, e, taken)
        if slot is None:
            continue
        if make_offer(db, user, e, slot[0], slot[1], now):
            taken.add(_key(slot[1], slot[0]))
            n += 1
    db.commit()
    return n


def tick(db: Session, now: Optional[datetime] = None, force: bool = False) -> int:
    """Раз в минуту из booking_notify.run_once: просрочки, затем предложения (только в окно отправок по записи)."""
    from backend.models.user import User
    from backend.services import notify_queue as nq
    now = now or bs.now_utc()
    try:
        _expire(db, now)
    except Exception as e:
        db.rollback()
        logger.warning(f"[WAITLIST] expire failed: {e}")
    lo, hi = nq.SERVICE_WINDOW
    loc = bs.to_local(now)
    if not force and not (lo <= loc.hour and (loc + timedelta(minutes=OFFER_MINUTES)).hour < hi
                          and (loc + timedelta(minutes=OFFER_MINUTES)).date() == loc.date()):
        return 0
    uids = [r[0] for r in db.execute(text(
        "SELECT DISTINCT user_id FROM waitlist_entries WHERE status = 'waiting' AND date_to >= :d"),
        {"d": loc.date()}).fetchall()]
    db.commit()
    n = 0
    mono = _time.monotonic()
    for uid in uids:
        if not force and mono - _last_scan.get(str(uid), -1e9) < SCAN_EVERY:
            continue
        _last_scan[str(uid)] = mono
        try:
            user = db.query(User).filter(User.id == uid).first()
            if user is None or not user.is_active or not user.has_agent_chat_access():
                continue
            n += scan_user(db, user, now)
        except Exception as e:
            db.rollback()
            logger.warning(f"[WAITLIST] scan {uid} failed: {e}")
    return n


def offer_now(db: Session, user, entry_id) -> Dict[str, Any]:
    """Кнопка «Предложить сейчас» в кабинете: найти окно этому клиенту и написать (в обход очереди сканирования)."""
    from backend.services import notify_queue as nq
    e = get_owned(db, user.id, entry_id)
    if e.status != "waiting":
        raise WaitlistError("Клиенту уже предложено время — ждём ответа" if e.status == "offered" else "Запись в листе закрыта")
    if not nq._window_ok("service", bs.now_utc()):
        raise WaitlistError("Сообщения клиентам уходят с 8:00 до 21:00 — утром предложим сами")
    db.commit()
    n = scan_user(db, user, only_entry=e.id)
    db.refresh(e)
    if n:
        return {"ok": True, "offered": True, "offer": entry_to_dict(e, {})["offer"]}
    return {"ok": True, "offered": False,
            "reason": e.last_note or "Свободного окна в эти дни и время пока нет — предложим, как только освободится"}


# ───────────────────────────── ответ клиента ─────────────────────────────

def _entry_for_answer(db: Session, entry_id, lock: bool = True):
    from backend.models.waitlist import WaitlistEntry
    q = db.query(WaitlistEntry).filter(WaitlistEntry.id == entry_id)
    if lock:
        q = q.with_for_update()
    return q.first()


def accept(db: Session, entry_id, source: str = "online") -> Dict[str, Any]:
    """«Записаться» на предложенное окно. Окно заняли — запись листа снова ждёт следующего."""
    from backend.models.user import User
    e = _entry_for_answer(db, entry_id)
    if e is None:
        raise WaitlistError("Запись в листе ожидания не найдена")
    if e.status == "booked":
        return {"ok": True, "status": "booked", "booking_id": str(e.booking_id) if e.booking_id else None, "already": True}
    if e.status != "offered" or not e.offer_start or bs._aw(e.offer_expires_at) < bs.now_utc():
        db.rollback()
        raise WaitlistError("Предложение уже не действует — мы напишем, когда освободится другое время")
    user = db.query(User).filter(User.id == e.user_id).first()
    try:
        with db.begin_nested():
            b = bs.create_booking(db, user, start=e.offer_start, service_id=e.service_entry_id,
                                  resource_id=e.offer_resource_id, contact_id=e.contact_id, client_name=e.client_name,
                                  client_phone=e.client_phone, source=source, channel="waitlist",
                                  note="Из листа ожидания")
    except bs.BookingError:
        e.status, e.last_note, e.updated_at = "waiting", "Предложенное время успели занять", _now()
        _clear_offer(e)
        db.commit()
        _last_scan.pop(str(e.user_id), None)
        raise WaitlistError("Это время уже успели занять. Вы остаётесь в листе ожидания — напишем, как только "
                            "освободится другое")
    e.status, e.booking_id, e.closed_at, e.updated_at = "booked", b.id, _now(), _now()
    _clear_offer(e)
    db.commit()
    try:
        from backend.core import background_loop
        from backend.services.booking_notify import notify_owner_new_booking
        background_loop.submit(notify_owner_new_booking(e.user_id, b.id, "waitlist"))
    except Exception as ex:
        logger.warning(f"[WAITLIST] owner notify not scheduled: {ex}")
    return {"ok": True, "status": "booked", "booking_id": str(b.id), "start": b.start_at.isoformat(),
            "label": bs.fmt_local(b.start_at)}


def decline(db: Session, entry_id, stop: bool = False) -> Dict[str, Any]:
    """«Не подходит» — ждём другое окно (это больше не предложим); «больше не нужно» — убрать из листа."""
    from backend.models.user import User
    e = _entry_for_answer(db, entry_id)
    if e is None:
        raise WaitlistError("Запись в листе ожидания не найдена")
    if e.status not in ("waiting", "offered"):
        db.rollback()
        return {"ok": True, "status": e.status}
    if stop:
        user = db.query(User).filter(User.id == e.user_id).first()
        cancel(db, user, e.id, by_client=True)
    else:
        e.status, e.last_note, e.updated_at = "waiting", "Клиенту не подошло предложенное время", _now()
        _clear_offer(e)
        _last_scan.pop(str(e.user_id), None)
    db.commit()
    return {"ok": True, "status": e.status}


def link_state(db: Session, token: str) -> Dict[str, Any]:
    from backend.models.booking import BookingPage, BookingResource
    from backend.models.user import User
    from backend.services import salon_media as sm
    from backend.services import salon_page as sp
    eid = entry_id_from_token(token)
    e = _entry_for_answer(db, eid, lock=False) if eid else None
    if e is None:
        raise WaitlistError("Ссылка неверная или устарела")
    owner = db.query(User).filter(User.id == e.user_id).first()
    page = db.query(BookingPage).filter(BookingPage.user_id == e.user_id).first()
    if owner is None or not owner.is_active or page is None:
        raise WaitlistError("Салон сейчас не принимает записи")
    prof, cfg = sp.profile_of(page), bs.settings_of(page)
    offer = None
    if e.status == "offered" and e.offer_start and bs._aw(e.offer_expires_at) > bs.now_utc():
        m = db.query(BookingResource).filter(BookingResource.id == e.offer_resource_id).first() if e.offer_resource_id else None
        loc = bs.to_local(e.offer_start)
        offer = {"start": e.offer_start.isoformat(), "date": loc.date().isoformat(), "time": loc.strftime("%H:%M"),
                 "label": bs.fmt_local(e.offer_start),
                 "expires_at": bs._aw(e.offer_expires_at).isoformat(),
                 "minutes_left": max(0, int((bs._aw(e.offer_expires_at) - bs.now_utc()).total_seconds() // 60)),
                 "master": ({"name": m.name, "photo_url": sm.url_of(m.photo_id) if m.photo_id else None,
                             "initials": bs.initials(m.name), "color": m.color} if m else None)}
    booked = None
    if e.status == "booked" and e.booking_id:
        from backend.models.booking import Booking
        b = db.query(Booking).filter(Booking.id == e.booking_id).first()
        if b is not None:
            booked = {"label": bs.fmt_local(b.start_at), "status": b.status}
    return {
        "salon": {"name": cfg.get("title") or owner.company_name or "Салон", "logo_url": sm.url_of(prof.get("logo_id")),
                  "accent": prof.get("accent") or sp.DEFAULT_ACCENT, "address": cfg.get("address") or "",
                  "page_url": sp.page_path(page, owner.business_niche), "book_url": f"/b/{page.slug}" if page.enabled else None},
        "entry": {"status": e.status, "service": e.service_title or "", "period": _period_words(e),
                  "client_name": e.client_name or ""},
        "offer": offer, "booked": booked,
        "_entry_id": e.id,
    }


# ───────────────────────────── бот ─────────────────────────────

def active_offer(db: Session, user_id, contact_id):
    from backend.models.waitlist import WaitlistEntry
    return (db.query(WaitlistEntry).filter(WaitlistEntry.user_id == user_id, WaitlistEntry.contact_id == contact_id,
                                           WaitlistEntry.status == "offered")
            .order_by(WaitlistEntry.offered_at.desc()).first())


def offer_hint(db: Session, user_id, contact_id) -> str:
    """Строка для разбора переписки: клиенту предложено окно из листа ожидания — «да» записывает."""
    try:
        e = active_offer(db, user_id, contact_id)
        if e is None or bs._aw(e.offer_expires_at) < bs.now_utc():
            return ""
        return (f"\n\nЛИСТ ОЖИДАНИЯ: клиенту предложили освободившееся время {bs.fmt_local(e.offer_start)}"
                f" ({e.service_title}), ответ ждём до {bs.to_local(e.offer_expires_at).strftime('%H:%M')}. "
                "Согласился («да», «записывайте», «ооба») — answer_waitlist_offer(accept=true) и подтверди запись "
                "одной фразой; не подходит — answer_waitlist_offer(accept=false); больше не нужно — "
                "answer_waitlist_offer(accept=false, stop=true). Второй раз итог не спрашивай: предложение и было итогом.")
    except Exception as e:
        logger.warning(f"[WAITLIST] hint: {e}")
        db.rollback()
        return ""
