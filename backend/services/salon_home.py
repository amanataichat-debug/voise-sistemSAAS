"""
Главная салона (Voksy Beauty, шаг 1.7): одна сводка для рабочей главной кабинета — телефон в первую очередь.

build(db, user, since) → GET /api/beauty/home. Только настоящие данные владельца (без демо), каждый блок
считается отдельно и не роняет остальные (_safe):

  now        — сегодня по Бишкеку: кто сейчас в салоне (статус «пришёл»), что идёт по времени, но не отмечено,
               следующие записи, счётчики (всего / пришли / впереди / не пришли / отменены), выручка за день
               (визиты «пришёл / завершён») и сколько ещё ожидается;
  attention  — требует внимания: не отмечены прошедшие записи, завтра без подтверждения, ждём предоплату,
               ждут одобрения ИИ-управляющего, новые записи от бота / звонка / клиента по ссылке с прошлого
               захода (since; иначе за сутки), низкие оценки 1–3 за 14 дней, сообщения без ответа дольше 10 мин;
  gaps       — свободные окна сегодня (от «сейчас») и завтра по мастерам: интервалы рабочего времени минус
               записи (с подготовкой, уборкой и общим перерывом) и блоки времени; can_offer — можно ли попросить
               ИИ-управляющего предложить окна клиентам (автономный агент Beauty Pro / Profi);
  load       — загрузка мастеров сегодня и на этой неделе (пн–вс): минуты записей / рабочие минуты без блоков;
  recent     — 5 последних строк журнала действий (если смотрящему он открыт).

offer_gaps(db, user, resource_id, day) — «Предложить клиентам»: ИИ-управляющий подбирает давних клиентов этого
мастера и заводит предложения ВСЕГДА на одобрение владельца (manager_actions.status = proposed).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.models.booking import ACTIVE_STATUSES, VISITED_STATUSES, Booking
from backend.services import activity
from backend.services import booking_service as bs

logger = logging.getLogger(__name__)

MIN_GAP_MIN = 30            # свободный кусок короче — не окно
UNANSWERED_AFTER_MIN = 10   # сообщение клиента без ответа дольше — «требует внимания»
NEXT_COUNT = 6
LOW_REVIEW = 3


def _safe(name: str, db: Session, fn, default):
    try:
        with db.begin_nested():
            return fn()
    except Exception as e:
        logger.warning(f"[SALON-HOME] {name}: {e}")
        return default


def _aw(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _hm(dt: datetime) -> str:
    return bs.to_local(dt).strftime("%H:%M")


def _first(name: Optional[str]) -> str:
    return (name or "Клиент").split()[0]


def _merge(iv: List[Tuple[datetime, datetime]]) -> List[Tuple[datetime, datetime]]:
    out: List[Tuple[datetime, datetime]] = []
    for a, b in sorted(iv):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _subtract(work: List[Tuple[datetime, datetime]], busy: List[Tuple[datetime, datetime]]) -> List[Tuple[datetime, datetime]]:
    free: List[Tuple[datetime, datetime]] = []
    taken = _merge(busy)
    for a, b in work:
        cur = a
        for ta, tb in taken:
            if tb <= cur or ta >= b:
                continue
            if ta > cur:
                free.append((cur, ta))
            cur = max(cur, tb)
        if cur < b:
            free.append((cur, b))
    return free


def _minutes(iv: List[Tuple[datetime, datetime]]) -> int:
    return int(sum((b - a).total_seconds() for a, b in iv) // 60)


def _booking_row(b: Booking, names: Dict[str, str], colors: Dict[str, str]) -> Dict[str, Any]:
    rid = str(b.resource_id) if b.resource_id else None
    return {"id": str(b.id), "client": b.client_name or "Клиент", "service": b.service_title,
            "master": names.get(rid) if rid else None, "color": colors.get(rid) if rid else None,
            "start": _aw(b.start_at).isoformat(), "end": _aw(b.end_at).isoformat(),
            "time": _hm(b.start_at), "until": _hm(b.end_at), "status": b.status,
            "status_title": bs.STATUS_TITLES.get(b.status, b.status), "price": b.price, "source": b.source,
            "prepay": b.prepay_amount if b.prepay_amount and b.prepaid_at is None else None}


# ───────────────────────────── блоки ─────────────────────────────

def now_block(db: Session, user, now: datetime, names, colors) -> Dict[str, Any]:
    today = bs.to_local(now).date()
    a, b = bs.day_bounds(today)
    rows = bs.list_bookings(db, user.id, a, b)
    live = [x for x in rows if x.status != "cancelled"]
    in_salon = [x for x in live if x.status == "arrived"]
    running = [x for x in live if x.status in ("booked", "confirmed") and _aw(x.start_at) <= now < _aw(x.end_at)]
    upcoming = [x for x in live if x.status in ("booked", "confirmed") and _aw(x.start_at) > now]
    visited = [x for x in live if x.status in VISITED_STATUSES]
    return {
        "date": today.isoformat(),
        "counts": {"total": len(live), "came": len(visited), "in_salon": len(in_salon), "upcoming": len(upcoming),
                   "no_show": sum(1 for x in live if x.status == "no_show"),
                   "cancelled": sum(1 for x in rows if x.status == "cancelled")},
        "revenue": round(sum(float(x.price or 0) for x in visited), 2),
        # как в аналитике: записи дня, где визит ещё не отмечен (впереди, идут сейчас, прошли без отметки)
        "expected": round(sum(float(x.price or 0) for x in live if x.status in ("booked", "confirmed")), 2),
        "in_salon": [_booking_row(x, names, colors) for x in in_salon],
        "running": [_booking_row(x, names, colors) for x in running],
        "next": [_booking_row(x, names, colors) for x in upcoming[:NEXT_COUNT]],
        "more": max(0, len(upcoming) - NEXT_COUNT),
    }


def _item(key: str, count: int, title: str, hint: str, href: str, level: str = "warn",
          rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    return {"key": key, "count": int(count), "title": title, "hint": hint, "href": href, "level": level,
            "rows": rows or []}


def attention_block(db: Session, user, now: datetime, since: Optional[datetime], names, colors,
                    owner_view: bool) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    today = bs.to_local(now).date()
    a, _ = bs.day_bounds(today)

    def not_marked():
        rows = (db.query(Booking).filter(Booking.user_id == user.id, Booking.status.in_(("booked", "confirmed")),
                                         Booking.end_at <= now, Booking.end_at > a - timedelta(days=2))
                .order_by(Booking.start_at).limit(20).all())
        if rows:
            out.append(_item("not_marked", len(rows), "Отметьте, пришли ли клиенты",
                             ", ".join(f"{_first(x.client_name)} {_hm(x.start_at)}" for x in rows[:4]),
                             "/static/booking.html", "warn", [_booking_row(x, names, colors) for x in rows[:5]]))
    _safe("not_marked", db, not_marked, None)

    def unconfirmed():
        ta, tb = bs.day_bounds(today + timedelta(days=1))
        rows = (db.query(Booking).filter(Booking.user_id == user.id, Booking.status == "booked",
                                         Booking.start_at >= ta, Booking.start_at < tb)
                .order_by(Booking.start_at).all())
        if rows:
            out.append(_item("unconfirmed", len(rows), "Завтра без подтверждения",
                             ", ".join(f"{_first(x.client_name)} {_hm(x.start_at)}" for x in rows[:4])
                             + (" …" if len(rows) > 4 else ""),
                             "/static/booking.html?date=" + (today + timedelta(days=1)).isoformat(), "info",
                             [_booking_row(x, names, colors) for x in rows[:5]]))
    _safe("unconfirmed", db, unconfirmed, None)

    def prepay():
        rows = (db.query(Booking).filter(Booking.user_id == user.id, Booking.status.in_(("booked", "confirmed")),
                                         Booking.prepay_amount > 0, Booking.prepaid_at.is_(None),
                                         Booking.start_at >= now)
                .order_by(Booking.start_at).limit(30).all())
        if rows:
            total = sum(float(x.prepay_amount or 0) for x in rows)
            out.append(_item("prepay", len(rows), "Ждём предоплату",
                             f"{activity.money(total)} · ближайшая: {_first(rows[0].client_name)}, {activity.when(rows[0].start_at)}",
                             "/static/booking.html", "warn", [_booking_row(x, names, colors) for x in rows[:5]]))
    _safe("prepay", db, prepay, None)

    if owner_view:
        def pending():
            n = int(db.execute(text("SELECT count(*) FROM manager_actions WHERE user_id = :u AND status = 'proposed'"),
                               {"u": str(user.id)}).scalar() or 0)
            if n:
                out.append(_item("approve", n, "Ждут вашего одобрения", "предложения ИИ-управляющего клиентам",
                                 "/static/manager.html#approve", "accent"))
        _safe("pending", db, pending, None)

    def new_online():
        frm = since or (now - timedelta(hours=24))
        frm = max(frm, now - timedelta(days=7))
        rows = (db.query(Booking).filter(Booking.user_id == user.id, Booking.source.in_(("bot", "call", "online")),
                                         Booking.created_at > frm, Booking.status != "cancelled")
                .order_by(Booking.created_at.desc()).limit(30).all())
        if rows:
            by = {"bot": 0, "call": 0, "online": 0}
            for x in rows:
                by[x.source] = by.get(x.source, 0) + 1
            hint = ", ".join(t for t in [f"бот {by['bot']}" if by["bot"] else "", f"звонок {by['call']}" if by["call"] else "",
                                          f"по ссылке {by['online']}" if by["online"] else ""] if t)
            out.append(_item("new_online", len(rows), "Новые записи без вас" if since else "Новые записи за сутки без вас",
                             hint, "/static/booking.html", "ok", [_booking_row(x, names, colors) for x in rows[:5]]))
    _safe("new_online", db, new_online, None)

    def low_reviews():
        # шаг 2.3: оценки 1–3 ждут решения владельца (на странице салона их не видно, пока он не решит)
        from backend.models.review import SalonReview
        q = db.query(SalonReview).filter(SalonReview.user_id == user.id, SalonReview.status == "pending")
        n = q.count()
        rows = q.order_by(SalonReview.created_at.desc()).limit(3).all()
        if n:
            out.append(_item("low_reviews", n, "Отзывы ждут проверки",
                             "; ".join(f"{_first(x.author_name) or 'клиент'} — {x.score}"
                                       + (f": «{(x.text or '')[:40]}»" if x.text else "") for x in rows),
                             "/static/reviews.html?status=pending", "bad"))
    _safe("low_reviews", db, low_reviews, None)

    def unanswered():
        from backend.services import inbox_service
        d = inbox_service.list_dialogs(db, user, folder="waiting", limit=40)
        edge = datetime.utcnow() - timedelta(minutes=UNANSWERED_AFTER_MIN)
        late = []
        for it in d.get("items") or []:
            t = it.get("last_in_at")
            try:
                ts = datetime.fromisoformat(str(t).replace("Z", "+00:00")).replace(tzinfo=None) if t else None
            except ValueError:
                ts = None
            if ts is not None and ts < edge and not it.get("paused"):
                late.append(it)
        if late:
            out.append(_item("unanswered", len(late), "Сообщения без ответа",
                             ", ".join(x["name"] for x in late[:4]) + (" …" if len(late) > 4 else ""),
                             "/static/inbox.html?folder=waiting", "bad"))
    _safe("unanswered", db, unanswered, None)
    order = {"bad": 0, "warn": 1, "accent": 2, "info": 3, "ok": 4}
    out.sort(key=lambda x: order.get(x["level"], 5))
    return out


def _ceil10(dt: datetime) -> datetime:
    """Сегодняшнее окно начинается не «с 17:22», а с ближайших десяти минут (17:30)."""
    dt = dt.replace(second=0, microsecond=0)
    return dt + timedelta(minutes=(-dt.minute) % 10)


def _free_for(db: Session, user, masters, day: date, now: datetime, cfg) -> Dict[str, List[Tuple[datetime, datetime]]]:
    a, b = bs.day_bounds(day)
    now = _ceil10(now)
    busy = bs._busy(db, [r.id for r in masters], a, b, cfg["buffer_min"], user_id=user.id)
    out: Dict[str, List[Tuple[datetime, datetime]]] = {}
    for r in masters:
        work = bs._work_intervals(r, day)
        free = _subtract(work, busy.get(str(r.id), []))
        free = [(max(x, now), y) for x, y in free if y > now]
        out[str(r.id)] = [(x, y) for x, y in free if (y - x) >= timedelta(minutes=MIN_GAP_MIN)]
    return out


def gaps_block(db: Session, user, now: datetime, masters, can_offer: bool) -> Dict[str, Any]:
    cfg = bs.settings_of(bs.get_page(db, user, create=False))
    today = bs.to_local(now).date()
    days = []
    for d in (today, today + timedelta(days=1)):
        free = _free_for(db, user, masters, d, now, cfg)
        rows = []
        for r in masters:
            iv = free.get(str(r.id), [])
            works = bool(bs._work_intervals(r, d))
            rows.append({"id": str(r.id), "name": r.name, "color": r.color, "photo": _photo(r), "works": works,
                         "online": bs.bookable_online(r), "free_min": _minutes(iv),
                         "ranges": [{"from": _hm(x), "to": _hm(y), "start": x.isoformat(), "min": int((y - x).total_seconds() // 60)}
                                    for x, y in iv]})
        rows.sort(key=lambda x: (not x["works"], -x["free_min"]))
        days.append({"date": d.isoformat(), "title": "Сегодня" if d == today else "Завтра",
                     "weekday": bs.DAY_TITLES[d.weekday()], "masters": rows,
                     "free_min": sum(x["free_min"] for x in rows)})
    return {"days": days, "can_offer": can_offer}


def _photo(r) -> Optional[str]:
    if not getattr(r, "photo_id", None):
        return None
    try:
        from backend.services import salon_media
        return salon_media.url_of(r.photo_id)
    except Exception:
        return None


def load_block(db: Session, user, now: datetime, masters) -> Dict[str, Any]:
    today = bs.to_local(now).date()
    monday = today - timedelta(days=today.weekday())

    def period(d0: date, n: int) -> Dict[str, Dict[str, int]]:
        a = datetime.combine(d0, time(0), bs.BISHKEK).astimezone(timezone.utc)
        b = a + timedelta(days=n)
        ids = [r.id for r in masters]
        blocks = bs._blocks_busy(db, user.id, ids, a, b)
        booked: Dict[str, int] = {str(r.id): 0 for r in masters}
        for rid, sa, sb in (db.query(Booking.resource_id, Booking.start_at, Booking.end_at)
                            .filter(Booking.user_id == user.id, Booking.resource_id.in_(ids),
                                    Booking.status.in_(ACTIVE_STATUSES), Booking.start_at < b, Booking.end_at > a).all()):
            sa, sb = max(_aw(sa), a), min(_aw(sb), b)
            if sb > sa and str(rid) in booked:
                booked[str(rid)] += int((sb - sa).total_seconds() // 60)
        out = {}
        for r in masters:
            work = []
            for i in range(n):
                work += bs._work_intervals(r, d0 + timedelta(days=i))
            wm = _minutes(_subtract(work, blocks.get(str(r.id), [])))
            out[str(r.id)] = {"work_min": wm, "booked_min": booked[str(r.id)]}
        return out

    t, w = period(today, 1), period(monday, 7)
    rows = []
    for r in masters:
        a, b = t[str(r.id)], w[str(r.id)]
        rows.append({"id": str(r.id), "name": r.name, "color": r.color, "photo": _photo(r),
                     "today": {"pct": _pct(a), "booked_min": a["booked_min"], "work_min": a["work_min"]},
                     "week": {"pct": _pct(b), "booked_min": b["booked_min"], "work_min": b["work_min"]}})
    tot = lambda key: {"booked_min": sum(x[key]["booked_min"] for x in rows), "work_min": sum(x[key]["work_min"] for x in rows)}  # noqa: E731
    return {"masters": rows, "today": dict(tot("today"), pct=_pct(tot("today"))),
            "week": dict(tot("week"), pct=_pct(tot("week"))),
            "week_from": monday.isoformat(), "week_to": (monday + timedelta(days=6)).isoformat()}


def _pct(x: Dict[str, int]) -> Optional[int]:
    if not x["work_min"]:
        return None
    return min(100, int(round(x["booked_min"] * 100 / x["work_min"])))


# ───────────────────────────── сводка ─────────────────────────────

def can_offer(db: Session, user) -> bool:
    from backend.services import salon_manager as sm
    return bool(sm.can_run_scenarios(user) and bs._first_agent(db, user.id) is not None)


def build(db: Session, user, since: Optional[datetime] = None, *, show_recent: bool = True,
          owner_view: bool = True) -> Dict[str, Any]:
    now = bs.now_utc()
    masters = bs.resources(db, user.id)
    names = {str(r.id): r.name for r in bs.resources(db, user.id, active_only=False)}
    colors = {str(r.id): r.color for r in bs.resources(db, user.id, active_only=False)}
    offer = _safe("can_offer", db, lambda: can_offer(db, user), False) if owner_view else False
    out: Dict[str, Any] = {
        "generated_at": now.isoformat(),
        "has_masters": bool(masters),
        "now": _safe("now", db, lambda: now_block(db, user, now, names, colors), None),
        "attention": attention_block(db, user, now, since, names, colors, owner_view),
        "gaps": _safe("gaps", db, lambda: gaps_block(db, user, now, masters, offer), None),
        "load": _safe("load", db, lambda: load_block(db, user, now, masters), None),
        "recent": None,
    }
    if show_recent:
        out["recent"] = _safe("recent", db, lambda: activity.query(db, user.id, period="all", limit=5)["items"], [])
    return out


def offer_gaps(db: Session, user, resource_id: str, day: Optional[str] = None) -> Dict[str, Any]:
    """«Предложить клиентам»: давние клиенты мастера → предложения ИИ-управляющего на одобрение владельца."""
    from backend.models.salon_manager import ManagerAction
    from backend.services import salon_manager as sm
    import uuid as _uuid
    agent = bs._first_agent(db, user.id)
    if agent is None or not sm.can_run_scenarios(user):
        raise bs.BookingError("Предлагать окна клиентам может ИИ-управляющий на тарифе Beauty Pro")
    r = next((x for x in bs.resources(db, user.id) if str(x.id) == str(resource_id)), None)
    if r is None:
        raise bs.BookingError("Мастер не найден")
    if not bs.bookable_online(r):
        raise bs.BookingError(f"К мастеру {r.name} записывает только администратор — включите ему онлайн-запись")
    d = bs.parse_day(day) if day else sm.now_local().date()
    cfg = sm.settings_of(agent)
    skip = sm._recent_action_contacts(db, user.id, sm.CONTACT_COOLDOWN_DAYS)
    found = sm.fill_gaps_candidates(db, user, agent, cfg, skip, day=d, resource=r, min_free=1, limit=5)
    now = bs.now_utc()
    rows = []
    for c in found:
        a = ManagerAction(id=_uuid.uuid4(), user_id=user.id, agent_config_id=agent.id, contact_id=c["contact"].id,
                          scenario="fill_gaps", kind=c["kind"], status="proposed", payload=c["payload"], created_at=now)
        db.add(a)
        rows.append(a)
    activity.log(db, user.id, "manager.offer", "manager",
                 f"окна {r.name} на {activity.when(datetime.combine(d, time(12), bs.BISHKEK))[:8]}: "
                 + (f"{len(rows)} {sm._plural(len(rows), 'клиент', 'клиента', 'клиентов')} на одобрение" if rows
                    else "подходящих клиентов не нашлось"), object_id=r.id, user=user)
    db.commit()
    return {"ok": True, "proposed": len(rows), "master": r.name, "day": d.isoformat(),
            "actions": [sm.action_to_dict(a, c["contact"]) for a, c in zip(rows, found)]}
