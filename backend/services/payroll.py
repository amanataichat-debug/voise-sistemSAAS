"""
Зарплата мастеров v2 (Voksy Beauty, шаг 3.3; решения владельца 27.11.2026 — docs/voksy_beauty_plan.md, разд. 9, 12).

Правило владельца: без лишних функций, только необходимое. Схема мастера (booking_resources) — любые части вместе:
  • % с услуг: общий (pay_percent) и свой по категориям (pay_categories: {«Волосы / Окрашивание»: 40, «Ногти»: 35} —
    сначала точная категория услуги, потом её верхняя категория, иначе общий %);
  • ставка за выход (pay_shift) × отмеченные выходы (master_shifts; отмечает администратор в журнале записи, сам
    выход не ставится — мастер с записями только подсказывается);
  • оклад в месяц (pay_fixed) — доля по дням периода (finance_service.month_share).

База процента — ОПЛАЧЕННЫЕ услуги мастера: деньги кассы по его записям (cash_payments.resource_id) минус возвраты,
по дате оплаты; до первой смены салона (cashbox.cash_start) — цена визитов «пришёл / завершён», как раньше; визит,
начавшийся до кассы и оплаченный после, дважды не считается (то же правило, что у выручки). Материалы не вычитаются.

Руками: аванс / штраф / премия (payroll_adjustments). Начислено = % + выходы + оклад + премии − штрафы;
к выплате = начислено − авансы − выплачено за этот период (выплаты, период которых лежит внутри ведомости).
«Выплатить» — наличными (изъятие в смене того, кто платит: cashbox.cash_move) или переводом; выплата хранит снимок
строки ведомости. Ведомость и выплаты — владелец и бухгалтер (раздел «Финансы»), выход — ещё и администратор
(раздел «Онлайн-запись»). Всё — в журнале действий (группа «Деньги»).

Одна реализация на «Зарплату», «Финансы» (зарплаты как расход) и кабинет мастера. Функции без commit — коммитит API.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.services import booking_service as bs
from backend.services import cashbox
from backend.services.cashbox import CashError, Forbidden, NotFound

logger = logging.getLogger(__name__)

KIND_TITLES = {"advance": "Аванс", "fine": "Штраф", "bonus": "Премия"}
METHOD_TITLES = {"cash": "Наличными из кассы", "transfer": "Переводом"}
MAX_PERIOD_DAYS = 92
MAX_AMOUNT = 10_000_000.0
EPS = 0.005


def _r(v) -> float:
    return round(float(v or 0) + 0.0, 2)


def money(v) -> str:
    return cashbox.money_text(v)


def _uuid(v, what: str = "Мастер не найден") -> uuid.UUID:
    try:
        return v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))
    except (TypeError, ValueError):
        raise NotFound(what)


def can_manage(user) -> bool:
    """Ведомость, авансы/штрафы/премии, выплаты, схема оплаты — владелец и бухгалтер (раздел «Финансы»)."""
    return cashbox.can_manage(user)


def can_mark(user) -> bool:
    """Отметить выход мастера — владелец, администратор («Онлайн-запись») и бухгалтер."""
    return cashbox.can_take(user) or cashbox.can_manage(user)


def _need_manage(user) -> None:
    if not can_manage(user):
        raise Forbidden("Зарплату ведут владелец и бухгалтер (раздел «Финансы»)")


# ───────────────────────────── период ─────────────────────────────

def parse_period(date_from: Optional[str], date_to: Optional[str], today: Optional[date] = None) -> Tuple[date, date]:
    """Период ведомости (даты Бишкека, включительно). По умолчанию — с 1-го числа по сегодня."""
    today = today or bs.to_local(bs.now_utc()).date()
    try:
        a = date.fromisoformat(str(date_from)[:10]) if date_from else today.replace(day=1)
        b = date.fromisoformat(str(date_to)[:10]) if date_to else max(a, today)
    except ValueError:
        raise CashError("Неверная дата периода")
    if b < a:
        raise CashError("Конец периода раньше начала")
    if (b - a).days + 1 > MAX_PERIOD_DAYS:
        raise CashError(f"Период ведомости — не больше {MAX_PERIOD_DAYS} дней")
    return a, b


def _bounds(a: date, b: date) -> Tuple[datetime, datetime]:
    return bs.day_bounds(a)[0], bs.day_bounds(b)[1]


def period_label(a: date, b: date) -> str:
    months = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
    if a == b:
        return f"{a.day} {months[a.month - 1]}"
    if (a.year, a.month) == (b.year, b.month):
        return f"{a.day}–{b.day} {months[b.month - 1]}"
    return f"{a.day} {months[a.month - 1]} – {b.day} {months[b.month - 1]}"


# ───────────────────────────── схема мастера ─────────────────────────────

def scheme_of(r) -> Dict[str, Any]:
    cats = getattr(r, "pay_categories", None)
    cats = {str(k): float(v) for k, v in (cats or {}).items() if v not in (None, "")} if isinstance(cats, dict) else {}
    return {"percent": float(getattr(r, "pay_percent", None) or 0), "categories": cats,
            "shift": float(getattr(r, "pay_shift", None) or 0), "fixed": float(getattr(r, "pay_fixed", None) or 0)}


def pct_for(scheme: Dict[str, Any], collection: Optional[str]) -> float:
    """Процент мастера за услугу этой категории: точная категория → её верхняя категория → общий %."""
    cats = scheme.get("categories") or {}
    col = " ".join(str(collection or "").split())
    if col and col in cats:
        return float(cats[col])
    top = bs.split_category(col)[0] if col else ""
    if top and top in cats:
        return float(cats[top])
    return float(scheme.get("percent") or 0)


def configured(scheme: Dict[str, Any]) -> bool:
    return bool(scheme["percent"] or scheme["categories"] or scheme["shift"] or scheme["fixed"])


def scheme_text(scheme: Dict[str, Any]) -> str:
    parts = []
    if scheme["percent"]:
        parts.append(f"{scheme['percent']:g}% с услуг")
    for k, v in (scheme.get("categories") or {}).items():
        parts.append(f"{k} — {v:g}%")
    if scheme["shift"]:
        parts.append(f"выход {money(scheme['shift'])}")
    if scheme["fixed"]:
        parts.append(f"оклад {money(scheme['fixed'])}")
    return ", ".join(parts) or "не задана"


def _num(v: float) -> str:
    return f"{v:,.0f}".replace(",", " ") if float(v).is_integer() else f"{v:g}"


def _clean_scheme(data: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    new = dict(current)

    def num(v, lo, hi, what):
        if v in (None, ""):
            return 0.0
        try:
            f = float(str(v).replace(" ", "").replace(",", "."))
        except (TypeError, ValueError):
            raise CashError(f"{what}: укажите число")
        if f < lo or f > hi:
            raise CashError(f"{what}: от {_num(lo)} до {_num(hi)}")
        return f
    if "percent" in data:
        new["percent"] = num(data.get("percent"), 0, 100, "Процент с услуг")
    if "shift" in data:
        new["shift"] = num(data.get("shift"), 0, 1_000_000, "Ставка за выход")
    if "fixed" in data:
        new["fixed"] = num(data.get("fixed"), 0, 100_000_000, "Оклад")
    if "categories" in data:
        raw = data.get("categories") or {}
        if isinstance(raw, list):
            raw = {str((x or {}).get("category") or ""): (x or {}).get("percent") for x in raw}
        if not isinstance(raw, dict):
            raise CashError("Проценты по категориям — список «категория: %»")
        cats: Dict[str, float] = {}
        for k, v in raw.items():
            name = " ".join(str(k or "").split())[:120]
            if not name or v in (None, ""):
                continue
            cats[name] = num(v, 0, 100, f"Процент «{name}»")
        if len(cats) > 40:
            raise CashError("Не больше 40 категорий со своим процентом")
        new["categories"] = cats
    return new


def save_scheme(db: Session, user, resource_id, data: Dict[str, Any]) -> Dict[str, Any]:
    """Схема оплаты мастера: общий %, % по категориям, ставка за выход, оклад (журнал действий — было → стало)."""
    from backend.models.booking import BookingResource
    from backend.services import activity
    _need_manage(user)
    r = db.query(BookingResource).filter(BookingResource.id == _uuid(resource_id), BookingResource.user_id == user.id).first()
    if r is None:
        raise NotFound("Мастер не найден")
    cur = scheme_of(r)
    new = _clean_scheme(data or {}, cur)
    r.pay_percent = new["percent"] or None
    r.pay_shift = new["shift"] or None
    r.pay_fixed = new["fixed"] or None
    r.pay_categories = new["categories"] or None
    if new != cur:
        activity.log(db, user.id, "payroll.update", "payroll", f"{r.name}: {scheme_text(cur)} → {scheme_text(new)}",
                     object_id=r.id, user=user, changes={"scheme": [cur, new]})
    db.flush()
    _invalidate(user.id)
    return {"id": str(r.id), "name": r.name, "scheme": new, "scheme_text": scheme_text(new)}


def categories(db: Session, user_id) -> List[str]:
    """Категории услуг салона для «своего % по категориям»: верхние и с подкатегорией."""
    rows = db.execute(text("SELECT DISTINCT collection FROM agent_knowledge_entries WHERE user_id = :u AND kind = 'product' "
                           "AND collection IS NOT NULL AND collection <> ''"), {"u": str(user_id)}).fetchall()
    out: List[str] = []
    for (col,) in rows:
        top, sub = bs.split_category(col)
        if top and top not in out:
            out.append(top)
        if sub:
            full = f"{top} / {sub}"
            if full not in out:
                out.append(full)
    return sorted(out, key=lambda x: (bs.split_category(x)[0].lower(), x.lower()))


# ───────────────────────────── выходы ─────────────────────────────

def attendance(db: Session, user, day: date) -> Dict[str, Any]:
    """«Кто сегодня работает»: мастера дня, отмечен ли выход, подсказка (есть записи), ставка."""
    from backend.models.payroll import MasterShift
    a, b = bs.day_bounds(day)
    masters = bs.resources(db, user.id)
    marked = {str(x.resource_id): x for x in db.query(MasterShift).filter(MasterShift.user_id == user.id, MasterShift.day == day).all()}
    busy = {str(rid) for (rid,) in db.execute(text(
        "SELECT DISTINCT resource_id FROM bookings WHERE user_id = :u AND start_at >= :a AND start_at < :b "
        "AND status <> 'cancelled' AND resource_id IS NOT NULL"), {"u": str(user.id), "a": a, "b": b}).fetchall()}
    return {"date": day.isoformat(), "can_mark": can_mark(user),
            "any_rate": any((m.pay_shift or 0) > 0 for m in masters),
            "masters": [{"id": str(m.id), "name": m.name, "color": m.color, "rate": float(m.pay_shift or 0),
                         "worked": str(m.id) in marked, "suggested": str(m.id) in busy,
                         "by": marked[str(m.id)].marked_by_name if str(m.id) in marked else None} for m in masters]}


def set_attendance(db: Session, user, resource_id, day: date, worked: bool) -> Dict[str, Any]:
    from backend.models.booking import BookingResource
    from backend.models.payroll import MasterShift
    from backend.services import activity
    if not can_mark(user):
        raise Forbidden("Выход мастера отмечает администратор или владелец")
    r = db.query(BookingResource).filter(BookingResource.id == _uuid(resource_id), BookingResource.user_id == user.id).first()
    if r is None:
        raise NotFound("Мастер не найден")
    today = bs.to_local(bs.now_utc()).date()
    if day > today + timedelta(days=1):
        raise CashError("Выход отмечают за прошедший день, сегодня или завтра")
    row = db.query(MasterShift).filter(MasterShift.resource_id == r.id, MasterShift.day == day).first()
    who = cashbox.cashier_of(user)["name"]
    changed = False
    if worked and row is None:
        sp = db.begin_nested()
        try:
            db.add(MasterShift(id=uuid.uuid4(), user_id=user.id, resource_id=r.id, day=day, marked_by_name=(who or "")[:120]))
            db.flush()
            sp.commit()
            changed = True
        except IntegrityError:           # то же нажатие из другой вкладки — выход уже отмечен
            sp.rollback()
    elif not worked and row is not None:
        db.delete(row)
        db.flush()
        changed = True
    if changed:
        activity.log(db, user.id, "payroll.shift", "payroll",
                     f"{r.name}: {'вышел на работу' if worked else 'выход снят'} · {day.strftime('%d.%m')}",
                     object_id=r.id, user=user, meta={"date": day.isoformat(), "worked": worked})
        _invalidate(user.id)
    return {"id": str(r.id), "date": day.isoformat(), "worked": worked}


# ───────────────────────────── ведомость ─────────────────────────────

def _base(db: Session, user_id, a: date, b: date, rids: List[str]) -> Dict[str, Dict[str, Any]]:
    """База процента по мастерам и категориям: оплаты кассы (после её начала) + визиты до начала кассы."""
    s, u = _bounds(a, b)
    t = cashbox.cash_start(db, user_id)
    out: Dict[str, Dict[str, Any]] = {rid: {"by_cat": {}, "visits": 0, "paid_visits": 0} for rid in rids}
    p = {"u": str(user_id), "s": s, "e": u, "t": t, "ids": rids}
    rows = db.execute(text(
        "SELECT b.resource_id, e.collection, COALESCE(sum(b.price), 0), count(*) FROM bookings b "
        "LEFT JOIN agent_knowledge_entries e ON e.id = b.service_entry_id "
        "WHERE b.user_id = :u AND b.status IN ('arrived', 'completed') AND b.start_at >= :s AND b.start_at < :e "
        "AND (CAST(:t AS timestamptz) IS NULL OR b.start_at < CAST(:t AS timestamptz)) "
        "AND b.resource_id = ANY(CAST(:ids AS uuid[])) GROUP BY 1, 2"), p).fetchall()
    for rid, col, total, n in rows:
        o = out.setdefault(str(rid), {"by_cat": {}, "visits": 0, "paid_visits": 0})
        o["by_cat"][col or ""] = o["by_cat"].get(col or "", 0.0) + float(total or 0)
    if t is not None:
        rows = db.execute(text(
            "SELECT cp.resource_id, e.collection, COALESCE(sum(cp.amount), 0) FROM cash_payments cp "
            "JOIN bookings b ON b.id = cp.booking_id LEFT JOIN agent_knowledge_entries e ON e.id = b.service_entry_id "
            "WHERE cp.user_id = :u AND cp.kind IN ('payment', 'refund') AND cp.created_at >= :s AND cp.created_at < :e "
            "AND b.start_at >= CAST(:t AS timestamptz) AND cp.resource_id = ANY(CAST(:ids AS uuid[])) GROUP BY 1, 2"), p).fetchall()
        for rid, col, total in rows:
            o = out.setdefault(str(rid), {"by_cat": {}, "visits": 0, "paid_visits": 0})
            o["by_cat"][col or ""] = o["by_cat"].get(col or "", 0.0) + float(total or 0)
    for rid, n in db.execute(text(
            "SELECT resource_id, count(DISTINCT COALESCE(visit_id, id)) FROM bookings WHERE user_id = :u "
            "AND status IN ('arrived', 'completed') AND start_at >= :s AND start_at < :e "
            "AND resource_id = ANY(CAST(:ids AS uuid[])) GROUP BY 1"), p).fetchall():
        out.setdefault(str(rid), {"by_cat": {}, "visits": 0, "paid_visits": 0})["visits"] = int(n or 0)
    return out


def _shifts(db: Session, user_id, a: date, b: date, rids: List[str]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {rid: [] for rid in rids}
    for rid, d in db.execute(text(
            "SELECT resource_id, day FROM master_shifts WHERE user_id = :u AND day >= :a AND day <= :b "
            "AND resource_id = ANY(CAST(:ids AS uuid[])) ORDER BY day"),
            {"u": str(user_id), "a": a, "b": b, "ids": rids}).fetchall():
        out.setdefault(str(rid), []).append(d.isoformat())
    return out


def _adjustments(db: Session, user_id, a: date, b: date, rids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    from backend.models.payroll import PayrollAdjustment
    out: Dict[str, List[Dict[str, Any]]] = {rid: [] for rid in rids}
    rows = (db.query(PayrollAdjustment).filter(PayrollAdjustment.user_id == user_id, PayrollAdjustment.day >= a,
                                               PayrollAdjustment.day <= b,
                                               PayrollAdjustment.resource_id.in_([uuid.UUID(x) for x in rids]))
            .order_by(PayrollAdjustment.day, PayrollAdjustment.created_at).all()) if rids else []
    for x in rows:
        out.setdefault(str(x.resource_id), []).append({
            "id": str(x.id), "kind": x.kind, "title": KIND_TITLES.get(x.kind, x.kind), "day": x.day.isoformat(),
            "amount": _r(x.amount), "comment": x.comment or "", "by": x.created_by_name or ""})
    return out


def _payouts(db: Session, user_id, a: date, b: date, rids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """Выплаты, период которых лежит внутри ведомости (выплатили за 1–15 — видно и в ведомости за месяц)."""
    from backend.models.payroll import PayrollPayout
    out: Dict[str, List[Dict[str, Any]]] = {rid: [] for rid in rids}
    rows = (db.query(PayrollPayout).filter(PayrollPayout.user_id == user_id, PayrollPayout.date_from >= a,
                                           PayrollPayout.date_to <= b,
                                           PayrollPayout.resource_id.in_([uuid.UUID(x) for x in rids]))
            .order_by(PayrollPayout.created_at).all()) if rids else []
    for x in rows:
        out.setdefault(str(x.resource_id), []).append(payout_dict(x))
    return out


def payout_dict(x) -> Dict[str, Any]:
    return {"id": str(x.id), "amount": _r(x.amount), "method": x.method, "method_title": METHOD_TITLES.get(x.method, x.method),
            "from": x.date_from.isoformat(), "to": x.date_to.isoformat(), "period": period_label(x.date_from, x.date_to),
            "at": cashbox._aw(x.created_at).isoformat() if x.created_at else None, "by": x.created_by_name or "",
            "comment": x.comment or "", "receipt_id": str(x.receipt_id) if x.receipt_id else None}


def _line(r, a: date, b: date, base: Dict[str, Any], shifts: List[str], adj: List[Dict[str, Any]],
          payouts: List[Dict[str, Any]]) -> Dict[str, Any]:
    from backend.services.finance_service import month_share
    sc = scheme_of(r)
    by_cat = []
    pct_amount = 0.0
    total_base = 0.0
    for col, amount in sorted((base.get("by_cat") or {}).items(), key=lambda kv: -kv[1]):
        pct = pct_for(sc, col)
        part = amount * pct / 100.0
        pct_amount += part
        total_base += amount
        by_cat.append({"category": col or "Без категории", "sum": _r(amount), "pct": pct, "amount": _r(part),
                       "own": bool(col) and pct != sc["percent"]})
    started = bs.to_local(cashbox._aw(r.created_at)).date() if getattr(r, "created_at", None) else a
    fixed_amount = month_share(sc["fixed"], a, b, min(started, a), None) if sc["fixed"] else 0.0
    shift_amount = len(shifts) * sc["shift"]
    sums = {k: sum(x["amount"] for x in adj if x["kind"] == k) for k in KIND_TITLES}
    accrued = pct_amount + shift_amount + fixed_amount + sums["bonus"] - sums["fine"]
    paid = sum(x["amount"] for x in payouts)
    to_pay = accrued - sums["advance"] - paid
    return {
        "id": str(r.id), "name": r.name, "color": r.color, "role": r.role, "active": bool(r.is_active),
        "scheme": sc, "scheme_text": scheme_text(sc), "configured": configured(sc),
        "visits": int(base.get("visits") or 0), "base": _r(total_base), "by_cat": by_cat, "percent_amount": _r(pct_amount),
        "shifts": {"count": len(shifts), "rate": sc["shift"], "amount": _r(shift_amount), "days": shifts},
        "fixed_amount": _r(fixed_amount), "bonuses": _r(sums["bonus"]), "fines": _r(sums["fine"]),
        "advances": _r(sums["advance"]), "accrued": _r(accrued), "paid": _r(paid),
        "to_pay": _r(to_pay) if abs(to_pay) >= EPS else 0.0, "adjustments": adj, "payouts": payouts,
        "closed": bool(payouts) and abs(to_pay) < 0.5,
    }


def sheet(db: Session, user, a: date, b: date, resource_id=None, check: bool = True) -> Dict[str, Any]:
    """Ведомость за период: по каждому мастеру (или одному) — начислено, авансы, выплачено, к выплате."""
    if check:
        _need_manage(user)
    masters = bs.resources(db, user.id, active_only=False)
    if resource_id is not None:
        masters = [m for m in masters if str(m.id) == str(resource_id)]
        if not masters:
            raise NotFound("Мастер не найден")
    rids = [str(m.id) for m in masters]
    base = _base(db, user.id, a, b, rids) if rids else {}
    shifts = _shifts(db, user.id, a, b, rids) if rids else {}
    adj = _adjustments(db, user.id, a, b, rids)
    pays = _payouts(db, user.id, a, b, rids)
    lines = []
    for m in masters:
        ln = _line(m, a, b, base.get(str(m.id)) or {}, shifts.get(str(m.id)) or [], adj.get(str(m.id)) or [],
                   pays.get(str(m.id)) or [])
        if not m.is_active and not (ln["base"] or ln["shifts"]["count"] or ln["adjustments"] or ln["payouts"]):
            continue
        lines.append(ln)
    keys = ("base", "percent_amount", "fixed_amount", "bonuses", "fines", "advances", "accrued", "paid", "to_pay")
    totals = {k: _r(sum(x[k] for x in lines)) for k in keys}
    totals["shifts"] = _r(sum(x["shifts"]["amount"] for x in lines))
    t = cashbox.cash_start(db, user.id)
    return {"from": a.isoformat(), "to": b.isoformat(), "label": period_label(a, b), "masters": lines, "totals": totals,
            "basis": "payments" if t is not None else "visits",
            "basis_since": t.astimezone(cashbox.BISHKEK).date().isoformat() if t is not None else None}


def master_month(db: Session, user, r, today: Optional[date] = None) -> Dict[str, Any]:
    """Кабинет мастера: с 1-го числа по сегодня — начислено, премии, штрафы, авансы, выплачено, к выплате."""
    today = today or bs.to_local(bs.now_utc()).date()
    a = today.replace(day=1)
    ln = sheet(db, user, a, today, resource_id=r.id, check=False)["masters"]
    if not ln:
        return {"from": a.isoformat(), "to": today.isoformat(), "accrued": 0.0, "to_pay": 0.0}
    x = ln[0]
    return {"from": a.isoformat(), "to": today.isoformat(), "label": period_label(a, today),
            "percent_amount": x["percent_amount"], "shifts": x["shifts"], "fixed_amount": x["fixed_amount"],
            "bonuses": x["bonuses"], "fines": x["fines"], "advances": x["advances"], "accrued": x["accrued"],
            "paid": x["paid"], "to_pay": x["to_pay"], "scheme_text": x["scheme_text"], "configured": x["configured"],
            "adjustments": x["adjustments"], "payouts": x["payouts"]}


def earned(db: Session, user, r, a: date, b: date) -> Dict[str, Any]:
    """Заработок без ручных строк (кабинет мастера: сегодня / неделя / месяц): база, визиты, % + выходы + оклад."""
    base = _base(db, user.id, a, b, [str(r.id)]).get(str(r.id)) or {}
    x = _line(r, a, b, base, _shifts(db, user.id, a, b, [str(r.id)]).get(str(r.id)) or [], [], [])
    return {"visits": x["visits"], "revenue": x["base"],
            "salary": _r(x["percent_amount"] + x["shifts"]["amount"] + x["fixed_amount"])}


# ───────────────────────────── авансы, штрафы, премии ─────────────────────────────

def _amount(v, what: str = "Сумма") -> float:
    try:
        f = round(float(str(v).replace(" ", "").replace(",", ".")), 2)
    except (TypeError, ValueError):
        raise CashError(f"{what}: укажите число")
    if f <= 0:
        raise CashError(f"{what} должна быть больше нуля")
    if f > MAX_AMOUNT:
        raise CashError(f"{what} слишком большая")
    return f


def add_adjustment(db: Session, user, resource_id, kind: str, amount, day=None, comment: Optional[str] = None) -> Dict[str, Any]:
    from backend.models.booking import BookingResource
    from backend.models.payroll import PayrollAdjustment
    from backend.services import activity
    _need_manage(user)
    if kind not in KIND_TITLES:
        raise CashError("Выберите: аванс, штраф или премия")
    r = db.query(BookingResource).filter(BookingResource.id == _uuid(resource_id), BookingResource.user_id == user.id).first()
    if r is None:
        raise NotFound("Мастер не найден")
    a = _amount(amount)
    try:
        d = date.fromisoformat(str(day)[:10]) if day else bs.to_local(bs.now_utc()).date()
    except ValueError:
        raise CashError("Неверная дата")
    why = (comment or "").strip()[:300]
    if kind == "fine" and len(why) < 2:
        raise CashError("За что штраф? Напишите коротко — мастер увидит это в своём кабинете")
    who = cashbox.cashier_of(user)["name"]
    x = PayrollAdjustment(id=uuid.uuid4(), user_id=user.id, resource_id=r.id, kind=kind, day=d, amount=a,
                          comment=why or None, created_by_name=(who or "")[:120])
    db.add(x)
    db.flush()
    activity.log(db, user.id, "payroll.adjust", "payroll",
                 f"{r.name}: {KIND_TITLES[kind].lower()} {money(a)} · {d.strftime('%d.%m')}" + (f" · {why}" if why else ""),
                 object_id=r.id, user=user, meta={"kind": kind, "amount": a, "date": d.isoformat()})
    _invalidate(user.id)
    return {"id": str(x.id), "kind": kind, "title": KIND_TITLES[kind], "day": d.isoformat(), "amount": a, "comment": why}


def delete_adjustment(db: Session, user, adj_id) -> Dict[str, Any]:
    from backend.models.booking import BookingResource
    from backend.models.payroll import PayrollAdjustment
    from backend.services import activity
    _need_manage(user)
    x = db.query(PayrollAdjustment).filter(PayrollAdjustment.id == _uuid(adj_id, "Строка не найдена"),
                                           PayrollAdjustment.user_id == user.id).first()
    if x is None:
        raise NotFound("Строка не найдена")
    r = db.query(BookingResource).filter(BookingResource.id == x.resource_id).first()
    activity.log(db, user.id, "payroll.adjust", "payroll",
                 f"{r.name if r else 'Мастер'}: удалена строка «{KIND_TITLES.get(x.kind, x.kind).lower()} {money(x.amount)}» "
                 f"от {x.day.strftime('%d.%m')}", object_id=x.resource_id, user=user,
                 meta={"kind": x.kind, "amount": x.amount, "deleted": True})
    db.delete(x)
    db.flush()
    _invalidate(user.id)
    return {"ok": True}


# ───────────────────────────── выплата ─────────────────────────────

def payout(db: Session, user, resource_id, a: date, b: date, amount, method: str, idem_key,
           comment: Optional[str] = None) -> Tuple[Dict[str, Any], bool]:
    """Выплатить мастеру за период. Наличными — изъятие в смене того, кто платит. Повтор idem_key — та же выплата."""
    from backend.models.booking import BookingResource
    from backend.models.payroll import PayrollPayout
    from backend.services import activity
    _need_manage(user)
    idem = cashbox._clean_idem(idem_key)
    old = db.query(PayrollPayout).filter(PayrollPayout.user_id == user.id, PayrollPayout.idem_key == idem).first()
    if old is not None:
        return payout_dict(old), False
    if method not in METHOD_TITLES:
        raise CashError("Выберите, как выплатить: наличными из кассы или переводом")
    r = (db.query(BookingResource).filter(BookingResource.id == _uuid(resource_id), BookingResource.user_id == user.id)
         .with_for_update().first())
    if r is None:
        raise NotFound("Мастер не найден")
    old = db.query(PayrollPayout).filter(PayrollPayout.user_id == user.id, PayrollPayout.idem_key == idem).first()
    if old is not None:                       # пока ждали блокировку мастера, то же нажатие прошло
        return payout_dict(old), False
    line = sheet(db, user, a, b, resource_id=r.id)["masters"]
    line = line[0] if line else None
    left = line["to_pay"] if line else 0.0
    if left <= EPS:
        raise CashError("За этот период выплачивать нечего — всё выплачено", "paid")
    amt = _amount(amount if amount not in (None, "") else left)
    if amt > left + EPS:
        raise CashError(f"К выплате за период {money(left)} — сумма больше", "too_much")
    why = (comment or "").strip()[:300]
    label = period_label(a, b)
    receipt_id = None
    if method == "cash":
        if not cashbox.can_take(user):
            raise CashError("Наличными выплачивает тот, кто ведёт смену в кассе. Выберите «Переводом» или попросите "
                            "администратора", "shift_required")
        rc, _new = cashbox.cash_move(db, user, "cash_out", amt, "pay-" + idem,
                                     f"Зарплата: {r.name} за {label}" + (f" · {why}" if why else ""))
        receipt_id = uuid.UUID(rc["id"])
    who = cashbox.cashier_of(user)["name"]
    x = PayrollPayout(id=uuid.uuid4(), user_id=user.id, resource_id=r.id, date_from=a, date_to=b, amount=amt, method=method,
                      receipt_id=receipt_id, comment=why or None, idem_key=idem,
                      snapshot={k: line[k] for k in ("percent_amount", "fixed_amount", "bonuses", "fines", "advances",
                                                     "accrued", "paid", "to_pay", "base", "visits")} | {"shifts": line["shifts"]["count"]},
                      created_by_name=(who or "")[:120])
    sp = db.begin_nested()
    try:
        db.add(x)
        db.flush()
        sp.commit()
    except IntegrityError:
        sp.rollback()
        old = db.query(PayrollPayout).filter(PayrollPayout.user_id == user.id, PayrollPayout.idem_key == idem).first()
        if old is not None:
            return payout_dict(old), False
        raise
    activity.log(db, user.id, "payroll.payout", "payroll",
                 f"Выплата {r.name}: {money(amt)} за {label} · {METHOD_TITLES[method].lower()}" + (f" · {why}" if why else ""),
                 object_id=r.id, user=user, meta={"amount": amt, "from": a.isoformat(), "to": b.isoformat(), "method": method})
    _invalidate(user.id)
    db.flush()
    return payout_dict(x), True


def _invalidate(user_id) -> None:
    try:
        from backend.services import analytics_service
        analytics_service.invalidate(user_id)
    except Exception:
        pass
