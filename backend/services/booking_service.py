"""
Онлайн-запись: свободное время, запись, перенос, отмена (ветка 1811-niche-admin-speed).

Одна реализация на все входы — календарь владельца (/api/booking), бот в переписке (инструменты
агента booking_*), голосовой агент на звонке (functions/book_appointment.py), клиент по ссылке
(/api/public/booking/<slug>). Поэтому правила одни и те же везде:

  • время — по Бишкеку (UTC+6, без перехода на летнее), в базе — UTC;
  • свободное окно = рабочий интервал мастера в этот день (график по дням недели, выходные даты)
    минус занятые записи (booked/confirmed/arrived) с буфером, шаг сетки step_min, не раньше чем
    через lead_min от «сейчас» и не дальше horizon_days;
  • длительность и цена — у услуги (agent_knowledge_entries.duration_min / price), а у мастера могут
    быть свои (booking_resources.service_prices: топ-мастер дороже и дольше) — service_terms();
  • мастер с online_booking = false в онлайн-записи, у бота и на звонке не предлагается — только
    администратор записывает к нему из кабинета; то же для услуги (agent_knowledge_entries.online_booking);
  • услуга занимает мастера дольше, чем клиента: подготовка до (prep_min) и перерыв после (after_min,
    уборка/стерилизация) — значения берутся в запись при создании; общий перерыв buffer_min
    действует в обе стороны (между любыми двумя записями мастера);
  • предоплата — правило услуги (prepay: сумма или % от цены), сумма считается в запись, отметку
    «получена» ставит администратор (оплату салон принимает сам);
  • журнал v2 (шаг 1.4): блоки времени (booking_blocks: перерыв, обучение, отпуск, больничный — у
    одного мастера или у всех) в свободное время не попадают; услуга может требовать кабинет/кресло/
    аппарат (room_ids) — запись занимает и мастера, и свободный кабинет; визит = несколько услуг подряд
    одному клиенту (visit_id), планируется целиком (plan_visit) и пишется одной транзакцией;
    последний рубеж от двойной записи — ограничение EXCLUDE в базе (IntegrityError → «время занято»);
    любое изменение журнала шлёт pg_notify (уходит при commit) — открытые журналы обновляются сразу.
  • двойная запись невозможна: перед вставкой строка мастера блокируется (SELECT … FOR UPDATE),
    пересечение проверяется под блокировкой;
  • клиент записи — карточка CRM (по id, по телефону или новая), стадия «Новый» → «Записан».
"""

from __future__ import annotations

import logging
import re
import secrets
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import and_, or_, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models.booking import (ACTIVE_STATUSES, BLOCK_KINDS, BOOKING_STATUSES, VISITED_STATUSES, Booking,
                                    BookingBlock, BookingPage, BookingResource, BookingRoom)

logger = logging.getLogger(__name__)

BISHKEK = timezone(timedelta(hours=6))
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_TITLES = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
DEFAULT_DURATION = 60
DEFAULT_SCHEDULE = {d: ([["09:00", "19:00"]] if d != "sun" else []) for d in DAYS}
DEFAULT_SETTINGS = {
    "step_min": 30,        # шаг сетки свободного времени
    "buffer_min": 0,       # перерыв после записи
    "lead_min": 60,        # не раньше чем через N минут от «сейчас» (бот и ссылка)
    "horizon_days": 30,    # насколько вперёд можно записаться
    "reminders": True,     # напоминания клиенту за сутки и за 2 часа
    "thanks": True,        # «спасибо» после визита
    "address": "",
    "title": "",
    "master_phones": False,   # мастера (свой журнал) видят телефоны клиентов
    "prepay_info": "",        # как внести предоплату (реквизиты: Mbank, Элсом, наличные) — клиенту и боту
}
COLORS = ["#2a5ce8", "#0d9488", "#7c3aed", "#db2777", "#ea580c", "#0891b2", "#4d7c0f", "#b45309"]
STATUS_TITLES = {"booked": "Записан", "confirmed": "Подтвердил", "arrived": "Пришёл", "completed": "Завершён",
                 "no_show": "Не пришёл", "cancelled": "Отменена"}
BLOCK_TITLES = {"block": "Не записывать", "break": "Перерыв", "training": "Обучение", "vacation": "Отпуск",
                "sick": "Больничный"}
ROOM_KINDS = {"room": "Кабинет", "chair": "Кресло", "device": "Аппарат"}
NOTIFY_CHANNEL = "voksy_booking"


def visited(status: Optional[str]) -> bool:
    """Клиент пришёл: «пришёл» (в салоне) или «завершён» — визит и выручка."""
    return status in VISITED_STATUSES


def notify_changed(db: Session, user_id) -> None:
    """Журнал владельца изменился: NOTIFY уходит при commit этой транзакции (откат — не уходит).
    Слушают воркеры (services/booking_stream.py) и обновляют открытые журналы."""
    try:
        db.execute(text("SELECT pg_notify(:c, :u)"), {"c": NOTIFY_CHANNEL, "u": str(user_id)})
    except Exception as e:     # не PostgreSQL или соединение уже в ошибке — журнал обновится опросом
        logger.debug(f"[BOOKING] notify skipped: {e}")


def _flush(db: Session) -> None:
    """flush с переводом нарушения ограничения «нет пересечений» в понятную причину."""
    try:
        db.flush()
    except IntegrityError as e:
        if "no_overlap" in str(e):
            raise BookingError("Это время уже занято — его только что заняли, выберите другое")
        raise


class BookingError(ValueError):
    """Понятная причина отказа (уходит владельцу, боту или клиенту как есть)."""


# ───────────────────────────── время ─────────────────────────────

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def to_local(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(BISHKEK)


def parse_hm(value: str) -> Optional[time]:
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(value or ""))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h == 24 and mi == 0:
        return time(23, 59)
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return time(h, mi)


def parse_start(value) -> datetime:
    """ISO-время → aware UTC. Без часового пояса — считаем, что это время Бишкека."""
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value or "").strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            raise BookingError("Неверное время записи: нужно, например, 2026-11-20T15:00")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BISHKEK)
    return dt.astimezone(timezone.utc)


def parse_day(value) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    raw = str(value or "").strip().lower()
    today = to_local(now_utc()).date()
    if raw in ("", "today", "сегодня"):
        return today
    if raw in ("tomorrow", "завтра"):
        return today + timedelta(days=1)
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        raise BookingError("Неверная дата: нужно, например, 2026-11-20")


def fmt_local(dt: datetime) -> str:
    loc = to_local(dt)
    return f"{loc.day:02d}.{loc.month:02d} {DAY_TITLES[loc.weekday()]} {loc.hour:02d}:{loc.minute:02d}"


# ───────────────────────────── настройки и мастера ─────────────────────────────

def _slugify(text_value: str) -> str:
    tr = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "y",
          "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
          "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ы": "y", "э": "e", "ю": "yu", "я": "ya", "ң": "n",
          "ө": "o", "ү": "u", "ь": "", "ъ": ""}
    s = "".join(tr.get(ch, ch) for ch in (text_value or "").lower())
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:40]


def get_page(db: Session, user, create: bool = True) -> Optional[BookingPage]:
    page = db.query(BookingPage).filter(BookingPage.user_id == user.id).first()
    if page is None and create:
        base = _slugify(getattr(user, "company_name", None) or getattr(user, "first_name", None) or "") or "zapis"
        slug = f"{base}-{secrets.token_hex(2)}"
        while db.query(BookingPage).filter(BookingPage.slug == slug).first() is not None:
            slug = f"{base}-{secrets.token_hex(3)}"
        page = BookingPage(user_id=user.id, slug=slug, enabled=True, settings={})
        db.add(page)
        db.flush()
    return page


def settings_of(page: Optional[BookingPage]) -> Dict[str, Any]:
    out = dict(DEFAULT_SETTINGS)
    for k, v in ((page.settings if page else None) or {}).items():
        if k in DEFAULT_SETTINGS:
            out[k] = v
    out["step_min"] = max(5, min(int(out.get("step_min") or 30), 240))
    out["buffer_min"] = max(0, min(int(out.get("buffer_min") or 0), 240))
    out["lead_min"] = max(0, min(int(out.get("lead_min") or 0), 7 * 24 * 60))
    out["horizon_days"] = max(1, min(int(out.get("horizon_days") or 30), 180))
    return out


def clean_schedule(raw: Any) -> Dict[str, List[List[str]]]:
    out: Dict[str, List[List[str]]] = {}
    src = raw if isinstance(raw, dict) else {}
    for d in DAYS:
        items = []
        for iv in (src.get(d) or [])[:4]:
            if not isinstance(iv, (list, tuple)) or len(iv) != 2:
                continue
            a, b = parse_hm(iv[0]), parse_hm(iv[1])
            if a and b and a < b:
                items.append([a.strftime("%H:%M"), b.strftime("%H:%M")])
        items.sort()
        out[d] = items
    return out


def resources(db: Session, user_id, active_only: bool = True) -> List[BookingResource]:
    q = db.query(BookingResource).filter(BookingResource.user_id == user_id)
    if active_only:
        q = q.filter(BookingResource.is_active.is_(True))
    return q.order_by(BookingResource.position, BookingResource.created_at).all()


def ensure_resources(db: Session, user) -> List[BookingResource]:
    """Первый заход в записи: один мастер — сам владелец, с графиком пн–сб 9–19."""
    rows = resources(db, user.id, active_only=False)
    if rows:
        return [r for r in rows if r.is_active]
    r = BookingResource(user_id=user.id, name=(getattr(user, "first_name", None) or "Мастер")[:120],
                        role="Мастер", color=COLORS[0], schedule=dict(DEFAULT_SCHEDULE), days_off=[], position=0)
    db.add(r)
    db.flush()
    return [r]


def experience_years(since) -> Optional[int]:
    """«Работает с 2018 года» → 7 (лет опыта на сегодня по Бишкеку)."""
    try:
        y = int(since)
    except (TypeError, ValueError):
        return None
    now_y = to_local(now_utc()).year
    if y < 1950 or y > now_y:
        return None
    return now_y - y


def initials(name: Optional[str]) -> str:
    parts = [p for p in re.split(r"\s+", (name or "").strip()) if p]
    if not parts:
        return "?"
    return (parts[0][0] + (parts[1][0] if len(parts) > 1 else "")).upper()


def clean_service_prices(raw: Any) -> Dict[str, Dict[str, Any]]:
    """{service_id: {"price": 1500, "duration_min": 90}} — только числа в разумных пределах."""
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(raw, dict):
        return out
    for sid, v in list(raw.items())[:500]:
        try:
            key = str(uuid.UUID(str(sid)))
        except (ValueError, TypeError):
            continue
        if not isinstance(v, dict):
            continue
        item: Dict[str, Any] = {}
        try:
            if v.get("price") not in (None, ""):
                p = float(v["price"])
                if 0 <= p <= 10_000_000:
                    item["price"] = round(p, 2)
        except (TypeError, ValueError):
            pass
        try:
            if v.get("duration_min") not in (None, ""):
                d = int(float(v["duration_min"]))
                if 5 <= d <= 1440:
                    item["duration_min"] = d
        except (TypeError, ValueError):
            pass
        if item:
            out[key] = item
    return out


def service_terms(resource: Optional[BookingResource], service) -> Tuple[int, Optional[float]]:
    """Длительность и цена услуги у этого мастера: своя у мастера, иначе из каталога."""
    dur = duration_of(service) if service is not None else DEFAULT_DURATION
    price = getattr(service, "price", None) if service is not None else None
    if resource is not None and service is not None:
        own = (resource.service_prices or {}).get(str(service.id)) or {}
        if isinstance(own, dict):
            if own.get("duration_min"):
                dur = int(own["duration_min"])
            if own.get("price") is not None:
                price = float(own["price"])
    return dur, price


def resource_to_dict(r: BookingResource) -> Dict[str, Any]:
    from backend.services.salon_media import media_items, url_of
    return {"id": str(r.id), "name": r.name, "role": r.role, "color": r.color or COLORS[0], "phone": r.phone,
            "initials": initials(r.name),
            "schedule": clean_schedule(r.schedule or DEFAULT_SCHEDULE), "days_off": list(r.days_off or []),
            "service_ids": r.service_ids, "is_active": bool(r.is_active), "position": r.position or 0,
            "photo_id": str(r.photo_id) if r.photo_id else None, "photo_url": url_of(r.photo_id),
            "bio": r.bio or "", "experience_since": r.experience_since,
            "experience_years": experience_years(r.experience_since),
            "portfolio": media_items(r.portfolio, "caption"), "certificates": media_items(r.certificates, "title"),
            "service_prices": clean_service_prices(r.service_prices),
            "team_member_id": str(r.team_member_id) if r.team_member_id else None,
            "online_booking": r.online_booking is not False}


# ───────────────────────────── услуги ─────────────────────────────

def services(db: Session, user_id) -> List[Any]:
    """Услуги для записи = активные позиции каталога владельца (kind=product)."""
    from backend.models.agent_knowledge import AgentKnowledgeEntry
    return (db.query(AgentKnowledgeEntry)
            .filter(AgentKnowledgeEntry.user_id == user_id, AgentKnowledgeEntry.kind == "product",
                    AgentKnowledgeEntry.is_active.is_(True))
            .order_by(AgentKnowledgeEntry.collection, AgentKnowledgeEntry.title).limit(500).all())


def split_category(collection: Optional[str]) -> Tuple[str, str]:
    """«Волосы / Окрашивание» → («Волосы», «Окрашивание»); без подкатегории — («Волосы», "")."""
    raw = " ".join(str(collection or "").split())
    if " / " in raw:
        a, b = raw.split(" / ", 1)
        return a.strip(), b.strip()
    return raw, ""


def join_category(category: Optional[str], sub: Optional[str]) -> Optional[str]:
    a = " ".join(str(category or "").replace("/", " ").split())[:60]
    b = " ".join(str(sub or "").replace("/", " ").split())[:56]
    if not a:
        return None
    return f"{a} / {b}" if b else a


def _num(v: float) -> str:
    v = float(v)
    return f"{v:,.0f}".replace(",", " ") if v == int(v) else f"{v:,.2f}".replace(",", " ")


def price_text(price, price_to=None, currency: Optional[str] = "KGS") -> str:
    """«1 500 сом», «1 500–2 500 сом», «от 1 500 сом» (только «до» неизвестно — нет)."""
    cur = "сом" if (currency or "KGS").upper() in ("KGS", "СОМ", "SOM") else (currency or "")
    if price is None and price_to is None:
        return ""
    if price is None:
        return f"до {_num(price_to)} {cur}".strip()
    if price_to is not None and float(price_to) > float(price):
        return f"{_num(price)}–{_num(price_to)} {cur}".strip()
    return f"{_num(price)} {cur}".strip()


def service_online(service) -> bool:
    return service is None or getattr(service, "online_booking", None) is not False


def service_pads(service) -> Tuple[int, int]:
    """Подготовка до и перерыв после услуги, минуты."""
    if service is None:
        return 0, 0
    return int(getattr(service, "prep_min", None) or 0), int(getattr(service, "after_min", None) or 0)


def clean_prepay(raw: Any) -> Optional[Dict[str, Any]]:
    """{"type": "fixed"|"percent", "value": N} или None."""
    if not isinstance(raw, dict):
        return None
    t = raw.get("type")
    try:
        v = float(raw.get("value") or 0)
    except (TypeError, ValueError):
        return None
    if t == "percent" and 0 < v <= 100:
        return {"type": "percent", "value": round(v, 1)}
    if t == "fixed" and 0 < v <= 10_000_000:
        return {"type": "fixed", "value": round(v, 2)}
    return None


def prepay_amount(service, price) -> Optional[float]:
    """Сколько предоплаты по правилу услуги (процент — от цены этой записи)."""
    rule = clean_prepay(getattr(service, "prepay", None)) if service is not None else None
    if not rule:
        return None
    if rule["type"] == "fixed":
        return rule["value"]
    if price is None:
        return None
    return float(round(float(price) * rule["value"] / 100.0))


def prepay_text(rule: Optional[Dict[str, Any]]) -> str:
    rule = clean_prepay(rule)
    if not rule:
        return ""
    return (f"предоплата {_num(rule['value'])} сом" if rule["type"] == "fixed"
            else f"предоплата {_num(rule['value'])}%")


def service_to_dict(e) -> Dict[str, Any]:
    cat, sub = split_category(e.collection)
    prep, after = service_pads(e)
    return {"id": str(e.id), "title": e.title, "price": e.price, "price_to": getattr(e, "price_to", None),
            "price_text": price_text(e.price, getattr(e, "price_to", None), e.currency),
            "currency": e.currency or "KGS",
            "duration_min": e.duration_min or DEFAULT_DURATION, "collection": e.collection or "",
            "category": cat, "subcategory": sub, "prep_min": prep, "after_min": after,
            "online_booking": service_online(e), "prepay": clean_prepay(getattr(e, "prepay", None)),
            "prepay_text": prepay_text(getattr(e, "prepay", None)),
            "room_ids": [str(x) for x in (getattr(e, "room_ids", None) or [])],
            "description": (e.body or "")[:300]}


def find_service(db: Session, user_id, service_id=None, query: Optional[str] = None):
    """Услуга по id или по названию (бот и голос называют её словами: «стрижка», «маникюр с покрытием»)."""
    from backend.models.agent_knowledge import AgentKnowledgeEntry
    if service_id:
        try:
            sid = uuid.UUID(str(service_id))
        except ValueError:
            sid = None
        if sid:
            e = db.query(AgentKnowledgeEntry).filter(AgentKnowledgeEntry.id == sid,
                                                     AgentKnowledgeEntry.user_id == user_id).first()
            if e:
                return e
    q = (query or (service_id if service_id and not re.fullmatch(r"[0-9a-f-]{36}", str(service_id)) else "") or "").strip().lower()
    if not q:
        return None
    best, best_score = None, 0
    words = [w for w in re.split(r"[^\wёңөү]+", q) if len(w) >= 3]
    for e in services(db, user_id):
        title = (e.title or "").lower()
        hay = title + " " + (e.keywords or "").lower() + " " + (e.collection or "").lower()
        score = 10 if q == title else (6 if q in title else 0)
        score += sum(2 for w in words if w[:5] in hay)
        if score > best_score:
            best, best_score = e, score
    return best if best_score >= 2 else None


def duration_of(service) -> int:
    return int(getattr(service, "duration_min", None) or DEFAULT_DURATION)


# ───────────────────────────── свободное время ─────────────────────────────

def _work_intervals(r: BookingResource, day: date) -> List[Tuple[datetime, datetime]]:
    if day.isoformat() in set(r.days_off or []):
        return []
    sched = clean_schedule(r.schedule or DEFAULT_SCHEDULE)
    out = []
    for a, b in sched.get(DAYS[day.weekday()], []):
        ta, tb = parse_hm(a), parse_hm(b)
        out.append((datetime.combine(day, ta, BISHKEK).astimezone(timezone.utc),
                    datetime.combine(day, tb, BISHKEK).astimezone(timezone.utc)))
    return out


def _aw(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _blocks_busy(db: Session, user_id, ids: List, start: datetime, end: datetime) -> Dict[str, List[Tuple[datetime, datetime]]]:
    """Блоки времени мастеров (и общие на всех) в окне — как занятые интервалы."""
    out: Dict[str, List[Tuple[datetime, datetime]]] = {str(r): [] for r in ids}
    if not ids:
        return out
    flt = BookingBlock.resource_id.in_(ids)
    if user_id is not None:
        flt = or_(flt, and_(BookingBlock.resource_id.is_(None), BookingBlock.user_id == user_id))
    for rid, a, b in (db.query(BookingBlock.resource_id, BookingBlock.start_at, BookingBlock.end_at)
                      .filter(flt, BookingBlock.start_at < end, BookingBlock.end_at > start).all()):
        for key in ([str(rid)] if rid else list(out.keys())):
            if key in out:
                out[key].append((_aw(a), _aw(b)))
    return out


def _busy(db: Session, resource_ids: Iterable, start: datetime, end: datetime, buffer_min: int,
          exclude_id=None, user_id=None) -> Dict[str, List[Tuple[datetime, datetime]]]:
    ids = [r for r in resource_ids]
    out: Dict[str, List[Tuple[datetime, datetime]]] = {str(r): [] for r in ids}
    if not ids:
        return out
    for rid, items in _blocks_busy(db, user_id, ids, start - timedelta(hours=4), end + timedelta(hours=4)).items():
        out[rid].extend(items)
    pad = timedelta(hours=4)    # подготовка/перерыв соседних записей могут залезать в окно
    q = db.query(Booking.resource_id, Booking.start_at, Booking.end_at, Booking.prep_min, Booking.after_min).filter(
        Booking.resource_id.in_(ids), Booking.status.in_(ACTIVE_STATUSES),
        Booking.start_at < end + pad, Booking.end_at > start - pad)
    if exclude_id is not None:
        q = q.filter(Booking.id != exclude_id)
    buf = timedelta(minutes=buffer_min)
    for rid, a, b, prep, after in q.all():
        a = a if a.tzinfo else a.replace(tzinfo=timezone.utc)
        b = b if b.tzinfo else b.replace(tzinfo=timezone.utc)
        # время мастера: подготовка до, сама услуга, перерыв после и общий перерыв
        out[str(rid)].append((a - timedelta(minutes=prep or 0), b + timedelta(minutes=after or 0) + buf))
    return out


def rooms(db: Session, user_id, active_only: bool = True) -> List[BookingRoom]:
    q = db.query(BookingRoom).filter(BookingRoom.user_id == user_id)
    if active_only:
        q = q.filter(BookingRoom.is_active.is_(True))
    return q.order_by(BookingRoom.position, BookingRoom.created_at).all()


def room_to_dict(r: BookingRoom) -> Dict[str, Any]:
    return {"id": str(r.id), "name": r.name, "kind": r.kind or "room", "kind_title": ROOM_KINDS.get(r.kind or "room", "Кабинет"),
            "is_active": bool(r.is_active), "position": r.position or 0}


def service_rooms(db: Session, user_id, service) -> List[BookingRoom]:
    """Кабинеты, один из которых нужен услуге (пусто — кабинет не нужен)."""
    ids = {str(x) for x in (getattr(service, "room_ids", None) or [])} if service is not None else set()
    if not ids:
        return []
    return [r for r in rooms(db, user_id) if str(r.id) in ids]


def _room_busy(db: Session, room_ids: List, start: datetime, end: datetime,
               exclude_id=None) -> Dict[str, List[Tuple[datetime, datetime]]]:
    """Занятость кабинетов: запись с подготовкой и уборкой (общий перерыв мастера к кабинету не относится)."""
    out: Dict[str, List[Tuple[datetime, datetime]]] = {str(r): [] for r in room_ids}
    if not room_ids:
        return out
    pad = timedelta(hours=4)
    q = db.query(Booking.room_id, Booking.start_at, Booking.end_at, Booking.prep_min, Booking.after_min).filter(
        Booking.room_id.in_(room_ids), Booking.status.in_(ACTIVE_STATUSES),
        Booking.start_at < end + pad, Booking.end_at > start - pad)
    if exclude_id is not None:
        q = q.filter(Booking.id != exclude_id)
    for rid, a, b, prep, after in q.all():
        out[str(rid)].append((_aw(a) - timedelta(minutes=prep or 0), _aw(b) + timedelta(minutes=after or 0)))
    return out


def _free_room(candidates: List[BookingRoom], busy: Dict[str, List[Tuple[datetime, datetime]]],
               start: datetime, end: datetime, prep: int, after: int, prefer=None) -> Optional[BookingRoom]:
    a, b = start - timedelta(minutes=prep), end + timedelta(minutes=after)
    ordered = sorted(candidates, key=lambda r: (0 if prefer and str(r.id) == str(prefer) else 1, r.position or 0))
    for r in ordered:
        if not any(a < tb and b > ta for ta, tb in busy.get(str(r.id), [])):
            return r
    return None


def _block(start: datetime, end: datetime, prep: int, after: int, buffer_min: int) -> Tuple[datetime, datetime]:
    """Сколько времени мастера занимает новая запись: подготовка, услуга, перерыв после, общий перерыв."""
    return start - timedelta(minutes=prep), end + timedelta(minutes=after + buffer_min)


def _resource_does(r: BookingResource, service) -> bool:
    ids = r.service_ids
    if not ids or service is None:
        return True
    return str(service.id) in {str(x) for x in ids}


def bookable_online(r: BookingResource) -> bool:
    return r.online_booking is not False


def free_slots(db: Session, user, service=None, resource_id=None, day=None, days: int = 1,
               ignore_lead: bool = False, limit_per_day: Optional[int] = None,
               online_only: bool = False) -> List[Dict[str, Any]]:
    """Свободное время по дням: [{date, weekday, slots: [{time, start, resource_ids}]}].
    online_only — для клиента по ссылке, бота и звонка: без мастеров «только через администратора»."""
    page = get_page(db, user, create=False)
    cfg = settings_of(page)
    step = timedelta(minutes=cfg["step_min"])
    if online_only and not service_online(service):
        return []                      # на эту услугу записывает администратор
    res = [r for r in ensure_resources(db, user) if _resource_does(r, service)]
    if online_only:
        res = [r for r in res if bookable_online(r)]
    prep, after = service_pads(service)
    if resource_id:
        res = [r for r in res if str(r.id) == str(resource_id)]
    if not res:
        return []
    start_day = parse_day(day) if day is not None else to_local(now_utc()).date()
    days = max(1, min(int(days or 1), 31))
    earliest = now_utc() + (timedelta(0) if ignore_lead else timedelta(minutes=cfg["lead_min"]))
    horizon = to_local(now_utc()).date() + timedelta(days=cfg["horizon_days"])
    window_start = datetime.combine(start_day, time(0), BISHKEK).astimezone(timezone.utc)
    window_end = window_start + timedelta(days=days)
    busy = _busy(db, [r.id for r in res], window_start, window_end, cfg["buffer_min"], user_id=user.id)
    need_rooms = service_rooms(db, user.id, service)
    if service is not None and getattr(service, "room_ids", None) and not need_rooms:
        return []                      # нужные кабинеты выключены или удалены — записать некуда
    rbusy = _room_busy(db, [r.id for r in need_rooms], window_start, window_end) if need_rooms else {}
    out = []
    for i in range(days):
        d = start_day + timedelta(days=i)
        if d > horizon:
            break
        slots: Dict[datetime, List[str]] = {}
        for r in res:
            taken = busy.get(str(r.id), [])
            dur = timedelta(minutes=service_terms(r, service)[0])
            for a, b in _work_intervals(r, d):
                t = a                                 # сетка — от начала смены с шагом step
                while t + dur <= b:                   # клиент уходит до конца смены; уборка может после
                    ba, bb = _block(t, t + dur, prep, after, cfg["buffer_min"])
                    if (t >= earliest and ba >= a       # подготовка — в рабочее время мастера
                            and not any(ba < tb and bb > ta for ta, tb in taken)
                            and (not need_rooms or _free_room(need_rooms, rbusy, t, t + dur, prep, after))):
                        slots.setdefault(t, []).append(str(r.id))
                    t += step
        items = [{"time": to_local(t).strftime("%H:%M"), "start": t.isoformat(), "resource_ids": ids}
                 for t, ids in sorted(slots.items())]
        if limit_per_day:
            items = items[:limit_per_day]
        out.append({"date": d.isoformat(), "weekday": DAY_TITLES[d.weekday()], "slots": items})
    return out


def nearest_slots(db: Session, user, service=None, resource_id=None, day=None, count: int = 3,
                  max_days: int = 14, online_only: bool = True) -> List[Dict[str, Any]]:
    """2–3 ближайших свободных времени (для бота и голоса):
    [{start, label, resource_id, resource, price, duration_min}] — цена и длительность у этого мастера."""
    rows = {str(r.id): r for r in resources(db, user.id)}
    out = []
    for d in free_slots(db, user, service, resource_id, day, days=max_days, online_only=online_only):
        for s in d["slots"]:
            rid = s["resource_ids"][0]
            item = {"start": s["start"], "label": fmt_local(parse_start(s["start"])),
                    "resource_id": rid, "resource": rows[rid].name if rid in rows else None}
            if service is not None:
                dur, price = service_terms(rows.get(rid), service)
                item["duration_min"], item["price"] = dur, price
            out.append(item)
            if len(out) >= count:
                return out
    return out


# ───────────────────────────── клиент записи ─────────────────────────────

def _first_agent(db: Session, user_id):
    from backend.models.agent_config import AgentConfig
    return db.query(AgentConfig).filter(AgentConfig.user_id == user_id).order_by(AgentConfig.created_at).first()


def resolve_contact(db: Session, user_id, contact_id=None, name: Optional[str] = None, phone: Optional[str] = None,
                    create: bool = True):
    """Карточка CRM для записи: по id, по телефону, иначе новая (если есть агент)."""
    from backend.models.agent_contact import AgentContact
    if contact_id:
        try:
            cid = uuid.UUID(str(contact_id))
            c = db.query(AgentContact).filter(AgentContact.id == cid, AgentContact.user_id == user_id).first()
            if c:
                return c
        except ValueError:
            pass
    agent = _first_agent(db, user_id)
    if agent is None:
        return None
    if phone:
        try:
            from backend.services.agent_contact_phones import find_contact_by_phone
            c = find_contact_by_phone(db, agent.id, phone)
            if c is not None:
                return c
        except Exception as e:
            logger.warning(f"[BOOKING] contact by phone failed: {e}")
    if not create or not (phone or name):
        return None
    c = AgentContact(user_id=user_id, agent_config_id=agent.id, name=(name or "Клиент")[:255],
                     phone=phone or f"web:{uuid.uuid4().hex[:10]}", status="new")
    db.add(c)
    db.flush()
    return c


def _mark_booked_stage(db: Session, contact) -> None:
    """Новый клиент записался → стадия «Записан» (системный ключ active), если она есть в воронке."""
    if contact is None or (contact.status or "new") != "new":
        return
    try:
        from backend.core.pipeline_stages import get_agent_stage_keys
        from backend.models.agent_config import AgentConfig
        agent = db.query(AgentConfig).filter(AgentConfig.id == contact.agent_config_id).first()
        if agent is not None and "active" in get_agent_stage_keys(agent):
            contact.status = "active"
    except Exception as e:
        logger.warning(f"[BOOKING] stage not moved: {e}")


# ───────────────────────────── запись ─────────────────────────────

def _check_free(db: Session, user, resource: BookingResource, start: datetime, end: datetime, cfg: Dict[str, Any],
                exclude_id=None, within_hours: bool = True, prep: int = 0, after: int = 0,
                extra: Optional[List[Tuple[datetime, datetime]]] = None) -> None:
    if within_hours:
        day = to_local(start).date()
        ok = any(a <= start - timedelta(minutes=prep) and end <= b for a, b in _work_intervals(resource, day))
        if not ok:
            raise BookingError(f"У «{resource.name}» это время нерабочее")
    taken = _busy(db, [resource.id], start - timedelta(days=1), end + timedelta(days=1), cfg["buffer_min"],
                  exclude_id=exclude_id, user_id=user.id).get(str(resource.id), [])
    taken = taken + list(extra or [])
    ba, bb = _block(start, end, prep, after, cfg["buffer_min"])
    if any(ba < tb and bb > ta for ta, tb in taken):
        if _blocked(db, user.id, resource.id, ba, bb):
            raise BookingError(f"У «{resource.name}» в это время перерыв, отпуск или блок времени")
        raise BookingError("Это время уже занято")


def _blocked(db: Session, user_id, resource_id, a: datetime, b: datetime) -> bool:
    return db.query(BookingBlock.id).filter(
        BookingBlock.start_at < b, BookingBlock.end_at > a,
        or_(BookingBlock.resource_id == resource_id,
            and_(BookingBlock.resource_id.is_(None), BookingBlock.user_id == user_id))).first() is not None


def _pick_room(db: Session, user, service, start: datetime, end: datetime, prep: int, after: int,
               exclude_id=None, prefer=None, lock: bool = True, extra: Optional[Dict[str, list]] = None):
    """Свободный кабинет для услуги (None — кабинет услуге не нужен). Строки кабинетов блокируются,
    как строка мастера: две записи в один кабинет в одну секунду не пройдут."""
    need = service_rooms(db, user.id, service)
    if service is not None and getattr(service, "room_ids", None) and not need:
        raise BookingError("Для этой услуги не осталось включённых кабинетов — проверьте настройки услуги")
    if not need:
        return None
    if lock:
        ids = sorted(str(r.id) for r in need)
        db.query(BookingRoom.id).filter(BookingRoom.id.in_([uuid.UUID(x) for x in ids])).order_by(BookingRoom.id).with_for_update().all()
    busy = _room_busy(db, [r.id for r in need], start - timedelta(days=1), end + timedelta(days=1), exclude_id=exclude_id)
    for rid, items in (extra or {}).items():
        busy.setdefault(rid, []).extend(items)
    room = _free_room(need, busy, start, end, prep, after, prefer=prefer)
    if room is None:
        names = ", ".join(r.name for r in need[:3])
        raise BookingError(f"В это время заняты все кабинеты для услуги ({names}) — выберите другое время")
    return room


def _lock_resource(db: Session, resource_id) -> Optional[BookingResource]:
    return (db.query(BookingResource).filter(BookingResource.id == resource_id).with_for_update().first())


def create_booking(db: Session, user, *, start, service_id=None, service_query: Optional[str] = None,
                   resource_id=None, client_name: Optional[str] = None, client_phone: Optional[str] = None,
                   contact_id=None, source: str = "owner", channel: Optional[str] = None, note: Optional[str] = None,
                   price: Optional[float] = None, duration_min: Optional[int] = None, room_id=None,
                   visit_id=None) -> Booking:
    """Записать клиента. Для бота и ссылки — строго в рабочее и будущее время; владелец может вне графика."""
    from backend.utils.phone import normalize_phone_e164
    st = parse_start(start)
    page = get_page(db, user)
    cfg = settings_of(page)
    strict = source in ("bot", "online", "call")
    if strict and st < now_utc() + timedelta(minutes=min(cfg["lead_min"], 30)):
        raise BookingError("Это время уже прошло или слишком близко — предложите другое")
    service = find_service(db, user.id, service_id, service_query)
    if (service_id or service_query) and service is None and strict:
        raise BookingError("Не нашёл такую услугу в каталоге — уточните название")
    if strict and not service_online(service):
        raise BookingError("На эту услугу записывает администратор — предложите передать заявку владельцу")
    prep, after = service_pads(service)
    phone = normalize_phone_e164(client_phone) if client_phone else None
    if client_phone and not phone:
        raise BookingError("Проверьте номер телефона клиента")

    candidates = [r for r in ensure_resources(db, user) if _resource_does(r, service)]
    if resource_id:
        candidates = [r for r in candidates if str(r.id) == str(resource_id)]
        if not candidates:
            raise BookingError("Этот мастер не делает такую услугу или выключен")
    if strict:
        online = [r for r in candidates if bookable_online(r)]
        if candidates and not online:
            raise BookingError("К этому мастеру записывает администратор — предложите другого мастера "
                               "или передайте владельцу")
        candidates = online
    chosen = None
    last_err = None
    end = st
    for r in candidates:
        locked = _lock_resource(db, r.id)
        r_dur = int(duration_min or service_terms(locked, service)[0])
        r_end = st + timedelta(minutes=r_dur)
        try:
            _check_free(db, user, locked, st, r_end, cfg, within_hours=strict, prep=prep, after=after)
            chosen, end = locked, r_end
            break
        except BookingError as e:
            last_err = e
    if chosen is None:
        raise last_err or BookingError("Нет свободного мастера на это время")
    master_price = service_terms(chosen, service)[1] if service is not None else None
    room = _pick_room(db, user, service, st, end, prep, after, prefer=room_id)

    contact = resolve_contact(db, user.id, contact_id, client_name, phone)
    b = Booking(user_id=user.id, resource_id=chosen.id, service_entry_id=service.id if service else None,
                service_title=(service.title if service else None), contact_id=contact.id if contact else None,
                client_name=(client_name or (contact.name if contact else None) or "")[:200] or None,
                client_phone=phone or (contact.phone if contact and not str(contact.phone or "").startswith(("web:", "tg:", "ig:", "wa:")) else None),
                start_at=st, end_at=end, status="booked", prep_min=prep or None, after_min=after or None,
                room_id=room.id if room is not None else None, visit_id=visit_id,
                price=price if price is not None else master_price,
                source=source, channel=channel, note=(note or None) and str(note)[:2000])
    b.prepay_amount = prepay_amount(service, b.price)
    db.add(b)
    _mark_booked_stage(db, contact)
    _flush(db)
    notify_changed(db, user.id)
    return b


def get_booking(db: Session, user_id, booking_id) -> Booking:
    try:
        bid = uuid.UUID(str(booking_id))
    except ValueError:
        raise BookingError("Запись не найдена")
    b = db.query(Booking).filter(Booking.id == bid, Booking.user_id == user_id).first()
    if b is None:
        raise BookingError("Запись не найдена")
    return b


def update_booking(db: Session, user, booking_id, *, start=None, resource_id=None, service_id=None,
                   status: Optional[str] = None, note: Optional[str] = None, price=None,
                   cancel_reason: Optional[str] = None, source: str = "owner", prepaid: Optional[bool] = None,
                   duration_min: Optional[int] = None) -> Booking:
    """Перенос (время / мастер / услуга), растяжка (длительность), статус, заметка, цена, предоплата."""
    b = get_booking(db, user.id, booking_id)
    if prepaid is not None:
        b.prepaid_at = now_utc() if prepaid else None
    cfg = settings_of(get_page(db, user))
    if duration_min is not None:
        try:
            duration_min = int(duration_min)
        except (TypeError, ValueError):
            raise BookingError("Неверная длительность")
        if not (5 <= duration_min <= 1440):
            raise BookingError("Длительность — от 5 минут до суток")
    if start is not None or resource_id is not None or service_id is not None or duration_min is not None:
        new_start = parse_start(start) if start is not None else (b.start_at if b.start_at.tzinfo else b.start_at.replace(tzinfo=timezone.utc))
        service = find_service(db, user.id, service_id) if service_id else None
        rid = resource_id or b.resource_id
        locked = _lock_resource(db, rid)
        if locked is None or locked.user_id != user.id:
            raise BookingError("Мастер не найден")
        s_dur, s_price = service_terms(locked, service) if service else (None, None)
        dur = s_dur if service else int(((b.end_at - b.start_at).total_seconds()) // 60)
        if duration_min is not None:
            dur = duration_min
        new_end = new_start + timedelta(minutes=dur)
        prep, after = service_pads(service) if service else (int(b.prep_min or 0), int(b.after_min or 0))
        strict = source in ("bot", "online", "call")
        if strict and new_start < now_utc():
            raise BookingError("Это время уже прошло — предложите другое")
        _check_free(db, user, locked, new_start, new_end, cfg, exclude_id=b.id, within_hours=strict,
                    prep=prep, after=after)
        svc_now = service or (find_service(db, user.id, b.service_entry_id) if b.service_entry_id else None)
        room = _pick_room(db, user, svc_now, new_start, new_end, prep, after, exclude_id=b.id, prefer=b.room_id)
        b.room_id = room.id if room is not None else None
        b.start_at, b.end_at, b.resource_id = new_start, new_end, locked.id
        if service is not None:
            b.service_entry_id, b.service_title = service.id, service.title
            b.prep_min, b.after_min = prep or None, after or None
            if price is None and s_price is not None:
                b.price = s_price
            if b.prepaid_at is None:
                b.prepay_amount = prepay_amount(service, b.price)
        b.reminder_24h_at = b.reminder_2h_at = None     # новое время — напоминания заново
        if b.status in ("cancelled", "no_show"):
            b.status = "booked"
    if status is not None:
        if status not in BOOKING_STATUSES:
            raise BookingError("Неизвестный статус записи")
        b.status = status
        if status == "cancelled":
            b.cancel_reason = (cancel_reason or "")[:300] or None
    if note is not None:
        b.note = str(note)[:2000] or None
    if price is not None:
        try:
            b.price = float(price) if price != "" else None
        except (TypeError, ValueError):
            raise BookingError("Неверная цена")
    _flush(db)
    notify_changed(db, user.id)
    return b


def cancel_booking(db: Session, user, booking_id, reason: Optional[str] = None) -> Booking:
    return update_booking(db, user, booking_id, status="cancelled", cancel_reason=reason)


def list_bookings(db: Session, user_id, start: datetime, end: datetime, resource_id=None, contact_id=None,
                  include_cancelled: bool = True) -> List[Booking]:
    q = db.query(Booking).filter(Booking.user_id == user_id, Booking.start_at < end, Booking.end_at > start)
    if resource_id:
        q = q.filter(Booking.resource_id == resource_id)
    if contact_id:
        q = q.filter(Booking.contact_id == contact_id)
    if not include_cancelled:
        q = q.filter(Booking.status != "cancelled")
    return q.order_by(Booking.start_at).all()


def upcoming_for_contact(db: Session, user_id, contact_id, limit: int = 5) -> List[Booking]:
    return (db.query(Booking).filter(Booking.user_id == user_id, Booking.contact_id == contact_id,
                                     Booking.status.in_(("booked", "confirmed")), Booking.start_at >= now_utc())
            .order_by(Booking.start_at).limit(limit).all())


def booking_to_dict(b: Booking, names: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    st = b.start_at if b.start_at.tzinfo else b.start_at.replace(tzinfo=timezone.utc)
    en = b.end_at if b.end_at.tzinfo else b.end_at.replace(tzinfo=timezone.utc)
    return {
        "id": str(b.id), "resource_id": str(b.resource_id) if b.resource_id else None,
        "resource": (names or {}).get(str(b.resource_id)) if b.resource_id else None,
        "service_id": str(b.service_entry_id) if b.service_entry_id else None, "service": b.service_title,
        "contact_id": str(b.contact_id) if b.contact_id else None, "client_name": b.client_name,
        "client_phone": b.client_phone, "start": st.isoformat(), "end": en.isoformat(),
        "label": fmt_local(st), "status": b.status, "status_title": STATUS_TITLES.get(b.status, b.status),
        "price": b.price, "source": b.source, "channel": b.channel, "note": b.note,
        "duration_min": int((en - st).total_seconds() // 60),
        "prep_min": int(b.prep_min or 0), "after_min": int(b.after_min or 0),
        "prepay_amount": b.prepay_amount, "prepaid": b.prepaid_at is not None,
        "room_id": str(b.room_id) if b.room_id else None, "visit_id": str(b.visit_id) if b.visit_id else None,
    }


def day_bounds(day: date) -> Tuple[datetime, datetime]:
    a = datetime.combine(day, time(0), BISHKEK).astimezone(timezone.utc)
    return a, a + timedelta(days=1)


def stats(db: Session, user_id, start: datetime, end: datetime) -> Dict[str, Any]:
    """Записи за период: по статусам, выручка (пришли), неявки, новые/повторные клиенты."""
    row = db.execute(text("""
        SELECT count(*) FILTER (WHERE status <> 'cancelled') AS total,
               count(*) FILTER (WHERE status IN ('arrived', 'completed')) AS arrived,
               count(*) FILTER (WHERE status = 'no_show') AS no_show,
               count(*) FILTER (WHERE status = 'cancelled') AS cancelled,
               coalesce(sum(price) FILTER (WHERE status IN ('arrived', 'completed')), 0) AS revenue,
               count(*) FILTER (WHERE source = 'bot' OR source = 'call') AS by_bot,
               count(*) FILTER (WHERE source = 'online') AS online
        FROM bookings WHERE user_id = :u AND start_at >= :a AND start_at < :b
    """), {"u": user_id, "a": start, "b": end}).mappings().first() or {}
    return {k: (float(v) if k == "revenue" else int(v or 0)) for k, v in dict(row).items()}


# ───────────────────────────── блоки времени (шаг 1.4) ─────────────────────────────

def block_to_dict(k: BookingBlock) -> Dict[str, Any]:
    st, en = _aw(k.start_at), _aw(k.end_at)
    return {"id": str(k.id), "resource_id": str(k.resource_id) if k.resource_id else None, "kind": k.kind,
            "kind_title": BLOCK_TITLES.get(k.kind, "Блок"), "start": st.isoformat(), "end": en.isoformat(),
            "note": k.note or "", "label": f"{fmt_local(st)}–{to_local(en).strftime('%H:%M')}"}


def list_blocks(db: Session, user_id, start: datetime, end: datetime) -> List[BookingBlock]:
    return (db.query(BookingBlock).filter(BookingBlock.user_id == user_id, BookingBlock.start_at < end,
                                          BookingBlock.end_at > start).order_by(BookingBlock.start_at).all())


def create_block(db: Session, user, *, start, end, resource_id=None, kind: str = "block",
                 note: Optional[str] = None) -> Tuple[BookingBlock, int]:
    """Блок времени: у мастера или у всех (resource_id=None). Возвращает (блок, сколько активных записей
    уже стоит в этом времени — их не трогаем, администратор перенесёт сам)."""
    if kind not in BLOCK_KINDS:
        raise BookingError("Неизвестный вид блока")
    a, b = parse_start(start), parse_start(end)
    if b <= a:
        raise BookingError("Конец блока должен быть позже начала")
    if b - a > timedelta(days=62):
        raise BookingError("Блок — не длиннее двух месяцев")
    rid = None
    if resource_id:
        r = db.query(BookingResource).filter(BookingResource.user_id == user.id,
                                             BookingResource.id == uuid.UUID(str(resource_id))).first()
        if r is None:
            raise BookingError("Мастер не найден")
        rid = r.id
    k = BookingBlock(user_id=user.id, resource_id=rid, kind=kind, start_at=a, end_at=b,
                     note=(str(note or "").strip()[:300] or None))
    db.add(k)
    q = db.query(Booking.id).filter(Booking.user_id == user.id, Booking.status.in_(("booked", "confirmed")),
                                    Booking.start_at < b, Booking.end_at > a)
    if rid is not None:
        q = q.filter(Booking.resource_id == rid)
    clash = q.count()
    db.flush()
    notify_changed(db, user.id)
    return k, clash


def delete_block(db: Session, user, block_id) -> None:
    try:
        bid = uuid.UUID(str(block_id))
    except ValueError:
        raise BookingError("Блок не найден")
    k = db.query(BookingBlock).filter(BookingBlock.id == bid, BookingBlock.user_id == user.id).first()
    if k is None:
        raise BookingError("Блок не найден")
    db.delete(k)
    db.flush()
    notify_changed(db, user.id)


# ───────────────────────────── визит: несколько услуг подряд (шаг 1.4) ─────────────────────────────

MAX_VISIT_ITEMS = 6


def plan_visit(db: Session, user, items: List[Dict[str, Any]], start, *, source: str = "owner",
               lock: bool = False) -> List[Dict[str, Any]]:
    """
    Разложить визит по времени: услуги идут одна за другой (клиент переходит от мастера к мастеру сразу;
    у того же мастера — после его перерыва и уборки). items: [{service_id | service, resource_id?}].
    Возвращает [{resource, service, start, end, prep, after, room, price}] или BookingError.
    Ничего не пишет; lock=True — блокирует строки выбранных мастеров (для записи).
    """
    if not items:
        raise BookingError("Добавьте хотя бы одну услугу")
    if len(items) > MAX_VISIT_ITEMS:
        raise BookingError(f"В одном визите — не больше {MAX_VISIT_ITEMS} услуг")
    cfg = settings_of(get_page(db, user))
    strict = source in ("bot", "online", "call")
    t = parse_start(start)
    if strict and t < now_utc() + timedelta(minutes=min(cfg["lead_min"], 30)):
        raise BookingError("Это время уже прошло или слишком близко — выберите другое")
    masters = ensure_resources(db, user)
    plan: List[Dict[str, Any]] = []
    extra: Dict[str, List[Tuple[datetime, datetime]]] = {}
    extra_rooms: Dict[str, List[Tuple[datetime, datetime]]] = {}
    for it in items:
        service = it.get("service") or (find_service(db, user.id, it.get("service_id")) if it.get("service_id") else None)
        if it.get("service_id") and service is None:
            raise BookingError("Услуга не найдена")
        if strict and not service_online(service):
            raise BookingError(f"На «{service.title}» записывает администратор")
        prep, after = service_pads(service)
        cands = [r for r in masters if _resource_does(r, service)]
        if strict:
            cands = [r for r in cands if bookable_online(r)]
        if it.get("resource_id"):
            cands = [r for r in cands if str(r.id) == str(it["resource_id"])]
        title = service.title if service is not None else "услуга"
        if not cands:
            raise BookingError(f"«{title}» не делает выбранный мастер" if it.get("resource_id") else f"«{title}» никто не делает")
        placed, last = None, None
        for r in cands:
            st = t
            if plan and str(plan[-1]["resource"].id) == str(r.id):     # тот же мастер — после его уборки и перерыва
                st = t + timedelta(minutes=plan[-1]["after"] + cfg["buffer_min"])
            dur, price = service_terms(r, service)
            en = st + timedelta(minutes=dur)
            try:
                if lock:
                    _lock_resource(db, r.id)
                _check_free(db, user, r, st, en, cfg, within_hours=strict, prep=prep, after=after,
                            extra=extra.get(str(r.id)))
                room = _pick_room(db, user, service, st, en, prep, after, lock=lock, extra=extra_rooms)
                placed = {"resource": r, "service": service, "start": st, "end": en, "prep": prep, "after": after,
                          "room": room, "price": price, "duration_min": dur}
                break
            except BookingError as e:
                last = e
        if placed is None:
            raise last or BookingError("Нет свободного мастера на это время")
        plan.append(placed)
        extra.setdefault(str(placed["resource"].id), []).append(
            _block(placed["start"], placed["end"], prep, after, cfg["buffer_min"]))
        if placed["room"] is not None:
            extra_rooms.setdefault(str(placed["room"].id), []).append(
                (placed["start"] - timedelta(minutes=prep), placed["end"] + timedelta(minutes=after)))
        t = placed["end"]
    return plan


def visit_slots(db: Session, user, items: List[Dict[str, Any]], day=None, *, source: str = "owner",
                limit: int = 60) -> List[Dict[str, Any]]:
    """Начала визита в этот день, при которых вся цепочка услуг помещается."""
    if not items:
        return []
    first = items[0]
    service = find_service(db, user.id, first.get("service_id")) if first.get("service_id") else None
    days = free_slots(db, user, service, first.get("resource_id"), day, 1, ignore_lead=(source not in ("bot", "online", "call")),
                      online_only=source in ("bot", "online", "call"))
    out = []
    for s in (days[0]["slots"] if days else [])[:limit * 2]:
        try:
            plan = plan_visit(db, user, items, s["start"], source=source)
        except BookingError:
            continue
        out.append({"time": s["time"], "start": s["start"], "end": plan[-1]["end"].isoformat(),
                    "end_time": to_local(plan[-1]["end"]).strftime("%H:%M"),
                    "masters": [str(p["resource"].id) for p in plan]})
        if len(out) >= limit:
            break
    return out


def create_visit(db: Session, user, items: List[Dict[str, Any]], start, *, client_name: Optional[str] = None,
                 client_phone: Optional[str] = None, contact_id=None, source: str = "owner",
                 channel: Optional[str] = None, note: Optional[str] = None) -> List[Booking]:
    """Записать визит целиком: одна транзакция, общий visit_id; не поместилась одна услуга — не пишется ничего."""
    plan = plan_visit(db, user, items, start, source=source)            # куда ставим
    pinned = [dict(it, resource_id=str(p["resource"].id)) for it, p in zip(items, plan)]
    for rid in sorted({str(p["resource"].id) for p in plan}):           # один порядок блокировок — без взаимных ожиданий
        _lock_resource(db, uuid.UUID(rid))
    plan = plan_visit(db, user, pinned, start, source=source, lock=True)  # под блокировкой — ещё раз
    vid = uuid.uuid4() if len(plan) > 1 else None
    out: List[Booking] = []
    contact = None
    for i, p in enumerate(plan):
        b = create_booking(db, user, start=p["start"], service_id=str(p["service"].id) if p["service"] else None,
                           resource_id=str(p["resource"].id), client_name=client_name, client_phone=client_phone,
                           contact_id=contact_id or (str(contact) if contact else None), source=source, channel=channel,
                           note=note if i == 0 else None, room_id=str(p["room"].id) if p["room"] else None, visit_id=vid)
        contact = b.contact_id
        out.append(b)
    return out


def visit_bookings(db: Session, user_id, visit_id) -> List[Booking]:
    if not visit_id:
        return []
    return (db.query(Booking).filter(Booking.user_id == user_id, Booking.visit_id == uuid.UUID(str(visit_id)))
            .order_by(Booking.start_at).all())


def cancel_visit(db: Session, user, visit_id, reason: Optional[str] = None) -> List[Booking]:
    rows = [b for b in visit_bookings(db, user.id, visit_id) if b.status not in ("cancelled",)]
    if not rows:
        raise BookingError("Визит не найден")
    for b in rows:
        b.status, b.cancel_reason = "cancelled", (reason or "")[:300] or None
    db.flush()
    notify_changed(db, user.id)
    return rows
