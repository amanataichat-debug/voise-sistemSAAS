"""
Заказы: оформление, статусы, склад, оповещения (ветка 1811-niche-admin-speed).

Одна реализация на все входы — страница «Заказы» владельца и сотрудников (/api/orders), бот в
переписке (инструменты create_order / list_orders / update_order, services/order_tools.py) и
голосовой помощник владельца (get_orders). Правила везде одни:

  • номер заказа — по порядку у владельца, с 1001 («№ 1024»); выдаётся под транзакционной
    блокировкой владельца (pg_advisory_xact_lock), два заказа одновременно не получат один номер;
  • позиция — из каталога (agent_knowledge_entries, kind=product: по id или по названию словами)
    или свободной строкой (только владелец/сотрудник; бот берёт только то, что есть в каталоге);
    у позиций с размерами/цветами указывается вариант, бот не продаёт то, чего нет в наличии;
  • остаток каталога резервируется сразу при оформлении (последнюю шубу не продадут дважды) и
    возвращается при отмене; события склада («закончился», «осталось мало») — владельцу, как при
    правке каталога;
  • статусы: new (новый) → confirmed (подтверждён) → packed (собран / готовится) → delivering
    (в пути) → done (выдан), cancelled (отменён). Самовывоз: packed = «готов, можно забирать»;
  • клиент заказа — карточка CRM (по id, по телефону или новая): новый клиент → стадия «В работе»
    (active), выданный заказ → «Успех» (success), если этапы есть в воронке;
  • сообщения клиенту о статусе (принят, готов к выдаче, передан курьеру) — текстом кода, в мессенджер,
    где клиент писал последним, один раз на статус; выключаются настройкой notify_client.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.models.order import OPEN_STATUSES, ORDER_STATUSES, Order, OrderSettings

logger = logging.getLogger(__name__)

BISHKEK = timezone(timedelta(hours=6))
FIRST_NUMBER = 1001
MAX_ITEMS = 50
MAX_QTY = 999
DEFAULT_SETTINGS = {
    "notify_client": True,       # сообщать клиенту: принят, готов к выдаче, передан курьеру
    "delivery_fee": 0,           # доставка, сом (0 — бесплатно / не считаем)
    "free_delivery_from": 0,     # бесплатная доставка от суммы, сом (0 — нет)
    "pickup_address": "",        # откуда забирать при самовывозе
    "payment_note": "",          # как оплатить (номер карты / QR) — в сообщение «принят»
}
STATUS_TITLES = {"new": "Новый", "confirmed": "Подтверждён", "packed": "Собран", "delivering": "В пути",
                 "done": "Выдан", "cancelled": "Отменён"}
FOOD_TITLES = {**STATUS_TITLES, "confirmed": "Принят", "packed": "Готовится"}
PAYMENT_TITLES = {"cash": "наличными", "card": "картой", "transfer": "переводом", "qr": "по QR"}
NOTIFY_ON = ("confirmed", "packed", "delivering")


class OrderError(ValueError):
    """Понятная причина отказа (уходит владельцу, сотруднику или боту как есть)."""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fmt_local(dt: Optional[datetime]) -> str:
    dt = _aware(dt)
    if dt is None:
        return ""
    loc = dt.astimezone(BISHKEK)
    today = now_utc().astimezone(BISHKEK).date()
    hm = loc.strftime("%H:%M")
    if loc.date() == today:
        return f"сегодня {hm}"
    if loc.date() == today - timedelta(days=1):
        return f"вчера {hm}"
    return f"{loc.day:02d}.{loc.month:02d} {hm}"


def money(v) -> str:
    try:
        x = float(v or 0)
    except (TypeError, ValueError):
        x = 0.0
    s = f"{x:,.0f}" if abs(x - round(x)) < 0.005 else f"{x:,.2f}"
    return s.replace(",", " ") + " сом"


def is_food(user) -> bool:
    try:
        from backend.services import niche_presets
        return niche_presets.preset(getattr(user, "business_niche", None)).get("group") == "food"
    except Exception:
        return False


def status_titles(user) -> Dict[str, str]:
    return FOOD_TITLES if is_food(user) else STATUS_TITLES


def orders_enabled(user) -> bool:
    try:
        from backend.services.business_profile import modules_of
        return bool(user is not None and modules_of(user).get("orders"))
    except Exception:
        return False


# ───────────────────────────── настройки ─────────────────────────────

def get_settings(db: Session, user_id) -> Dict[str, Any]:
    row = db.query(OrderSettings).filter(OrderSettings.user_id == user_id).first()
    out = dict(DEFAULT_SETTINGS)
    if row is not None and isinstance(row.data, dict):
        out.update({k: v for k, v in row.data.items() if k in DEFAULT_SETTINGS})
    return out


def save_settings(db: Session, user_id, patch: Dict[str, Any]) -> Dict[str, Any]:
    cur = get_settings(db, user_id)
    for k, v in (patch or {}).items():
        if k not in DEFAULT_SETTINGS or v is None:
            continue
        if k == "notify_client":
            cur[k] = bool(v)
        elif k in ("delivery_fee", "free_delivery_from"):
            try:
                cur[k] = max(0.0, min(float(v), 1_000_000.0))
            except (TypeError, ValueError):
                raise OrderError("Укажите сумму числом")
        else:
            cur[k] = str(v).strip()[:400]
    row = db.query(OrderSettings).filter(OrderSettings.user_id == user_id).first()
    if row is None:
        db.add(OrderSettings(user_id=user_id, data=cur))
    else:
        row.data = cur
    db.flush()
    return cur


# ───────────────────────────── каталог ─────────────────────────────

def products(db: Session, user_id, query: Optional[str] = None, limit: int = 30) -> List[Any]:
    """Позиции каталога для заказа: активные, по запросу — подходящие по названию и характеристикам."""
    from backend.models.agent_knowledge import AgentKnowledgeEntry
    q = (db.query(AgentKnowledgeEntry)
         .filter(AgentKnowledgeEntry.user_id == user_id, AgentKnowledgeEntry.kind == "product",
                 AgentKnowledgeEntry.is_active.is_(True)))
    rows = q.order_by(AgentKnowledgeEntry.collection, AgentKnowledgeEntry.title).limit(2000).all()
    query = (query or "").strip().lower()
    if not query:
        return rows[:limit]
    words = [w for w in re.split(r"[^\wёңөү-]+", query) if len(w) >= 2]
    scored = []
    for e in rows:
        title = (e.title or "").lower()
        hay = " ".join([title, (e.keywords or "").lower(), (e.collection or "").lower(),
                        " ".join(str(v).lower() for v in (e.attributes or {}).values() if not isinstance(v, (dict, list)))])
        score = 20 if query == title else (10 if query in title else 0)
        score += sum(3 if w in title else (2 if w[:5] in hay else 0) for w in words)
        if score:
            scored.append((score, e))
    scored.sort(key=lambda x: -x[0])
    return [e for _, e in scored[:limit]]


def find_product(db: Session, user_id, entry_id=None, query: Optional[str] = None):
    from backend.models.agent_knowledge import AgentKnowledgeEntry
    if entry_id:
        try:
            eid = uuid.UUID(str(entry_id))
        except (ValueError, TypeError):
            eid = None
        if eid:
            e = db.query(AgentKnowledgeEntry).filter(AgentKnowledgeEntry.id == eid, AgentKnowledgeEntry.user_id == user_id,
                                                     AgentKnowledgeEntry.kind == "product").first()
            if e:
                return e
    if not (query or "").strip():
        return None
    found = products(db, user_id, query, limit=2)
    return found[0] if found else None


def product_to_dict(e) -> Dict[str, Any]:
    from backend.services import catalog_service
    variants = [v for v in (e.variants or []) if isinstance(v, dict)]
    return {
        "id": str(e.id), "title": e.title, "price": e.price, "currency": e.currency or "KGS",
        "collection": e.collection or "", "in_stock": bool(e.in_stock), "stock_qty": e.stock_qty,
        "sizes": catalog_service.sizes_in_stock(e) if variants else list(e.sizes or []),
        "colors": catalog_service.colors_in_stock(e) if variants else [],
        "variants": [{"size": v.get("size") or "", "color": v.get("color") or "", "qty": int(v.get("qty") or 0)} for v in variants],
    }


def _norm(s) -> str:
    return " ".join(str(s or "").lower().replace("ё", "е").split())


def _variant(row, size: str, color: str) -> Optional[Dict[str, Any]]:
    for v in (row.variants or []):
        if (not size or _norm(v.get("size")) == _norm(size)) and (not color or _norm(v.get("color")) == _norm(color)):
            return v
    return None


def clean_items(db: Session, user, raw_items, strict: bool) -> List[Dict[str, Any]]:
    """
    Позиции заказа: из каталога (по id или названию) или свободной строкой. strict (бот): только
    каталог, вариант обязателен, если у позиции есть размеры/цвета, и он должен быть в наличии.
    """
    if not isinstance(raw_items, list) or not raw_items:
        raise OrderError("В заказе нет позиций")
    out: List[Dict[str, Any]] = []
    for raw in raw_items[:MAX_ITEMS]:
        if not isinstance(raw, dict):
            continue
        try:
            qty = int(float(raw.get("qty") or raw.get("quantity") or 1))
        except (TypeError, ValueError):
            qty = 1
        qty = max(1, min(qty, MAX_QTY))
        size = " ".join(str(raw.get("size") or "").split())[:20]
        color = " ".join(str(raw.get("color") or "").split())[:40]
        name = " ".join(str(raw.get("title") or raw.get("name") or raw.get("product") or "").split())[:300]
        row = find_product(db, user.id, raw.get("entry_id") or raw.get("product_id"), name)
        price = raw.get("price")
        try:
            price = None if price in (None, "") else max(0.0, float(price))
        except (TypeError, ValueError):
            raise OrderError("Цена позиции — числом")
        if row is None:
            if strict:
                raise OrderError(f"Не нашёл «{name or 'позицию'}» в каталоге — уточните у клиента, что именно он хочет")
            if not name:
                raise OrderError("У позиции нет названия")
            out.append({"entry_id": None, "title": name, "size": size, "color": color, "qty": qty,
                        "price": price if price is not None else 0.0})
            continue
        if row.variants:
            v = _variant(row, size, color)
            sizes = sorted({x.get("size") for x in row.variants if x.get("size")})
            colors = sorted({x.get("color") for x in row.variants if x.get("color")})
            if strict and ((sizes and not size) or (colors and not color and len(colors) > 1)):
                need = "размер" if sizes and not size else "цвет"
                raise OrderError(f"У «{row.title}» нужно уточнить {need}: "
                                 + ", ".join(x for x in (sizes if need == 'размер' else colors) if x))
            if v is None and (size or color) and strict:
                raise OrderError(f"У «{row.title}» нет варианта «{' '.join(x for x in (size, color) if x)}»")
            if v is None and not strict and len(row.variants) == 1:
                v = row.variants[0]   # единственный вариант — выбирать нечего
            if v is not None:
                size = size or v.get("size") or ""
                color = color or v.get("color") or ""
                if strict and int(v.get("qty") or 0) < qty:
                    left = int(v.get("qty") or 0)
                    raise OrderError(f"«{row.title}» ({' '.join(x for x in (size, color) if x)}) — "
                                     + (f"осталось только {left} шт." if left else "нет в наличии"))
        elif strict and (not row.in_stock or (row.stock_qty is not None and row.stock_qty < qty)):
            left = int(row.stock_qty or 0) if row.stock_qty is not None else 0
            raise OrderError(f"«{row.title}» — " + (f"осталось только {left} шт." if left else "нет в наличии"))
        out.append({"entry_id": str(row.id), "title": row.title, "size": size, "color": color, "qty": qty,
                    "price": price if price is not None else float(row.price or 0)})
    if not out:
        raise OrderError("В заказе нет позиций")
    return out


def _adjust_stock(db: Session, items: List[Dict[str, Any]], sign: int) -> List[Tuple[Any, str]]:
    """Остаток каталога: sign=-1 — зарезервировать, +1 — вернуть. События склада — владельцу."""
    from backend.models.agent_knowledge import AgentKnowledgeEntry
    from backend.services import catalog_service
    events: List[Tuple[Any, str]] = []
    for it in items or []:
        if not it.get("entry_id"):
            continue
        try:
            eid = uuid.UUID(str(it["entry_id"]))
        except (ValueError, TypeError):
            continue
        row = db.query(AgentKnowledgeEntry).filter(AgentKnowledgeEntry.id == eid).with_for_update().first()
        if row is None:
            continue
        before = catalog_service.stock_state(row)
        q = int(it.get("qty") or 1)
        if row.variants:
            if not it.get("size") and not it.get("color"):
                continue   # вариант не указан (владелец не выбрал размер) — не угадываем, какой списать
            variants = [dict(v) for v in row.variants]
            hit = None
            for v in variants:
                if (not it.get("size") or _norm(v.get("size")) == _norm(it.get("size"))) and \
                        (not it.get("color") or _norm(v.get("color")) == _norm(it.get("color"))):
                    hit = v
                    break
            if hit is None:
                continue
            hit["qty"] = max(0, int(hit.get("qty") or 0) + sign * q)
            row.variants = variants
            catalog_service.apply_variants(row)
        elif row.stock_qty is not None:
            row.stock_qty = max(0, int(row.stock_qty) + sign * q)
            row.in_stock = row.stock_qty > 0
        else:
            continue
        ev = catalog_service.stock_event(before, row)
        if ev:
            events.append((row, ev))
    return events


def _notify_stock(db: Session, user_id, events) -> None:
    if not events:
        return
    try:
        from backend.services import catalog_service
        agent = _first_agent(db, user_id)
        if agent is not None:
            catalog_service.schedule_stock_notify(agent.id, events)
    except Exception as e:
        logger.warning(f"[ORDERS] stock notify skipped: {e}")


def _first_agent(db: Session, user_id):
    from backend.models.agent_config import AgentConfig
    return db.query(AgentConfig).filter(AgentConfig.user_id == user_id).order_by(AgentConfig.created_at).first()


# ───────────────────────────── суммы и номер ─────────────────────────────

def totals(items: List[Dict[str, Any]], delivery_type: str, cfg: Dict[str, Any],
           delivery_fee: Optional[float] = None, discount: float = 0) -> Tuple[float, float, float]:
    sub = round(sum(float(i.get("price") or 0) * int(i.get("qty") or 1) for i in items), 2)
    if delivery_type != "delivery":
        fee = 0.0
    elif delivery_fee is not None:
        fee = max(0.0, float(delivery_fee))
    else:
        fee = float(cfg.get("delivery_fee") or 0)
        free_from = float(cfg.get("free_delivery_from") or 0)
        if free_from and sub >= free_from:
            fee = 0.0
    disc = max(0.0, min(float(discount or 0), sub))
    return sub, fee, round(sub + fee - disc, 2)


def _next_number(db: Session, user_id) -> int:
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"orders:{user_id}"})
    cur = db.execute(text("SELECT max(number) FROM orders WHERE user_id = :u"), {"u": str(user_id)}).scalar()
    return max(int(cur or 0) + 1, FIRST_NUMBER)


def _stage(db: Session, contact, key: str, only_from: Iterable[str]) -> None:
    """Стадия CRM клиента: только если она есть в воронке и клиент сейчас на одной из only_from."""
    if contact is None or (contact.status or "new") not in set(only_from):
        return
    try:
        from backend.core.pipeline_stages import get_agent_stage_keys
        from backend.models.agent_config import AgentConfig
        agent = db.query(AgentConfig).filter(AgentConfig.id == contact.agent_config_id).first()
        if agent is not None and key in get_agent_stage_keys(agent):
            contact.status = key
    except Exception as e:
        logger.warning(f"[ORDERS] stage not moved: {e}")


# ───────────────────────────── заказ ─────────────────────────────

def create_order(db: Session, user, *, items, contact_id=None, client_name: Optional[str] = None,
                 client_phone: Optional[str] = None, delivery_type: str = "delivery", address: Optional[str] = None,
                 delivery_when: Optional[str] = None, payment_method: Optional[str] = None, note: Optional[str] = None,
                 source: str = "owner", channel: Optional[str] = None, created_by: Optional[str] = None,
                 delivery_fee: Optional[float] = None, discount: float = 0, paid: bool = False) -> Order:
    """Оформить заказ. Бот (source=bot) — строго по каталогу и наличию, с адресом для доставки."""
    from backend.services import booking_service as bs
    from backend.utils.phone import normalize_phone_e164
    strict = source == "bot"
    delivery_type = "pickup" if str(delivery_type or "").lower() in ("pickup", "самовывоз") else "delivery"
    clean = clean_items(db, user, items, strict)
    address = (address or "").strip()[:1000] or None
    if strict and delivery_type == "delivery" and not address:
        raise OrderError("Для доставки нужен адрес — спросите у клиента, куда привезти")
    phone = normalize_phone_e164(client_phone) if client_phone else None
    if client_phone and not phone and strict:
        raise OrderError("Проверьте номер телефона клиента")
    if payment_method and payment_method not in PAYMENT_TITLES:
        payment_method = None
    contact = bs.resolve_contact(db, user.id, contact_id, client_name, phone)
    cfg = get_settings(db, user.id)
    sub, fee, total = totals(clean, delivery_type, cfg, delivery_fee, discount)
    number = _next_number(db, user.id)
    real_phone = phone or (contact.phone if contact is not None and str(contact.phone or "").startswith("+") else None)
    o = Order(user_id=user.id, number=number, contact_id=contact.id if contact else None,
              client_name=(client_name or (contact.name if contact else None) or "")[:200] or None,
              client_phone=real_phone, status="new", items=clean, subtotal=sub, delivery_fee=fee,
              discount=max(0.0, float(discount or 0)), total=total, delivery_type=delivery_type, address=address,
              delivery_when=(delivery_when or "").strip()[:120] or None, payment_method=payment_method, paid=bool(paid),
              source=source, channel=channel, created_by=(created_by or None) and str(created_by)[:120],
              note=(note or "").strip()[:2000] or None, stock_reserved=True)
    db.add(o)
    events = _adjust_stock(db, clean, -1)
    _stage(db, contact, "active", ("new",))
    db.flush()
    _notify_stock(db, user.id, events)
    return o


def get_order(db: Session, user_id, order_id) -> Order:
    try:
        oid = uuid.UUID(str(order_id))
    except (ValueError, TypeError):
        raise OrderError("Заказ не найден")
    o = db.query(Order).filter(Order.id == oid, Order.user_id == user_id).first()
    if o is None:
        raise OrderError("Заказ не найден")
    return o


def find_by_number(db: Session, user_id, number) -> Optional[Order]:
    try:
        n = int(str(number).strip().lstrip("№#").strip())
    except (ValueError, TypeError):
        return None
    return db.query(Order).filter(Order.user_id == user_id, Order.number == n).first()


def set_status(db: Session, user, order_id, status: str, reason: Optional[str] = None) -> Tuple[Order, str]:
    """Сменить статус. Отмена возвращает остаток, возврат из отмены снова резервирует. → (заказ, прежний)."""
    if status not in ORDER_STATUSES:
        raise OrderError("Неизвестный статус заказа")
    o = get_order(db, user.id, order_id)
    prev = o.status
    if prev == status:
        return o, prev
    now = now_utc()
    events = []
    if status == "cancelled":
        if o.stock_reserved:
            events = _adjust_stock(db, o.items or [], +1)
            o.stock_reserved = False
        o.cancelled_at = now
        o.cancel_reason = (reason or "").strip()[:300] or None
    elif prev == "cancelled":
        if not o.stock_reserved:
            events = _adjust_stock(db, o.items or [], -1)
            o.stock_reserved = True
        o.cancelled_at = None
        o.cancel_reason = None
    if status == "confirmed" and not o.confirmed_at:
        o.confirmed_at = now
    if status == "done":
        o.done_at = now
        from backend.models.agent_contact import AgentContact
        contact = db.query(AgentContact).filter(AgentContact.id == o.contact_id).first() if o.contact_id else None
        _stage(db, contact, "success", ("new", "active"))
    elif prev == "done":
        o.done_at = None
    o.status = status
    db.flush()
    _notify_stock(db, user.id, events)
    return o, prev


EDITABLE = ("client_name", "client_phone", "address", "delivery_when", "delivery_type", "payment_method", "paid",
            "note", "delivery_fee", "discount")


def update_order(db: Session, user, order_id, data: Dict[str, Any]) -> Order:
    """Правка заказа: данные клиента и доставки, оплата, позиции (остаток пересчитывается)."""
    o = get_order(db, user.id, order_id)
    if o.status in ("done", "cancelled") and "items" in data:
        raise OrderError("Выданный или отменённый заказ не меняют — оформите новый")
    prev_type = o.delivery_type
    for k in EDITABLE:
        if k not in data or data[k] is None and k not in ("delivery_when", "address", "note", "payment_method"):
            continue
        v = data[k]
        if k == "delivery_type":
            o.delivery_type = "pickup" if v == "pickup" else "delivery"
        elif k == "payment_method":
            o.payment_method = v if v in PAYMENT_TITLES else None
        elif k == "paid":
            o.paid = bool(v)
        elif k in ("delivery_fee", "discount"):
            try:
                setattr(o, k, max(0.0, float(v or 0)))
            except (TypeError, ValueError):
                raise OrderError("Сумма — числом")
        elif k == "client_phone":
            from backend.utils.phone import normalize_phone_e164
            o.client_phone = normalize_phone_e164(v) if v else None
        else:
            limit = {"client_name": 200, "delivery_when": 120}.get(k, 2000)
            setattr(o, k, (str(v or "").strip()[:limit]) or None)
    events = []
    if "items" in data and data["items"] is not None:
        new_items = clean_items(db, user, data["items"], strict=False)
        if o.stock_reserved:
            events += _adjust_stock(db, o.items or [], +1)
        events += _adjust_stock(db, new_items, -1)
        o.items = new_items
        o.stock_reserved = True
    cfg = get_settings(db, user.id)
    # Доставка: задали руками — как задали; была доставка и осталась — прежняя сумма; иначе — по настройкам
    if "delivery_fee" in data and data["delivery_fee"] is not None:
        fee_arg = o.delivery_fee
    elif o.delivery_type == "delivery" and prev_type == "delivery":
        fee_arg = o.delivery_fee
    else:
        fee_arg = None
    o.subtotal, o.delivery_fee, o.total = totals(o.items or [], o.delivery_type, cfg, fee_arg, o.discount or 0)
    db.flush()
    _notify_stock(db, user.id, events)
    return o


def list_orders(db: Session, user_id, *, status: Optional[str] = None, q: Optional[str] = None, contact_id=None,
                since: Optional[datetime] = None, until: Optional[datetime] = None, limit: int = 100,
                offset: int = 0) -> Tuple[List[Order], int]:
    qs = db.query(Order).filter(Order.user_id == user_id)
    if status == "open":
        qs = qs.filter(Order.status.in_(OPEN_STATUSES))
    elif status in ORDER_STATUSES:
        qs = qs.filter(Order.status == status)
    if contact_id:
        try:
            qs = qs.filter(Order.contact_id == uuid.UUID(str(contact_id)))
        except (ValueError, TypeError):
            return [], 0
    if since is not None:
        qs = qs.filter(Order.created_at >= since)
    if until is not None:
        qs = qs.filter(Order.created_at < until)
    q = (q or "").strip()
    if q:
        digits = re.sub(r"\D", "", q)
        like = f"%{q.lower()}%"
        cond = "(lower(coalesce(client_name,'')) LIKE :lk OR lower(coalesce(address,'')) LIKE :lk OR lower(CAST(items AS text)) LIKE :lk"
        params = {"lk": like}
        if digits:
            cond += " OR CAST(number AS text) = :num OR regexp_replace(coalesce(client_phone,''), '\\D', '', 'g') LIKE :ph"
            params.update({"num": digits, "ph": f"%{digits[-9:]}%"})
        qs = qs.filter(text(cond + ")").bindparams(**params))
    total = qs.count()
    rows = qs.order_by(Order.created_at.desc()).offset(max(0, offset)).limit(max(1, min(limit, 500))).all()
    return rows, total


def counts(db: Session, user_id) -> Dict[str, int]:
    """Сколько заказов в каждом открытом статусе + выдано и отменено сегодня (по Бишкеку)."""
    start = datetime.combine(now_utc().astimezone(BISHKEK).date(), datetime.min.time(), BISHKEK).astimezone(timezone.utc)
    row = db.execute(text("""
        SELECT count(*) FILTER (WHERE status = 'new') AS new,
               count(*) FILTER (WHERE status = 'confirmed') AS confirmed,
               count(*) FILTER (WHERE status = 'packed') AS packed,
               count(*) FILTER (WHERE status = 'delivering') AS delivering,
               count(*) FILTER (WHERE status = 'done' AND done_at >= :t) AS done_today,
               count(*) FILTER (WHERE status = 'cancelled' AND cancelled_at >= :t) AS cancelled_today,
               count(*) FILTER (WHERE created_at >= :t AND status <> 'cancelled') AS today
        FROM orders WHERE user_id = :u
    """), {"u": str(user_id), "t": start}).mappings().first() or {}
    return {k: int(v or 0) for k, v in dict(row).items()}


def items_text(items: List[Dict[str, Any]], limit: int = 4) -> str:
    parts = []
    for it in (items or [])[:limit]:
        var = ", ".join(x for x in (it.get("size"), it.get("color")) if x)
        parts.append(f"{it.get('title')}" + (f" ({var})" if var else "") + (f" × {it.get('qty')}" if int(it.get("qty") or 1) > 1 else ""))
    more = len(items or []) - limit
    return "; ".join(parts) + (f" и ещё {more}" if more > 0 else "")


def order_to_dict(o: Order, titles: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    titles = titles or STATUS_TITLES
    created = _aware(o.created_at)
    return {
        "id": str(o.id), "number": o.number, "label": f"№ {o.number}", "status": o.status,
        "status_title": titles.get(o.status, o.status), "items": o.items or [], "items_text": items_text(o.items or []),
        "items_count": sum(int(i.get("qty") or 1) for i in (o.items or [])),
        "subtotal": o.subtotal, "delivery_fee": o.delivery_fee, "discount": o.discount, "total": o.total,
        "currency": o.currency or "KGS", "delivery_type": o.delivery_type, "address": o.address,
        "delivery_when": o.delivery_when, "payment_method": o.payment_method,
        "payment_title": PAYMENT_TITLES.get(o.payment_method or "", ""), "paid": bool(o.paid),
        "contact_id": str(o.contact_id) if o.contact_id else None, "client_name": o.client_name,
        "client_phone": o.client_phone, "source": o.source, "channel": o.channel, "created_by": o.created_by,
        "note": o.note, "cancel_reason": o.cancel_reason,
        "created_at": created.isoformat() if created else None, "created_label": fmt_local(created),
        "done_at": _aware(o.done_at).isoformat() if o.done_at else None,
        "client_notified": o.client_notified or [],
    }


def next_status(o: Order) -> Optional[str]:
    """Следующий шаг заказа (большая кнопка на телефоне): самовывоз пропускает «в пути»."""
    flow = ["new", "confirmed", "packed", "done"] if o.delivery_type == "pickup" else \
        ["new", "confirmed", "packed", "delivering", "done"]
    if o.status not in flow:
        return None
    i = flow.index(o.status)
    return flow[i + 1] if i + 1 < len(flow) else None


def stats(db: Session, user_id, start: datetime, end: datetime) -> Dict[str, Any]:
    """Заказы за период: сколько, выручка (не отменённые), средний чек, выдано, отменено, топ позиций."""
    row = db.execute(text("""
        SELECT count(*) FILTER (WHERE status <> 'cancelled') AS orders,
               coalesce(sum(total) FILTER (WHERE status <> 'cancelled'), 0) AS revenue,
               count(*) FILTER (WHERE status = 'done') AS done,
               count(*) FILTER (WHERE status = 'cancelled') AS cancelled,
               count(*) FILTER (WHERE source = 'bot') AS by_bot,
               count(DISTINCT contact_id) FILTER (WHERE status <> 'cancelled') AS clients
        FROM orders WHERE user_id = :u AND created_at >= :a AND created_at < :b
    """), {"u": str(user_id), "a": start, "b": end}).mappings().first() or {}
    out = {k: (round(float(v or 0), 2) if k == "revenue" else int(v or 0)) for k, v in dict(row).items()}
    out["avg_check"] = round(out["revenue"] / out["orders"]) if out.get("orders") else 0
    top = db.execute(text("""
        SELECT it->>'title' AS title, sum(coalesce((it->>'qty')::int, 1)) AS qty,
               sum(coalesce((it->>'qty')::int, 1) * coalesce((it->>'price')::float, 0)) AS amount
        FROM orders o, jsonb_array_elements(o.items) it
        WHERE o.user_id = :u AND o.created_at >= :a AND o.created_at < :b AND o.status <> 'cancelled'
        GROUP BY 1 ORDER BY 2 DESC, 3 DESC LIMIT 5
    """), {"u": str(user_id), "a": start, "b": end}).fetchall()
    out["top"] = [{"title": t, "qty": int(q or 0), "amount": round(float(a or 0), 2)} for t, q, a in top]
    return out


# ───────────────────────────── сообщения ─────────────────────────────

def _lead(hello: str, rest: str) -> str:
    """«Асель, ваш заказ…» или «Ваш заказ…» — без str.capitalize(): он ломал регистр в названиях позиций."""
    return hello + rest if hello else rest[:1].upper() + rest[1:]


def client_message(o: Order, status: str, cfg: Dict[str, Any], food: bool = False) -> str:
    """Текст клиенту о статусе (пишет код, не модель: цифры и номер — без ошибок)."""
    name = (o.client_name or "").strip().split()
    hello = f"{name[0]}, " if name and not name[0].startswith(("+", "Клиент")) else ""
    n = f"№ {o.number}"
    if status == "confirmed":
        lines = [_lead(hello, f"ваш заказ {n} принят: {items_text(o.items or [], 6)}."),
                 f"Сумма: {money(o.total)}" + (f" (доставка {money(o.delivery_fee)})" if o.delivery_fee else "") + "."]
        if o.delivery_type == "pickup":
            lines.append("Самовывоз" + (f": {cfg.get('pickup_address')}" if cfg.get("pickup_address") else "")
                         + (f", {o.delivery_when}" if o.delivery_when else "") + ".")
        else:
            lines.append("Доставка" + (f": {o.address}" if o.address else "") + (f", {o.delivery_when}" if o.delivery_when else "") + ".")
        if cfg.get("payment_note") and not o.paid:
            lines.append(str(cfg["payment_note"]))
        return "\n".join(lines)
    if status == "packed" and o.delivery_type == "pickup":
        return (_lead(hello, f"заказ {n} готов, можно забирать")
                + (f": {cfg.get('pickup_address')}" if cfg.get("pickup_address") else "") + ".")
    if status == "delivering":
        return (_lead(hello, f"заказ {n} передан курьеру")
                + (f", ждите {o.delivery_when}" if o.delivery_when else "") + ". Если планы изменились — напишите.")
    return ""


def _claim_notified(db: Session, order_id, status: str) -> bool:
    """Сообщение о статусе — один раз (атомарно: несколько воркеров, повторные клики)."""
    row = db.execute(text("""
        UPDATE orders SET client_notified = coalesce(client_notified, '[]'::jsonb) || to_jsonb(CAST(:s AS text))
        WHERE id = :id AND NOT (coalesce(client_notified, '[]'::jsonb) ? :s) RETURNING id
    """), {"id": str(order_id), "s": status}).first()
    db.commit()
    return row is not None


async def notify_client_status(owner_id, order_id, status: str) -> bool:
    """Клиенту в его последний мессенджер: принят / готов к выдаче / передан курьеру. Своя сессия БД."""
    if status not in NOTIFY_ON:
        return False
    from backend.db.session import SessionLocal
    from backend.models.agent_contact import AgentContact
    from backend.models.user import User
    from backend.services.booking_notify import send_to_client
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == owner_id).first()
        o = db.query(Order).filter(Order.id == order_id).first()
        if user is None or o is None or o.contact_id is None or o.status != status:
            return False
        cfg = get_settings(db, user.id)
        if not cfg.get("notify_client"):
            return False
        msg = client_message(o, status, cfg, is_food(user))
        if not msg:
            return False
        contact = db.query(AgentContact).filter(AgentContact.id == o.contact_id).first()
        if contact is None or not _claim_notified(db, o.id, status):
            return False
        return await send_to_client(db, user, contact, msg)
    except Exception as e:
        db.rollback()
        logger.warning(f"[ORDERS] client notify failed: {e}")
        return False
    finally:
        db.close()


async def notify_owner_new_order(owner_id, order_id) -> bool:
    """Владельцу в Telegram-бот агента: бот оформил заказ."""
    from backend.db.session import SessionLocal
    from backend.services.owner_notifications import _send
    db = SessionLocal()
    try:
        o = db.query(Order).filter(Order.id == order_id).first()
        agent = _first_agent(db, owner_id)
        if o is None or agent is None:
            return False
        how = "самовывоз" if o.delivery_type == "pickup" else ("доставка" + (f": {o.address}" if o.address else ""))
        lines = [f"**🛍 Бот оформил заказ № {o.number}**",
                 f"{o.client_name or 'Клиент'}" + (f" · {o.client_phone}" if o.client_phone else ""),
                 items_text(o.items or [], 6), f"Сумма: {money(o.total)} · {how}"
                 + (f", {o.delivery_when}" if o.delivery_when else ""),
                 "Подтвердите заказ в кабинете → «Заказы»."]
        if o.note:
            lines.insert(3, f"Комментарий: {o.note[:200]}")
        return await _send(agent, db, "\n".join(lines))
    except Exception as e:
        logger.warning(f"[ORDERS] owner notify failed: {e}")
        return False
    finally:
        db.close()


def schedule(coro) -> None:
    try:
        from backend.core.spawn import spawn
        spawn(coro)
    except RuntimeError:
        try:
            from backend.core import background_loop
            background_loop.submit(coro)
        except Exception as e:
            logger.warning(f"[ORDERS] background task skipped: {e}")
