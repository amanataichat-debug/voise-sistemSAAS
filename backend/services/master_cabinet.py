"""
Кабинет мастера (Voksy Beauty, шаг 1.2): что видит мастер на телефоне — /static/master.html.

Мастер = мастер журнала (booking_resources), привязанный ко входу сотрудника (team_member_id).
Сотрудник с разделом «Свой журнал» (booking_own) видит ТОЛЬКО своё:
  • записи к себе (день, неделя), может отметить «подтвердил / пришёл / не пришёл», оставить
    заметку, перенести свою запись и записать клиента к себе (повторный визит). Отменять запись
    и переносить к другому мастеру — нет (это администратор);
  • своих клиентов: кто приходил и придёт к нему, сколько раз, что делал, заметки визитов;
  • заработок: % с выручки визитов «пришёл» + доля оклада (booking_resources.pay_percent / pay_fixed —
    те же цифры, что у владельца в «Финансах»), сегодня / неделя / месяц и «ещё ожидается сегодня»;
  • статистику за 30 дней: визиты, неявки, средний чек, вернувшиеся клиенты, частые услуги;
  • новое: записи к нему и отмены за 7 дней (лента; непрочитанное считает страница).
Телефоны клиентов мастер видит, только если владелец включил это в правилах записи
(booking_pages.settings.master_phones) — защита клиентской базы салона.

Владелец (и сотрудник с полным журналом) может открыть кабинет любого мастера — «как видит мастер».
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.models.booking import Booking, BookingResource
from backend.services import booking_service as bs

MASTER_STATUSES = ("booked", "confirmed", "arrived", "completed", "no_show")   # что мастер может поставить сам
FEED_DAYS = 7
STATS_DAYS = 30
SOURCE_TITLES = {"owner": "администратор", "staff": "администратор", "bot": "бот в переписке",
                 "call": "голосовой агент", "online": "онлайн по ссылке"}


def find_for_member(db: Session, owner_id, member_id) -> Optional[BookingResource]:
    """Мастер журнала, привязанный ко входу сотрудника (активный в приоритете)."""
    try:
        mid = uuid.UUID(str(member_id))
    except (ValueError, TypeError):
        return None
    rows = (db.query(BookingResource)
            .filter(BookingResource.user_id == owner_id, BookingResource.team_member_id == mid)
            .order_by(BookingResource.is_active.desc(), BookingResource.created_at).all())
    return rows[0] if rows else None


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _real_phone(p: Optional[str]) -> Optional[str]:
    return p if p and not str(p).startswith(("web:", "tg:", "ig:", "wa:")) else None


def booking_view(b: Booking, show_phone: bool) -> Dict[str, Any]:
    d = bs.booking_to_dict(b)
    d.pop("resource", None)
    d["client_phone"] = _real_phone(d.get("client_phone")) if show_phone else None
    d["source_title"] = SOURCE_TITLES.get(b.source or "", "")
    return d


# ───────────────────────────── заработок ─────────────────────────────

def _sum_arrived(db: Session, rid, a: datetime, b: datetime) -> Tuple[int, float]:
    row = db.execute(text(
        "SELECT count(*), COALESCE(sum(price), 0) FROM bookings WHERE resource_id = :r AND status IN ('arrived', 'completed') "
        "AND start_at >= :a AND start_at < :b"), {"r": str(rid), "a": a, "b": b}).first()
    return int(row[0] or 0), float(row[1] or 0)


def earnings(db: Session, r: BookingResource, today: Optional[date] = None) -> Dict[str, Any]:
    """Сегодня / неделя (с понедельника) / месяц (с 1-го): визиты, выручка, заработок мастера."""
    from backend.services.finance_service import month_share
    today = today or bs.to_local(bs.now_utc()).date()
    pct = float(r.pay_percent or 0)
    fixed = float(r.pay_fixed or 0)
    started = bs.to_local(_aware(r.created_at)).date() if r.created_at else today
    periods = {"today": today, "week": today - timedelta(days=today.weekday()), "month": today.replace(day=1)}
    out: Dict[str, Any] = {"pay_percent": pct, "pay_fixed": fixed, "configured": bool(pct or fixed)}
    end = bs.day_bounds(today)[1]
    for key, first in periods.items():
        cnt, rev = _sum_arrived(db, r.id, bs.day_bounds(first)[0], end)
        fixed_part = month_share(fixed, first, today, min(started, first), None) if fixed else 0.0
        out[key] = {"visits": cnt, "revenue": round(rev, 2),
                    "salary": round(rev * pct / 100.0 + fixed_part, 2)}
    # ещё ожидается сегодня: записи, которые не отмечены (кроме неявок и отмен)
    a, b = bs.day_bounds(today)
    row = db.execute(text(
        "SELECT count(*), COALESCE(sum(price), 0) FROM bookings WHERE resource_id = :r "
        "AND status IN ('booked', 'confirmed') AND start_at >= :a AND start_at < :b"),
        {"r": str(r.id), "a": a, "b": b}).first()
    exp_rev = float(row[1] or 0)
    out["expected_today"] = {"visits": int(row[0] or 0), "revenue": round(exp_rev, 2),
                             "salary": round(exp_rev * pct / 100.0, 2)}
    return out


# ───────────────────────────── статистика ─────────────────────────────

def stats(db: Session, r: BookingResource, days: int = STATS_DAYS) -> Dict[str, Any]:
    today = bs.to_local(bs.now_utc()).date()
    a = bs.day_bounds(today - timedelta(days=days - 1))[0]
    b = bs.day_bounds(today)[1]
    row = db.execute(text("""
        SELECT count(*) FILTER (WHERE status IN ('arrived', 'completed')) AS arrived,
               count(*) FILTER (WHERE status = 'no_show') AS no_show,
               count(*) FILTER (WHERE status <> 'cancelled') AS total,
               COALESCE(sum(price) FILTER (WHERE status IN ('arrived', 'completed')), 0) AS revenue
        FROM bookings WHERE resource_id = :r AND start_at >= :a AND start_at < :b
    """), {"r": str(r.id), "a": a, "b": b}).mappings().first() or {}
    arrived = int(row.get("arrived") or 0)
    no_show = int(row.get("no_show") or 0)
    revenue = float(row.get("revenue") or 0)
    # вернувшиеся: клиенты периода, у которых был визит к этому мастеру раньше
    ret = db.execute(text("""
        WITH cur AS (
            SELECT DISTINCT contact_id FROM bookings
            WHERE resource_id = :r AND status IN ('arrived', 'completed') AND contact_id IS NOT NULL
              AND start_at >= :a AND start_at < :b)
        SELECT count(*) AS clients,
               count(*) FILTER (WHERE EXISTS (
                   SELECT 1 FROM bookings p WHERE p.resource_id = :r AND p.contact_id = cur.contact_id
                     AND p.status IN ('arrived', 'completed') AND p.start_at < :a)) AS returning
        FROM cur
    """), {"r": str(r.id), "a": a, "b": b}).mappings().first() or {}
    top = db.execute(text("""
        SELECT COALESCE(service_title, 'Без услуги') AS title, count(*) AS n
        FROM bookings WHERE resource_id = :r AND status IN ('arrived', 'completed') AND start_at >= :a AND start_at < :b
        GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 5
    """), {"r": str(r.id), "a": a, "b": b}).fetchall()
    clients = int(ret.get("clients") or 0)
    returning = int(ret.get("returning") or 0)
    done = arrived + no_show
    return {
        "days": days, "visits": arrived, "no_show": no_show, "total": int(row.get("total") or 0),
        "no_show_pct": round(no_show * 100.0 / done) if done else 0,
        "revenue": round(revenue, 2), "avg_check": round(revenue / arrived) if arrived else 0,
        "clients": clients, "returning": returning,
        "returning_pct": round(returning * 100.0 / clients) if clients else 0,
        "top_services": [{"title": t, "count": int(n)} for t, n in top],
    }


# ───────────────────────────── лента «новое» ─────────────────────────────

def feed(db: Session, r: BookingResource, show_phone: bool, limit: int = 30) -> List[Dict[str, Any]]:
    """Новые записи и отмены за неделю — только на будущее время (визит, внесённый задним числом, не новость)."""
    now = bs.now_utc()
    since = now - timedelta(days=FEED_DAYS)
    rows = (db.query(Booking).filter(Booking.resource_id == r.id,
                                     (Booking.created_at >= since) | (Booking.updated_at >= since))
            .order_by(Booking.updated_at.desc()).limit(limit * 2).all())
    out = []
    for b in rows:
        created = _aware(b.created_at)
        updated = _aware(b.updated_at) if b.updated_at else created
        if _aware(b.end_at) < now:
            continue
        if b.status == "cancelled" and updated >= since:
            kind, at = "cancelled", updated
        elif created >= since:
            kind, at = "new", created
        else:
            continue
        item = booking_view(b, show_phone)
        item.update({"kind": kind, "at": at.isoformat()})
        out.append(item)
    out.sort(key=lambda x: x["at"], reverse=True)
    return out[:limit]


# ───────────────────────────── клиенты мастера ─────────────────────────────

def clients(db: Session, r: BookingResource, show_phone: bool, limit: int = 300) -> List[Dict[str, Any]]:
    """Клиенты мастера: кто приходил или записан к нему. Последние визиты, следующий визит."""
    rows = (db.query(Booking).filter(Booking.resource_id == r.id, Booking.status != "cancelled")
            .order_by(Booking.start_at.desc()).limit(3000).all())
    now = bs.now_utc()
    groups: Dict[str, Dict[str, Any]] = {}
    by_phone: Dict[str, str] = {}          # телефон → группа: визит без карточки с тем же номером — тот же клиент
    for b in rows:
        if b.contact_id and _real_phone(b.client_phone):
            by_phone.setdefault(b.client_phone, str(b.contact_id))
    for b in rows:
        phone = _real_phone(b.client_phone)
        if b.contact_id:
            key = str(b.contact_id)
        elif phone:
            key = by_phone.get(phone) or "p:" + phone
        else:
            key = "n:" + (b.client_name or "").strip().lower()
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"key": key, "contact_id": key if not key.startswith(("p:", "n:")) else None,
                               "name": b.client_name or "Клиент", "phone": None, "visits": 0, "no_show": 0,
                               "spent": 0.0, "last_visit": None, "next_visit": None, "history": []}
        if show_phone and not g["phone"]:
            g["phone"] = _real_phone(b.client_phone)
        st = _aware(b.start_at)
        if bs.visited(b.status):
            g["visits"] += 1
            g["spent"] += float(b.price or 0)
            if g["last_visit"] is None:
                g["last_visit"] = st.isoformat()
        elif b.status == "no_show":
            g["no_show"] += 1
        if st >= now and b.status in ("booked", "confirmed"):
            g["next_visit"] = st.isoformat()      # строки идут от новых к старым → останется ближайшая
        if len(g["history"]) < 12:
            g["history"].append({"start": st.isoformat(), "label": bs.fmt_local(st), "service": b.service_title,
                                 "status": b.status, "status_title": bs.STATUS_TITLES.get(b.status, b.status),
                                 "price": b.price, "note": b.note})
    out = list(groups.values())
    for g in out:
        g["spent"] = round(g["spent"], 2)
    # ближайший визит первым; без визита — по свежести последнего
    with_next = [g for g in out if g["next_visit"]]
    without = sorted([g for g in out if not g["next_visit"]], key=lambda g: g["last_visit"] or "", reverse=True)
    return (sorted(with_next, key=lambda g: g["next_visit"]) + without)[:limit]


# ───────────────────────────── всё для страницы ─────────────────────────────

def overview(db: Session, owner, r: BookingResource, day=None, days: int = 7) -> Dict[str, Any]:
    page = bs.get_page(db, owner, create=False)
    cfg = bs.settings_of(page)
    show_phone = bool(cfg.get("master_phones"))
    start_day = bs.parse_day(day) if day is not None else bs.to_local(bs.now_utc()).date()
    days = max(1, min(int(days or 7), 31))
    a = bs.day_bounds(start_day)[0]
    b = a + timedelta(days=days)
    rows = bs.list_bookings(db, owner.id, a, b, resource_id=r.id)
    services = []
    for e in bs.services(db, owner.id):
        if bs._resource_does(r, e):
            d = bs.service_to_dict(e)
            d["duration_min"], d["price"] = bs.service_terms(r, e)
            services.append(d)
    return {
        "linked": True, "date": start_day.isoformat(), "days": days,
        "today": bs.to_local(bs.now_utc()).date().isoformat(),
        "master": bs.resource_to_dict(r),
        "company": getattr(owner, "company_name", None) or "",
        "show_phones": show_phone,
        "bookings": [booking_view(x, show_phone) for x in rows],
        "services": services,
        "earnings": earnings(db, r),
        "stats": stats(db, r),
        "feed": feed(db, r, show_phone),
    }


def check_own_booking(db: Session, owner_id, r: BookingResource, booking_id) -> Booking:
    b = bs.get_booking(db, owner_id, booking_id)
    if b.resource_id is None or str(b.resource_id) != str(r.id):
        raise bs.BookingError("Это запись другого мастера")
    return b
