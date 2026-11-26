"""
ИИ-администратор салона (Voksy Beauty, шаг 1.6а): инструменты бота из ТЗ поверх журнала записи.

  get_services          — прайс: услуги по категориям, цена «от–до» и у мастеров, длительность, предоплата
  get_staff             — мастера: специализация, опыт, о себе, что делают и почём, есть ли фото работ
  get_customer_history  — история клиента: визиты, любимый мастер и услуги, неявки, сумма, как часто ходит
  get_salon_information — адрес, карта, часы, телефоны, соцсети, акции, удобства, правила отмены и предоплаты
  send_staff_portfolio  — фото работ мастера клиенту в мессенджер (по образцу send_product_card)

Даются агенту вместе с инструментами записи (модуль «Запись» включён — booking_tools.augment).
Всё отвечает из базы: цены, мастера и время бот не придумывает. В разборе переписки история и
отправка — только про клиента этого обращения.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from backend.services import booking_service as bs

logger = logging.getLogger(__name__)

TOOL_NAMES = {"get_services", "get_staff", "get_customer_history", "get_salon_information", "send_staff_portfolio",
              "get_salon_report", "save_review"}
OWNER_TOOL_NAMES = {"get_salon_report"}      # только чат владельца: клиенту в переписке не даётся
LAPSED_DAYS = 45                             # «давно не приходил» по умолчанию
MAX_PORTFOLIO_SEND = 4
MAX_SERVICES_OUT = 40

TOOLS = [
    {
        "type": "function",
        "name": "get_services",
        "description": (
            "Прайс салона: услуги по категориям, цена (диапазон «от–до» и цена у конкретных мастеров), длительность, "
            "предоплата, можно ли записаться онлайн. Вызывай на «сколько стоит», «что у вас есть», «какие услуги». "
            "Называй цену ровно как в price_text."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Что ищем словами: «маникюр», «окрашивание» (пусто — весь прайс)"},
                "category": {"type": "string", "description": "Категория прайса, если клиент спросил про раздел"},
            },
        },
    },
    {
        "type": "function",
        "name": "get_staff",
        "description": (
            "Мастера салона: специализация, опыт, пара слов о себе, какие услуги делают и почём, есть ли фото работ. "
            "Вызывай на «кто у вас делает…», «какой мастер лучше», «расскажите про Айгуль». Фото работ — send_staff_portfolio."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "master": {"type": "string", "description": "Имя мастера (пусто — все)"},
                "service": {"type": "string", "description": "Только мастера, которые делают эту услугу"},
            },
        },
    },
    {
        "type": "function",
        "name": "get_customer_history",
        "description": (
            "История клиента в салоне: сколько раз был, когда последний раз, любимый мастер и услуги, неявки, ближайшая "
            "запись. Вызывай, чтобы предложить «как в прошлый раз» или того же мастера. В переписке — текущий клиент."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_contact_id": {"type": "string", "description": "Клиент (в переписке — текущий)"},
            },
        },
    },
    {
        "type": "function",
        "name": "get_salon_information",
        "description": (
            "Информация о салоне: адрес и ссылка на карту, часы работы, телефон, WhatsApp, Instagram, Telegram, сайт, "
            "акции, удобства (парковка, Wi-Fi…), как внести предоплату, за сколько можно отменить запись, ссылка "
            "на страницу салона с онлайн-записью. Вызывай на «где вы находитесь», «до скольки работаете» и т.п."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "send_staff_portfolio",
        "description": (
            "Отправить клиенту фото работ мастера (до 4) в его мессенджер, первое — с подписью: имя, специализация, "
            "опыт. Когда клиент хочет посмотреть работы или выбирает мастера. Потом коротко спроси, записать ли к нему."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "master": {"type": "string", "description": "Имя мастера"},
                "agent_contact_id": {"type": "string", "description": "Клиент (в переписке — текущий)"},
                "count": {"type": "integer", "description": "Сколько фото (1–4, по умолчанию 4)"},
                "channel": {"type": "string", "enum": ["whatsapp", "telegram", "instagram", "webchat"],
                            "description": "Канал (пусто — где клиент писал последним)"},
            },
            "required": ["master"],
        },
    },
    {
        "type": "function",
        "name": "save_review",
        "description": (
            "Сохранить оценку визита: клиента после визита попросили оценить от 1 до 5, и он ответил (цифрой или "
            "словами: «всё отлично» = 5, «нормально» = 4, «так себе» = 3, «плохо» = 2). Оценка 1–3 сразу уходит "
            "владельцу. Потом ответь клиенту по полю next."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "score": {"type": "integer", "description": "Оценка 1–5"},
                "comment": {"type": "string", "description": "Что клиент написал о визите (если написал)"},
                "agent_contact_id": {"type": "string", "description": "Клиент (в переписке — текущий)"},
            },
            "required": ["score"],
        },
    },
]


OWNER_TOOLS = [
    {
        "type": "function",
        "name": "get_salon_report",
        "description": (
            "Отчёт салона для владельца: записи и визиты, неявки, выручка и средний чек, сравнение с прошлым "
            "таким же периодом, загрузка и выручка мастеров, популярные услуги, прибыль (если внесены расходы) и "
            "клиенты, которые давно не приходили (кому пора на повтор). Для «как неделя», «выручка за месяц», "
            "«как работают мастера», «кто давно не приходил», «кто не пришёл»."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "period": {"type": "string", "enum": ["today", "yesterday", "week", "month", "this_month", "quarter"],
                           "description": "Период (по умолчанию week)"},
                "lapsed_days": {"type": "integer", "description": "Сколько дней без визита считать «давно» (по умолчанию 45)"},
            },
        },
    },
]


def _dump(d: Dict[str, Any]) -> str:
    return json.dumps(d, ensure_ascii=False)


def _clip(v: Any, n: int) -> str:
    s = " ".join(str(v or "").split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _low(v: Any) -> str:
    return str(v or "").lower().replace("ё", "е")


def _match_master(rows: List[Any], name: Optional[str]):
    if not name:
        return None
    m = _low(name).strip()
    for r in rows:
        if str(r.id) == name.strip():
            return r
    for r in rows:
        n = _low(r.name)
        if m == n or m in n or (n.split() and n.split()[0][:4] == m[:4]):
            return r
    return None


# ───────────────────────────── прайс ─────────────────────────────

def get_services(db: Session, user, query: Optional[str] = None, category: Optional[str] = None) -> Dict[str, Any]:
    from backend.services import beauty_services
    ov = beauty_services.overview(db, user)
    masters = {m["id"]: m["name"] for m in ov["masters"] if m.get("is_active")}
    q, cat = _low(query).strip(), _low(category).strip()
    out = []
    for s in ov["services"]:
        if not s.get("is_active"):
            continue
        hay = _low(" ".join([s.get("title") or "", s.get("category") or "", s.get("subcategory") or "",
                             s.get("description") or ""]))
        if cat and cat not in _low((s.get("category") or "") + " " + (s.get("subcategory") or "")):
            continue
        if q and not all(w[:5] in hay for w in q.split() if len(w) > 2):
            continue
        own = [{"master": masters.get(m["id"]), "price": m["price"], "duration_min": m["duration_min"]}
               for m in s.get("masters") or [] if m["id"] in masters and (m["price"] is not None or m["duration_min"])]
        item = {
            "id": s["id"], "title": s.get("title"),
            "category": " / ".join(x for x in (s.get("category"), s.get("subcategory")) if x) or None,
            "price_text": s.get("price_text") or "", "duration_min": s.get("duration_min"),
            "masters": [masters.get(m["id"]) for m in s.get("masters") or [] if m["id"] in masters],
            "online_booking": s.get("online_booking", True) is not False,
        }
        if own:
            item["master_prices"] = [dict(o, price_text=bs.price_text(o["price"]) if o["price"] is not None else None)
                                     for o in own]
        if s.get("prepay_text"):
            item["prepay"] = s["prepay_text"]
        if s.get("description"):
            item["description"] = _clip(s["description"], 200)
        if not item["online_booking"]:
            item["note"] = "Записывает только администратор"
        out.append(item)
    res: Dict[str, Any] = {"ok": True, "services": out[:MAX_SERVICES_OUT], "total": len(out),
                           "categories": [c["name"] for c in ov["categories"]]}
    if not out:
        res["note"] = ("Такой услуги в прайсе нет — не выдумывай, уточни у клиента или передай администратору"
                       if (q or cat) else "Прайс пуст — владелец ещё не добавил услуги")
    elif len(out) > MAX_SERVICES_OUT:
        res["note"] = "Показаны первые услуги — уточни запрос (query или category)"
    return res


# ───────────────────────────── мастера ─────────────────────────────

def _staff_dict(r, services: Dict[str, Any], detailed: bool) -> Dict[str, Any]:
    from backend.services.salon_media import media_items
    does = [s for sid, s in services.items() if not r.service_ids or sid in {str(x) for x in r.service_ids}]
    own = r.service_prices or {}
    items = []
    for s in does[: (30 if detailed else 8)]:
        dur, price = bs.service_terms(r, s)
        mine = isinstance(own.get(str(s.id)), dict) and own[str(s.id)].get("price") is not None
        items.append({"title": s.title,
                      "price_text": bs.price_text(price, None if mine else getattr(s, "price_to", None),
                                                  getattr(s, "currency", None) or "KGS"),
                      "duration_min": dur})
    d = {"id": str(r.id), "name": r.name, "specialty": r.role or None,
         "experience_years": bs.experience_years(r.experience_since),
         "works_photos": len(media_items(r.portfolio, "caption")), "has_photo": bool(r.photo_id),
         "services": items, "online_booking": bs.bookable_online(r)}
    if r.bio:
        d["about"] = _clip(r.bio, 400 if detailed else 160)
    certs = [c.get("title") for c in media_items(r.certificates, "title") if c.get("title")]
    if certs:
        d["certificates"] = certs[:6]
    if not d["online_booking"]:
        d["note"] = "К этому мастеру записывает только администратор"
    return d


def get_staff(db: Session, user, master: Optional[str] = None, service: Optional[str] = None) -> Dict[str, Any]:
    rows = bs.resources(db, user.id)
    services = {str(s.id): s for s in bs.services(db, user.id) if s.is_active is not False}
    if master:
        r = _match_master(rows, master)
        if r is None:
            return {"ok": False, "error": f"Мастера «{master}» нет — вот кто есть: " + ", ".join(x.name for x in rows)}
        return {"ok": True, "master": _staff_dict(r, services, True)}
    if service:
        svc = bs.find_service(db, user.id, None, service)
        if svc is None:
            return {"ok": False, "error": "Такой услуги в прайсе нет — уточни название (get_services)"}
        rows = [r for r in rows if bs._resource_does(r, svc)]
    return {"ok": True, "masters": [_staff_dict(r, services, False) for r in rows],
            "note": None if rows else "Мастеров пока нет в журнале"}


# ───────────────────────────── история клиента ─────────────────────────────

def get_customer_history(db: Session, user, contact_id) -> Dict[str, Any]:
    from backend.models.booking import Booking, VISITED_STATUSES
    if not contact_id:
        return {"ok": False, "error": "Не указан клиент"}
    rows = (db.query(Booking).filter(Booking.user_id == user.id, Booking.contact_id == contact_id)
            .order_by(Booking.start_at.desc()).limit(200).all())
    names = {str(r.id): r.name for r in bs.resources(db, user.id, active_only=False)}
    if not rows:
        out0: Dict[str, Any] = {"ok": True, "visits": 0, "note": "Клиент у нас ещё не был — это новый клиент"}
        _add_profile(db, user, contact_id, out0)
        return out0
    visited = [b for b in rows if b.status in VISITED_STATUSES]
    now = bs.now_utc()
    upcoming = sorted([b for b in rows if b.status in ("booked", "confirmed") and bs._aw(b.start_at) >= now],
                      key=lambda b: b.start_at)
    masters = Counter(names.get(str(b.resource_id)) for b in visited if b.resource_id)
    services = Counter(b.service_title for b in visited if b.service_title)
    out: Dict[str, Any] = {
        "ok": True, "visits": len(visited),
        "no_shows": sum(1 for b in rows if b.status == "no_show"),
        "cancelled": sum(1 for b in rows if b.status == "cancelled"),
        "spent_text": bs.price_text(sum(float(b.price or 0) for b in visited)) if visited else None,
        "favorite_master": masters.most_common(1)[0][0] if masters else None,
        "favorite_services": [s for s, _ in services.most_common(3)],
        "last_visits": [{"date": bs.fmt_local(b.start_at), "service": b.service_title,
                         "master": names.get(str(b.resource_id))} for b in visited[:5]],
    }
    if visited:
        last = bs._aw(visited[0].start_at)
        out["last_visit"] = bs.fmt_local(last)
        out["days_since_last_visit"] = max(0, (now - last).days)
        if len(visited) >= 2:
            ts = sorted(bs._aw(b.start_at) for b in visited)
            gaps = [(b - a).days for a, b in zip(ts, ts[1:]) if (b - a).days > 0]
            if gaps:
                out["usual_interval_days"] = int(sorted(gaps)[len(gaps) // 2])
    if upcoming:
        b = upcoming[0]
        out["next_booking"] = {"id": str(b.id), "date": bs.fmt_local(b.start_at), "service": b.service_title,
                               "master": names.get(str(b.resource_id))}
    if out["no_shows"] >= 2:
        out["note"] = "Клиент не раз не приходил — вежливо напомни подтвердить запись накануне"
    _add_profile(db, user, contact_id, out)
    return out


def _add_profile(db: Session, user, contact_id, out: Dict[str, Any]) -> None:
    """ДР и источник из карточки (шаг 2.1) — бот поздравит и не спросит ДР второй раз."""
    from backend.models.agent_contact import AgentContact
    from backend.services import salon_clients
    try:
        c = db.query(AgentContact).filter(AgentContact.id == contact_id, AgentContact.user_id == user.id).first()
    except Exception:
        db.rollback()
        return
    if c is None:
        return
    info = salon_clients.birthday_info(c.birthday, bs.to_local(bs.now_utc()).date())
    if info:
        out["birthday"] = info["text"]
        out["birthday_in_days"] = info["in_days"]
    else:
        out["birthday"] = None
    if c.source:
        out["source"] = salon_clients.SOURCE_TITLES.get(c.source, c.source)


# ───────────────────────────── о салоне ─────────────────────────────

def get_salon_information(db: Session, user, host: Optional[str] = None) -> Dict[str, Any]:
    from backend.services import salon_page
    page = bs.get_page(db, user, create=False)
    cfg = bs.settings_of(page)
    host = (host or "").rstrip("/")
    if not host:
        try:
            from backend.core.config import settings
            host = (getattr(settings, "HOST_URL", "") or "").rstrip("/")
        except Exception:
            host = ""
    if page is None:
        hours = salon_page.salon_hours(bs.resources(db, user.id))
        return {"ok": True, "name": user.company_name or None, "address": cfg.get("address") or None,
                "hours": salon_page.hours_text(hours) or None,
                "note": "Страница салона не оформлена — адрес и контакты уточни у администратора"}
    v = salon_page.public_view(db, page, user)
    out = {
        "ok": True, "name": v["name"], "address": v["address"] or None, "map_url": v["map_url"],
        "hours": salon_page.hours_text(v["hours"]) or None, "hours_note": v["hours_note"] or None,
        "phone": v["phone"] or None, "whatsapp": v["whatsapp"] or None,
        "instagram": ("https://instagram.com/" + v["instagram"]) if v["instagram"] else None,
        "telegram": ("https://t.me/" + v["telegram"]) if v["telegram"] else None,
        "website": v["website"] or None, "about": _clip(v["about"], 400) or None,
        "amenities": [a["title"] for a in v["amenities"]],
        "promos": [{"title": p.get("title"), "text": _clip(p.get("text"), 200), "until": p.get("until")}
                   for p in v["promos"]],
        "page_url": (host + v["page_path"]) if host and v["booking_enabled"] else None,
        "prepay_how": cfg.get("prepay_info") or None,
        "cancel_rule": (f"Отменить или перенести — не позже чем за {cfg['cancel_hours']} ч до визита"
                        if cfg.get("cancel_hours") else None),
    }
    return {k: val for k, val in out.items() if val not in (None, [], "")} | {"ok": True}


# ───────────────────────────── отчёт владельцу ─────────────────────────────

def lapsed_clients(db: Session, user, days: int = LAPSED_DAYS, limit: int = 15) -> List[Dict[str, Any]]:
    """Клиенты, которые бывали, но не приходят дольше days дней и не записаны вперёд."""
    from sqlalchemy import text
    rows = db.execute(text("""
        SELECT b.contact_id, max(b.start_at) AS last_at, count(*) AS visits,
               (array_agg(b.resource_id ORDER BY b.start_at DESC))[1] AS last_master,
               (array_agg(b.service_title ORDER BY b.start_at DESC))[1] AS last_service
        FROM bookings b
        WHERE b.user_id = :uid AND b.contact_id IS NOT NULL AND b.status IN ('arrived', 'completed')
          AND NOT EXISTS (SELECT 1 FROM bookings f WHERE f.contact_id = b.contact_id AND f.user_id = :uid
                          AND f.status IN ('booked', 'confirmed') AND f.start_at > now())
        GROUP BY b.contact_id
        HAVING max(b.start_at) < now() - make_interval(days => :d)
        ORDER BY count(*) DESC, max(b.start_at) DESC
        LIMIT :lim
    """), {"uid": str(user.id), "d": int(days), "lim": int(limit)}).fetchall()
    if not rows:
        return []
    from backend.models.agent_contact import AgentContact
    names = {str(r.id): r.name for r in bs.resources(db, user.id, active_only=False)}
    contacts = {str(c.id): c for c in db.query(AgentContact).filter(AgentContact.id.in_([r[0] for r in rows])).all()}
    now = bs.now_utc()
    out = []
    for cid, last_at, visits, master, service in rows:
        c = contacts.get(str(cid))
        out.append({"agent_contact_id": str(cid), "name": c.name if c else None, "visits": int(visits),
                    "days_since": (now - bs._aw(last_at)).days, "last_service": service,
                    "last_master": names.get(str(master)) if master else None})
    return out


def get_salon_report(db: Session, user, period: Optional[str] = "week", lapsed_days: Optional[int] = None) -> Dict[str, Any]:
    from backend.services import analytics_service
    period = period if period in ("today", "yesterday", "week", "month", "this_month", "quarter") else "week"
    rep = analytics_service.cached_report(db, user, period=period, sections=("bookings", "finance"))
    b = rep.get("bookings") or {}
    out: Dict[str, Any] = {"ok": True, "period": (rep.get("period") or {}).get("label") or period}
    if b:
        out["bookings"] = {
            "total": b.get("total"), "prev_total": b.get("prev_total"), "came": b.get("arrived"),
            "no_show": b.get("no_show"), "no_show_pct": b.get("no_show_pct"), "cancelled": b.get("cancelled"),
            "upcoming": b.get("upcoming"), "revenue_text": bs.price_text(b.get("revenue") or 0),
            "prev_revenue_text": bs.price_text(b.get("prev_revenue") or 0),
            "avg_check_text": bs.price_text(b["avg_check"]) if b.get("avg_check") else None,
            "load_pct": b.get("load_pct"), "repeat_clients": b.get("repeat_clients"),
        }
        out["masters"] = [{"name": m["name"], "bookings": m["count"], "load_pct": m["load_pct"],
                           "revenue_text": bs.price_text(m["revenue"] or 0)} for m in b.get("masters") or []]
        out["top_services"] = [{"title": x["title"], "count": x["count"], "revenue_text": bs.price_text(x["revenue"] or 0)}
                               for x in (b.get("services") or [])[:5]]
    fin = rep.get("finance") or {}
    if fin and fin.get("expenses_count"):
        out["profit_text"] = bs.price_text(fin.get("profit") or 0)
        out["expenses_text"] = bs.price_text(fin.get("expenses") or 0)
    days = max(14, min(int(lapsed_days or LAPSED_DAYS), 365))
    out["lapsed_days"] = days
    out["lapsed_clients"] = lapsed_clients(db, user, days)
    if not b:
        out["note"] = "Записей пока нет — журнал записи пуст"
    return out


# ───────────────────────────── фото работ мастера ─────────────────────────────

async def _mirror(db: Session, user_id, agent_config_id, media_id, filename: str, title: Optional[str]):
    """Фото салона → файл агента (только для отправки; в библиотеке владельца не показывается, source=salon).
    Один раз на фото: повторная отправка берёт ту же копию."""
    from backend.models.agent_file import AgentFile
    from backend.services import agent_media_service, salon_media
    key = f"salon:{media_id}"
    row = (db.query(AgentFile).filter(AgentFile.agent_config_id == agent_config_id, AgentFile.source == "salon",
                                      AgentFile.external_message_id == key).first())
    if row is not None and row.storage_key:
        return row
    data = salon_media.read_bytes(db, media_id)
    if not data:
        return None
    return await agent_media_service.store_file(
        db, user_id=user_id, agent_config_id=agent_config_id, data=data, filename=filename, mime="image/jpeg",
        source="salon", kind="image", external_message_id=key, title=title, status="ready")


async def send_staff_portfolio(db: Session, user, args: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    from backend.models.agent_config import AgentConfig
    from backend.models.agent_contact import AgentContact
    from backend.services import agent_tools as at
    from backend.services.salon_media import media_items
    agent_config_id = context.get("agent_config_id")
    contact_id = context.get("current_contact_id") if context.get("surface") == "postcall" else None
    contact_id = contact_id or args.get("agent_contact_id") or context.get("current_contact_id")
    if not contact_id:
        return {"ok": False, "error": "Не указан клиент (agent_contact_id)"}
    agent = context.get("agent_config") or db.query(AgentConfig).filter(AgentConfig.id == agent_config_id).first()
    if agent is None:
        return {"ok": False, "error": "Агент не найден"}
    contact = db.query(AgentContact).filter(AgentContact.id == contact_id, AgentContact.user_id == user.id).first()
    if contact is None:
        return {"ok": False, "error": "Клиент не найден"}
    r = _match_master(bs.resources(db, user.id), args.get("master"))
    if r is None:
        return {"ok": False, "error": "Такого мастера нет — проверь имя через get_staff"}
    channel = (args.get("channel") or "").strip().lower() or at._detect_recent_channel(db, agent.id, contact.id)
    if channel not in ("whatsapp", "telegram", "instagram", "webchat"):
        return {"ok": False, "error": "Клиент ещё не писал ни в один мессенджер — некуда отправить фото"}
    try:
        count = max(1, min(int(args.get("count") or MAX_PORTFOLIO_SEND), MAX_PORTFOLIO_SEND))
    except (TypeError, ValueError):
        count = MAX_PORTFOLIO_SEND
    works = media_items(r.portfolio, "caption")[:count]
    photos = [(w["id"], w.get("caption") or "") for w in works]
    if not photos and r.photo_id:
        photos = [(str(r.photo_id), "")]
    years = bs.experience_years(r.experience_since)
    head = r.name + (f" — {r.role}" if r.role else "") + (f", опыт {years} {_years_word(years)}" if years else "")
    if not photos:
        return {"ok": False, "error": f"У мастера {r.name} нет фото работ — расскажи о нём словами (get_staff)"}
    from backend.services import webchat_service
    send_file = {"whatsapp": at.fn_whatsapp_send_file, "telegram": at.fn_telegram_send_file,
                 "instagram": at.fn_instagram_send_file, "webchat": webchat_service.fn_webchat_send_file}[channel]
    sent = 0
    for i, (mid, cap) in enumerate(photos):
        f = await _mirror(db, user.id, agent.id, mid, f"{r.name}-{i + 1}.jpg", cap or r.name)
        if f is None or not f.storage_key:
            continue
        caption = (("Работы мастера " + head) + (f"\n{cap}" if cap else "")) if sent == 0 else cap
        res = await send_file({"agent_contact_id": str(contact.id), "file_id": str(f.id), "caption": caption},
                              str(user.id), agent, db)
        if not res.get("ok"):
            if sent == 0:
                return res
            break
        sent += 1
    if not sent:
        return {"ok": False, "error": "Фото мастера сейчас недоступны — расскажи о нём словами"}
    return {"ok": True, "channel": channel, "sent_photos": sent, "master": r.name,
            "note": "Фото отправлены. Не описывай их заново — коротко спроси, записать ли к этому мастеру."}


def _years_word(n: int) -> str:
    n = abs(int(n))
    if 11 <= n % 100 <= 14:
        return "лет"
    return {1: "год", 2: "года", 3: "года", 4: "года"}.get(n % 10, "лет")


# ───────────────────────────── вход ─────────────────────────────

def _contact_for(context: Dict[str, Any], args: Dict[str, Any]) -> Optional[str]:
    if context.get("surface") == "postcall" and context.get("current_contact_id"):
        return str(context["current_contact_id"])
    return args.get("agent_contact_id") or context.get("current_contact_id")


async def execute(tool_name: str, args: Dict[str, Any], context: Dict[str, Any], db: Session) -> str:
    from backend.models.user import User
    from backend.services.booking_tools import booking_enabled
    user = context.get("user") or db.query(User).filter(User.id == context.get("user_id")).first()
    args = args or {}
    try:
        if not booking_enabled(db, user):
            return _dump({"ok": False, "error": "Онлайн-запись выключена у владельца"})
        if tool_name == "get_services":
            return _dump(get_services(db, user, args.get("query"), args.get("category")))
        if tool_name == "get_staff":
            return _dump(get_staff(db, user, args.get("master"), args.get("service")))
        if tool_name == "get_customer_history":
            return _dump(get_customer_history(db, user, _contact_for(context, args)))
        if tool_name == "get_salon_information":
            return _dump(get_salon_information(db, user))
        if tool_name == "send_staff_portfolio":
            return _dump(await send_staff_portfolio(db, user, args, context))
        if tool_name == "save_review":
            from backend.services import salon_manager
            cid = _contact_for(context, args)
            if not cid:
                return _dump({"ok": False, "error": "Не указан клиент"})
            return _dump(salon_manager.save_review(db, user, cid, args.get("score"), args.get("comment")))
        if tool_name == "get_salon_report":
            if context.get("surface") == "postcall":
                return _dump({"ok": False, "error": "Отчёт салона — только владельцу"})
            return _dump(get_salon_report(db, user, args.get("period"), args.get("lapsed_days")))
    except Exception as e:
        db.rollback()
        logger.error(f"[SALON-AI] {tool_name} failed: {e}", exc_info=True)
        return _dump({"ok": False, "error": "Не удалось получить данные, попробуйте ещё раз"})
    return _dump({"ok": False, "error": "unknown_tool"})


def augment_owner(tools: list, agent_config, db: Session) -> list:
    """Отчёт салона — в чат владельца (и Telegram владельца), если включена запись."""
    try:
        from backend.models.user import User
        from backend.services.agent_tools import to_chat_completions_tools
        from backend.services.booking_tools import booking_enabled
        user = db.query(User).filter(User.id == agent_config.user_id).first()
        if not booking_enabled(db, user):
            return tools
        have = {(t.get("function") or {}).get("name") for t in tools}
        return tools + [t for t in to_chat_completions_tools(OWNER_TOOLS) if t["function"]["name"] not in have]
    except Exception as e:
        logger.warning(f"[SALON-AI] owner tools skipped: {e}")
        return tools


PROMPT_BLOCK = (
    "\n\n## Салон: прайс, мастера, клиент\n"
    "Цены, услуги и мастеров бери только из get_services и get_staff, адрес и часы — из get_salon_information. "
    "Постоянному клиенту сначала загляни в get_customer_history и предложи «как в прошлый раз» или его мастера. "
    "Хочет посмотреть работы — send_staff_portfolio. Не перечисляй весь прайс: две-три услуги под его вопрос. "
    "Клиент ответил оценкой визита (1–5) на «спасибо» — сохрани её через save_review. "
    "Назвал свой день рождения — запиши его в карточку (update_contact_info, birthday). Если ДР ещё не известен, "
    "после подтверждённой записи можно один раз спросить его, чтобы поздравить; не настаивай и не спрашивай снова."
)
