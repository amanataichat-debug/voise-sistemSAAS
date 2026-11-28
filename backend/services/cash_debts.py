"""
Долги клиентов и отчёт дня (Voksy Beauty, шаг 3.2; решения владельца 27.11.2026 — docs/voksy_beauty_plan.md, разд. 9, 12).

Долг — визит «пришёл / завершён» с ценой, оплаченный в кассе не полностью:
  • считается только с первой смены салона (cashbox.cash_start) — до кассы выручка шла по визитам, долгов нет;
  • визит из нескольких услуг — ОДИН долг: цены пришедших услуг минус все оплаты и возвраты по визиту
    (деньги, принятые заранее за ещё не отмеченную услугу визита, тоже гасят долг);
  • отменённые и «не пришёл» — не долг; списанный владельцем (bookings.debt_off) — не долг.

Напомнить о долге — ТОЛЬКО вручную, кнопкой: одно сообщение клиенту через очередь отправок (notify_queue: окна по
Бишкеку, запасной канал, повторы), не чаще раза в день по визиту (idem_key debt:<визит>:<дата>), RU/KY по языку
клиента. Автоматической рассылки нет — клиенты обычно платят сразу.

Списать (простить) долг — только владелец, с причиной: debt_off у всех услуг визита ({at, reason, by, amount}),
строка в журнале действий; выручкой не считается, визит из долгов уходит.

Отчёт дня — владельцу в Telegram-бот агента и push, раз в день в выбранное время (booking_pages.cash_settings →
day_report {on, hour}, по умолчанию вкл., 21:00): выручка по способам, смены и расхождения, новые долги, неявки,
возвраты. Один раз за день на всех воркерах — атомарная отметка day_report_sent в cash_settings (как
claim_agent_job, но без агента: у салона бота может не быть). Дня без кассы и визитов в отчёте нет. Те же цифры —
карточка «Итог дня» на экране «Касса» (day_report).

Функции не делают commit (кроме тика) — коммитит API.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.services import booking_service as bs
from backend.services import cashbox
from backend.services.cashbox import CashError, Forbidden

logger = logging.getLogger(__name__)

EPS = cashbox.EPS
VISITED = ("arrived", "completed")
LIST_LIMIT = 200
REMIND_RULE = "debt"
DEFAULT_DAY_REPORT = {"on": True, "hour": 21}
REPORT_HOURS = list(range(17, 24))            # 17:00 … 23:00 по Бишкеку
WRITE_OFF_REASONS = ["Клиент не вернётся", "Подарок / комплимент", "Ошибка в цене", "Оплатил переводом владельцу"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
          "ноября", "декабря"]

REMIND_TEXT = {
    "ru": ("Здравствуйте{имя}! Это {салон}. Напоминаем об оплате визита {дата}{услуга}: осталось оплатить {сумма}."
           "{как} Если уже оплатили — напишите, пожалуйста, мы проверим. Спасибо!"),
    "ky": ("Саламатсызбы{имя}! Бул {салон}. {дата_ky} күнкү визит{услуга} үчүн {сумма} төлөнө элек."
           "{как} Эгер төлөп койгон болсоңуз, жазып коюңуз — текшерип беребиз. Рахмат!"),
}
HOW_TO_PAY = {"ru": " Оплатить можно так: {x}.", "ky": " Төлөө жолу: {x}."}


def _r(v) -> float:
    return round(float(v or 0) + 0.0, 2)


def _aw(dt: Optional[datetime]) -> Optional[datetime]:
    return cashbox._aw(dt)


def money(v) -> str:
    return cashbox.money_text(v)


def _day_words(dt: datetime) -> str:
    loc = bs.to_local(_aw(dt))
    return f"{loc.day} {MONTHS[loc.month - 1]}"


# ───────────────────────────── долги ─────────────────────────────

_DEBTS_SQL = """
WITH cand AS (
  SELECT DISTINCT COALESCE(visit_id, id) AS vk FROM bookings
  WHERE user_id = :u AND status IN ('arrived', 'completed') AND price > 0 AND start_at >= :t AND debt_off IS NULL
    AND (CAST(:c AS uuid) IS NULL OR contact_id = CAST(:c AS uuid))
    AND (CAST(:vk AS uuid) IS NULL OR COALESCE(visit_id, id) = CAST(:vk AS uuid))
), v AS (
  SELECT COALESCE(b.visit_id, b.id) AS vk,
         min(b.start_at) FILTER (WHERE b.status IN ('arrived', 'completed')) AS started,
         COALESCE(sum(b.price) FILTER (WHERE b.status IN ('arrived', 'completed')), 0) AS due,
         bool_or(b.debt_off IS NOT NULL) AS off
  FROM bookings b JOIN cand ON cand.vk = COALESCE(b.visit_id, b.id)
  WHERE b.user_id = :u
  GROUP BY 1
), p AS (
  SELECT COALESCE(b.visit_id, b.id) AS vk, COALESCE(sum(cp.amount), 0) AS paid
  FROM cash_payments cp JOIN bookings b ON b.id = cp.booking_id JOIN cand ON cand.vk = COALESCE(b.visit_id, b.id)
  WHERE cp.user_id = :u AND cp.kind IN ('payment', 'refund')
  GROUP BY 1
)
SELECT v.vk, v.started, v.due, COALESCE(p.paid, 0) AS paid
FROM v LEFT JOIN p ON p.vk = v.vk
WHERE NOT v.off AND v.started >= :t AND v.due - COALESCE(p.paid, 0) > 0.005
  AND (CAST(:a AS timestamptz) IS NULL OR v.started >= CAST(:a AS timestamptz))
  AND (CAST(:b AS timestamptz) IS NULL OR v.started < CAST(:b AS timestamptz))
ORDER BY v.started DESC
LIMIT :lim
"""


def _debt_rows(db: Session, user_id, *, contact_id=None, visit_key=None, since: Optional[datetime] = None,
               until: Optional[datetime] = None, limit: int = 1000) -> List[Tuple[Any, datetime, float, float]]:
    t = cashbox.cash_start(db, user_id)
    if t is None:
        return []
    rows = db.execute(text(_DEBTS_SQL), {
        "u": str(user_id), "t": t, "c": str(contact_id) if contact_id else None,
        "vk": str(visit_key) if visit_key else None, "a": since, "b": until, "lim": int(limit)}).fetchall()
    return [(r[0], _aw(r[1]), float(r[2] or 0), float(r[3] or 0)) for r in rows]


def _reminded(db: Session, user_id, booking_ids: List[str]) -> Dict[str, datetime]:
    if not booking_ids:
        return {}
    try:
        rows = db.execute(text(
            "SELECT booking_id, max(created_at) FROM notification_queue WHERE user_id = :u AND rule = :r "
            "AND booking_id = ANY(CAST(:ids AS uuid[])) GROUP BY 1"),
            {"u": str(user_id), "r": REMIND_RULE, "ids": booking_ids}).fetchall()
    except Exception:
        db.rollback()
        return {}
    return {str(b): _aw(t) for b, t in rows}


def _describe(db: Session, user_id, rows) -> List[Dict[str, Any]]:
    """Строки долга → «кто, за что, сколько, с какого дня»: первая пришедшая услуга визита — его лицо."""
    from sqlalchemy import and_, or_
    from backend.models.booking import Booking
    from backend.services.salon_clients import real_phone
    if not rows:
        return []
    keys = [r[0] for r in rows]
    parts = (db.query(Booking).filter(Booking.user_id == user_id,
                                      or_(Booking.visit_id.in_(keys), and_(Booking.visit_id.is_(None), Booking.id.in_(keys))))
             .order_by(Booking.start_at, Booking.id).all())
    by_key: Dict[str, List[Any]] = {}
    for p in parts:
        by_key.setdefault(str(p.visit_id or p.id), []).append(p)
    names = {str(r.id): r.name for r in bs.resources(db, user_id, active_only=False)}
    out: List[Dict[str, Any]] = []
    firsts: List[str] = []
    for vk, started, due, paid in rows:
        ps = by_key.get(str(vk)) or []
        came = [p for p in ps if p.status in VISITED] or ps
        if not came:
            continue
        head = came[0]
        firsts.append(str(head.id))
        masters = []
        for p in came:
            m = names.get(str(p.resource_id)) if p.resource_id else None
            if m and m not in masters:
                masters.append(m)
        out.append({
            "booking_id": str(head.id), "visit_id": str(head.visit_id) if head.visit_id else None,
            "contact_id": str(head.contact_id) if head.contact_id else None,
            "client_name": head.client_name or "Клиент", "phone": real_phone(head.client_phone),
            "services": [p.service_title or "Услуга" for p in came], "masters": masters,
            "date": bs.to_local(started).date().isoformat(), "start": started.isoformat(),
            "day_words": _day_words(started), "due": _r(due), "paid": _r(paid), "left": _r(due - paid),
        })
    rem = _reminded(db, user_id, firsts)
    today = bs.to_local(bs.now_utc()).date()
    for d in out:
        t = rem.get(d["booking_id"])
        d["reminded_at"] = t.isoformat() if t else None
        d["reminded_today"] = bool(t and bs.to_local(t).date() == today)
    return out


def debts(db: Session, user, *, contact_id=None, limit: int = LIST_LIMIT) -> Dict[str, Any]:
    """Долги салона (или одного клиента): список, сколько клиентов и сумма. Касса не открывалась — пусто."""
    rows = _debt_rows(db, user.id, contact_id=contact_id)
    described = _describe(db, user.id, rows)
    clients = {d["contact_id"] or d["phone"] or d["booking_id"] for d in described}
    return {"enabled": cashbox.cash_start(db, user.id) is not None,
            "items": described[:max(1, min(int(limit or LIST_LIMIT), 500))], "count": len(rows),
            "clients": len(clients), "total": _r(sum(due - paid for _vk, _s, due, paid in rows)),
            "can_take": cashbox.can_take(user), "can_write_off": can_write_off(user),
            "reasons": WRITE_OFF_REASONS}


def totals(db: Session, user_id) -> Dict[str, Any]:
    """Сколько должны салону — для главной и голоса (без подробностей)."""
    rows = _debt_rows(db, user_id)
    return {"count": len(rows), "total": _r(sum(due - paid for _vk, _s, due, paid in rows))}


def contact_debt(db: Session, user, contact_id) -> Optional[Dict[str, Any]]:
    """Долг одного клиента — в карточку клиента («Долг N сом» и «Принять оплату»)."""
    if not contact_id:
        return None
    rows = _debt_rows(db, user.id, contact_id=contact_id, limit=50)
    if not rows:
        return None
    items = _describe(db, user.id, rows)
    return {"total": _r(sum(d["left"] for d in items)), "items": items, "can_take": cashbox.can_take(user)}


def visit_left(db: Session, user_id, parts, paid: Optional[Dict[str, float]] = None) -> float:
    """Сколько осталось оплатить за визит (для «Моих записей»): 0 — не долг (до кассы, оплачен, списан)."""
    t = cashbox.cash_start(db, user_id)
    came = [p for p in parts if p.status in VISITED and p.price]
    if t is None or not came or any(getattr(p, "debt_off", None) for p in parts):
        return 0.0
    if min(_aw(p.start_at) for p in came) < t:
        return 0.0
    if paid is None:
        paid = cashbox.paid_by_booking(db, [p.id for p in parts])
    left = sum(float(p.price or 0) for p in came) - sum(paid.get(str(p.id), 0.0) for p in parts)
    return _r(left) if left > EPS else 0.0


def _debt_of_booking(db: Session, user_id, booking_id, lock: bool = False):
    """(запись-лицо визита, все услуги визита, строка долга или None)."""
    b, parts = cashbox._visit_parts(db, user_id, booking_id, lock=lock)
    vk = b.visit_id or b.id
    rows = _debt_rows(db, user_id, visit_key=vk, limit=1)
    return b, parts, (rows[0] if rows else None)


def can_write_off(user) -> bool:
    """Списать долг — только владелец (и вход поддержки в его кабинет), не сотрудник."""
    return not getattr(user, "_staff_member_id", None)


# ───────────────────────────── напомнить о долге ─────────────────────────────

def remind_text(lang: str, *, name: str, salon: str, when: datetime, services: List[str], amount: float,
                how: str = "") -> str:
    lang = lang if lang in REMIND_TEXT else "ru"
    svc = ", ".join(s for s in services if s)[:120]
    loc = bs.to_local(_aw(when))
    return REMIND_TEXT[lang].format(
        имя=f", {name}" if name else "", салон=salon or ("салон" if lang == "ru" else "салон"),
        дата=_day_words(when), дата_ky=loc.strftime("%d.%m"), услуга=f" ({svc})" if svc else "",
        сумма=money(amount), как=HOW_TO_PAY[lang].format(x=how.strip().rstrip(".")) if (how or "").strip() else "")


def remind(db: Session, user, booking_id, send: bool = False) -> Dict[str, Any]:
    """Напоминание клиенту о долге: send=False — только текст (окно предпросмотра), True — в очередь отправок."""
    from backend.models.agent_config import AgentConfig
    from backend.models.agent_contact import AgentContact
    from backend.services import activity, automations, inbox_service
    from backend.services import notify_queue as nq
    if not cashbox.can_take(user):
        raise Forbidden("Напомнить о долге может владелец или администратор (раздел «Онлайн-запись»)")
    b, parts, row = _debt_of_booking(db, user.id, booking_id)
    if row is None:
        raise CashError("Долга по этому визиту нет — он оплачен или списан", "no_debt")
    item = _describe(db, user.id, [row])[0]
    if not item["contact_id"]:
        raise CashError("У клиента нет карточки — напомните по телефону" + (f": {item['phone']}" if item["phone"] else ""),
                        "no_contact")
    contact = db.query(AgentContact).filter(AgentContact.id == uuid.UUID(item["contact_id"]),
                                            AgentContact.user_id == user.id).first()
    agent = db.query(AgentConfig).filter(AgentConfig.id == contact.agent_config_id).first() if contact else None
    avail = inbox_service.send_channels(db, contact, agent) if (contact and agent) else {}
    channel = inbox_service.default_channel(db, contact, avail) if avail else None
    if not channel:
        channel = next((c for c in nq.CHANNEL_ORDER if (avail.get(c) or {}).get("can_send")), None)
    if not channel:
        raise CashError("Клиенту некуда написать: нет переписки в WhatsApp, Telegram или Instagram — напомните по телефону"
                        + (f": {item['phone']}" if item["phone"] else ""), "no_channel")
    lang = automations.client_lang(db, contact.id, automations.limits_for(db, user.id).get("lang", "auto"))
    salon = automations._salon_values(db, user)
    cfg = bs.settings_of(bs.get_page(db, user, create=False))
    msg = remind_text(lang, name=automations._first_name(contact.name or item["client_name"]), salon=salon.get("салон") or "",
                      when=datetime.fromisoformat(item["start"]), services=item["services"], amount=item["left"],
                      how=cfg.get("prepay_info") or "")
    now = bs.now_utc()
    out = {"text": msg, "lang": lang, "channel": channel, "channel_title": nq.CHANNEL_TITLES.get(channel, channel),
           "left": item["left"], "reminded_today": item["reminded_today"],
           "later": not nq._window_ok("service", now)}
    if not send:
        return out
    if item["reminded_today"]:
        raise CashError("Сегодня этому клиенту уже напоминали — следующее напоминание можно завтра", "reminded")
    vk = str(b.visit_id or b.id)
    qid = nq.enqueue(db, user_id=user.id, contact_id=contact.id, rule=REMIND_RULE, text_=msg, lang=lang,
                     idem_key=f"debt:{vk}:{bs.to_local(now).date().isoformat()}", booking_id=item["booking_id"],
                     meta={"amount": item["left"]})
    if qid is None:
        raise CashError("Сегодня этому клиенту уже напоминали — следующее напоминание можно завтра", "reminded")
    activity.log(db, user.id, "debt.remind", "debt",
                 f"Напоминание о долге {money(item['left'])} · {item['client_name']} · визит {item['day_words']}",
                 object_id=item["booking_id"], user=user, client_name=item["client_name"],
                 meta={"booking_id": item["booking_id"], "visit_id": item["visit_id"], "amount": item["left"],
                       "date": item["date"], "channel": channel})
    out["queued"] = True
    return out


# ───────────────────────────── списать долг ─────────────────────────────

def write_off(db: Session, user, booking_id, reason: Optional[str]) -> Dict[str, Any]:
    """Простить долг (только владелец, с причиной): визит больше не долг, выручкой не считается."""
    from backend.services import activity
    if not can_write_off(user):
        raise Forbidden("Списать долг может только владелец")
    why = (reason or "").strip()
    if len(why) < 3:
        raise CashError("Напишите причину — она останется в журнале действий", "reason")
    b, parts, row = _debt_of_booking(db, user.id, booking_id, lock=True)
    if row is None:
        raise CashError("Долга по этому визиту нет — он оплачен или уже списан", "no_debt")
    item = _describe(db, user.id, [row])[0]
    who = cashbox.cashier_of(user)
    mark = {"at": bs.now_utc().isoformat(), "reason": why[:300], "by": who["name"], "amount": item["left"]}
    for p in parts:
        p.debt_off = dict(mark)
    activity.log(db, user.id, "debt.write_off", "debt",
                 f"Долг списан: {money(item['left'])} · {item['client_name']} · визит {item['day_words']} · причина: {why[:200]}",
                 object_id=item["booking_id"], user=user, client_name=item["client_name"],
                 meta={"booking_id": item["booking_id"], "visit_id": item["visit_id"], "amount": item["left"],
                       "date": item["date"], "reason": why[:300]})
    bs.notify_changed(db, user.id)
    cashbox._invalidate(user.id)
    db.flush()
    return {"ok": True, "booking_id": item["booking_id"], "amount": item["left"]}


# ───────────────────────────── итог дня ─────────────────────────────

def day_report(db: Session, user_id, day: Optional[str] = None) -> Dict[str, Any]:
    """«Итог дня» (экран «Касса», отчёт владельцу, голос): деньги по способам, смены, новые долги, неявки, возвраты."""
    from backend.models.booking import Booking
    from backend.models.cashbox import CashShift
    d, a, b = cashbox.day_range(day)
    nums = cashbox.day_numbers(db, user_id, d.isoformat())
    shifts = []
    for sh in (db.query(CashShift).filter(CashShift.user_id == user_id, CashShift.opened_at < b)
               .filter((CashShift.closed_at.is_(None)) | (CashShift.closed_at >= a))
               .order_by(CashShift.opened_at).all()):
        if sh.status == "open" and _aw(sh.opened_at) < a - timedelta(days=3):
            continue                                  # забытая смена — у неё своя строка «Смена не закрыта»
        shifts.append({"id": str(sh.id), "cashier": sh.opened_by_name or "Кассир", "status": sh.status,
                       "opened_at": _aw(sh.opened_at).isoformat(),
                       "closed_at": _aw(sh.closed_at).isoformat() if sh.closed_at else None,
                       "diff": _r(sh.diff) if sh.diff is not None else None})
    rows = _debt_rows(db, user_id, since=a, until=b, limit=200)
    new_debts = _describe(db, user_id, rows[:20])
    day_rows = (db.query(Booking).filter(Booking.user_id == user_id, Booking.start_at >= a, Booking.start_at < b,
                                         Booking.status.in_(("arrived", "completed", "no_show"))).all())
    no_shows = sorted([x for x in day_rows if x.status == "no_show"], key=lambda x: x.start_at)
    visits = len({str(x.visit_id or x.id) for x in day_rows if x.status in VISITED})
    diffs = [s["diff"] for s in shifts if s["status"] == "closed" and s["diff"] is not None and abs(s["diff"]) >= 1]
    return {
        "date": d.isoformat(), "day_words": _day_words(a + timedelta(hours=12)),
        "revenue": nums["revenue"], "payments": nums["payments"], "refunds": nums["refunds"],
        "by_method": nums["by_method"], "receipts": nums["receipts"], "visits": visits,
        "shifts": shifts, "open_shifts": sum(1 for s in shifts if s["status"] == "open"), "diff_total": _r(sum(diffs)),
        "diffs": len(diffs),
        "new_debts": {"count": len(rows), "total": _r(sum(due - paid for _vk, _s, due, paid in rows)), "items": new_debts},
        "no_shows": {"count": len(no_shows), "items": [{"client": x.client_name or "Клиент", "service": x.service_title,
                                                         "time": bs.to_local(_aw(x.start_at)).strftime("%H:%M")}
                                                        for x in no_shows[:10]]},
        "empty": not (nums["receipts"] or nums["refunds"] or shifts or visits or no_shows),
    }


def report_text(rep: Dict[str, Any], salon: str = "") -> Tuple[str, str]:
    """(заголовок для push, текст для Telegram)."""
    titles = cashbox.METHOD_TITLES
    by = rep.get("by_method") or {}
    ways = " · ".join(f"{titles[k].lower()} {money(v)}" for k, v in by.items() if abs(v or 0) >= 0.5)
    lines = [f"📊 Итог дня · {rep['day_words']}" + (f" · {salon}" if salon else ""),
             f"Выручка: {money(rep['revenue'])}" + (f" ({ways})" if ways else "")]
    if rep.get("refunds"):
        lines.append(f"Возвраты: −{money(rep['refunds'])}")
    sh = rep.get("shifts") or []
    if sh:
        parts = []
        for s in sh:
            if s["status"] == "open":
                parts.append(f"{s['cashier']} — смена не закрыта")
            elif s["diff"] is None or abs(s["diff"]) < 1:
                parts.append(f"{s['cashier']} — сходится")
            else:
                parts.append(f"{s['cashier']} — {'лишние' if s['diff'] > 0 else 'не хватает'} {money(abs(s['diff']))}")
        lines.append("Смены: " + "; ".join(parts))
    nd = rep.get("new_debts") or {}
    if nd.get("count"):
        who = ", ".join(f"{x['client_name']} — {money(x['left'])}" for x in (nd.get("items") or [])[:4])
        lines.append(f"Новые долги: {nd['count']} · {money(nd['total'])}" + (f" ({who})" if who else ""))
    ns = rep.get("no_shows") or {}
    if ns.get("count"):
        who = ", ".join(f"{x['client']} {x['time']}" for x in (ns.get("items") or [])[:4])
        lines.append(f"Не пришли: {ns['count']}" + (f" ({who})" if who else ""))
    lines.append("Подробно — в разделе «Касса».")
    head = f"Выручка {money(rep['revenue'])}"
    if nd.get("count"):
        head += f" · долги {money(nd['total'])}"
    if ns.get("count"):
        head += f" · не пришли {ns['count']}"
    if rep.get("diffs"):
        head += " · есть расхождение"
    return head, "\n".join(lines)


# ───────────────────────────── настройки и тик отчёта ─────────────────────────────

def _cfg_raw(db: Session, user_id) -> Dict[str, Any]:
    try:
        v = db.execute(text("SELECT cash_settings FROM booking_pages WHERE user_id = :u"), {"u": str(user_id)}).scalar()
    except Exception:
        db.rollback()
        return {}
    return dict(v) if isinstance(v, dict) else {}


def day_report_settings(db: Session, user_id) -> Dict[str, Any]:
    raw = (_cfg_raw(db, user_id).get("day_report") or {})
    out = dict(DEFAULT_DAY_REPORT)
    if isinstance(raw.get("on"), bool):
        out["on"] = raw["on"]
    try:
        h = int(raw.get("hour"))
        if h in REPORT_HOURS:
            out["hour"] = h
    except (TypeError, ValueError):
        pass
    return out


def save_day_report_settings(db: Session, user, data: Dict[str, Any]) -> Dict[str, Any]:
    from backend.services import activity
    if getattr(user, "_staff_member_id", None):
        raise Forbidden("Отчёт дня настраивает владелец")
    cur = day_report_settings(db, user.id)
    new = dict(cur)
    if "on" in data:
        new["on"] = bool(data.get("on"))
    if "hour" in data:
        try:
            h = int(data.get("hour"))
        except (TypeError, ValueError):
            raise CashError("Выберите время отчёта")
        if h not in REPORT_HOURS:
            raise CashError("Отчёт приходит с 17:00 до 23:00 — выберите время из списка")
        new["hour"] = h
    page = bs.get_page(db, user, create=True)
    db.execute(text("UPDATE booking_pages SET cash_settings = jsonb_set(COALESCE(cash_settings, '{}'::jsonb), "
                    "'{day_report}', CAST(:v AS jsonb)) WHERE user_id = :u"),
               {"v": __import__("json").dumps(new), "u": str(page.user_id)})
    if new != cur:
        activity.log(db, user.id, "cash.settings", "cash",
                     ("Отчёт дня: каждый день в " + f"{new['hour']}:00") if new["on"] else "Отчёт дня выключен",
                     user=user, changes={"day_report": {"old": cur, "new": new}})
    return new


def claim_day_report(db: Session, user_id, day: date) -> bool:
    """Атомарно отметить «отчёт за этот день отправлен». True — этот воркер его и отправляет."""
    try:
        row = db.execute(text(
            "UPDATE booking_pages SET cash_settings = jsonb_set(COALESCE(cash_settings, '{}'::jsonb), "
            "'{day_report_sent}', to_jsonb(CAST(:d AS text))) "
            "WHERE user_id = :u AND COALESCE(cash_settings ->> 'day_report_sent', '') <> :d RETURNING user_id"),
            {"d": day.isoformat(), "u": str(user_id)}).first()
        db.commit()
        return row is not None
    except Exception as e:
        db.rollback()
        logger.warning(f"[CASH] claim day report {user_id}: {e}")
        return False


def _cash_salons(db: Session) -> List[Tuple[Any, Any]]:
    """Салоны, которые работают через кассу (была смена или чек), с настройками отчёта."""
    return db.execute(text(
        "SELECT p.user_id, p.cash_settings FROM booking_pages p WHERE "
        "EXISTS (SELECT 1 FROM cash_shifts s WHERE s.user_id = p.user_id) OR "
        "EXISTS (SELECT 1 FROM cash_receipts r WHERE r.user_id = p.user_id)")).fetchall()


async def deliver_day_report(db: Session, user, rep: Dict[str, Any]) -> Dict[str, bool]:
    from backend.services import automations, owner_notifications, push_service
    salon = automations._salon_values(db, user).get("салон") or ""
    head, body = report_text(rep, salon)
    out = {"telegram": False, "push": False}
    agent = bs._first_agent(db, user.id)
    try:
        if agent is not None and getattr(agent, "telegram_enabled", False):
            out["telegram"] = await owner_notifications._send(agent, db, body)
    except Exception as e:
        logger.warning(f"[CASH] day report telegram: {e}")
    try:
        out["push"] = bool(await push_service.send_to_user(user.id, "Итог дня", head, "/static/cash.html", tag="cash-day"))
    except Exception as e:
        logger.warning(f"[CASH] day report push: {e}")
    return out


DELIVER = deliver_day_report        # тесты подменяют


async def tick(db: Session, now: Optional[datetime] = None) -> int:
    """Тик weekly_jobs (раз в 5 минут): кому пора — собрать итог дня и отправить владельцу один раз."""
    from backend.models.user import User
    from backend.services.booking_tools import booking_enabled
    loc = bs.to_local(now or bs.now_utc())
    sent = 0
    try:
        salons = _cash_salons(db)
    except Exception as e:
        db.rollback()
        logger.warning(f"[CASH] day report salons: {e}")
        return 0
    for uid, raw in salons:
        try:
            cfg = dict(DEFAULT_DAY_REPORT)
            r = (raw or {}).get("day_report") if isinstance(raw, dict) else None
            if isinstance(r, dict):
                cfg.update({k: r[k] for k in ("on", "hour") if k in r})
            if not cfg.get("on") or loc.hour < int(cfg.get("hour") or 21):
                continue
            if isinstance(raw, dict) and raw.get("day_report_sent") == loc.date().isoformat():
                continue
            user = db.query(User).filter(User.id == uid).first()
            if user is None or user.agent_mode() is None or not booking_enabled(db, user):
                continue
            if not claim_day_report(db, uid, loc.date()):
                continue
            rep = day_report(db, uid, loc.date().isoformat())
            if rep["empty"]:
                continue
            await DELIVER(db, user, rep)
            sent += 1
        except Exception as e:
            db.rollback()
            logger.error(f"[CASH] day report {uid}: {e}", exc_info=True)
    return sent


# ───────────────────────────── владельцу: голос и чат ─────────────────────────────

def owner_summary(db: Session, user, day: Optional[str] = None) -> Dict[str, Any]:
    """«Сколько сегодня в кассе?», «кто должен?» — одним ответом для голоса и чата владельца."""
    if cashbox.cash_start(db, user.id) is None:
        return {"ok": True, "enabled": False,
                "message": "Касса ещё не открывалась: оплаты не принимались, долгов нет. Касса — раздел «Касса» в кабинете."}
    d = None
    if day in (None, "", "today"):
        d = None
    elif day == "yesterday":
        d = (bs.to_local(bs.now_utc()).date() - timedelta(days=1)).isoformat()
    else:
        d = str(day)[:10]
    try:
        rep = day_report(db, user.id, d)
    except CashError:
        rep = day_report(db, user.id, None)
    rows = _debt_rows(db, user.id, limit=500)
    items = _describe(db, user.id, rows[:10])
    total = _r(sum(due - paid for _vk, _s, due, paid in rows))
    head, body = report_text(rep)
    debts_line = (f"Должны {len(rows)} {_plural(len(rows), 'визит', 'визита', 'визитов')} на {money(total)}: "
                  + ", ".join(f"{x['client_name']} — {money(x['left'])} ({x['day_words']})" for x in items[:6])
                  if rows else "Долгов нет.")
    return {
        "ok": True, "enabled": True, "day": rep["date"], "day_words": rep["day_words"], "revenue": rep["revenue"],
        "by_method": rep["by_method"], "refunds": rep["refunds"], "shifts": rep["shifts"], "no_shows": rep["no_shows"],
        "new_debts": {k: rep["new_debts"][k] for k in ("count", "total")},
        "debts": {"count": len(rows), "total": total,
                  "items": [{"client": x["client_name"], "left": x["left"], "day": x["day_words"],
                             "services": ", ".join(x["services"])} for x in items]},
        "summary": body.replace("📊 ", "") + "\n" + debts_line,
    }


def _plural(n: int, one: str, few: str, many: str) -> str:
    a, b = abs(n) % 100, abs(n) % 10
    if 10 < a < 20:
        return many
    return one if b == 1 else few if 2 <= b <= 4 else many
