"""
Каталог как у магазина (13.11.2026): остаток по размерам и цветам, массовые действия,
карточка товара для клиента, сводка склада и события «закончился / осталось мало».

Позиция каталога — строка agent_knowledge_entries (kind="product"). Остаток бывает трёх видов:
  • не считаем: stock_qty = NULL, наличие — переключатель in_stock;
  • одним числом: stock_qty;
  • по вариантам: variants = [{"size": "46", "color": "чёрный", "qty": 2}, …] — тогда
    stock_qty = сумма, in_stock = сумма > 0, а размеры в наличии — те, где qty > 0.
Одна реализация для кабинета, инструментов агента (переписка, звонок, чат владельца) и оповещений.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from backend.core.logging import get_logger

logger = get_logger(__name__)

LOW_STOCK = 3                 # «осталось мало» — 1…3 шт.
MAX_VARIANTS = 120
_NUM_RE = re.compile(r"^\d+(?:[.,]\d+)?$")


# ============================================================================
# ВАРИАНТЫ: размер × цвет → остаток
# ============================================================================

def _clip(value, n: int) -> str:
    return " ".join(str(value or "").split())[:n]


def normalize_variants(raw) -> Optional[List[Dict[str, Any]]]:
    """Почистить варианты из формы/таблицы: размер и цвет строкой, остаток целым ≥ 0, без повторов."""
    if not raw or not isinstance(raw, list):
        return None
    out: List[Dict[str, Any]] = []
    seen = set()
    for item in raw[:MAX_VARIANTS * 2]:
        if not isinstance(item, dict):
            continue
        size = _clip(item.get("size"), 20)
        color = _clip(item.get("color"), 40)
        if not size and not color:
            continue
        try:
            qty = max(0, min(int(float(item.get("qty") or 0)), 1_000_000))
        except (TypeError, ValueError):
            qty = 0
        key = (size.lower(), color.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append({"size": size, "color": color, "qty": qty})
        if len(out) >= MAX_VARIANTS:
            break
    return out or None


def apply_variants(row) -> None:
    """Остаток, наличие и список размеров позиции — из её вариантов (если они есть)."""
    variants = getattr(row, "variants", None)
    if not variants:
        if getattr(row, "stock_qty", None) is not None:
            row.in_stock = row.stock_qty > 0
        return
    total = sum(int(v.get("qty") or 0) for v in variants)
    row.stock_qty = total
    row.in_stock = total > 0
    sizes: List[str] = []
    for v in variants:
        s = v.get("size")
        if s and s not in sizes:
            sizes.append(s)
    row.sizes = sizes or None


def sizes_in_stock(row) -> List[str]:
    """Размеры, которые реально есть: из вариантов с остатком, иначе — весь список размеров."""
    variants = getattr(row, "variants", None)
    if variants:
        out: List[str] = []
        for v in variants:
            if int(v.get("qty") or 0) > 0 and v.get("size") and v["size"] not in out:
                out.append(v["size"])
        return out
    if getattr(row, "in_stock", True) is False:
        return []
    return list(getattr(row, "sizes", None) or [])


def colors_in_stock(row) -> List[str]:
    out: List[str] = []
    for v in getattr(row, "variants", None) or []:
        if int(v.get("qty") or 0) > 0 and v.get("color") and v["color"] not in out:
            out.append(v["color"])
    return out


def variant_lines(row, limit: int = 30) -> List[str]:
    """«46, чёрный: 2 шт.» — для агента; закончившиеся тоже, чтобы честно сказать «46 нет»."""
    lines = []
    for v in (getattr(row, "variants", None) or [])[:limit]:
        name = ", ".join(x for x in (v.get("size"), v.get("color")) if x)
        q = int(v.get("qty") or 0)
        lines.append(f"{name}: {q} шт." if q else f"{name}: нет")
    return lines


def size_available(row, want_norm: str, norm) -> Optional[bool]:
    """Есть ли размер с остатком. None — у позиции нет вариантов (решает обычная проверка размера)."""
    variants = getattr(row, "variants", None)
    if not variants:
        return None
    return any(norm(v.get("size")) == want_norm and int(v.get("qty") or 0) > 0 for v in variants)


# ============================================================================
# СОБЫТИЯ СКЛАДА: закончился / осталось мало / снова в наличии
# ============================================================================

def stock_state(row) -> Tuple[bool, Optional[int]]:
    return bool(getattr(row, "in_stock", True)), getattr(row, "stock_qty", None)


def stock_event(before: Tuple[bool, Optional[int]], row) -> Optional[str]:
    """
    Что случилось с остатком после правки: "out" (закончился), "low" (стало мало),
    "back" (снова появился) или None. «Мало» — только при переходе через порог, а не на
    каждой правке: иначе владелец получал бы сообщение на любое изменение 3 → 2.
    """
    was_in, was_qty = before
    now_in, now_qty = stock_state(row)
    if getattr(row, "is_active", True) is False:
        return None
    if was_in and not now_in:
        return "out"
    if not was_in and now_in:
        return "back"
    if now_in and now_qty is not None and 0 < now_qty <= LOW_STOCK and (was_qty is None or was_qty > LOW_STOCK):
        return "low"
    return None


def stock_event_text(event: str, row) -> str:
    from backend.services.agent_knowledge_service import format_price
    title = row.title or "Позиция"
    col = f" ({row.collection})" if getattr(row, "collection", None) else ""
    if event == "out":
        return f"🔴 Закончился товар: {title}{col}. Бот теперь говорит клиентам, что его нет в наличии."
    if event == "back":
        q = f", {row.stock_qty} шт." if row.stock_qty else ""
        return f"🟢 Снова в наличии: {title}{col}{q}."
    if event == "low":
        sizes = sizes_in_stock(row)
        tail = f" Размеры: {', '.join(sizes)}." if sizes else ""
        return f"🟡 Осталось мало: {title}{col}: {row.stock_qty} шт.{tail} Цена {format_price(row.price, row.currency)}."
    return ""


# ============================================================================
# СВОДКА СКЛАДА (оповещения, итоги дня, чат владельца)
# ============================================================================

def catalog_overview(db, agent_config_id, limit: int = 15) -> Dict[str, Any]:
    """Сколько позиций, что закончилось, где мало осталось, что без фото и без цены — по коллекциям."""
    from backend.models.agent_knowledge import AgentKnowledgeEntry as E

    rows = (
        db.query(E)
        .filter(E.agent_config_id == agent_config_id, E.kind == "product", E.is_active.is_(True))
        .order_by(E.collection.asc(), E.title.asc())
        .all()
    )
    def short(r):
        out = {"product_id": str(r.id), "title": r.title, "collection": r.collection or None}
        if r.stock_qty is not None:
            out["stock_left"] = r.stock_qty
        sizes = sizes_in_stock(r)
        if sizes:
            out["sizes_in_stock"] = sizes
        return out

    out_rows = [r for r in rows if not r.in_stock]
    low_rows = [r for r in rows if r.in_stock and r.stock_qty is not None and 0 < r.stock_qty <= LOW_STOCK]
    collections: Dict[str, Dict[str, int]] = {}
    for r in rows:
        c = r.collection or "Без коллекции"
        st = collections.setdefault(c, {"items": 0, "in_stock": 0, "units": 0})
        st["items"] += 1
        st["in_stock"] += 1 if r.in_stock else 0
        st["units"] += int(r.stock_qty or 0)
    return {
        "ok": True,
        "total": len(rows),
        "in_stock": len(rows) - len(out_rows),
        "units": sum(int(r.stock_qty or 0) for r in rows),
        "collections": collections,
        "out_of_stock": [short(r) for r in out_rows[:limit]],
        "out_of_stock_count": len(out_rows),
        "low_stock": [short(r) for r in sorted(low_rows, key=lambda r: r.stock_qty)[:limit]],
        "low_stock_count": len(low_rows),
        "no_photo_count": sum(1 for r in rows if not r.media_file_ids and not (r.attributes or {}).get("_photo_url")),
        "no_price_count": sum(1 for r in rows if r.price is None),
    }


def overview_text(ov: Dict[str, Any], max_items: int = 8) -> str:
    """Сводка склада для Telegram владельцу: коротко, по делу."""
    if not ov.get("total"):
        return ""
    lines = [f"📦 Каталог: {ov['total']} позиций, в наличии {ov['in_stock']}"
             + (f", всего {ov['units']} шт." if ov.get("units") else ".")]
    if len(ov.get("collections") or {}) > 1:
        lines.append(" · ".join(f"{k}: {v['in_stock']}/{v['items']}" for k, v in ov["collections"].items()))
    if ov.get("out_of_stock_count"):
        names = ", ".join(x["title"] for x in ov["out_of_stock"][:max_items])
        more = ov["out_of_stock_count"] - min(len(ov["out_of_stock"]), max_items)
        lines.append(f"🔴 Нет в наличии ({ov['out_of_stock_count']}): {names}" + (f" и ещё {more}" if more > 0 else ""))
    if ov.get("low_stock_count"):
        names = ", ".join(f"{x['title']} ({x.get('stock_left')} шт.)" for x in ov["low_stock"][:max_items])
        lines.append(f"🟡 Осталось мало ({ov['low_stock_count']}): {names}")
    gaps = []
    if ov.get("no_photo_count"):
        gaps.append(f"без фото: {ov['no_photo_count']}")
    if ov.get("no_price_count"):
        gaps.append(f"без цены: {ov['no_price_count']}")
    if gaps:
        lines.append("✏️ Заполнить: " + ", ".join(gaps) + " — бот не сможет их показать или назвать цену.")
    return "\n".join(lines)


# ============================================================================
# КАРТОЧКА ТОВАРА ДЛЯ КЛИЕНТА
# ============================================================================

def discount_percent(row) -> int:
    old, price = getattr(row, "old_price", None), getattr(row, "price", None)
    if not old or price is None or old <= price:
        return 0
    return int(round((1 - price / old) * 100))


def product_card_text(row, max_desc: int = 350) -> str:
    """
    Подпись карточки: название, цена (и старая, если скидка), размеры и цвета в наличии,
    коротко описание. Без тире, маркеров и звёздочек — их всё равно чистит message_style,
    а в WhatsApp/Telegram такие подписи выглядят как спам.
    """
    from backend.services.agent_knowledge_service import format_price

    lines = [row.title or "Товар"]
    if row.price is not None:
        d = discount_percent(row)
        price = format_price(row.price, row.currency)
        if d:
            lines.append(f"Цена: {price} вместо {format_price(row.old_price, row.currency)}, скидка {d}%")
        else:
            lines.append(f"Цена: {price}")
    if not row.in_stock:
        lines.append("Сейчас нет в наличии")
    else:
        sizes = sizes_in_stock(row)
        if sizes:
            lines.append("Размеры в наличии: " + ", ".join(sizes))
        colors = colors_in_stock(row)
        if colors:
            lines.append("Цвета: " + ", ".join(colors))
        if row.stock_qty is not None and 0 < row.stock_qty <= LOW_STOCK:
            lines.append(f"Осталось {row.stock_qty} шт.")
    attrs = row.attributes if isinstance(row.attributes, dict) else {}
    extra = [f"{k}: {v}" for k, v in attrs.items()
             if not str(k).startswith("_") and v not in (None, "") and not re.match(r"^(артикул|код|sku)$", str(k), re.I)][:3]
    lines += extra
    desc = " ".join((row.body or "").split())
    if desc:
        if len(desc) > max_desc:
            cut = desc[:max_desc].rsplit(" ", 1)[0]
            desc = cut.rstrip(",.;:") + "…"
        lines.append("")
        lines.append(desc)
    return "\n".join(lines).strip()


# ============================================================================
# МАССОВЫЕ ДЕЙСТВИЯ
# ============================================================================

BULK_ACTIONS = ("collection", "price_percent", "price_set", "discount", "discount_off",
                "in_stock", "out_of_stock", "activate", "deactivate", "delete")


def _round_price(value: float) -> float:
    """Цена после ±%: круглая (до 10 сом), как ставят в магазине."""
    if value >= 1000:
        return float(int(round(value / 10.0)) * 10)
    return float(round(value))


def apply_bulk(rows: Iterable, action: str, value=None) -> Tuple[int, List[Tuple[Any, str]]]:
    """
    Применить действие к позициям. Возвращает (сколько изменено, события склада [(row, event)]).
    Удаление делает вызывающий (нужен db.delete).
    """
    changed = 0
    events: List[Tuple[Any, str]] = []
    for row in rows:
        before = stock_state(row)
        if action == "collection":
            row.collection = " ".join(str(value or "").split())[:120] or None
        elif action == "price_percent":
            if row.price is None:
                continue
            row.price = max(0.0, _round_price(row.price * (1 + float(value) / 100.0)))
        elif action == "price_set":
            row.price = float(value)
        elif action == "discount":
            # Скидка N%: старая цена = текущая (если скидки ещё не было), новая — меньше на N%
            if row.price is None:
                continue
            base = row.old_price if row.old_price and row.old_price > row.price else row.price
            row.old_price = base
            row.price = _round_price(base * (1 - float(value) / 100.0))
        elif action == "discount_off":
            if not row.old_price:
                continue
            row.price, row.old_price = row.old_price, None
        elif action == "in_stock":
            row.in_stock = True
            if row.stock_qty == 0 and not row.variants:
                row.stock_qty = None
        elif action == "out_of_stock":
            row.in_stock = False
            if row.variants:
                row.variants = [dict(v, qty=0) for v in row.variants]
                apply_variants(row)
            elif row.stock_qty is not None:
                row.stock_qty = 0
        elif action == "activate":
            row.is_active = True
        elif action == "deactivate":
            row.is_active = False
        else:
            continue
        row.vector_status = "pending"
        changed += 1
        ev = stock_event(before, row)
        if ev:
            events.append((row, ev))
    return changed, events


def set_stock(row, qty: Optional[int] = None, size: Optional[str] = None, color: Optional[str] = None) -> str:
    """
    Поменять остаток по слову владельца: «кубанка 58 размер — осталось 2».
    С размером/цветом — правит вариант (заводит, если его не было); без — общий остаток.
    Возвращает человеческое описание сделанного.
    """
    qty = max(0, int(qty or 0))
    if size or color:
        variants = [dict(v) for v in (row.variants or [])]
        s, c = _clip(size, 20), _clip(color, 40)
        hit = None
        for v in variants:
            if (not s or v.get("size", "").lower() == s.lower()) and (not c or v.get("color", "").lower() == c.lower()):
                hit = v
                break
        if hit is None:
            # Первый вариант у позиции с общим остатком: дальше остаток считается по размерам
            hit = {"size": s, "color": c, "qty": 0}
            variants.append(hit)
        hit["qty"] = qty
        row.variants = normalize_variants(variants)
        apply_variants(row)
        what = ", ".join(x for x in (hit.get("size"), hit.get("color")) if x)
        return f"{row.title} ({what}): {qty} шт., всего {row.stock_qty} шт."
    if row.variants:
        # Общий остаток при вариантах — без размера непонятно, где менять
        raise ValueError("У позиции остаток по размерам — укажите размер или цвет.")
    row.stock_qty = qty
    row.in_stock = qty > 0
    return f"{row.title}: {qty} шт."


# ============================================================================
# ОПОВЕЩЕНИЕ ВЛАДЕЛЬЦУ О СКЛАДЕ
# ============================================================================

MAX_STOCK_LINES = 12


def stock_message(events: List[Tuple[Any, str]]) -> str:
    """Несколько событий склада — одним сообщением (массовая правка не должна сыпать десятком)."""
    lines = [stock_event_text(ev, row) for row, ev in events if ev]
    lines = [l for l in lines if l]
    if not lines:
        return ""
    if len(lines) > MAX_STOCK_LINES:
        rest = len(lines) - MAX_STOCK_LINES
        lines = lines[:MAX_STOCK_LINES] + [f"…и ещё {rest} изменений в каталоге."]
    return "\n".join(lines)


async def notify_stock(agent_id, message: str) -> bool:
    """Владельцу в Telegram-бот агента, если он подключён и «Склад» не выключен. Своя сессия БД."""
    if not message:
        return False
    from backend.db.session import SessionLocal
    from backend.models.agent_config import AgentConfig
    from backend.services import owner_notifications

    db = SessionLocal()
    try:
        agent = db.query(AgentConfig).filter(AgentConfig.id == agent_id).first()
        if agent is None or not getattr(agent, "telegram_enabled", False):
            return False
        if not getattr(agent, "notify_stock", True):
            return False
        return await owner_notifications._send(agent, db, message)
    except Exception as e:
        logger.warning(f"[CATALOG] оповещение о складе не отправлено: {e}")
        return False
    finally:
        db.close()


def schedule_stock_notify(agent_id, events: List[Tuple[Any, str]]) -> None:
    """Запустить оповещение в фоне: ответ кабинету/агенту его не ждёт."""
    message = stock_message(events)
    if not message:
        return
    try:
        from backend.core.spawn import spawn
        spawn(notify_stock(agent_id, message), name="catalog-stock-notify")
    except RuntimeError:
        # Нет запущенного цикла (синхронный вызов) — отправим через фоновый loop
        try:
            from backend.core import background_loop
            background_loop.submit(notify_stock(agent_id, message))
        except Exception as e:
            logger.warning(f"[CATALOG] оповещение о складе не запущено: {e}")
