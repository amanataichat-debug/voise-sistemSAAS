"""
Клиенты салона (Voksy Beauty, шаг 2.1): карточка, сегменты, выгрузка.

Решение владельца 26.11.2026: в карточке обязательны ТОЛЬКО имя и телефон; ДР, пол и источник — по желанию.
Всё про визиты СЧИТАЕТСЯ из журнала записи (bookings), а не хранится в карточке, — поэтому не расходится с
журналом: визиты (визит из нескольких услуг = один визит), сумма, средний чек, неявки, отмены, первый и
последний визит, ближайшая запись, любимые мастер и услуга (чаще всего у визитов), обычный интервал.

Сегменты (одни и те же условия для счётчиков, списка и выгрузки — SEGMENT_SQL):
  regular  Постоянные      — 3+ визита, последний не дольше 90 дней назад
  new      Новые           — один визит, не дольше 60 дней назад
  vip      VIP             — верхние 10% по сумме визитов, от 2 визитов
  sleeping Давно не были   — последний визит 45–120 дней назад, вперёд не записаны
  lost     Ушли            — последний визит больше 120 дней назад, вперёд не записаны
  no_show  С неявками      — хотя бы одна неявка
  birthday ДР скоро        — день рождения в ближайшие 14 дней
  upcoming Записаны        — есть будущая запись
  never    Не записывались — карточка есть (писали, звонили), записей нет

Источник (agent_contacts.source): ставит вход, который завёл карточку (WhatsApp, Telegram, Instagram, чат на сайте,
звонок, запись по ссылке, импорт, вручную); старые карточки без источника досчитывает фоновый проход
backfill_sources по первому касанию (как аналитика «откуда пришли»). Владелец может выбрать и то, чего система
сама не знает: рекомендация, 2ГИС, Google Карты, реклама, зашёл с улицы, другое.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.models.agent_contact import BIRTHDAY_NO_YEAR, AgentContact
from backend.models.booking import VISITED_SQL
from backend.services import booking_service as bs

logger = logging.getLogger(__name__)

# (ключ, название, ставится системой сама)
SOURCES: List[Tuple[str, str, bool]] = [
    ("whatsapp", "WhatsApp", True), ("instagram", "Instagram", True), ("telegram", "Telegram", True),
    ("webchat", "Чат на сайте", True), ("booking", "Запись по ссылке", True), ("call", "Звонок", True),
    ("recommendation", "Рекомендация", False), ("2gis", "2ГИС", False), ("google", "Google Карты", False),
    ("ads", "Реклама", False), ("walk_in", "Зашёл с улицы", False), ("import", "Импорт", True),
    ("manual", "Добавлен вручную", True), ("other", "Другое", False),
]
SOURCE_TITLES = {k: t for k, t, _ in SOURCES}
GENDERS = {"f": "Женщина", "m": "Мужчина"}

SLEEP_DAYS = 45
LOST_DAYS = 120
REGULAR_VISITS = 3
REGULAR_DAYS = 90
NEW_DAYS = 60
BIRTHDAY_AHEAD = 14
EXPORT_MAX = 20000

SEGMENTS: List[Tuple[str, str, str]] = [
    ("all", "Все", "Все клиенты"),
    ("regular", "Постоянные", f"{REGULAR_VISITS}+ визита, последний — не дольше {REGULAR_DAYS} дней назад"),
    ("new", "Новые", f"Один визит, не дольше {NEW_DAYS} дней назад"),
    ("vip", "VIP", "Тратят больше всех: верхние 10% по сумме визитов"),
    ("sleeping", "Давно не были", f"Последний визит {SLEEP_DAYS}–{LOST_DAYS} дней назад, вперёд не записаны"),
    ("lost", "Ушли", f"Не были больше {LOST_DAYS} дней и не записаны"),
    ("no_show", "С неявками", "Хотя бы раз не пришли на запись"),
    ("birthday", "ДР скоро", f"День рождения в ближайшие {BIRTHDAY_AHEAD} дней"),
    ("upcoming", "Записаны", "Есть будущая запись"),
    ("never", "Не записывались", "Писали или звонили, но ни разу не записывались"),
]
SEGMENT_KEYS = {k for k, _, _ in SEGMENTS}

# Условия сегментов над строкой base (см. _base_sql) и p (порог VIP). :now — сейчас (UTC), :bd — дни рождения.
SEGMENT_SQL: Dict[str, str] = {
    "all": "TRUE",
    "regular": f"base.visits >= {REGULAR_VISITS} AND base.last_visit >= :now - interval '{REGULAR_DAYS} days'",
    "new": f"base.visits = 1 AND base.last_visit >= :now - interval '{NEW_DAYS} days'",
    "vip": "base.visits >= 2 AND base.spent > 0 AND p.p90 IS NOT NULL AND base.spent >= p.p90",
    "sleeping": (f"base.visits > 0 AND base.next_at IS NULL AND base.last_visit < :now - interval '{SLEEP_DAYS} days' "
                 f"AND base.last_visit >= :now - interval '{LOST_DAYS} days'"),
    "lost": f"base.visits > 0 AND base.next_at IS NULL AND base.last_visit < :now - interval '{LOST_DAYS} days'",
    "no_show": "base.no_shows > 0",
    "birthday": "to_char(base.birthday, 'MM-DD') = ANY(:bd)",
    "upcoming": "base.next_at IS NOT NULL",
    "never": "COALESCE(base.bookings, 0) = 0",
}
SORTS = {
    "last_visit": "base.last_visit DESC NULLS LAST, base.created_at DESC",
    "spent": "base.spent DESC NULLS LAST, base.last_visit DESC NULLS LAST",
    "visits": "base.visits DESC NULLS LAST, base.last_visit DESC NULLS LAST",
    "next": "base.next_at ASC NULLS LAST, base.last_visit DESC NULLS LAST",
    "name": "lower(COALESCE(base.name, '')) ASC, base.created_at DESC",
    "created": "base.created_at DESC",
    "birthday": ("(base.birthday IS NULL), (to_char(base.birthday, 'MMDD') < :tmmdd), "
                 "to_char(base.birthday, 'MMDD'), base.created_at DESC"),
}

_STATS_SQL = f"""
st AS MATERIALIZED (
  SELECT b.contact_id AS cid,
    count(DISTINCT COALESCE(b.visit_id, b.id)) FILTER (WHERE b.status IN {VISITED_SQL}) AS visits,
    COALESCE(sum(b.price) FILTER (WHERE b.status IN {VISITED_SQL}), 0) AS spent,
    count(DISTINCT COALESCE(b.visit_id, b.id)) FILTER (WHERE b.status = 'no_show') AS no_shows,
    count(DISTINCT COALESCE(b.visit_id, b.id)) FILTER (WHERE b.status = 'cancelled') AS cancelled,
    max(b.start_at) FILTER (WHERE b.status IN {VISITED_SQL}) AS last_visit,
    min(b.start_at) FILTER (WHERE b.status IN {VISITED_SQL}) AS first_visit,
    min(b.start_at) FILTER (WHERE b.status IN ('booked', 'confirmed') AND b.start_at > :now) AS next_at,
    mode() WITHIN GROUP (ORDER BY b.resource_id)
      FILTER (WHERE b.status IN {VISITED_SQL} AND b.resource_id IS NOT NULL) AS fav_master,
    mode() WITHIN GROUP (ORDER BY b.service_title)
      FILTER (WHERE b.status IN {VISITED_SQL} AND b.service_title IS NOT NULL) AS fav_service,
    count(*) AS bookings
  FROM bookings b
  WHERE b.user_id = :uid AND b.contact_id IS NOT NULL {{stats_where}}
  GROUP BY b.contact_id
)"""

_BASE_SQL = """
base AS MATERIALIZED (
  SELECT c.id, c.name, c.phone, c.birthday, c.gender, c.source, c.tags, c.status, c.created_at,
         st.visits, st.spent, st.no_shows, st.cancelled, st.last_visit, st.first_visit, st.next_at,
         st.fav_master, st.fav_service, st.bookings
  FROM agent_contacts c LEFT JOIN st ON st.cid = c.id
  WHERE c.user_id = :uid {contact_where}
),
p AS MATERIALIZED (SELECT percentile_cont(0.9) WITHIN GROUP (ORDER BY spent) AS p90 FROM base WHERE visits > 0)"""

_HIDDEN_PHONE = ("web:", "tg:", "ig:", "wa:")


# ───────────────────────────── мелочи ─────────────────────────────

def real_phone(phone: Optional[str]) -> Optional[str]:
    p = (phone or "").strip()
    return None if not p or p.startswith(_HIDDEN_PHONE) else p


def birthday_days(today: date, n: int = BIRTHDAY_AHEAD) -> List[str]:
    """«ММ-ДД» на ближайшие n дней (сегодня включительно). 29.02 — рядом с 28.02 в невисокосный год."""
    out = []
    for i in range(n):
        d = today + timedelta(days=i)
        out.append(d.strftime("%m-%d"))
        if d.month == 2 and d.day == 28 and not _leap(d.year):
            out.append("02-29")
    return out


def _leap(y: int) -> bool:
    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)


def parse_birthday(raw: Any) -> Optional[date]:
    """ДР из «1990-03-12», «12.03.1990», «12.03», «--03-12», «12/03/90». Пусто → None. Неверно → ValueError."""
    if raw is None:
        return None
    if isinstance(raw, date):
        return raw
    s = str(raw).strip()
    if not s:
        return None
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.fullmatch(r"-{0,2}(\d{1,2})-(\d{1,2})", s)
        if m and s.startswith("--"):
            y, mo, d = BIRTHDAY_NO_YEAR, int(m.group(1)), int(m.group(2))
        else:
            m = re.fullmatch(r"(\d{1,2})[./ -](\d{1,2})(?:[./ -](\d{2,4}))?", s)
            if not m:
                raise ValueError("Дата рождения: например 12.03.1990 или 12.03")
            d, mo = int(m.group(1)), int(m.group(2))
            if m.group(3):
                y = int(m.group(3))
                if y < 100:
                    y += 1900 if y > (date.today().year % 100) else 2000
            else:
                y = BIRTHDAY_NO_YEAR
    try:
        out = date(y, mo, d)
    except ValueError:
        raise ValueError("Такой даты нет — проверьте день и месяц")
    if out.year != BIRTHDAY_NO_YEAR and not (1900 <= out.year <= date.today().year):
        raise ValueError("Проверьте год рождения")
    return out


_MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
           "ноября", "декабря"]


def birthday_text(d: Optional[date]) -> Optional[str]:
    if d is None:
        return None
    s = f"{d.day} {_MONTHS[d.month - 1]}"
    return s if d.year == BIRTHDAY_NO_YEAR else f"{s} {d.year}"


def birthday_info(d: Optional[date], today: date) -> Optional[Dict[str, Any]]:
    """Текст, через сколько дней ближайший ДР и сколько исполнится (если год известен)."""
    if d is None:
        return None
    nxt = _next_birthday(d, today)
    out: Dict[str, Any] = {"text": birthday_text(d), "in_days": (nxt - today).days,
                           "iso": f"--{d.month:02d}-{d.day:02d}" if d.year == BIRTHDAY_NO_YEAR else d.isoformat()}
    if d.year != BIRTHDAY_NO_YEAR:
        out["age"] = today.year - d.year - ((today.month, today.day) < (d.month, d.day))
        out["turns"] = nxt.year - d.year
    return out


def _next_birthday(d: date, today: date) -> date:
    for y in (today.year, today.year + 1, today.year + 2):
        try:
            cand = date(y, d.month, d.day)
        except ValueError:          # 29.02 в невисокосный год — 1 марта
            cand = date(y, 3, 1)
        if cand >= today:
            return cand
    return today


def _years_word(n: int) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return "год"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "года"
    return "лет"


def _days_ago(t: Optional[datetime], now: datetime) -> Optional[int]:
    return max(0, (now - bs._aw(t)).days) if t else None


def _num(v) -> float:
    return float(v or 0)


def _masters(db: Session, user_id) -> Dict[str, str]:
    return {str(r.id): r.name for r in bs.resources(db, user_id, active_only=False)}


# ───────────────────────────── выборка ─────────────────────────────

def _params(user, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or bs.now_utc()
    today = bs.to_local(now).date()
    return {"uid": str(user.id), "now": now, "bd": birthday_days(today), "tmmdd": today.strftime("%m%d")}


def _filters(params: Dict[str, Any], q: Optional[str], master: Optional[str], service: Optional[str],
             source: Optional[str], gender: Optional[str], tag: Optional[str]) -> List[str]:
    where: List[str] = []
    if q and q.strip():
        s = q.strip()
        digits = re.sub(r"\D", "", s)
        params["q"] = f"%{s}%"
        cond = "(base.name ILIKE :q OR base.phone ILIKE :q"
        if len(digits) >= 4:
            params["qd"] = f"%{digits[-9:]}%"
            cond += " OR regexp_replace(base.phone, '\\D', '', 'g') LIKE :qd"
        where.append(cond + ")")
    if master:
        try:
            params["master"] = str(uuid.UUID(str(master)))
        except ValueError:
            params["master"] = None
        where.append("EXISTS (SELECT 1 FROM bookings bm WHERE bm.contact_id = base.id AND bm.user_id = :uid "
                     f"AND bm.resource_id = CAST(:master AS uuid) AND bm.status IN {VISITED_SQL})")
    if service and service.strip():
        params["svc"] = f"%{service.strip()}%"
        where.append("EXISTS (SELECT 1 FROM bookings bs2 WHERE bs2.contact_id = base.id AND bs2.user_id = :uid "
                     f"AND bs2.service_title ILIKE :svc AND bs2.status IN {VISITED_SQL})")
    if source:
        if source == "none":
            where.append("base.source IS NULL")
        else:
            params["src"] = source
            where.append("base.source = :src")
    if gender in GENDERS:
        params["gender"] = gender
        where.append("base.gender = :gender")
    elif gender == "none":
        where.append("base.gender IS NULL")
    if tag and tag.strip():
        params["tag"] = "%," + tag.strip().lower() + ",%"
        where.append("(',' || COALESCE(base.tags, '') || ',') ILIKE :tag")
    return where


def _sql(select: str, where: List[str], order: str = "", tail: str = "", contact_where: str = "",
         stats_where: str = "") -> str:
    w = (" WHERE " + " AND ".join(where)) if where else ""
    return ("WITH " + _STATS_SQL.format(stats_where=stats_where) + "," + _BASE_SQL.format(contact_where=contact_where)
            + f" SELECT {select} FROM base CROSS JOIN p{w}" + (f" ORDER BY {order}" if order else "") + tail)


_ROW_COLS = ("base.id, base.visits, base.spent, base.no_shows, base.cancelled, base.last_visit, base.first_visit, "
             "base.next_at, base.fav_master, base.fav_service, base.bookings")


def _stats_from_row(r, masters: Dict[str, str], now: datetime) -> Dict[str, Any]:
    visits = int(r.visits or 0)
    spent = _num(r.spent)
    out = {
        "visits": visits, "spent": round(spent, 2), "no_shows": int(r.no_shows or 0), "cancelled": int(r.cancelled or 0),
        "avg_check": round(spent / visits, 2) if visits else None,
        "last_visit": bs._aw(r.last_visit).isoformat() if r.last_visit else None,
        "first_visit": bs._aw(r.first_visit).isoformat() if r.first_visit else None,
        "days_since_last": _days_ago(r.last_visit, now),
        "next_at": bs._aw(r.next_at).isoformat() if r.next_at else None,
        "favorite_master_id": str(r.fav_master) if r.fav_master else None,
        "favorite_master": masters.get(str(r.fav_master)) if r.fav_master else None,
        "favorite_service": r.fav_service,
        "bookings": int(r.bookings or 0),
    }
    return out


def segment_counts(db: Session, user) -> List[Dict[str, Any]]:
    params = _params(user)
    cols = ", ".join(f"count(*) FILTER (WHERE {cond}) AS {key}" for key, cond in SEGMENT_SQL.items())
    row = db.execute(text(_sql(cols, [])), params).mappings().first() or {}
    return [{"key": k, "title": t, "hint": h, "count": int(row.get(k) or 0)} for k, t, h in SEGMENTS]


def list_clients(db: Session, user, *, segment: str = "all", q: Optional[str] = None, master: Optional[str] = None,
                 service: Optional[str] = None, source: Optional[str] = None, gender: Optional[str] = None,
                 tag: Optional[str] = None, sort: str = "last_visit", limit: int = 50, offset: int = 0,
                 with_counts: bool = True) -> Dict[str, Any]:
    segment = segment if segment in SEGMENT_KEYS else "all"
    sort = sort if sort in SORTS else ("birthday" if segment == "birthday" else "last_visit")
    params = _params(user)
    where = [SEGMENT_SQL[segment]] + _filters(params, q, master, service, source, gender, tag)
    params.update(lim=max(1, min(int(limit), 200)), off=max(0, int(offset)))
    rows = db.execute(text(_sql(_ROW_COLS + ", count(*) OVER () AS total", where, SORTS[sort],
                                " LIMIT :lim OFFSET :off")), params).fetchall()
    total = int(rows[0].total) if rows else 0
    if not rows and offset:
        total = int(db.execute(text(_sql("count(*)", where)), params).scalar() or 0)
    items = _render(db, user, rows)
    out: Dict[str, Any] = {"total": total, "items": items, "segment": segment, "sort": sort}
    if with_counts:
        out["segments"] = segment_counts(db, user)
        out["sources"] = [{"key": k, "title": t, "auto": a} for k, t, a in SOURCES]
        out["masters"] = [{"id": str(r.id), "name": r.name} for r in bs.resources(db, user.id, active_only=False)]
    return out


def _render(db: Session, user, rows) -> List[Dict[str, Any]]:
    if not rows:
        return []
    ids = [r.id for r in rows]
    contacts = {c.id: c for c in db.query(AgentContact).filter(AgentContact.id.in_(ids)).all()}
    channels = _channels(db, ids)
    masters = _masters(db, user.id)
    now = bs.now_utc()
    today = bs.to_local(now).date()
    out = []
    for r in rows:
        c = contacts.get(r.id)
        if c is None:
            continue
        d = c.to_dict()
        d["phone"] = real_phone(c.phone)
        d["channels"] = channels.get(c.id, {})
        d["stats"] = _stats_from_row(r, masters, now)
        d["birthday_info"] = birthday_info(c.birthday, today)
        d["source_title"] = SOURCE_TITLES.get(c.source or "")
        d.pop("memory", None)
        out.append(d)
    return out


def _channels(db: Session, ids: List) -> Dict[Any, Dict[str, bool]]:
    out: Dict[Any, Dict[str, bool]] = {i: {} for i in ids}
    for ch, table in (("whatsapp", "agent_whatsapp_messages"), ("telegram", "agent_telegram_messages"),
                      ("instagram", "agent_instagram_messages"), ("webchat", "agent_webchat_messages")):
        try:
            for (cid,) in db.execute(text(f"SELECT DISTINCT agent_contact_id FROM {table} "
                                          "WHERE agent_contact_id = ANY(CAST(:ids AS uuid[]))"),
                                     {"ids": [str(i) for i in ids]}).fetchall():
                key = cid if cid in out else uuid.UUID(str(cid))
                if key in out:
                    out[key][ch] = True
        except Exception:
            db.rollback()
    return out


# ───────────────────────────── карточка ─────────────────────────────

STATUS_TITLES = {"booked": "Записан", "confirmed": "Подтвердил", "arrived": "Пришёл", "completed": "Завершён",
                 "no_show": "Не пришёл", "cancelled": "Отменена"}


def client_card(db: Session, user, contact_id) -> Optional[Dict[str, Any]]:
    """Салонная часть карточки: цифры, любимые, ДР, источник, сегменты, история визитов и будущие записи."""
    from backend.models.booking import Booking
    try:
        cid = uuid.UUID(str(contact_id))
    except ValueError:
        return None
    c = db.query(AgentContact).filter(AgentContact.id == cid, AgentContact.user_id == user.id).first()
    if c is None:
        return None
    params = _params(user)
    params["cid"] = str(cid)
    seg_cols = ", ".join(f"({cond}) AS s_{key}" for key, cond in SEGMENT_SQL.items() if key != "all")
    row = db.execute(text(_sql(_ROW_COLS + ", " + seg_cols, ["base.id = CAST(:cid AS uuid)"],
                               contact_where="AND c.id = CAST(:cid AS uuid)",
                               stats_where="AND b.contact_id = CAST(:cid AS uuid)")), params).fetchone()
    # p (порог VIP) считается по всем клиентам — поэтому отдельный запрос без фильтра по карточке
    p90 = db.execute(text(_sql("p.p90", [], tail=" LIMIT 1")), params).scalar()
    now = bs.now_utc()
    today = bs.to_local(now).date()
    masters = _masters(db, user.id)
    stats = _stats_from_row(row, masters, now) if row is not None else _stats_from_row(_Empty(), masters, now)
    segs = []
    if row is not None:
        for key, title, _h in SEGMENTS:
            if key == "all":
                continue
            if key == "vip":
                if p90 is not None and stats["visits"] >= 2 and stats["spent"] > 0 and stats["spent"] >= float(p90):
                    segs.append({"key": key, "title": title})
            elif getattr(row, "s_" + key):
                segs.append({"key": key, "title": title})
    bks = (db.query(Booking).filter(Booking.user_id == user.id, Booking.contact_id == cid)
           .order_by(Booking.start_at.desc()).limit(60).all())
    history, upcoming = [], []
    seen_visits = set()
    for b in bks:
        item = {"id": str(b.id), "start": bs._aw(b.start_at).isoformat(), "service": b.service_title,
                "master": masters.get(str(b.resource_id)) if b.resource_id else None,
                "master_id": str(b.resource_id) if b.resource_id else None,
                "price": float(b.price) if b.price is not None else None, "status": b.status,
                "status_title": STATUS_TITLES.get(b.status, b.status), "visit_id": str(b.visit_id) if b.visit_id else None,
                "review_score": getattr(b, "review_score", None), "review_text": getattr(b, "review_text", None)}
        if b.status in ("booked", "confirmed") and bs._aw(b.start_at) > now:
            upcoming.append(item)
        else:
            history.append(item)
        if b.visit_id:
            seen_visits.add(str(b.visit_id))
    upcoming.sort(key=lambda x: x["start"])
    visited_ts = sorted(bs._aw(b.start_at) for b in bks if b.status in ("arrived", "completed"))
    gaps = [(b - a).days for a, b in zip(visited_ts, visited_ts[1:]) if (b - a).days > 0]
    if gaps:
        stats["usual_interval_days"] = int(sorted(gaps)[len(gaps) // 2])
    scores = [b.review_score for b in bks if getattr(b, "review_score", None)]
    if scores:
        stats["avg_review"] = round(sum(scores) / len(scores), 1)
    return {
        "contact_id": str(cid), "stats": stats, "segments": segs,
        "birthday": birthday_info(c.birthday, today), "gender": c.gender,
        "source": c.source, "source_title": SOURCE_TITLES.get(c.source or ""),
        "history": history[:30], "upcoming": upcoming[:10],
        "sources": [{"key": k, "title": t, "auto": a} for k, t, a in SOURCES],
        "my_url": _my_url(cid),     # шаг 2.5: ссылка «Мои записи» — владелец может отправить клиенту сам
    }


def _my_url(contact_id) -> Optional[str]:
    try:
        from backend.services import client_cabinet
        return client_cabinet.link_for_contact(contact_id)
    except Exception:
        return None


class _Empty:
    visits = spent = no_shows = cancelled = bookings = 0
    last_visit = first_visit = next_at = fav_master = fav_service = None


def update_profile(contact: AgentContact, data: Dict[str, Any]) -> List[str]:
    """ДР, пол, источник из карточки (PUT /api/agent/contacts/{id}). Неверная дата — ValueError с понятным текстом."""
    changed = []
    if "birthday" in data:
        v = parse_birthday(data.get("birthday"))
        if v != contact.birthday:
            contact.birthday = v
            changed.append("birthday")
    if "gender" in data:
        g = (data.get("gender") or "").strip().lower()[:1] or None
        if g not in (None, "f", "m"):
            raise ValueError("Пол: f или m")
        if g != contact.gender:
            contact.gender = g
            changed.append("gender")
    if "marketing_opt_out" in data:
        want = bool(data.get("marketing_opt_out"))
        if want != (contact.marketing_opt_out_at is not None):
            contact.marketing_opt_out_at = datetime.utcnow() if want else None
            changed.append("marketing_opt_out")
    if "source" in data:
        s = (data.get("source") or "").strip() or None
        if s is not None and s not in SOURCE_TITLES:
            raise ValueError("Неизвестный источник")
        if s != contact.source:
            contact.source = s
            changed.append("source")
    return changed


# ───────────────────────────── выгрузка ─────────────────────────────

EXPORT_COLUMNS = ["Имя", "Телефон", "День рождения", "Пол", "Источник", "Визитов", "Сумма, сом", "Средний чек, сом",
                  "Неявок", "Отмен", "Первый визит", "Последний визит", "Дней без визита", "Ближайшая запись",
                  "Любимый мастер", "Любимая услуга", "Теги", "Заметки", "Добавлен"]


def export_csv(db: Session, user, **flt) -> str:
    """CSV с BOM для Excel (разделитель «;» — так Excel в русской локали сразу раскладывает по столбцам)."""
    flt.pop("limit", None)
    flt.pop("offset", None)
    segment = flt.pop("segment", "all")
    segment = segment if segment in SEGMENT_KEYS else "all"
    sort = flt.pop("sort", "")
    sort = sort if sort in SORTS else ("birthday" if segment == "birthday" else "last_visit")
    params = _params(user)
    where = [SEGMENT_SQL[segment]] + _filters(params, flt.get("q"), flt.get("master"), flt.get("service"),
                                              flt.get("source"), flt.get("gender"), flt.get("tag"))
    params["lim"] = EXPORT_MAX
    rows = db.execute(text(_sql(_ROW_COLS, where, SORTS[sort], " LIMIT :lim")), params).fetchall()
    masters = _masters(db, user.id)
    contacts = {}
    ids = [r.id for r in rows]
    for i in range(0, len(ids), 1000):
        for c in db.query(AgentContact).filter(AgentContact.id.in_(ids[i:i + 1000])).all():
            contacts[c.id] = c
    now = bs.now_utc()
    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf, delimiter=";")
    w.writerow(EXPORT_COLUMNS)

    def dt(v):
        return bs.to_local(bs._aw(v)).strftime("%d.%m.%Y") if v else ""

    for r in rows:
        c = contacts.get(r.id)
        if c is None:
            continue
        s = _stats_from_row(r, masters, now)
        bd = c.birthday
        w.writerow([
            _cell(c.name), _cell(real_phone(c.phone)),
            (f"{bd.day:02d}.{bd.month:02d}" + ("" if bd.year == BIRTHDAY_NO_YEAR else f".{bd.year}")) if bd else "",
            GENDERS.get(c.gender or "", ""), SOURCE_TITLES.get(c.source or "", ""),
            s["visits"], _money(s["spent"]), _money(s["avg_check"]), s["no_shows"], s["cancelled"],
            dt(r.first_visit), dt(r.last_visit), "" if s["days_since_last"] is None else s["days_since_last"],
            bs.to_local(bs._aw(r.next_at)).strftime("%d.%m.%Y %H:%M") if r.next_at else "",
            _cell(s["favorite_master"]), _cell(s["favorite_service"]), _cell((c.tags or "").replace(",", ", ")),
            _cell((c.notes or "").replace("\r", " ").replace("\n", " ")[:500]), dt(c.created_at.replace(tzinfo=timezone.utc)),
        ])
    return buf.getvalue()


def _money(v) -> str:
    if v is None:
        return ""
    f = float(v)
    return str(int(f)) if f == int(f) else f"{f:.2f}".replace(".", ",")


def _cell(v) -> str:
    """Защита от формул в Excel: значение, начинающееся с = + - @, экранируется апострофом."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@") and not re.fullmatch(r"\+\d[\d ()-]*", s) else s


# ───────────────────────────── досчёт источника ─────────────────────────────

_FIRST_TOUCH_SQL = """
WITH nc AS (
  SELECT id, created_at FROM agent_contacts WHERE source IS NULL AND created_at < :old ORDER BY created_at LIMIT :lim
), ev AS (
  SELECT x.agent_contact_id AS cid, 'whatsapp' AS ch, x.created_at FROM agent_whatsapp_messages x WHERE x.agent_contact_id IN (SELECT id FROM nc)
  UNION ALL SELECT x.agent_contact_id, 'telegram', x.created_at FROM agent_telegram_messages x WHERE x.agent_contact_id IN (SELECT id FROM nc)
  UNION ALL SELECT x.agent_contact_id, 'instagram', x.created_at FROM agent_instagram_messages x WHERE x.agent_contact_id IN (SELECT id FROM nc)
  UNION ALL SELECT x.agent_contact_id, 'webchat', x.created_at FROM agent_webchat_messages x WHERE x.agent_contact_id IN (SELECT id FROM nc)
  UNION ALL SELECT ac.agent_contact_id, 'call', ac.created_at FROM agent_calls ac
    WHERE ac.agent_contact_id IN (SELECT id FROM nc) AND ac.direction NOT IN :dirs
  UNION ALL SELECT b.contact_id, CASE WHEN b.source = 'online' THEN 'booking' WHEN b.source = 'call' THEN 'call' ELSE 'manual' END,
    (b.created_at AT TIME ZONE 'UTC') FROM bookings b WHERE b.contact_id IN (SELECT id FROM nc)
), fst AS (
  SELECT DISTINCT ON (cid) cid, ch FROM ev ORDER BY cid, created_at
)
SELECT nc.id, fst.ch FROM nc LEFT JOIN fst ON fst.cid = nc.id
"""


_BACKFILL_EVERY = 15 * 60
_last_backfill = 0.0


def backfill_sources(db: Session, limit: int = 500, force: bool = False) -> int:
    """Карточкам без источника (старше часа) — источник по первому касанию; без касаний — «добавлен вручную».
    Порциями, не чаще раза в 15 минут на воркер; вызывается из фонового тика (weekly_jobs).
    Возвращает, сколько карточек получили источник."""
    global _last_backfill
    import time as _time
    from sqlalchemy import bindparam
    from backend.services.analytics_service import MESSAGE_DIRECTIONS
    if not force and _time.monotonic() - _last_backfill < _BACKFILL_EVERY:
        return 0
    _last_backfill = _time.monotonic()
    try:
        rows = db.execute(text(_FIRST_TOUCH_SQL).bindparams(bindparam("dirs", expanding=True)),
                          {"old": datetime.utcnow() - timedelta(hours=1), "lim": int(limit),
                           "dirs": list(MESSAGE_DIRECTIONS)}).fetchall()
    except Exception as e:
        db.rollback()
        logger.warning(f"[SALON-CLIENTS] backfill query failed: {e}")
        return 0
    n = 0
    by_src: Dict[str, List[str]] = {}
    for cid, ch in rows:
        by_src.setdefault(ch or "manual", []).append(str(cid))
    for src, ids in by_src.items():
        db.execute(text("UPDATE agent_contacts SET source = :s WHERE source IS NULL AND id = ANY(CAST(:ids AS uuid[]))"),
                   {"s": src, "ids": ids})
        n += len(ids)
    db.commit()
    return n


def source_stats(db: Session, user) -> List[Dict[str, Any]]:
    """Сколько клиентов из каждого источника (для подсказки у фильтра)."""
    rows = db.execute(text("SELECT COALESCE(source, 'none'), count(*) FROM agent_contacts WHERE user_id = :uid "
                           "GROUP BY 1 ORDER BY 2 DESC"), {"uid": str(user.id)}).fetchall()
    return [{"key": k, "title": SOURCE_TITLES.get(k, "Не указан"), "count": int(n)} for k, n in rows]


def for_agent(card: Dict[str, Any]) -> Dict[str, Any]:
    """Коротко для бота (get_customer_history): ДР и источник, если известны."""
    out: Dict[str, Any] = {}
    b = card.get("birthday")
    if b:
        out["birthday"] = b["text"]
        out["birthday_in_days"] = b["in_days"]
    if card.get("source_title"):
        out["source"] = card["source_title"]
    if card.get("segments"):
        out["segments"] = [s["title"] for s in card["segments"]]
    return out


def years_text(n: int) -> str:
    return f"{n} {_years_word(n)}"
