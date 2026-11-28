"""
Инструменты онлайн-записи для агента (ветка 1811-niche-admin-speed): переписка (PostCall) и чат
владельца. Голосовой агент на звонке использует ту же логику через functions/book_appointment.py.

  find_booking_slots  — ближайшие свободные времена (услуга, мастер, день)
  create_booking      — записать клиента на согласованное время
  reschedule_booking  — перенести запись
  cancel_booking      — отменить запись
  list_bookings       — записи на день / ближайшие записи клиента

Даются агенту, только если у владельца включён модуль «Запись» (профиль бизнеса). В разборе
переписки (surface=postcall) бот записывает ТОЛЬКО клиента, чьё обращение разбирает.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from backend.services import booking_service as bs

logger = logging.getLogger(__name__)

TOOL_NAMES = {"find_booking_slots", "create_booking", "reschedule_booking", "cancel_booking", "list_bookings",
              "add_to_waitlist", "answer_waitlist_offer"}

TOOLS = [
    {
        "type": "function",
        "name": "find_booking_slots",
        "description": (
            "Онлайн-запись: найти ближайшее свободное время. Вызывай, когда клиент хочет записаться или спрашивает, "
            "есть ли время. Верни клиенту 2–3 варианта словами («завтра в 11:00 или в 15:30»), не всю сетку. "
            "Услугу и мастера можно назвать словами — система найдёт сама."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "Услуга словами или id из каталога (например «маникюр с покрытием»)"},
                "services": {"type": "array", "items": {"type": "string"},
                             "description": "Несколько услуг в один визит подряд (маникюр + педикюр) — вместо service"},
                "master": {"type": "string", "description": "Имя мастера, если клиент просит конкретного. Пусто — любой свободный"},
                "date": {"type": "string", "description": "С какого дня искать: YYYY-MM-DD, «сегодня» или «завтра». Пусто — с сегодня"},
                "count": {"type": "integer", "description": "Сколько вариантов вернуть (по умолчанию 3)"},
            },
        },
    },
    {
        "type": "function",
        "name": "create_booking",
        "description": (
            "Онлайн-запись: записать клиента на время из find_booking_slots. В переписке с клиентом — В ДВА ШАГА: "
            "первый вызов (без confirmation_id) ничего не записывает, а проверяет время и возвращает summary и "
            "confirmation_id — отправь клиенту этот итог и спроси, записывать ли. Когда клиент ответит согласием, "
            "вызови create_booking с confirmation_id: запись выполнится (код сам проверит, что клиент сказал «да»). "
            "В чате владельца запись выполняется сразу."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "start": {"type": "string", "description": "Начало, ISO по Бишкеку: 2026-11-20T15:00 (или поле start из find_booking_slots)"},
                "service": {"type": "string", "description": "Услуга словами или id"},
                "services": {"type": "array", "items": {"type": "string"},
                             "description": "Несколько услуг в один визит подряд — вместо service"},
                "master": {"type": "string", "description": "Имя мастера (необязательно)"},
                "agent_contact_id": {"type": "string", "description": "Клиент (карточка CRM). В переписке — текущий клиент"},
                "client_name": {"type": "string", "description": "Имя клиента, если карточки нет"},
                "client_phone": {"type": "string", "description": "Телефон клиента, если карточки нет"},
                "note": {"type": "string", "description": "Комментарий к записи (пожелания клиента)"},
                "confirmation_id": {"type": "string", "description": "Из первого вызова — когда клиент согласился с итогом"},
            },
        },
    },
    {
        "type": "function",
        "name": "reschedule_booking",
        "description": ("Онлайн-запись: перенести запись клиента на другое время (сначала найди свободное через "
                        "find_booking_slots). В переписке — в два шага, как create_booking: итог клиенту → его «да» → "
                        "вызов с confirmation_id."),
        "parameters": {
            "type": "object",
            "properties": {
                "booking_id": {"type": "string", "description": "Id записи. Пусто — ближайшая запись клиента"},
                "agent_contact_id": {"type": "string", "description": "Клиент, если id записи не знаешь"},
                "new_start": {"type": "string", "description": "Новое время, ISO по Бишкеку: 2026-11-21T12:00"},
                "confirmation_id": {"type": "string", "description": "Из первого вызова — когда клиент согласился"},
            },
        },
    },
    {
        "type": "function",
        "name": "cancel_booking",
        "description": ("Онлайн-запись: отменить запись клиента (когда он сам просит отменить). В переписке — в два шага: "
                        "итог клиенту → его «да» → вызов с confirmation_id."),
        "parameters": {
            "type": "object",
            "properties": {
                "booking_id": {"type": "string", "description": "Id записи. Пусто — ближайшая запись клиента"},
                "agent_contact_id": {"type": "string", "description": "Клиент, если id записи не знаешь"},
                "reason": {"type": "string", "description": "Причина отмены словами клиента"},
                "confirmation_id": {"type": "string", "description": "Из первого вызова — когда клиент согласился"},
            },
        },
    },
    {
        "type": "function",
        "name": "list_bookings",
        "description": "Онлайн-запись: кто записан на день (для владельца) или ближайшие записи клиента.",
        "parameters": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "День: YYYY-MM-DD, «сегодня», «завтра». Пусто — сегодня"},
                "days": {"type": "integer", "description": "Сколько дней показать (по умолчанию 1)"},
                "agent_contact_id": {"type": "string", "description": "Только записи этого клиента (ближайшие)"},
                "unconfirmed_only": {"type": "boolean",
                                     "description": "Только записи, которые клиент ещё не подтвердил (для звонков-подтверждений)"},
            },
        },
    },
    {
        "type": "function",
        "name": "add_to_waitlist",
        "description": (
            "Лист ожидания: свободного времени под клиента нет (find_booking_slots пусто или всё не подходит) — "
            "поставить его в очередь. Освободится подходящее время — клиенту придёт сообщение, ответить «да» можно "
            "30 минут. Спроси, какие дни и время суток подходят, и к какому мастеру (или к любому)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "Услуга (название из прайса)"},
                "master": {"type": "string", "description": "Мастер; пусто — любой"},
                "date_from": {"type": "string", "description": "С какого дня, YYYY-MM-DD (пусто — сегодня)"},
                "date_to": {"type": "string", "description": "По какой день, YYYY-MM-DD (пусто — две недели)"},
                "time_from": {"type": "string", "description": "Не раньше, ЧЧ:ММ (например, «после 18» → 18:00)"},
                "time_to": {"type": "string", "description": "Не позже, ЧЧ:ММ (начало визита)"},
                "note": {"type": "string", "description": "Пожелание клиента"},
                "agent_contact_id": {"type": "string", "description": "Клиент (в переписке — текущий)"},
            },
            "required": ["service"],
        },
    },
    {
        "type": "function",
        "name": "answer_waitlist_offer",
        "description": (
            "Ответ клиента на предложение из листа ожидания («освободилось время… записать вас?»): accept=true — "
            "записать на это время сразу (итог второй раз не спрашивать), accept=false — не подходит (ждём другое), "
            "stop=true — больше не нужно (убрать из листа)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "accept": {"type": "boolean"},
                "stop": {"type": "boolean"},
                "agent_contact_id": {"type": "string", "description": "Клиент (в переписке — текущий)"},
            },
            "required": ["accept"],
        },
    },
]


def booking_enabled(db: Session, user) -> bool:
    try:
        from backend.services.business_profile import modules_of
        return bool(user is not None and modules_of(user).get("booking"))
    except Exception:
        return False


def augment(tools: list, agent_config, db: Session) -> list:
    """Домешать инструменты записи (Chat Completions формат), если у владельца включена запись."""
    try:
        from backend.models.user import User
        from backend.services.agent_tools import to_chat_completions_tools
        user = db.query(User).filter(User.id == agent_config.user_id).first()
        if not booking_enabled(db, user):
            return tools
        from backend.services import salon_ai_tools
        have = {(t.get("function") or {}).get("name") for t in tools}
        return tools + [t for t in to_chat_completions_tools(TOOLS + salon_ai_tools.TOOLS)
                        if t["function"]["name"] not in have]
    except Exception as e:
        logger.warning(f"[BOOKING-TOOLS] augment skipped: {e}")
        return tools


PROMPT_BLOCK = (
    "\n\n## Онлайн-запись\n"
    "Клиент хочет записаться или спрашивает про время: find_booking_slots → предложи 2–3 ближайших варианта "
    "словами → клиент выбрал → create_booking. В переписке с клиентом запись, перенос и отмена идут в ДВА шага: "
    "первый вызов возвращает summary и confirmation_id — отправь итог клиенту одним сообщением и спроси, всё ли "
    "верно; после его согласия вызови тот же инструмент с confirmation_id — только тогда запись выполнится. "
    "Не выдумывай свободные окна и цены — только из ответов инструментов. Несколько услуг за один визит — "
    "services списком. Перенос — найди новое время и reschedule_booking; отмена по просьбе клиента — "
    "cancel_booking. Напоминание клиенту придёт само. "
    "Цену называй как в поле price_text (диапазон «1 500–2 500 сом» — так и говори, точную назовёт мастер). "
    "Если в ответе create_booking есть prepay — скажи клиенту сумму предоплаты и как её внести (prepay.how); "
    "запись подтвердят после оплаты. "
    "Нет подходящего времени — предложи лист ожидания (add_to_waitlist): освободится — клиенту придёт сообщение. "
    "Клиент ответил на «освободилось время» из листа ожидания — answer_waitlist_offer. "
    "Подтверждать визиты звонком — только если так сказано в инструкции владельца или он попросил в чате: "
    "list_bookings(unconfirmed_only=true) на нужный день → клиентам с can_call — create_agent_task (в notes — какую "
    "запись подтвердить), остальным — сообщение. На звонке голосовой агент сам подтвердит, перенесёт или отменит "
    "запись (client_bookings, change_booking)."
)


def _listed(b, names: Dict[str, str]) -> Dict[str, Any]:
    """Запись для list_bookings + можно ли ей позвонить (номер O!) — чтобы агент сам решал, кому звонить
    подтвердить визит, а кому написать (шаг 2.6: как у Profi — решает агент по инструкции владельца)."""
    d = bs.booking_to_dict(b, names)
    try:
        from backend.models.sip_gateway import is_o_number, normalize_sip_number
        digits = normalize_sip_number(b.client_phone or "")
        d["can_call"] = bool(len(digits) == 12 and digits.startswith("996") and is_o_number(digits))
    except Exception:
        d["can_call"] = False
    d["confirmed"] = b.status == "confirmed"
    return d


def _match_resource(db: Session, user_id, master: Optional[str]):
    if not master:
        return None
    m = master.strip().lower()
    rows = bs.resources(db, user_id)
    for r in rows:
        if str(r.id) == master.strip():
            return r
    best = None
    for r in rows:
        name = (r.name or "").lower()
        if m == name or m in name or name.split()[0][:4] == m[:4]:
            best = r
            break
    return best


def _contact_for(context: Dict[str, Any], args: Dict[str, Any]) -> Optional[str]:
    # В разборе переписки — только клиент этого обращения
    if context.get("surface") == "postcall" and context.get("current_contact_id"):
        return str(context["current_contact_id"])
    return args.get("agent_contact_id") or context.get("current_contact_id")


def _contact_booking(db: Session, user, args, context):
    if args.get("booking_id"):
        return bs.get_booking(db, user.id, args["booking_id"])
    cid = _contact_for(context, args)
    if not cid:
        raise bs.BookingError("Не указано, чью запись менять")
    rows = bs.upcoming_for_contact(db, user.id, cid, limit=1)
    if not rows:
        raise bs.BookingError("У клиента нет предстоящих записей")
    return rows[0]


async def execute(tool_name: str, args: Dict[str, Any], context: Dict[str, Any], db: Session) -> str:
    # Журнал действий: в разборе переписки — ИИ-администратор, в чате владельца — ИИ по его команде
    from backend.services import activity
    who = "ИИ-администратор" if context.get("surface") == "postcall" else "ИИ по команде владельца"
    with activity.acting("ai", who):
        return await _execute(tool_name, args, context, db)


async def _execute(tool_name: str, args: Dict[str, Any], context: Dict[str, Any], db: Session) -> str:
    from backend.models.user import User
    user = context.get("user")
    if user is None:
        user = db.query(User).filter(User.id == context.get("user_id")).first()
    args = args or {}
    try:
        if not booking_enabled(db, user):
            return json.dumps({"ok": False, "error": "Онлайн-запись выключена у владельца"}, ensure_ascii=False)
        names = {str(r.id): r.name for r in bs.resources(db, user.id, active_only=False)}
        if tool_name == "find_booking_slots":
            res = _match_resource(db, user.id, args.get("master"))
            count = max(1, min(int(args.get("count") or 3), 8))
            if len(_service_queries(args)) > 1:
                return _visit_slots(db, user, args, res, count, names)
            service = bs.find_service(db, user.id, None, args.get("service")) if args.get("service") else None
            slots = bs.nearest_slots(db, user, service, res.id if res else None, args.get("date"), count=count)
            db.commit()
            out = {"ok": True, "slots": slots,
                   "service": bs.service_to_dict(service) if service else None,
                   "masters": [r.name for r in bs.resources(db, user.id) if bs.bookable_online(r)]}
            if args.get("service") and service is None:
                out["note"] = "Такую услугу в каталоге не нашёл — уточни у клиента название (get_services — весь прайс)"
            if not slots:
                out["note"] = "Свободного времени в ближайшие 2 недели нет — предложи записаться в лист ожидания или передай владельцу"
            if res is not None and not bs.bookable_online(res):
                out["note"] = (f"К мастеру {res.name} записывает администратор — предложи другого мастера "
                               "или передай владельцу")
            return json.dumps(out, ensure_ascii=False)
        if tool_name == "create_booking":
            return _create(db, user, args, context, names)
        if tool_name == "reschedule_booking":
            return _reschedule(db, user, args, context, names)
        if tool_name == "cancel_booking":
            return _cancel(db, user, args, context, names)
        if tool_name in ("add_to_waitlist", "answer_waitlist_offer"):
            return _waitlist_tool(db, user, tool_name, args, context, names)
        if tool_name == "list_bookings":
            if _contact_for(context, args) and (context.get("surface") == "postcall" or args.get("agent_contact_id")):
                rows = bs.upcoming_for_contact(db, user.id, _contact_for(context, args), limit=5)
            else:
                day = bs.parse_day(args.get("date"))
                start, _ = bs.day_bounds(day)
                rows = bs.list_bookings(db, user.id, start, start + timedelta(days=max(1, min(int(args.get("days") or 1), 14))),
                                        include_cancelled=False)
            if args.get("unconfirmed_only"):
                rows = [b for b in rows if b.status == "booked" and bs._aw(b.start_at) > bs.now_utc()]
            return json.dumps({"ok": True, "bookings": [_listed(b, names) for b in rows]}, ensure_ascii=False)
    except bs.BookingError as e:
        db.rollback()
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
    except _Refusal as e:
        db.rollback()
        return json.dumps(dict({"ok": False, "error": e.message}, **e.extra), ensure_ascii=False)
    except Exception as e:
        db.rollback()
        logger.error(f"[BOOKING-TOOLS] {tool_name} failed: {e}", exc_info=True)
        return json.dumps({"ok": False, "error": "Не удалось выполнить запись, попробуйте ещё раз"}, ensure_ascii=False)
    return json.dumps({"ok": False, "error": "unknown_tool"}, ensure_ascii=False)


def _waitlist_tool(db: Session, user, tool_name: str, args: Dict[str, Any], context: Dict[str, Any],
                   names: Dict[str, str]) -> str:
    """Лист ожидания (шаг 2.4): в разборе переписки — только клиент этого обращения."""
    from backend.services import waitlist_service as wl
    cid = _contact_for(context, args)
    if not cid:
        return json.dumps({"ok": False, "error": "Не указан клиент"}, ensure_ascii=False)
    try:
        if tool_name == "add_to_waitlist":
            res = _match_resource(db, user.id, args.get("master"))
            if args.get("master") and res is None:
                return json.dumps({"ok": False, "error": f"Мастера «{args['master']}» нет — уточни у клиента"},
                                  ensure_ascii=False)
            source = "bot" if context.get("surface") == "postcall" else "owner"
            e = wl.add(db, user, service_query=args.get("service"), resource_id=res.id if res else None,
                       date_from=args.get("date_from"), date_to=args.get("date_to"), time_from=args.get("time_from"),
                       time_to=args.get("time_to"), contact_id=cid, note=args.get("note"), source=source)
            db.commit()
            return json.dumps({"ok": True, "service": e.service_title, "master": res.name if res else "любой",
                               "period": wl._period_words(e),
                               "next": ("Скажи клиенту одной фразой: он в листе ожидания, освободится время — придёт "
                                        "сообщение, ответить «да» можно 30 минут.")}, ensure_ascii=False)
        e = wl.active_offer(db, user.id, cid)
        if e is None:
            return json.dumps({"ok": False, "error": "Сейчас клиенту ничего не предложено из листа ожидания"},
                              ensure_ascii=False)
        if args.get("accept"):
            from backend.services import booking_confirm
            if booking_confirm.needs_client_confirmation(context) and e.offered_at is not None:
                state, last = booking_confirm.consent_state(booking_confirm.messages_since(db, cid, e.offered_at))
                if state != "yes":
                    db.rollback()
                    why = {"not_shown": "сообщение с предложением ещё не ушло клиенту — подожди его ответа",
                           "wait": "клиент ещё не ответил на предложение",
                           "no": "клиент не согласился (" + (last or "")[:100] + ") — вызови accept=false",
                           }.get(state, "из ответа клиента (" + (last or "")[:100] + ") не ясно, согласен ли он — "
                                        "коротко спроси, записывать ли")
                    return json.dumps({"ok": False, "error": "Не записано: " + why}, ensure_ascii=False)
            out = wl.accept(db, e.id, source="bot")
            return json.dumps(dict(out, next="Подтверди запись одной фразой: день, время, мастер. Напоминание придёт само."),
                              ensure_ascii=False)
        out = wl.decline(db, e.id, stop=bool(args.get("stop")))
        return json.dumps(dict(out, next=("Клиента убрали из листа ожидания — коротко попрощайся." if args.get("stop") else
                                          "Ок, ждём другое время — коротко скажи, что напишешь, когда освободится.")),
                          ensure_ascii=False)
    except wl.WaitlistError as ex:
        db.rollback()
        return json.dumps({"ok": False, "error": str(ex)}, ensure_ascii=False)


# ───────────────────────────── запись в два шага (шаг 1.6а) ─────────────────────────────

class _Refusal(Exception):
    def __init__(self, message: str, **extra):
        super().__init__(message)
        self.message, self.extra = message, extra


def _service_queries(args: Dict[str, Any]) -> List[str]:
    raw = args.get("services")
    items = [str(x).strip() for x in raw if str(x).strip()] if isinstance(raw, list) else []
    if not items and args.get("service"):
        items = [str(args["service"]).strip()]
    return items[:bs.MAX_VISIT_ITEMS]


def _resolve_items(db: Session, user, args: Dict[str, Any], res) -> List[Dict[str, Any]]:
    """Услуги словами → [{service_id, resource_id?}]. Мастер ставится там, где он делает эту услугу."""
    out = []
    for q in _service_queries(args):
        svc = bs.find_service(db, user.id, None, q)
        if svc is None:
            raise bs.BookingError(f"Не нашёл услугу «{q}» в прайсе — уточни у клиента (get_services — весь прайс)")
        it: Dict[str, Any] = {"service_id": str(svc.id)}
        if res is not None and bs._resource_does(res, svc):
            it["resource_id"] = str(res.id)
        out.append(it)
    return out


def _visit_slots(db: Session, user, args, res, count: int, names: Dict[str, str]) -> str:
    items = _resolve_items(db, user, args, res)
    day = bs.parse_day(args.get("date")) if args.get("date") else bs.to_local(bs.now_utc()).date()
    found: List[Dict[str, Any]] = []
    for i in range(14):
        for s in bs.visit_slots(db, user, items, day + timedelta(days=i), source="bot", limit=count):
            found.append({"start": s["start"], "label": bs.fmt_local(bs.parse_start(s["start"])),
                          "end_time": s["end_time"], "masters": [names.get(m) for m in s["masters"]]})
            if len(found) >= count:
                break
        if len(found) >= count:
            break
    db.commit()
    out = {"ok": True, "visit": True, "slots": found,
           "services": [bs.service_to_dict(bs.find_service(db, user.id, it["service_id"])) for it in items]}
    if not found:
        out["note"] = "Весь визит целиком в ближайшие 2 недели не помещается — предложи услуги в разные дни"
    return json.dumps(out, ensure_ascii=False)


def _channel(context: Dict[str, Any]) -> Optional[str]:
    return (context.get("call_direction") or "").replace("_inbound", "").replace("_outbound", "") or None


def _do_create(db: Session, user, p: Dict[str, Any]) -> List[Any]:
    common = dict(client_name=p.get("client_name"), client_phone=p.get("client_phone"),
                  contact_id=p.get("contact_id"), source="bot", channel=p.get("channel"), note=p.get("note"))
    items = p.get("items") or []
    if len(items) > 1:
        return bs.create_visit(db, user, items, p["start"], **common)
    it = items[0] if items else {}
    return [bs.create_booking(db, user, start=p["start"], service_id=it.get("service_id"),
                              resource_id=it.get("resource_id"), **common)]


def _line(b, names: Dict[str, str], db: Session, user_id) -> str:
    svc = bs.find_service(db, user_id, b.service_entry_id) if b.service_entry_id else None
    # Диапазон «от–до» — только если цена записи = цена прайса (у мастера своя цена — она и есть)
    same = svc is not None and b.price is not None and svc.price is not None and float(b.price) == float(svc.price)
    price = bs.price_text(b.price, getattr(svc, "price_to", None) if same else None,
                          getattr(svc, "currency", None) or "KGS")
    parts = [b.service_title or "визит", (names.get(str(b.resource_id)) or "") and f"мастер {names.get(str(b.resource_id))}",
             bs.fmt_local(b.start_at), price]
    return ", ".join(x for x in parts if x)


def _summary(db: Session, user, rows: List[Any], names: Dict[str, str]) -> Dict[str, Any]:
    lines = [_line(b, names, db, user.id) for b in rows]
    prepay = sum(float(b.prepay_amount or 0) for b in rows)
    text_ = "; ".join(lines)
    if prepay:
        text_ += f". Предоплата {bs._num(prepay)} сом"
    return {"text": text_, "prepay": prepay or None,
            "bookings": [bs.booking_to_dict(b, names) for b in rows]}


def _prepay_how(db: Session, user) -> str:
    return (bs.settings_of(bs.get_page(db, user, create=False)).get("prepay_info")
            or "Реквизиты для оплаты пришлёт администратор")


def _notify_new(user, rows) -> None:
    try:
        from backend.core.spawn import spawn
        from backend.services.booking_notify import notify_owner_new_booking
        spawn(notify_owner_new_booking(user.id, rows[0].id, by="bot"))
    except Exception:
        pass


def _done(db: Session, user, rows, names) -> str:
    summ = _summary(db, user, rows, names)
    out = {"ok": True, "booking": summ["bookings"][0], "bookings": summ["bookings"],
           "say": "Записал: " + summ["text"]}
    if summ["prepay"]:
        out["prepay"] = {"amount": summ["prepay"], "how": _prepay_how(db, user)}
    return json.dumps(out, ensure_ascii=False)


def _ask(row, summary_text: str, extra: Optional[Dict[str, Any]] = None) -> str:
    out = {"ok": True, "needs_confirmation": True, "confirmation_id": str(row.id), "summary": summary_text,
           "next": ("Ещё НЕ выполнено. Отправь клиенту этот итог одним сообщением своими словами и спроси, всё ли "
                    "верно. Когда клиент ответит согласием — вызови этот же инструмент с confirmation_id.")}
    out.update(extra or {})
    return json.dumps(out, ensure_ascii=False)


def _create(db: Session, user, args, context, names) -> str:
    from backend.services import booking_confirm as bc
    contact_id = _contact_for(context, args)
    two_step = bc.needs_client_confirmation(context)
    if two_step and args.get("confirmation_id"):
        row = bc.find_pending(db, user.id, contact_id, "create", args.get("confirmation_id"))
        if row is None:
            raise _Refusal("Этот итог не найден или устарел — заново вызови create_booking без confirmation_id")
        refusal = bc.check_consent(db, row)
        if refusal:
            raise _Refusal(refusal, needs_confirmation=True)
        rows = _do_create(db, user, row.payload)
        bc.mark_used(row, rows[0].id)
        db.commit()
        _notify_new(user, rows)
        return _done(db, user, rows, names)

    if not args.get("start"):
        raise bs.BookingError("Укажи start — время из find_booking_slots")
    res = _match_resource(db, user.id, args.get("master"))
    items = _resolve_items(db, user, args, res) if _service_queries(args) else []
    if not items and res is not None:
        items = [{"service_id": None, "resource_id": str(res.id)}]
    payload = {"start": args.get("start"), "items": items, "contact_id": contact_id,
               "client_name": args.get("client_name"), "client_phone": args.get("client_phone"),
               "note": args.get("note"), "channel": _channel(context)}
    if not two_step:
        rows = _do_create(db, user, payload)
        db.commit()
        _notify_new(user, rows)
        return _done(db, user, rows, names)

    # Пробная запись: всё проверяется как по-настоящему (время, мастер, кабинет, цена) и откатывается.
    # Страница записи и мастер по умолчанию создаются ДО отката: иначе у салона без мастеров итог
    # закреплял мастера, которого после отката уже нет, и «да» клиента падало «мастер не делает услугу».
    bs.get_page(db, user)
    bs.ensure_resources(db, user)
    db.flush()
    sp = db.begin_nested()
    try:
        rows = _do_create(db, user, payload)
        summ = _summary(db, user, rows, names)
        pinned = [{"service_id": str(b.service_entry_id) if b.service_entry_id else None,
                   "resource_id": str(b.resource_id)} for b in rows]
        start = rows[0].start_at.isoformat()
    finally:
        sp.rollback()
    payload.update(items=pinned, start=start)
    row = bc.create_pending(db, user.id, contact_id, "create", payload, summ["text"])
    db.commit()
    extra = {"prepay": {"amount": summ["prepay"], "how": _prepay_how(db, user)}} if summ["prepay"] else None
    return _ask(row, summ["text"], extra)


def _guard_late(db: Session, user, b, context) -> None:
    if context.get("surface") != "postcall":
        return
    msg = bs.change_too_late(b, bs.settings_of(bs.get_page(db, user, create=False)))
    if msg:
        raise _Refusal(msg)


def _reschedule(db: Session, user, args, context, names) -> str:
    from backend.services import booking_confirm as bc
    contact_id = _contact_for(context, args)
    two_step = bc.needs_client_confirmation(context)
    if two_step and args.get("confirmation_id"):
        row = bc.find_pending(db, user.id, contact_id, "reschedule", args.get("confirmation_id"))
        if row is None:
            raise _Refusal("Этот итог не найден или устарел — заново вызови reschedule_booking без confirmation_id")
        refusal = bc.check_consent(db, row)
        if refusal:
            raise _Refusal(refusal, needs_confirmation=True)
        b = bs.get_booking(db, user.id, row.payload["booking_id"])
        _guard_late(db, user, b, context)
        b = bs.update_booking(db, user, b.id, start=row.payload["new_start"], source="bot")
        bc.mark_used(row, b.id)
        db.commit()
        return json.dumps({"ok": True, "booking": bs.booking_to_dict(b, names),
                           "say": "Перенёс: " + _line(b, names, db, user.id)}, ensure_ascii=False)
    if not args.get("new_start"):
        raise bs.BookingError("Укажи new_start — новое время из find_booking_slots")
    b = _contact_booking(db, user, args, context)
    _guard_late(db, user, b, context)
    if not two_step:
        b = bs.update_booking(db, user, b.id, start=args.get("new_start"), source="bot")
        db.commit()
        return json.dumps({"ok": True, "booking": bs.booking_to_dict(b, names)}, ensure_ascii=False)
    was = _line(b, names, db, user.id)
    bid = b.id
    sp = db.begin_nested()
    try:
        moved = bs.update_booking(db, user, bid, start=args.get("new_start"), source="bot")
        now_text = _line(moved, names, db, user.id)
        new_start = moved.start_at.isoformat()
    finally:
        sp.rollback()
    summary = f"перенос: {was} → {now_text}"
    row = bc.create_pending(db, user.id, contact_id, "reschedule", {"booking_id": str(bid), "new_start": new_start},
                            summary)
    db.commit()
    return _ask(row, summary)


def _cancel(db: Session, user, args, context, names) -> str:
    from backend.services import booking_confirm as bc
    contact_id = _contact_for(context, args)
    two_step = bc.needs_client_confirmation(context)

    def run(b, reason):
        if b.visit_id:
            rows = bs.cancel_visit(db, user, b.visit_id, reason, source="bot")
        else:
            rows = [bs.cancel_booking(db, user, b.id, reason, source="bot")]
        return rows

    if two_step and args.get("confirmation_id"):
        row = bc.find_pending(db, user.id, contact_id, "cancel", args.get("confirmation_id"))
        if row is None:
            raise _Refusal("Этот итог не найден или устарел — заново вызови cancel_booking без confirmation_id")
        refusal = bc.check_consent(db, row)
        if refusal:
            raise _Refusal(refusal, needs_confirmation=True)
        b = bs.get_booking(db, user.id, row.payload["booking_id"])
        if b.status == "cancelled":
            bc.mark_used(row, b.id)
            db.commit()
            return json.dumps({"ok": True, "booking": bs.booking_to_dict(b, names), "note": "Запись уже отменена"},
                              ensure_ascii=False)
        _guard_late(db, user, b, context)
        rows = run(b, row.payload.get("reason"))
        bc.mark_used(row, b.id)
        db.commit()
        return json.dumps({"ok": True, "booking": bs.booking_to_dict(rows[0], names),
                           "cancelled": len(rows)}, ensure_ascii=False)
    b = _contact_booking(db, user, args, context)
    _guard_late(db, user, b, context)
    if not two_step:
        rows = run(b, args.get("reason"))
        db.commit()
        return json.dumps({"ok": True, "booking": bs.booking_to_dict(rows[0], names), "cancelled": len(rows)},
                          ensure_ascii=False)
    parts = bs.visit_bookings(db, user.id, b.visit_id) if b.visit_id else [b]
    summary = "отмена: " + "; ".join(_line(x, names, db, user.id) for x in parts if x.status != "cancelled")
    row = bc.create_pending(db, user.id, contact_id, "cancel", {"booking_id": str(b.id), "reason": args.get("reason")},
                            summary)
    db.commit()
    return _ask(row, summary)
