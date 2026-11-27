"""
Касса салона (Voksy Beauty, шаг 3.1): смены администраторов, оплата визита, возвраты, внесение и изъятие.

Решения владельца (27.11.2026):
  • касса — смены администраторов: каждый кассир (владелец или сотрудник) открывает и закрывает свою
    смену, при закрытии пересчитывает наличные, расхождение видно владельцу (журнал действий, главная,
    Telegram-бот агента);
  • выручка — ПО ОПЛАТАМ: визит без оплаты — долг клиента (шаг 3.2). Чтобы у салона, который кассой ещё
    не пользуется, выручка не стала нулём, до начала работы кассы (первая смена или первый чек — cash_start)
    выручка считается по-старому, по цене визитов «пришёл / завершён»; с этого момента — по оплатам.

Кто: оплату принимает владелец или сотрудник с разделом «Онлайн-запись» (booking); все смены и чужие
открытые смены — владельцу и бухгалтеру (раздел «Финансы»). Мастер (booking_own) кассой не пользуется.

Деньги:
  • наличные — только в открытой смене кассира (иначе shift_required); карта / QR / перевод без смены можно
    (владелец отметил перевод на свой Mbank из дома) — чек без смены, в итогах дня он есть;
  • оплата визита раскладывается по его услугам по порядку (сначала первая); больше, чем осталось к оплате,
    принять нельзя (если цена не задана — можно любую сумму);
  • одно нажатие «Принять» = один чек: idem_key (страница создаёт его при открытии окна) UNIQUE в базе; записи
    визита блокируются (FOR UPDATE), поэтому два кассира одновременно не примут одну оплату дважды;
  • возврат — по чеку, не больше оплаченного по нему; наличный возврат — тоже в открытой смене;
  • предоплата: строка записи получила оплату ≥ prepay_amount → bookings.prepaid_at (как прежняя галочка).

Функции не делают commit — коммитит API (или тест).
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models.cashbox import MONEY_KINDS, PAY_METHODS, CashPayment, CashReceipt, CashShift

logger = logging.getLogger(__name__)

BISHKEK = timezone(timedelta(hours=6))
METHOD_TITLES = {"cash": "Наличные", "card": "Карта", "qr": "QR", "transfer": "Перевод"}
METHOD_ICONS = {"cash": "banknote", "card": "credit-card", "qr": "qr-code", "transfer": "smartphone"}
KIND_TITLES = {"payment": "Оплата", "refund": "Возврат", "cash_in": "Внесение", "cash_out": "Изъятие"}
MAX_AMOUNT = 10_000_000.0
MAX_LINES = 4
EPS = 0.005
LONG_SHIFT_HOURS = 16          # смена открыта дольше — «забыли закрыть» на главной салона


class CashError(Exception):
    def __init__(self, message: str, code: str = "error"):
        super().__init__(message)
        self.code = code


class NotFound(CashError):
    def __init__(self, message: str = "Не найдено"):
        super().__init__(message, "not_found")


class Forbidden(CashError):
    def __init__(self, message: str = "Нет доступа"):
        super().__init__(message, "forbidden")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aw(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _r(v) -> float:
    return round(float(v or 0) + 0.0, 2)


def _uuid(v, what: str = "Не найдено") -> uuid.UUID:
    try:
        return v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))
    except (TypeError, ValueError):
        raise NotFound(what)


def money_text(v) -> str:
    from backend.services import activity
    return activity.money(v)


# ───────────────────────────── кто за кассой ─────────────────────────────

def cashier_of(user) -> Dict[str, Any]:
    """Кассир запроса: владелец, сотрудник (имя из снимка) или поддержка Voksy (вход админа)."""
    from backend.services import activity
    a = activity.actor_for(user)
    kind = a.get("kind") or "owner"
    if kind == "staff":
        key = f"staff:{a.get('id')}"
    elif kind == "admin":
        key = f"admin:{a.get('id')}"
    else:
        kind, key = "owner", "owner"
    return {"key": key[:80], "kind": kind, "id": a.get("id"), "name": a.get("name") or "Владелец"}


def _perms(user) -> Optional[set]:
    if not getattr(user, "_staff_member_id", None):
        return None
    return set(((getattr(user, "_staff", None) or {}).get("permissions")) or [])


def can_manage(user) -> bool:
    """Все смены, закрыть чужую смену, история — владелец (и вход поддержки) и бухгалтер (раздел «Финансы»)."""
    p = _perms(user)
    return p is None or "finance" in p


def can_take(user) -> bool:
    """Принимать оплату и вести смену — владелец и сотрудник с разделом «Онлайн-запись»."""
    p = _perms(user)
    return p is None or "booking" in p


def _need_take(user) -> None:
    if not can_take(user):
        raise Forbidden("Принимать оплату может владелец или администратор (раздел «Онлайн-запись»)")


# ───────────────────────────── смены ─────────────────────────────

def current_shift(db: Session, user, lock: bool = False) -> Optional[CashShift]:
    q = db.query(CashShift).filter(CashShift.user_id == user.id, CashShift.cashier_key == cashier_of(user)["key"],
                                   CashShift.status == "open")
    if lock:
        q = q.with_for_update()
    return q.first()


def suggest_opening(db: Session, user_id) -> Optional[float]:
    """Сколько наличных оставили в кассе при последнем закрытии (подсказка при открытии смены)."""
    v = db.execute(text("SELECT counted_cash FROM cash_shifts WHERE user_id = :u AND status = 'closed' "
                        "AND counted_cash IS NOT NULL ORDER BY closed_at DESC LIMIT 1"), {"u": str(user_id)}).scalar()
    return None if v is None else _r(v)


def _amount(v, what: str = "Сумма", allow_zero: bool = False) -> float:
    try:
        a = round(float(str(v).replace(" ", "").replace(",", ".")), 2)
    except (TypeError, ValueError):
        raise CashError(f"{what}: укажите число")
    if a != a or a < 0 or (a == 0 and not allow_zero) or a > MAX_AMOUNT:
        raise CashError(f"{what}: " + ("не может быть отрицательной" if a < 0 else
                                        "должна быть больше нуля" if a == 0 else "слишком большая"))
    return a


def open_shift(db: Session, user, opening_cash=None, note: Optional[str] = None) -> Tuple[CashShift, bool]:
    """Открыть свою смену. Уже открыта — вернуть её (второе нажатие и гонка двух вкладок безопасны). → (смена, новая ли)."""
    from backend.services import activity
    _need_take(user)
    who = cashier_of(user)
    cur = current_shift(db, user)
    if cur is not None:
        return cur, False
    if opening_cash in (None, ""):
        opening = suggest_opening(db, user.id) or 0.0
    else:
        opening = _amount(opening_cash, "Наличные в кассе", allow_zero=True)
    sh = CashShift(id=uuid.uuid4(), user_id=user.id, cashier_key=who["key"], opened_by_kind=who["kind"],
                   opened_by_id=who["id"], opened_by_name=(who["name"] or "")[:120], opened_at=_now(),
                   opening_cash=opening, status="open", note=(note or "").strip()[:500] or None)
    sp = db.begin_nested()
    try:
        db.add(sh)
        db.flush()
        sp.commit()
    except IntegrityError:
        sp.rollback()
        cur = current_shift(db, user)
        if cur is not None:
            return cur, False
        raise
    activity.log(db, user.id, "shift.open", "shift", f"Смена открыта · в кассе {money_text(opening)}",
                 object_id=sh.id, user=user, meta={"opening_cash": opening})
    _invalidate(user.id)
    return sh, True


def _get_shift(db: Session, user_id, shift_id, lock: bool = False) -> CashShift:
    q = db.query(CashShift).filter(CashShift.id == _uuid(shift_id, "Смена не найдена"), CashShift.user_id == user_id)
    if lock:
        q = q.with_for_update()
    sh = q.first()
    if sh is None:
        raise NotFound("Смена не найдена")
    return sh


def _own(user, sh: CashShift) -> bool:
    return sh.cashier_key == cashier_of(user)["key"]


def shift_numbers(db: Session, sh: CashShift) -> Dict[str, Any]:
    """Итоги смены по строкам чеков: по способам (оплаты − возвраты), внесено, изъято, наличные в кассе."""
    rows = db.execute(text(
        "SELECT kind, method, COALESCE(sum(amount), 0), count(DISTINCT receipt_id) FROM cash_payments "
        "WHERE shift_id = :s GROUP BY 1, 2"), {"s": str(sh.id)}).fetchall()
    by_method = {m: 0.0 for m in PAY_METHODS}
    payments = refunds = cash_in = cash_out = cash_flow = 0.0
    receipts = 0
    for kind, method, total, n in rows:
        total = float(total or 0)
        if kind in MONEY_KINDS:
            by_method[method] = by_method.get(method, 0.0) + total
        if kind == "payment":
            payments += total
            receipts += int(n or 0)
        elif kind == "refund":
            refunds += -total
        elif kind == "cash_in":
            cash_in += total
        elif kind == "cash_out":
            cash_out += -total
        if method == "cash":
            cash_flow += total
    expected = float(sh.opening_cash or 0) + cash_flow
    return {"by_method": {k: _r(v) for k, v in by_method.items()}, "revenue": _r(payments - refunds),
            "payments": _r(payments), "refunds": _r(refunds), "cash_in": _r(cash_in), "cash_out": _r(cash_out),
            "receipts": receipts, "expected_cash": _r(expected), "opening_cash": _r(sh.opening_cash)}


def close_shift(db: Session, user, shift_id, counted_cash, note: Optional[str] = None) -> CashShift:
    """Закрыть смену: пересчитанные наличные против ожидаемых → расхождение. Свою — кассир, любую — владелец."""
    from backend.services import activity
    sh = _get_shift(db, user.id, shift_id, lock=True)
    if not _own(user, sh) and not can_manage(user):
        raise Forbidden("Закрыть чужую смену может только владелец или бухгалтер")
    if sh.status != "open":
        raise CashError("Смена уже закрыта", "closed")
    counted = _amount(counted_cash, "Наличные в кассе", allow_zero=True)
    nums = shift_numbers(db, sh)
    who = cashier_of(user)
    sh.status, sh.closed_at = "closed", _now()
    sh.closed_by_kind, sh.closed_by_id, sh.closed_by_name = who["kind"], who["id"], (who["name"] or "")[:120]
    sh.expected_cash, sh.counted_cash = nums["expected_cash"], counted
    sh.diff = _r(counted - nums["expected_cash"])
    sh.totals = nums
    if note:
        sh.note = ((sh.note + "\n") if sh.note else "") + str(note).strip()[:500]
    db.flush()
    diff_txt = ("без расхождений" if abs(sh.diff) < EPS else
                f"{'лишние' if sh.diff > 0 else 'не хватает'} {money_text(abs(sh.diff))}")
    summary = (f"Смена {sh.opened_by_name or 'кассира'} закрыта: выручка {money_text(nums['revenue'])}, "
               f"в кассе {money_text(counted)} (ожидалось {money_text(nums['expected_cash'])}) — {diff_txt}")
    activity.log(db, user.id, "shift.close", "shift", summary, object_id=sh.id, user=user,
                 meta={"diff": sh.diff, "expected": nums["expected_cash"], "counted": counted})
    if abs(sh.diff) >= 1:
        NOTIFY_DIFF(user.id, sh.id)
    _invalidate(user.id)
    return sh


def shift_to_dict(db: Session, sh: CashShift, numbers: bool = True) -> Dict[str, Any]:
    d = {
        "id": str(sh.id), "status": sh.status, "cashier": sh.opened_by_name or "Кассир", "cashier_kind": sh.opened_by_kind,
        "cashier_key": sh.cashier_key, "opened_at": _aw(sh.opened_at).isoformat(),
        "closed_at": _aw(sh.closed_at).isoformat() if sh.closed_at else None,
        "closed_by": sh.closed_by_name, "opening_cash": _r(sh.opening_cash),
        "expected_cash": None if sh.expected_cash is None else _r(sh.expected_cash),
        "counted_cash": None if sh.counted_cash is None else _r(sh.counted_cash),
        "diff": None if sh.diff is None else _r(sh.diff), "note": sh.note or "",
        "hours": round(((_aw(sh.closed_at) or _now()) - _aw(sh.opened_at)).total_seconds() / 3600, 1),
    }
    if numbers:
        d["numbers"] = sh.totals if (sh.status == "closed" and sh.totals) else shift_numbers(db, sh)
    return d


def list_shifts(db: Session, user, before: Optional[str] = None, limit: int = 30,
                status: Optional[str] = None) -> Dict[str, Any]:
    q = db.query(CashShift).filter(CashShift.user_id == user.id)
    if not can_manage(user):
        q = q.filter(CashShift.cashier_key == cashier_of(user)["key"])
    if status in ("open", "closed"):
        q = q.filter(CashShift.status == status)
    if before:
        try:
            bf = datetime.fromisoformat(str(before).replace("Z", "+00:00"))
            q = q.filter(CashShift.opened_at < bf)
        except ValueError:
            pass
    limit = max(1, min(int(limit or 30), 100))
    rows = q.order_by(CashShift.opened_at.desc()).limit(limit + 1).all()
    more = len(rows) > limit
    rows = rows[:limit]
    return {"items": [shift_to_dict(db, s) for s in rows],
            "next": _aw(rows[-1].opened_at).isoformat() if more and rows else None}


# ───────────────────────────── деньги записи / визита ─────────────────────────────

def _visit_parts(db: Session, user_id, booking_id, lock: bool = False):
    """Запись и все части её визита по времени (FOR UPDATE — в одном порядке, по id, чтобы без взаимных блокировок)."""
    from backend.models.booking import Booking
    b = db.query(Booking).filter(Booking.id == _uuid(booking_id, "Запись не найдена"), Booking.user_id == user_id).first()
    if b is None:
        raise NotFound("Запись не найдена")
    if b.visit_id:
        q = db.query(Booking).filter(Booking.user_id == user_id, Booking.visit_id == b.visit_id)
    else:
        q = db.query(Booking).filter(Booking.id == b.id)
    if lock:
        list(q.order_by(Booking.id).with_for_update().all())
    parts = q.order_by(Booking.start_at, Booking.id).all()
    return b, parts


def paid_by_booking(db: Session, booking_ids: Iterable) -> Dict[str, float]:
    ids = [str(x) for x in booking_ids if x]
    if not ids:
        return {}
    rows = db.execute(text(
        "SELECT booking_id, COALESCE(sum(amount), 0) FROM cash_payments WHERE booking_id = ANY(CAST(:ids AS uuid[])) "
        "AND kind IN ('payment', 'refund') GROUP BY 1"), {"ids": ids}).fetchall()
    return {str(bid): _r(v) for bid, v in rows}


def _state(due: Optional[float], paid: float) -> str:
    if paid <= EPS:
        return "unpaid"
    if not due:
        return "paid"
    if paid + EPS < due:
        return "partial"
    return "paid" if paid <= due + EPS else "over"


def booking_money(db: Session, user, booking_id) -> Dict[str, Any]:
    """Карточка «Оплата» записи: весь визит — к оплате, оплачено, осталось, чеки с возвратами."""
    from backend.services import booking_service as bs
    b, parts = _visit_parts(db, user.id, booking_id)
    paid = paid_by_booking(db, [p.id for p in parts])
    names = {str(r.id): r.name for r in bs.resources(db, user.id, active_only=False)}
    out_parts = []
    due_total = paid_total = left = 0.0
    priced = False
    for p in parts:
        pd = paid.get(str(p.id), 0.0)
        live = p.status != "cancelled"
        due = float(p.price or 0) if live else 0.0
        priced = priced or (live and p.price is not None)
        due_total += due
        paid_total += pd
        part_left = max(due - pd, 0.0) if live else 0.0
        left += part_left       # как в pay(): деньги отменённой услуги не гасят остаток других
        out_parts.append({"booking_id": str(p.id), "service": p.service_title or "Услуга", "status": p.status,
                          "master": names.get(str(p.resource_id)) if p.resource_id else None,
                          "start": _aw(p.start_at).isoformat(), "price": p.price, "due": _r(due), "paid": pd,
                          "left": _r(part_left), "prepay_amount": p.prepay_amount,
                          "prepaid": p.prepaid_at is not None})
    receipts = _receipts_for_bookings(db, user.id, [p.id for p in parts])
    return {"booking_id": str(b.id), "visit_id": str(b.visit_id) if b.visit_id else None,
            "client_name": b.client_name, "parts": out_parts, "due": _r(due_total), "paid": _r(paid_total),
            "left": _r(left), "priced": priced,
            "state": _state(due_total if priced else None, paid_total) if left <= EPS or paid_total <= EPS else "partial",
            "prepay_due": _r(sum(float(p.prepay_amount or 0) for p in parts if p.status != "cancelled" and p.prepaid_at is None)),
            "receipts": receipts, "shift": _shift_brief(db, user), "methods": methods(),
            "suggest_opening": suggest_opening(db, user.id), "can_take": can_take(user)}


def methods() -> List[Dict[str, str]]:
    return [{"key": k, "title": METHOD_TITLES[k], "icon": METHOD_ICONS[k]} for k in PAY_METHODS]


def _shift_brief(db: Session, user) -> Optional[Dict[str, Any]]:
    sh = current_shift(db, user)
    if sh is None:
        return None
    return {"id": str(sh.id), "opened_at": _aw(sh.opened_at).isoformat(), "cashier": sh.opened_by_name}


def _existing_receipt(db: Session, user_id, idem: str) -> Optional[CashReceipt]:
    return db.query(CashReceipt).filter(CashReceipt.user_id == user_id, CashReceipt.idem_key == idem).first()


def _clean_idem(idem_key) -> str:
    k = str(idem_key or "").strip()[:80]
    if len(k) < 8:
        raise CashError("Нет ключа операции — обновите страницу")
    return k


def _clean_lines(lines) -> List[Tuple[str, float]]:
    if not isinstance(lines, (list, tuple)) or not lines:
        raise CashError("Укажите сумму и способ оплаты")
    if len(lines) > MAX_LINES:
        raise CashError(f"Не больше {MAX_LINES} способов в одном чеке")
    out: List[Tuple[str, float]] = []
    for ln in lines:
        ln = ln or {}
        m = str(ln.get("method") or "").strip()
        if m not in PAY_METHODS:
            raise CashError("Выберите способ оплаты: наличные, карта, QR или перевод")
        a = _amount(ln.get("amount"), METHOD_TITLES[m])
        out.append((m, a))
    return out


def _receipt_title(parts) -> str:
    live = [p for p in parts if p.status != "cancelled"] or parts
    titles = [p.service_title or "Услуга" for p in live]
    return (" + ".join(titles))[:300]


def _apply_prepaid(parts, paid_after: Dict[str, float], unset_ids: Iterable[str] = ()) -> None:
    """Оплачено по записи не меньше её предоплаты → «предоплата получена». После возврата по записи
    (unset_ids) денег стало меньше предоплаты → отметка снимается (ручную отметку без денег не трогаем)."""
    now = _now()
    unset = {str(x) for x in unset_ids}
    for p in parts:
        need = float(p.prepay_amount or 0)
        if need <= 0:
            continue
        got = paid_after.get(str(p.id), 0.0)
        if got + EPS >= need and p.prepaid_at is None:
            p.prepaid_at = now
        elif got + EPS < need and p.prepaid_at is not None and str(p.id) in unset:
            p.prepaid_at = None


def pay(db: Session, user, booking_id, lines, idem_key, prepay: Optional[bool] = None,
        comment: Optional[str] = None) -> Tuple[Dict[str, Any], bool]:
    """Принять оплату визита. → (чек, новый ли). Повтор с тем же idem_key — тот же чек, без второй оплаты."""
    from backend.services import activity
    from backend.services import booking_service as bs
    _need_take(user)
    idem = _clean_idem(idem_key)
    old = _existing_receipt(db, user.id, idem)
    if old is not None:
        return receipt_to_dict(db, old), False
    items = _clean_lines(lines)
    total = _r(sum(a for _m, a in items))
    b, parts = _visit_parts(db, user.id, booking_id, lock=True)
    old = _existing_receipt(db, user.id, idem)      # пока ждали блокировку, тот же чек мог провестись
    if old is not None:
        return receipt_to_dict(db, old), False
    live = [p for p in parts if p.status != "cancelled"]
    if not live:
        raise CashError("Запись отменена — оплату по ней принять нельзя", "cancelled")
    shift = current_shift(db, user)
    if any(m == "cash" for m, _a in items) and shift is None:
        raise CashError("Наличные принимаются в открытой смене — откройте смену", "shift_required")
    paid = paid_by_booking(db, [p.id for p in parts])
    priced = any(p.price is not None for p in live)
    left = {str(p.id): max(float(p.price or 0) - paid.get(str(p.id), 0.0), 0.0) for p in live}
    left_total = sum(left.values())
    if priced and left_total <= EPS:
        raise CashError("Визит уже оплачен полностью", "paid")
    if priced and total > left_total + EPS:
        raise CashError(f"К оплате осталось {money_text(left_total)} — сумма больше", "too_much")
    # раскладка по услугам визита по порядку
    alloc: List[Tuple[str, Any, float]] = []      # (method, booking, amount)
    queue = [(p, left[str(p.id)]) for p in live]
    qi = 0
    for m, a in items:
        rest = a
        while rest > EPS and qi < len(queue):
            p, need = queue[qi]
            if need <= EPS:
                qi += 1
                continue
            take = min(rest, need)
            alloc.append((m, p, _r(take)))
            queue[qi] = (p, need - take)
            rest -= take
        if rest > EPS:                            # цена не задана — вся сумма на первую услугу визита
            alloc.append((m, live[0], _r(rest)))
    is_prepay = bool(prepay) if prepay is not None else all(
        p.status in ("booked", "confirmed") and _aw(p.start_at) > _now() for p in live)
    who = cashier_of(user)
    now = _now()
    r = CashReceipt(id=uuid.uuid4(), user_id=user.id, shift_id=shift.id if shift else None, idem_key=idem,
                    kind="payment", total=total, booking_id=b.id, visit_id=b.visit_id, contact_id=b.contact_id,
                    client_name=(b.client_name or "")[:200] or None, title=_receipt_title(parts), prepay=is_prepay,
                    comment=(comment or "").strip()[:500] or None, created_by_kind=who["kind"], created_by_id=who["id"],
                    created_by_name=(who["name"] or "")[:120], created_at=now)
    sp = db.begin_nested()
    try:
        db.add(r)
        db.flush()
        for m, p, a in alloc:
            db.add(CashPayment(id=uuid.uuid4(), user_id=user.id, receipt_id=r.id, shift_id=r.shift_id, kind="payment",
                               method=m, amount=a, booking_id=p.id, resource_id=p.resource_id, created_at=now))
        db.flush()
        sp.commit()
    except IntegrityError:
        sp.rollback()
        old = _existing_receipt(db, user.id, idem)
        if old is not None:
            return receipt_to_dict(db, old), False
        raise
    after = dict(paid)
    for _m, p, a in alloc:
        after[str(p.id)] = after.get(str(p.id), 0.0) + a
    _apply_prepaid(parts, after)
    how = ", ".join(f"{METHOD_TITLES[m].lower()} {money_text(a)}" for m, a in items)
    label = bs._booking_label(b, None)
    summary = (f"{'Предоплата' if is_prepay else 'Оплата'} {money_text(total)} ({how}) · {label}, "
               f"{activity.when(_aw(b.start_at))}")
    activity.log(db, user.id, "payment.create", "payment", summary, object_id=r.id, user=user,
                 client_name=b.client_name,
                 meta={"booking_id": str(b.id), "visit_id": str(b.visit_id) if b.visit_id else None,
                       "date": bs.to_local(_aw(b.start_at)).date().isoformat(), "total": total})
    bs.notify_changed(db, user.id)
    _invalidate(user.id)
    db.flush()
    return receipt_to_dict(db, r), True


def refund(db: Session, user, receipt_id, amount, idem_key, reason: Optional[str] = None,
           method: Optional[str] = None) -> Tuple[Dict[str, Any], bool]:
    """Вернуть деньги по чеку оплаты (весь или часть). → (чек возврата, новый ли)."""
    from backend.services import activity
    from backend.services import booking_service as bs
    _need_take(user)
    idem = _clean_idem(idem_key)
    old = _existing_receipt(db, user.id, idem)
    if old is not None:
        return receipt_to_dict(db, old), False
    orig = (db.query(CashReceipt).filter(CashReceipt.id == _uuid(receipt_id, "Чек не найден"),
                                         CashReceipt.user_id == user.id).with_for_update().first())
    if orig is None:
        raise NotFound("Чек не найден")
    old = _existing_receipt(db, user.id, idem)
    if old is not None:
        return receipt_to_dict(db, old), False
    if orig.kind != "payment":
        raise CashError("Вернуть можно только оплату")
    reason = (reason or "").strip()
    if len(reason) < 2:
        raise CashError("Напишите причину возврата")
    a = _amount(amount, "Сумма возврата")
    done = float(db.execute(text("SELECT COALESCE(sum(total), 0) FROM cash_receipts WHERE refund_of = :r"),
                            {"r": str(orig.id)}).scalar() or 0)
    can = _r(float(orig.total) - done)
    if can <= EPS:
        raise CashError("По этому чеку уже всё вернули", "refunded")
    if a > can + EPS:
        raise CashError(f"Вернуть можно не больше {money_text(can)}", "too_much")
    lines = db.query(CashPayment).filter(CashPayment.receipt_id == orig.id).order_by(CashPayment.created_at, CashPayment.id).all()
    m = method if method in PAY_METHODS else (lines[0].method if lines else "cash")
    shift = current_shift(db, user)
    if m == "cash" and shift is None:
        raise CashError("Наличные возвращаются из открытой смены — откройте смену", "shift_required")
    # уже возвращённое по каждой записи этого чека — чтобы возврат лёг на те записи, где ещё есть деньги
    back: Dict[str, float] = {}
    for bid, v in db.execute(text(
            "SELECT cp.booking_id, COALESCE(sum(cp.amount), 0) FROM cash_payments cp JOIN cash_receipts r ON r.id = cp.receipt_id "
            "WHERE r.refund_of = :r GROUP BY 1"), {"r": str(orig.id)}).fetchall():
        back[str(bid)] = -float(v or 0)
    per_booking: Dict[str, Tuple[Any, Any, float]] = {}
    for ln in lines:
        k = str(ln.booking_id)
        bid, res, v = per_booking.get(k, (ln.booking_id, ln.resource_id, 0.0))
        per_booking[k] = (bid, res, v + float(ln.amount))
    rest = a
    alloc: List[Tuple[Any, Any, float]] = []
    for k in reversed(list(per_booking.keys())):      # с последней услуги визита
        bid, res, v = per_booking[k]
        free = max(v - back.get(k, 0.0), 0.0)
        if free <= EPS or rest <= EPS:
            continue
        take = min(rest, free)
        alloc.append((bid, res, _r(take)))
        rest -= take
    if rest > EPS and alloc:
        bid, res, v = alloc[-1]
        alloc[-1] = (bid, res, _r(v + rest))
    who = cashier_of(user)
    now = _now()
    r = CashReceipt(id=uuid.uuid4(), user_id=user.id, shift_id=shift.id if shift else None, idem_key=idem, kind="refund",
                    total=a, booking_id=orig.booking_id, visit_id=orig.visit_id, contact_id=orig.contact_id,
                    client_name=orig.client_name, title=orig.title, refund_of=orig.id, comment=reason[:500],
                    created_by_kind=who["kind"], created_by_id=who["id"], created_by_name=(who["name"] or "")[:120],
                    created_at=now)
    sp = db.begin_nested()
    try:
        db.add(r)
        db.flush()
        for bid, res, v in alloc:
            db.add(CashPayment(id=uuid.uuid4(), user_id=user.id, receipt_id=r.id, shift_id=r.shift_id, kind="refund",
                               method=m, amount=-v, booking_id=bid, resource_id=res, created_at=now))
        db.flush()
        sp.commit()
    except IntegrityError:
        sp.rollback()
        old = _existing_receipt(db, user.id, idem)
        if old is not None:
            return receipt_to_dict(db, old), False
        raise
    if orig.booking_id:
        try:
            b, parts = _visit_parts(db, user.id, orig.booking_id)
            _apply_prepaid(parts, paid_by_booking(db, [p.id for p in parts]), unset_ids=[bid for bid, _r2, _v in alloc])
        except NotFound:
            pass
    activity.log(db, user.id, "payment.refund", "payment",
                 f"Возврат {money_text(a)} ({METHOD_TITLES[m].lower()}) · {orig.client_name or 'клиент'}, {orig.title or ''} · причина: {reason}"[:1000],
                 object_id=r.id, user=user, client_name=orig.client_name,
                 meta={"booking_id": str(orig.booking_id) if orig.booking_id else None,
                       "visit_id": str(orig.visit_id) if orig.visit_id else None, "total": -a})
    bs.notify_changed(db, user.id)
    _invalidate(user.id)
    db.flush()
    return receipt_to_dict(db, r), True


def cash_move(db: Session, user, kind: str, amount, idem_key, comment: Optional[str] = None) -> Tuple[Dict[str, Any], bool]:
    """Внести наличные в кассу или изъять (купили воду, забрал владелец) — только в своей открытой смене."""
    from backend.services import activity
    _need_take(user)
    if kind not in ("cash_in", "cash_out"):
        raise CashError("Неизвестная операция")
    idem = _clean_idem(idem_key)
    old = _existing_receipt(db, user.id, idem)
    if old is not None:
        return receipt_to_dict(db, old), False
    a = _amount(amount, "Сумма")
    comment = (comment or "").strip()
    if kind == "cash_out" and len(comment) < 2:
        raise CashError("Напишите, на что или кому выдали деньги")
    shift = current_shift(db, user, lock=True)
    if shift is None:
        raise CashError("Сначала откройте смену", "shift_required")
    old = _existing_receipt(db, user.id, idem)
    if old is not None:
        return receipt_to_dict(db, old), False
    if kind == "cash_out":
        have = shift_numbers(db, shift)["expected_cash"]
        if a > have + EPS:
            raise CashError(f"В кассе по чекам {money_text(have)} — изъять больше нельзя", "too_much")
    who = cashier_of(user)
    now = _now()
    r = CashReceipt(id=uuid.uuid4(), user_id=user.id, shift_id=shift.id, idem_key=idem, kind=kind, total=a,
                    title=comment[:300] or None, comment=comment[:500] or None, created_by_kind=who["kind"],
                    created_by_id=who["id"], created_by_name=(who["name"] or "")[:120], created_at=now)
    sp = db.begin_nested()
    try:
        db.add(r)
        db.flush()
        db.add(CashPayment(id=uuid.uuid4(), user_id=user.id, receipt_id=r.id, shift_id=shift.id, kind=kind,
                           method="cash", amount=a if kind == "cash_in" else -a, created_at=now))
        db.flush()
        sp.commit()
    except IntegrityError:
        sp.rollback()
        old = _existing_receipt(db, user.id, idem)
        if old is not None:
            return receipt_to_dict(db, old), False
        raise
    activity.log(db, user.id, "cash.in" if kind == "cash_in" else "cash.out", "cash",
                 f"{KIND_TITLES[kind]} {money_text(a)}" + (f" · {comment}" if comment else ""), object_id=r.id, user=user,
                 meta={"shift_id": str(shift.id)})
    return receipt_to_dict(db, r), True


# ───────────────────────────── чеки ─────────────────────────────

def _lines_of(db: Session, receipt_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    if not receipt_ids:
        return {}
    out: Dict[str, List[Dict[str, Any]]] = {}
    for rid, m, total in db.execute(text(
            "SELECT receipt_id, method, sum(amount) FROM cash_payments WHERE receipt_id = ANY(CAST(:ids AS uuid[])) "
            "GROUP BY 1, 2 ORDER BY 1, 2"), {"ids": receipt_ids}).fetchall():
        out.setdefault(str(rid), []).append({"method": m, "title": METHOD_TITLES.get(m, m), "amount": _r(abs(float(total or 0)))})
    return out


def _refunded_of(db: Session, receipt_ids: List[str]) -> Dict[str, float]:
    if not receipt_ids:
        return {}
    return {str(k): _r(v) for k, v in db.execute(text(
        "SELECT refund_of, COALESCE(sum(total), 0) FROM cash_receipts WHERE refund_of = ANY(CAST(:ids AS uuid[])) GROUP BY 1"),
        {"ids": receipt_ids}).fetchall()}


def _receipts_dicts(db: Session, rows: List[CashReceipt]) -> List[Dict[str, Any]]:
    ids = [str(r.id) for r in rows]
    lines = _lines_of(db, ids)
    back = _refunded_of(db, [str(r.id) for r in rows if r.kind == "payment"])
    bids = list({str(r.booking_id) for r in rows if r.booking_id})
    days: Dict[str, str] = {}
    if bids:   # день записи — ссылка «Запись» открывает журнал на нужном дне
        for bid, st in db.execute(text("SELECT id, start_at FROM bookings WHERE id = ANY(CAST(:ids AS uuid[]))"),
                                  {"ids": bids}).fetchall():
            days[str(bid)] = _aw(st).astimezone(BISHKEK).date().isoformat()
    out = []
    for r in rows:
        d = {"id": str(r.id), "kind": r.kind, "kind_title": KIND_TITLES.get(r.kind, r.kind), "total": _r(r.total),
             "sign": -1 if r.kind in ("refund", "cash_out") else 1, "lines": lines.get(str(r.id), []),
             "client_name": r.client_name, "title": r.title, "comment": r.comment or "", "prepay": bool(r.prepay),
             "booking_id": str(r.booking_id) if r.booking_id else None, "visit_id": str(r.visit_id) if r.visit_id else None,
             "booking_date": days.get(str(r.booking_id)) if r.booking_id else None,
             "shift_id": str(r.shift_id) if r.shift_id else None, "refund_of": str(r.refund_of) if r.refund_of else None,
             "by": r.created_by_name or "", "at": _aw(r.created_at).isoformat()}
        if r.kind == "payment":
            d["refunded"] = back.get(str(r.id), 0.0)
            d["refundable"] = _r(max(float(r.total) - d["refunded"], 0))
        out.append(d)
    return out


def receipt_to_dict(db: Session, r: CashReceipt) -> Dict[str, Any]:
    return _receipts_dicts(db, [r])[0]


def _receipts_for_bookings(db: Session, user_id, booking_ids) -> List[Dict[str, Any]]:
    ids = [str(x) for x in booking_ids if x]
    if not ids:
        return []
    from sqlalchemy import select
    sub = select(CashPayment.receipt_id).where(CashPayment.booking_id.in_([uuid.UUID(i) for i in ids]))
    rows = (db.query(CashReceipt).filter(CashReceipt.user_id == user_id, CashReceipt.id.in_(sub))
            .order_by(CashReceipt.created_at).all())
    return _receipts_dicts(db, rows)


def day_range(day: Optional[str]) -> Tuple[date, datetime, datetime]:
    if day:
        try:
            d = date.fromisoformat(str(day)[:10])
        except ValueError:
            raise CashError("Неверная дата")
    else:
        d = _now().astimezone(BISHKEK).date()
    a = datetime(d.year, d.month, d.day, tzinfo=BISHKEK).astimezone(timezone.utc)
    return d, a, a + timedelta(days=1)


def list_receipts(db: Session, user, shift_id=None, day: Optional[str] = None, before: Optional[str] = None,
                  limit: int = 100) -> Dict[str, Any]:
    q = db.query(CashReceipt).filter(CashReceipt.user_id == user.id)
    if shift_id:
        sh = _get_shift(db, user.id, shift_id)
        if not _own(user, sh) and not can_manage(user):
            raise Forbidden("Это чужая смена")
        q = q.filter(CashReceipt.shift_id == sh.id)
    else:
        _d, a, b = day_range(day)
        q = q.filter(CashReceipt.created_at >= a, CashReceipt.created_at < b)
        if not can_manage(user):
            q = q.filter(CashReceipt.created_by_kind == cashier_of(user)["kind"],
                         CashReceipt.created_by_id == cashier_of(user)["id"])
    if before:
        try:
            q = q.filter(CashReceipt.created_at < datetime.fromisoformat(str(before).replace("Z", "+00:00")))
        except ValueError:
            pass
    limit = max(1, min(int(limit or 100), 300))
    rows = q.order_by(CashReceipt.created_at.desc()).limit(limit + 1).all()
    more = len(rows) > limit
    rows = rows[:limit]
    return {"items": _receipts_dicts(db, rows), "next": _aw(rows[-1].created_at).isoformat() if more and rows else None}


def day_numbers(db: Session, user_id, day: Optional[str] = None) -> Dict[str, Any]:
    """Итоги дня по всем кассирам: по способам, оплаты, возвраты, чеков."""
    d, a, b = day_range(day)
    by_method = {m: 0.0 for m in PAY_METHODS}
    payments = refunds = 0.0
    for kind, method, total in db.execute(text(
            "SELECT kind, method, COALESCE(sum(amount), 0) FROM cash_payments WHERE user_id = :u AND created_at >= :a "
            "AND created_at < :b AND kind IN ('payment', 'refund') GROUP BY 1, 2"), {"u": str(user_id), "a": a, "b": b}).fetchall():
        by_method[method] = by_method.get(method, 0.0) + float(total or 0)
        if kind == "payment":
            payments += float(total or 0)
        else:
            refunds += -float(total or 0)
    n = db.execute(text("SELECT count(*) FROM cash_receipts WHERE user_id = :u AND kind = 'payment' AND created_at >= :a "
                        "AND created_at < :b"), {"u": str(user_id), "a": a, "b": b}).scalar() or 0
    return {"date": d.isoformat(), "by_method": {k: _r(v) for k, v in by_method.items()}, "payments": _r(payments),
            "refunds": _r(refunds), "revenue": _r(payments - refunds), "receipts": int(n)}


def state(db: Session, user) -> Dict[str, Any]:
    """Экран «Касса»: моя смена и её итоги, подсказка «в кассе оставили», чужие открытые смены (владельцу), день."""
    sh = current_shift(db, user)
    out = {"can_take": can_take(user), "can_manage": can_manage(user), "methods": methods(),
           "shift": shift_to_dict(db, sh) if sh else None, "suggest_opening": suggest_opening(db, user.id),
           "cashier": cashier_of(user)["name"], "cash_start": None}
    if can_manage(user):
        others = (db.query(CashShift).filter(CashShift.user_id == user.id, CashShift.status == "open")
                  .order_by(CashShift.opened_at).all())
        out["open_shifts"] = [shift_to_dict(db, s) for s in others if not sh or s.id != sh.id]
        out["today"] = day_numbers(db, user.id)
        cs = cash_start(db, user.id)
        out["cash_start"] = cs.isoformat() if cs else None
    return out


# ───────────────────────────── выручка «по оплатам» ─────────────────────────────

_START_CACHE: Dict[str, datetime] = {}


def cash_start(db: Session, user_id) -> Optional[datetime]:
    """С какого момента салон работает через кассу (первая смена или первый чек). None — кассой не пользуется."""
    k = str(user_id)
    if k in _START_CACHE:
        return _START_CACHE[k]
    try:
        v = db.execute(text(
            "SELECT LEAST((SELECT min(opened_at) FROM cash_shifts WHERE user_id = :u), "
            "(SELECT min(created_at) FROM cash_receipts WHERE user_id = :u))"), {"u": k}).scalar()
    except Exception:
        db.rollback()
        return None
    if v is not None:
        v = _aw(v)
        _START_CACHE[k] = v
    return v


def _revenue_parts_sql(by_day: bool, off: int = 6) -> Tuple[str, str]:
    day = f"date(start_at + interval '{off} hour')" if by_day else None
    pday = f"date(cp.created_at + interval '{off} hour')" if by_day else None
    visits = ("SELECT " + (day + ", " if by_day else "") + "COALESCE(sum(price), 0) FROM bookings WHERE user_id = :uid "
              "AND status IN ('arrived', 'completed') AND start_at >= :s AND start_at < :u "
              "AND (CAST(:t AS timestamptz) IS NULL OR start_at < CAST(:t AS timestamptz))" + (" GROUP BY 1" if by_day else ""))
    pays = ("SELECT " + (pday + ", " if by_day else "") + "COALESCE(sum(cp.amount), 0) FROM cash_payments cp "
            "LEFT JOIN bookings b ON b.id = cp.booking_id WHERE cp.user_id = :uid AND cp.kind IN ('payment', 'refund') "
            "AND cp.created_at >= :s AND cp.created_at < :u AND (b.id IS NULL OR b.start_at >= CAST(:t AS timestamptz))"
            + (" GROUP BY 1" if by_day else ""))
    return visits, pays


def booking_revenue(db: Session, user_id, s: datetime, u: datetime) -> float:
    """Выручка услуг за [s, u): до начала кассы — цена визитов «пришёл», дальше — оплаты (минус возвраты)."""
    t = cash_start(db, user_id)
    visits, pays = _revenue_parts_sql(False)
    p = {"uid": str(user_id), "s": s, "u": u, "t": t}
    total = float(db.execute(text(visits), p).scalar() or 0)
    if t is not None and u > t:
        total += float(db.execute(text(pays), p).scalar() or 0)
    return _r(total)


def booking_revenue_by_day(db: Session, user_id, s: datetime, u: datetime, off: int = 6) -> Dict[date, float]:
    t = cash_start(db, user_id)
    visits, pays = _revenue_parts_sql(True, off)
    p = {"uid": str(user_id), "s": s, "u": u, "t": t}
    out: Dict[date, float] = {}
    for d, v in db.execute(text(visits), p).fetchall():
        out[d] = out.get(d, 0.0) + float(v or 0)
    if t is not None and u > t:
        for d, v in db.execute(text(pays), p).fetchall():
            out[d] = out.get(d, 0.0) + float(v or 0)
    return out


def revenue_basis(db: Session, user_id) -> Dict[str, Any]:
    t = cash_start(db, user_id)
    return {"basis": "payments" if t else "visits",
            "since": t.astimezone(BISHKEK).date().isoformat() if t else None}


# ───────────────────────────── главная салона и оповещения ─────────────────────────────

def attention(db: Session, user_id) -> List[Dict[str, Any]]:
    """«Требует внимания»: расхождение в закрытых сегодня сменах, смена открыта слишком долго."""
    out: List[Dict[str, Any]] = []
    try:
        _d, a, b = day_range(None)
        for sh in (db.query(CashShift).filter(CashShift.user_id == user_id, CashShift.status == "closed",
                                              CashShift.closed_at >= a - timedelta(hours=12)).all()):
            if sh.diff is not None and abs(sh.diff) >= 1:
                out.append({"kind": "cash_diff", "shift_id": str(sh.id), "cashier": sh.opened_by_name,
                            "diff": _r(sh.diff), "at": _aw(sh.closed_at).isoformat()})
        long_ago = _now() - timedelta(hours=LONG_SHIFT_HOURS)
        for sh in db.query(CashShift).filter(CashShift.user_id == user_id, CashShift.status == "open",
                                             CashShift.opened_at < long_ago).all():
            out.append({"kind": "shift_long", "shift_id": str(sh.id), "cashier": sh.opened_by_name,
                        "opened_at": _aw(sh.opened_at).isoformat()})
    except Exception as e:
        db.rollback()
        logger.warning(f"[CASH] attention: {e}")
    return out


def _notify_diff_submit(owner_id, shift_id) -> None:
    try:
        from backend.core import background_loop
        background_loop.submit(notify_diff(owner_id, shift_id))
    except Exception as e:
        logger.warning(f"[CASH] diff notify not scheduled: {e}")


NOTIFY_DIFF = _notify_diff_submit      # тесты подменяют


async def notify_diff(owner_id, shift_id) -> None:
    """Расхождение при закрытии смены — владельцу в Telegram-бот агента и push."""
    from backend.db.session import SessionLocal
    from backend.services import booking_service as bs
    from backend.services import owner_notifications, push_service
    db = SessionLocal()
    try:
        sh = db.query(CashShift).filter(CashShift.id == shift_id).first()
        if sh is None or sh.diff is None:
            return
        word = "лишние" if sh.diff > 0 else "не хватает"
        msg = (f"💵 Касса: при закрытии смены {sh.opened_by_name or 'кассира'} {word} {money_text(abs(sh.diff))}.\n"
               f"Ожидалось наличными {money_text(sh.expected_cash)}, насчитали {money_text(sh.counted_cash)}."
               + (f"\nКомментарий: {sh.note}" if sh.note else "") + "\nПодробно — в разделе «Касса».")
        agent = bs._first_agent(db, owner_id)
        if agent is not None:
            await owner_notifications._send(agent, db, msg)
        push_service.send_to_user_sync(db, owner_id, "Касса: расхождение", msg.split("\n")[0][2:],
                                       "/static/cash.html#shifts", tag="cash")
    except Exception as e:
        logger.warning(f"[CASH] diff notify: {e}")
    finally:
        db.close()


def _invalidate(user_id) -> None:
    try:
        from backend.services import analytics_service
        analytics_service.invalidate(user_id)
    except Exception:
        pass
