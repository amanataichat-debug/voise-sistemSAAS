"""
Доходы, расходы и прибыль владельца (ветка 1811-niche-admin-speed, 19.11.2026).

Одна логика на страницу «Финансы», раздел аналитики и виджет главной:

  выручка   = записи со статусом «пришёл» (цена услуги в записи) + заказы (кроме отменённых);
  расходы   = разовые расходы за период + доля ежемесячных (по дням) + зарплаты мастеров
              (% с выручки его записей + доля оклада);
  прибыль   = выручка − расходы.

Период — тот же Range, что у аналитики (даты Бишкека), сравнение с прошлым таким же периодом.
"""

from __future__ import annotations

import calendar
import uuid
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.models.finance import EXPENSE_CATEGORIES, BusinessExpense

CATEGORY_ICONS = {"rent": "house", "salary": "users", "supplies": "package", "ads": "megaphone",
                  "utilities": "zap", "taxes": "landmark", "other": "circle"}


def _r(v: float) -> float:
    return round(float(v or 0), 2)


def month_share(amount: float, a: date, b: date, start: date, end: Optional[date]) -> float:
    """Доля ежемесячной суммы, приходящаяся на дни [a, b] ∩ [start, end] — по дням каждого месяца."""
    lo = max(a, start)
    hi = min(b, end) if end else b
    if hi < lo:
        return 0.0
    total = 0.0
    d = lo
    while d <= hi:
        dim = calendar.monthrange(d.year, d.month)[1]
        month_end = date(d.year, d.month, dim)
        seg_end = min(hi, month_end)
        total += amount * ((seg_end - d).days + 1) / dim
        d = seg_end + timedelta(days=1)
    return total


def _expense_in(e: BusinessExpense, a: date, b: date) -> float:
    if not e.is_active:
        return 0.0
    if e.repeat == "month":
        return month_share(e.amount or 0, a, b, e.spent_on, e.ends_on)
    return float(e.amount or 0) if a <= e.spent_on <= b else 0.0


def expense_to_dict(e: BusinessExpense, a: Optional[date] = None, b: Optional[date] = None) -> Dict[str, Any]:
    d = {
        "id": str(e.id), "category": e.category, "category_title": EXPENSE_CATEGORIES.get(e.category, "Другое"),
        "icon": CATEGORY_ICONS.get(e.category, "circle"), "title": e.title, "amount": _r(e.amount),
        "repeat": e.repeat, "spent_on": e.spent_on.isoformat() if e.spent_on else None,
        "ends_on": e.ends_on.isoformat() if e.ends_on else None,
        "resource_id": str(e.resource_id) if e.resource_id else None, "note": e.note or "", "is_active": bool(e.is_active),
    }
    if a and b:
        d["in_period"] = _r(_expense_in(e, a, b))
    return d


def list_expenses(db: Session, user_id) -> List[BusinessExpense]:
    return (db.query(BusinessExpense).filter(BusinessExpense.user_id == user_id)
            .order_by(BusinessExpense.repeat.desc(), BusinessExpense.spent_on.desc(), BusinessExpense.created_at.desc()).all())


def _clean(data: Dict[str, Any], partial: bool = False) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if "category" in data or not partial:
        cat = (data.get("category") or "other").strip()
        out["category"] = cat if cat in EXPENSE_CATEGORIES else "other"
    if "title" in data or not partial:
        title = (data.get("title") or "").strip()[:200]
        if not title:
            title = EXPENSE_CATEGORIES.get(out.get("category") or "other", "Расход")
        out["title"] = title
    if "amount" in data or not partial:
        try:
            amount = float(data.get("amount"))
        except (TypeError, ValueError):
            raise ValueError("Укажите сумму")
        if amount <= 0 or amount > 1e9:
            raise ValueError("Сумма должна быть больше нуля")
        out["amount"] = amount
    if "repeat" in data or not partial:
        out["repeat"] = "month" if data.get("repeat") == "month" else "once"
    for key in ("spent_on", "ends_on"):
        if key in data or (key == "spent_on" and not partial):
            v = data.get(key)
            if not v:
                if key == "spent_on":
                    from backend.services.analytics_service import today_bk
                    out[key] = today_bk()
                else:
                    out[key] = None
            else:
                try:
                    out[key] = date.fromisoformat(str(v)[:10])
                except ValueError:
                    raise ValueError("Неверная дата")
    if "note" in data:
        out["note"] = (data.get("note") or "").strip()[:1000] or None
    if "resource_id" in data:
        out["resource_id"] = uuid.UUID(str(data["resource_id"])) if data.get("resource_id") else None
    if "is_active" in data:
        out["is_active"] = bool(data.get("is_active"))
    return out


def _expense_line(e: BusinessExpense) -> str:
    from backend.services import activity
    return (f"{e.title} · {activity.money(e.amount)}" + (" в месяц" if e.repeat == "month" else "")
            + (f" · {e.spent_on.strftime('%d.%m.%Y')}" if e.spent_on and e.repeat != "month" else ""))


def _expense_state(e: BusinessExpense) -> Dict[str, Any]:
    return {"title": e.title, "category": EXPENSE_CATEGORIES.get(e.category, e.category), "amount": e.amount,
            "repeat": "в месяц" if e.repeat == "month" else "разовый", "spent_on": e.spent_on.isoformat() if e.spent_on else None,
            "ends_on": e.ends_on.isoformat() if e.ends_on else None, "is_active": bool(e.is_active)}


def create_expense(db: Session, user, data: Dict[str, Any]) -> BusinessExpense:
    from backend.services import activity
    e = BusinessExpense(user_id=user.id, **_clean(data))
    db.add(e)
    db.flush()
    activity.log(db, user.id, "expense.create", "expense", _expense_line(e), object_id=e.id, user=user)
    db.commit()
    db.refresh(e)
    _invalidate(user.id)
    return e


def get_expense(db: Session, user_id, expense_id) -> BusinessExpense:
    try:
        eid = uuid.UUID(str(expense_id))
    except ValueError:
        raise LookupError("Расход не найден")
    e = db.query(BusinessExpense).filter(BusinessExpense.id == eid, BusinessExpense.user_id == user_id).first()
    if not e:
        raise LookupError("Расход не найден")
    return e


def update_expense(db: Session, user, expense_id, data: Dict[str, Any]) -> BusinessExpense:
    from backend.services import activity
    e = get_expense(db, user.id, expense_id)
    before = _expense_state(e)
    for k, v in _clean(data, partial=True).items():
        setattr(e, k, v)
    ch = activity.diff(before, _expense_state(e))
    if ch:
        labels = {"title": "название", "category": "категория", "amount": "сумма", "repeat": "повтор",
                  "spent_on": "дата", "ends_on": "до", "is_active": "учитывать"}
        def f(k, v):
            return activity.money(v) if k == "amount" else ("—" if v in (None, "") else str(v))
        activity.log(db, user.id, "expense.update", "expense",
                     f"{e.title}: " + "; ".join(f"{labels[k]}: {f(k, a)} → {f(k, b)}" for k, (a, b) in ch.items()),
                     object_id=e.id, changes=ch, user=user)
    db.commit()
    db.refresh(e)
    _invalidate(user.id)
    return e


def delete_expense(db: Session, user, expense_id) -> None:
    from backend.services import activity
    e = get_expense(db, user.id, expense_id)
    activity.log(db, user.id, "expense.delete", "expense", _expense_line(e), object_id=e.id, user=user)
    db.delete(e)
    db.commit()
    _invalidate(user.id)


def set_pay(db: Session, user, resource_id, percent=None, fixed=None) -> Dict[str, Any]:
    from backend.models.booking import BookingResource
    try:
        rid = uuid.UUID(str(resource_id))
    except ValueError:
        raise LookupError("Мастер не найден")
    r = db.query(BookingResource).filter(BookingResource.id == rid, BookingResource.user_id == user.id).first()
    if not r:
        raise LookupError("Мастер не найден")
    before = (r.pay_percent or 0, r.pay_fixed or 0)
    if percent is not None:
        p = float(percent or 0)
        if p < 0 or p > 100:
            raise ValueError("Процент — от 0 до 100")
        r.pay_percent = p or None
    if fixed is not None:
        f = float(fixed or 0)
        if f < 0 or f > 1e8:
            raise ValueError("Неверный оклад")
        r.pay_fixed = f or None
    after = (r.pay_percent or 0, r.pay_fixed or 0)
    if before != after:
        from backend.services import activity
        def f2(t):
            return " + ".join(x for x in [f"{t[0]:g}% с выручки" if t[0] else "", f"оклад {activity.money(t[1])}" if t[1] else ""] if x) or "не задана"
        activity.log(db, user.id, "payroll.update", "payroll", f"{r.name}: {f2(before)} → {f2(after)}", object_id=r.id,
                     changes={"pay_percent": [before[0], after[0]], "pay_fixed": [before[1], after[1]]}, user=user)
    db.commit()
    _invalidate(user.id)
    return {"id": str(r.id), "name": r.name, "pay_percent": r.pay_percent or 0, "pay_fixed": r.pay_fixed or 0}


def _invalidate(user_id) -> None:
    try:
        from backend.services import analytics_service
        analytics_service.invalidate(user_id)
    except Exception:
        pass


# ───────────────────────────── расчёт за период ─────────────────────────────

def _revenue(db: Session, uid: str, s, u) -> Dict[str, float]:
    out = {"bookings": 0.0, "orders": 0.0}
    try:
        out["bookings"] = float(db.execute(text(
            "SELECT COALESCE(sum(price), 0) FROM bookings WHERE user_id = :uid AND status IN ('arrived', 'completed') "
            "AND start_at >= :s AND start_at < :u"), {"uid": uid, "s": s, "u": u}).scalar() or 0)
    except Exception:
        db.rollback()
    try:
        out["orders"] = float(db.execute(text(
            "SELECT COALESCE(sum(total), 0) FROM orders WHERE user_id = :uid AND status <> 'cancelled' "
            "AND created_at >= :s AND created_at < :u"), {"uid": uid, "s": s, "u": u}).scalar() or 0)
    except Exception:
        db.rollback()
    return out


def _payroll(db: Session, user, a: date, b: date, s, u) -> List[Dict[str, Any]]:
    try:
        from backend.services import booking_service as bs
        resources = bs.resources(db, user.id, active_only=False)
    except Exception:
        db.rollback()
        return []
    if not resources:
        return []
    by_res: Dict[str, Dict[str, float]] = {}
    for rid, cnt, rev in db.execute(text(
        "SELECT resource_id, count(*), COALESCE(sum(price), 0) FROM bookings WHERE user_id = :uid "
        "AND status IN ('arrived', 'completed') AND start_at >= :s AND start_at < :u GROUP BY 1"
    ), {"uid": str(user.id), "s": s, "u": u}).fetchall():
        by_res[str(rid)] = {"count": int(cnt or 0), "revenue": float(rev or 0)}
    out = []
    for r in resources:
        st = by_res.get(str(r.id), {"count": 0, "revenue": 0.0})
        if not r.is_active and not st["count"]:
            continue
        pct = float(getattr(r, "pay_percent", None) or 0)
        fixed = float(getattr(r, "pay_fixed", None) or 0)
        start = (r.created_at.date() if getattr(r, "created_at", None) else a)
        fixed_part = month_share(fixed, a, b, min(start, a), None) if fixed else 0.0
        salary = st["revenue"] * pct / 100.0 + fixed_part
        out.append({"id": str(r.id), "name": r.name, "role": r.role, "color": r.color, "visits": st["count"],
                    "revenue": _r(st["revenue"]), "pay_percent": pct, "pay_fixed": fixed,
                    "salary": _r(salary), "configured": bool(pct or fixed)})
    out.sort(key=lambda m: -m["revenue"])
    return out


def _period_numbers(db: Session, user, a: date, b: date, s, u, expenses: List[BusinessExpense]) -> Dict[str, Any]:
    uid = str(user.id)
    rev = _revenue(db, uid, s, u)
    payroll = _payroll(db, user, a, b, s, u)
    by_cat: Dict[str, float] = {}
    items = []
    for e in expenses:
        v = _expense_in(e, a, b)
        if v <= 0:
            continue
        by_cat[e.category] = by_cat.get(e.category, 0.0) + v
        items.append((e, v))
    pay_total = sum(m["salary"] for m in payroll)
    if pay_total:
        by_cat["salary"] = by_cat.get("salary", 0.0) + pay_total
    revenue = rev["bookings"] + rev["orders"]
    exp_total = sum(by_cat.values())
    return {"revenue": revenue, "rev": rev, "expenses": exp_total, "by_cat": by_cat, "items": items,
            "payroll": payroll, "payroll_total": pay_total, "profit": revenue - exp_total}


def summary(db: Session, user, period: Optional[str] = "30d", date_from: Optional[str] = None,
            date_to: Optional[str] = None, rng=None, with_series: bool = True) -> Dict[str, Any]:
    from backend.services.analytics_service import resolve_period, _delta, _pct
    rng = rng or resolve_period(period, date_from, date_to)
    expenses = list_expenses(db, user.id)
    s, u = rng.aware()
    ps, pu = rng.aware(prev=True)
    cur = _period_numbers(db, user, rng.a, rng.b, s, u, expenses)
    prev = _period_numbers(db, user, rng.prev_a, rng.prev_b, ps, pu, expenses)
    cats = [{"key": k, "title": EXPENSE_CATEGORIES.get(k, "Другое"), "icon": CATEGORY_ICONS.get(k, "circle"),
             "amount": _r(v), "share": _pct(v, cur["expenses"])}
            for k, v in sorted(cur["by_cat"].items(), key=lambda kv: -kv[1])]
    # Ряд по дням/неделям — выручка, расходы, прибыль (график страницы «Финансы»)
    series = _series(db, user, rng, expenses) if with_series else None
    margin = _pct(cur["profit"], cur["revenue"]) if cur["revenue"] > 0 else None
    return {
        "period": {"key": rng.key, "label": rng.label, "from": rng.a.isoformat(), "to": rng.b.isoformat(),
                   "days": rng.days, "compare": rng.prev_label},
        "revenue": _r(cur["revenue"]), "prev_revenue": _r(prev["revenue"]), "revenue_delta": _delta(cur["revenue"], prev["revenue"]),
        "revenue_parts": {k: _r(v) for k, v in cur["rev"].items()},
        "expenses": _r(cur["expenses"]), "prev_expenses": _r(prev["expenses"]), "expenses_delta": _delta(cur["expenses"], prev["expenses"]),
        "profit": _r(cur["profit"]), "prev_profit": _r(prev["profit"]), "profit_delta": _delta(cur["profit"], prev["profit"]),
        "margin_pct": margin,
        "categories": cats,
        "payroll": cur["payroll"], "payroll_total": _r(cur["payroll_total"]),
        "has_masters": bool(cur["payroll"]),
        "items": [expense_to_dict(e, rng.a, rng.b) for e, _v in cur["items"]],
        "expenses_count": len(expenses),
        "series": series,
        "categories_all": [{"key": k, "title": t, "icon": CATEGORY_ICONS.get(k, "circle")} for k, t in EXPENSE_CATEGORIES.items()],
    }


def _series(db: Session, user, rng, expenses: List[BusinessExpense]) -> Dict[str, Any]:
    """Выручка и расходы по шагам: до 31 дня — по дням, дальше — по неделям, больше полугода — по месяцам."""
    from backend.services.analytics_service import BISHKEK_OFFSET
    days = rng.days
    step = "day" if days <= 31 else ("week" if days <= 183 else "month")
    buckets: List[List[date]] = []
    d = rng.a
    while d <= rng.b:
        if step == "day":
            e = d
        elif step == "week":
            e = min(rng.b, d + timedelta(days=6 - d.weekday()))
        else:
            e = min(rng.b, date(d.year, d.month, calendar.monthrange(d.year, d.month)[1]))
        buckets.append([d, e])
        d = e + timedelta(days=1)
    rev = [0.0] * len(buckets)

    def bi(day: date) -> Optional[int]:
        for i, (x, y) in enumerate(buckets):
            if x <= day <= y:
                return i
        return None
    s, u = rng.aware()
    off = int(BISHKEK_OFFSET.total_seconds() // 3600)
    for q in ("SELECT date(start_at + interval '%d hour'), COALESCE(sum(price), 0) FROM bookings WHERE user_id = :uid "
              "AND status IN ('arrived', 'completed') AND start_at >= :s AND start_at < :u GROUP BY 1",
              "SELECT date(created_at + interval '%d hour'), COALESCE(sum(total), 0) FROM orders WHERE user_id = :uid "
              "AND status <> 'cancelled' AND created_at >= :s AND created_at < :u GROUP BY 1"):
        try:
            for day, v in db.execute(text(q % off), {"uid": str(user.id), "s": s, "u": u}).fetchall():
                i = bi(day)
                if i is not None:
                    rev[i] += float(v or 0)
        except Exception:
            db.rollback()
    exp = [0.0] * len(buckets)
    for e in expenses:
        for i, (x, y) in enumerate(buckets):
            exp[i] += _expense_in(e, x, y)
    # Зарплата мастеров — пропорционально выручке шага (процент) + доля оклада
    payroll = _payroll(db, user, rng.a, rng.b, s, u)
    pct_total = sum(m["revenue"] * m["pay_percent"] / 100.0 for m in payroll)
    booking_rev_total = sum(m["revenue"] for m in payroll) or 0.0
    fixed_total = sum(m["pay_fixed"] for m in payroll)
    for i, (x, y) in enumerate(buckets):
        if booking_rev_total and pct_total:
            exp[i] += pct_total * (rev[i] / max(sum(rev), 1e-9)) if sum(rev) else 0.0
        if fixed_total:
            exp[i] += month_share(fixed_total, x, y, rng.a, None)
    months = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]

    def label(x: date, y: date) -> str:
        if step == "day":
            return f"{x.day} {months[x.month - 1]}"
        if step == "month":
            return ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"][x.month - 1]
        return f"{x.day}–{y.day} {months[y.month - 1]}" if x.month == y.month else f"{x.day} {months[x.month - 1]}–{y.day} {months[y.month - 1]}"
    return {"step": step, "labels": [label(x, y) for x, y in buckets], "from": [x.isoformat() for x, _ in buckets],
            "revenue": [_r(v) for v in rev], "expenses": [_r(v) for v in exp], "profit": [_r(rev[i] - exp[i]) for i in range(len(buckets))]}
