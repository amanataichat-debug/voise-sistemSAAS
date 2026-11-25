"""
Услуги салона (Voksy Beauty, шаг 1.3): прайс по категориям и подкатегориям, цена «от — до», длительность,
подготовка и перерыв после, кто делает и за сколько, онлайн-запись, предоплата.

Услуга = позиция каталога бота (agent_knowledge_entries, kind=product) — поэтому бот в переписке, голосовой
агент, поиск, прайс в промпте звонка и онлайн-запись видят услуги сами, без синхронизации. Здесь — только
удобное хозяйство над теми же строками:
  • категория и подкатегория хранятся в collection как «Волосы / Окрашивание» (booking_service.split_category);
    порядок категорий — справочник коллекций агента (agent_configs.catalog_collections);
  • «кто делает» и цена/длительность у мастера — в карточке мастера (booking_resources.service_ids и
    service_prices): правка услуги меняет их в той же транзакции;
  • удаление услуги с будущими записями её выключает (записи остаются), без записей — удаляет и чистит
    упоминания у мастеров.
Нет бота — нет каталога: список отдаёт has_agent=false, страница предлагает создать бота.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from backend.models.booking import Booking, BookingResource
from backend.services import booking_service as bs

logger = logging.getLogger(__name__)

MAX_SERVICES = 500
DURATIONS = (5, 1440)
PADS = (0, 240)


class ServiceError(ValueError):
    """Понятная причина отказа (уходит в кабинет как есть)."""


def agent_for(db: Session, user):
    return bs._first_agent(db, user.id)


def _entries(db: Session, user_id) -> List[Any]:
    from backend.models.agent_knowledge import AgentKnowledgeEntry
    return (db.query(AgentKnowledgeEntry)
            .filter(AgentKnowledgeEntry.user_id == user_id, AgentKnowledgeEntry.kind == "product")
            .order_by(AgentKnowledgeEntry.position, AgentKnowledgeEntry.title).limit(MAX_SERVICES).all())


def _category_order(agent, entries) -> List[str]:
    """Порядок категорий: справочник коллекций агента, затем остальные по алфавиту."""
    out: List[str] = []
    for name in (getattr(agent, "catalog_collections", None) or []):
        cat = bs.split_category(name)[0]
        if cat and cat not in out:
            out.append(cat)
    rest = sorted({bs.split_category(e.collection)[0] for e in entries if e.collection} - set(out), key=str.lower)
    return out + rest


def sort_services(db: Session, user, entries: List[Any]) -> List[Any]:
    """Услуги в порядке категорий владельца (справочник коллекций), внутри — подкатегория и название."""
    agent = agent_for(db, user)
    order = {c: i for i, c in enumerate(_category_order(agent, entries))} if agent is not None else {}

    def key(e):
        c, sub = bs.split_category(e.collection)
        return (0 if c else 1, order.get(c, 10_000), c.lower(), sub.lower(), e.position or 0, (e.title or "").lower())
    return sorted(entries, key=key)


def _does(r: BookingResource, sid: str) -> bool:
    return not r.service_ids or sid in {str(x) for x in r.service_ids}


def service_dict(e, masters: List[BookingResource]) -> Dict[str, Any]:
    d = bs.service_to_dict(e)
    sid = str(e.id)
    d.update({"is_active": bool(e.is_active), "description": e.body or "", "position": e.position or 0,
              "has_photo": bool(e.media_file_ids)})
    d["masters"] = []
    for r in masters:
        if not r.is_active or not _does(r, sid):
            continue
        own = (r.service_prices or {}).get(sid) or {}
        d["masters"].append({"id": str(r.id), "price": own.get("price"), "duration_min": own.get("duration_min")})
    return d


def overview(db: Session, user) -> Dict[str, Any]:
    agent = agent_for(db, user)
    masters = bs.resources(db, user.id, active_only=False)
    entries = sort_services(db, user, _entries(db, user.id)) if agent is not None else []
    order = _category_order(agent, entries) if agent is not None else []
    cats: Dict[str, Dict[str, Any]] = {c: {"name": c, "subs": [], "count": 0} for c in order}
    for e in entries:
        c, sub = bs.split_category(e.collection)
        if not c:
            continue
        cat = cats.setdefault(c, {"name": c, "subs": [], "count": 0})
        cat["count"] += 1
        if sub and sub not in cat["subs"]:
            cat["subs"].append(sub)
    return {
        "has_agent": agent is not None,
        "services": [service_dict(e, masters) for e in entries],
        "categories": [cats[c] for c in order if c in cats] + [v for k, v in cats.items() if k not in order],
        "masters": [{"id": str(r.id), "name": r.name, "role": r.role, "color": r.color or bs.COLORS[0],
                     "initials": bs.initials(r.name), "photo_url": bs.resource_to_dict(r)["photo_url"],
                     "is_active": bool(r.is_active)} for r in masters],
        "rooms": [bs.room_to_dict(r) for r in bs.rooms(db, user.id)],
    }


# ───────────────────────────── проверка полей ─────────────────────────────

def _int(v, lo: int, hi: int, what: str) -> Optional[int]:
    if v in (None, ""):
        return None
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        raise ServiceError(f"{what}: нужно число")
    if not (lo <= n <= hi):
        raise ServiceError(f"{what}: от {lo} до {hi} минут")
    return n


def _money(v, what: str) -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        n = round(float(v), 2)
    except (TypeError, ValueError):
        raise ServiceError(f"{what}: нужно число")
    if not (0 <= n <= 10_000_000):
        raise ServiceError(f"{what}: проверьте сумму")
    return n


def _remember(agent, collection: Optional[str]) -> None:
    if not collection:
        return
    cur = list(getattr(agent, "catalog_collections", None) or [])
    if collection not in cur:
        cur.append(collection)
        agent.catalog_collections = cur[:100]


def _apply(e, data: Dict[str, Any], agent, db: Optional[Session] = None) -> None:
    if "title" in data:
        t = " ".join(str(data.get("title") or "").split())[:500]
        if not t:
            raise ServiceError("Укажите название услуги")
        e.title = t
    if "category" in data or "subcategory" in data:
        cur_cat, cur_sub = bs.split_category(e.collection)
        cat = data.get("category", cur_cat)
        sub = data.get("subcategory", cur_sub)
        e.collection = bs.join_category(cat, sub)
        _remember(agent, e.collection)
    if "description" in data:
        e.body = str(data.get("description") or "").strip()[:4000]
        e.char_count = len(e.body)
    if "price" in data:
        e.price = _money(data.get("price"), "Цена")
    if "price_to" in data:
        e.price_to = _money(data.get("price_to"), "Цена «до»")
    if e.price is not None and e.price_to is not None and e.price_to <= e.price:
        e.price_to = None                          # «до» не больше «от» — значит цена одна
    if e.price is None and e.price_to is not None:
        raise ServiceError("Укажите цену «от» — с неё начинается диапазон")
    if "duration_min" in data:
        e.duration_min = _int(data.get("duration_min"), *DURATIONS, "Длительность")
    if "prep_min" in data:
        e.prep_min = _int(data.get("prep_min"), *PADS, "Подготовка") or None
    if "after_min" in data:
        e.after_min = _int(data.get("after_min"), *PADS, "Перерыв после") or None
    if "online_booking" in data and data["online_booking"] is not None:
        e.online_booking = None if data["online_booking"] else False
    if "prepay" in data:
        raw = data.get("prepay")
        rule = bs.clean_prepay(raw)
        if raw and raw.get("type") in ("fixed", "percent") and rule is None:
            raise ServiceError("Предоплата: сумма больше нуля, процент — от 1 до 100")
        e.prepay = rule
    if "is_active" in data and data["is_active"] is not None:
        e.is_active = bool(data["is_active"])
    if "room_ids" in data:
        mine = {str(r.id) for r in bs.rooms(db, e.user_id, active_only=False)} if data.get("room_ids") and db is not None else set()
        ids = [str(x) for x in (data.get("room_ids") or []) if str(x) in mine]
        e.room_ids = ids or None


def _apply_masters(db: Session, user, sid: str, rows: List[Dict[str, Any]], all_ids: List[str]) -> None:
    """Кто делает услугу и свои цена/длительность — в карточках мастеров."""
    want = {str(m.get("id")): m for m in rows or [] if m.get("id")}
    for r in bs.resources(db, user.id, active_only=False):
        rid = str(r.id)
        m = want.get(rid)
        does = bool(m and m.get("does", True))
        ids = [str(x) for x in r.service_ids] if r.service_ids else None
        if does and ids is not None and sid not in ids:
            ids.append(sid)
            r.service_ids = ids
        elif not does:
            if ids is None:   # делал «всё» — теперь всё, кроме этой
                ids = [x for x in all_ids if x != sid]
                r.service_ids = ids or []
            elif sid in ids:
                ids = [x for x in ids if x != sid]
                r.service_ids = ids
        prices = dict(r.service_prices or {})
        own = {}
        if does and m:
            p = _money(m.get("price"), "Цена мастера")
            d = _int(m.get("duration_min"), *DURATIONS, "Длительность у мастера")
            if p is not None:
                own["price"] = p
            if d is not None:
                own["duration_min"] = d
        if own:
            prices[sid] = own
        else:
            prices.pop(sid, None)
        r.service_prices = bs.clean_service_prices(prices) or None


def save(db: Session, user, entry_id: Optional[str], data: Dict[str, Any]):
    """Создать или изменить услугу (и кто её делает). Без commit."""
    from backend.models.agent_knowledge import AgentKnowledgeEntry
    agent = agent_for(db, user)
    if agent is None:
        raise ServiceError("Сначала создайте бота: услуги хранятся в его каталоге")
    if entry_id:
        e = get(db, user.id, entry_id)
    else:
        if db.query(AgentKnowledgeEntry.id).filter(AgentKnowledgeEntry.user_id == user.id,
                                                   AgentKnowledgeEntry.kind == "product").count() >= MAX_SERVICES:
            raise ServiceError(f"Не больше {MAX_SERVICES} услуг")
        e = AgentKnowledgeEntry(user_id=user.id, agent_config_id=agent.id, kind="product", title="", body="",
                                currency="KGS", in_stock=True, in_prompt=False, is_active=True, position=0)
        if "title" not in data:
            raise ServiceError("Укажите название услуги")
    _apply(e, data, agent, db)
    if not entry_id:
        e.duration_min = e.duration_min or bs.DEFAULT_DURATION
        db.add(e)
    e.vector_status = "pending"
    db.flush()
    if "masters" in data and data["masters"] is not None:
        all_ids = [str(x.id) for x in _entries(db, user.id)]
        _apply_masters(db, user, str(e.id), data["masters"], all_ids)
        db.flush()
    return e


def get(db: Session, user_id, entry_id):
    from backend.models.agent_knowledge import AgentKnowledgeEntry
    try:
        eid = uuid.UUID(str(entry_id))
    except (ValueError, TypeError):
        raise ServiceError("Услуга не найдена")
    e = (db.query(AgentKnowledgeEntry)
         .filter(AgentKnowledgeEntry.id == eid, AgentKnowledgeEntry.user_id == user_id,
                 AgentKnowledgeEntry.kind == "product").first())
    if e is None:
        raise ServiceError("Услуга не найдена")
    return e


def remove(db: Session, user, entry_id) -> Dict[str, Any]:
    """Удалить услугу. С будущими записями — выключить (записи остаются). Без commit."""
    e = get(db, user.id, entry_id)
    upcoming = (db.query(Booking.id).filter(Booking.user_id == user.id, Booking.service_entry_id == e.id,
                                            Booking.status.in_(("booked", "confirmed")),
                                            Booking.start_at >= bs.now_utc()).count())
    if upcoming:
        e.is_active = False
        db.flush()
        return {"disabled": True, "upcoming": upcoming}
    sid = str(e.id)
    for r in bs.resources(db, user.id, active_only=False):
        changed = False
        if r.service_ids and sid in {str(x) for x in r.service_ids}:
            r.service_ids = [x for x in r.service_ids if str(x) != sid] or []
            changed = True
        if r.service_prices and sid in r.service_prices:
            p = dict(r.service_prices)
            p.pop(sid, None)
            r.service_prices = p or None
            changed = True
        if changed:
            db.flush()
    db.delete(e)
    db.flush()
    return {"deleted": True, "id": sid}


def rename_category(db: Session, user, old: str, new: str, sub_old: Optional[str] = None) -> int:
    """Переименовать категорию (вместе с её подкатегориями) или одну подкатегорию. Без commit."""
    agent = agent_for(db, user)
    if agent is None:
        raise ServiceError("Сначала создайте бота")
    old = " ".join(str(old or "").split())
    new = " ".join(str(new or "").replace("/", " ").split())[:60]
    if not old:
        raise ServiceError("Не указана категория")
    n = 0
    for e in _entries(db, user.id):
        c, s = bs.split_category(e.collection)
        if c != old:
            continue
        if sub_old is None:
            if not new:
                raise ServiceError("Укажите новое название")
            e.collection = bs.join_category(new, s)
        else:
            if s != sub_old:
                continue
            e.collection = bs.join_category(c, new)   # пустое — услуги остаются в категории без подкатегории
        e.vector_status = "pending"
        n += 1
    cur = []
    for name in (getattr(agent, "catalog_collections", None) or []):
        c, s = bs.split_category(name)
        if c == old and (sub_old is None or s == sub_old):
            name = bs.join_category(new, s) if sub_old is None else bs.join_category(c, new)
        if name and name not in cur:
            cur.append(name)
    agent.catalog_collections = cur[:100]
    db.flush()
    return n


def set_category_order(db: Session, user, names: List[str]) -> None:
    """Порядок категорий: справочник коллекций в новом порядке (подкатегории — за своей категорией)."""
    agent = agent_for(db, user)
    if agent is None:
        raise ServiceError("Сначала создайте бота")
    cur = list(getattr(agent, "catalog_collections", None) or [])
    for e in _entries(db, user.id):
        if e.collection and e.collection not in cur:
            cur.append(e.collection)
    order = {n: i for i, n in enumerate([" ".join(str(x or "").split()) for x in names or []])}
    pos = {n: i for i, n in enumerate(cur)}          # внутри категории — прежний порядок подкатегорий
    agent.catalog_collections = sorted(cur, key=lambda n: (order.get(bs.split_category(n)[0], 10_000), pos[n]))[:100]
    db.flush()


def after_change(db: Session, user, deleted_ids: Optional[List[str]] = None) -> None:
    """После правки каталога: голосовой карточке вернутся/уйдут функции каталога (как при правке в
    «Знаниях»), а поиск по смыслу пересчитает записи в фоновом loop'е (обработчики услуг — обычные
    функции в потоке, своего event loop у них нет)."""
    agent = agent_for(db, user)
    if agent is None:
        return
    try:
        from backend.api.agent import _voice_sync_kb_functions
        _voice_sync_kb_functions(db, agent)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f"[SERVICES] функции голоса не пересчитаны: {e}")
    try:
        from backend.services import agent_knowledge_vectors as kv
        if not kv.enabled():
            return
        from backend.core.background_loop import submit
        agent_id = agent.id

        async def _sync():
            try:
                if deleted_ids:
                    await kv.delete_ids(agent_id, list(deleted_ids))
                await kv.sync_agent(agent_id)
            except Exception as e:
                logger.error(f"[SERVICES] поиск по смыслу не обновлён: {e}")

        submit(_sync())
    except Exception as e:
        logger.warning(f"[SERVICES] синхронизация поиска не запущена: {e}")
