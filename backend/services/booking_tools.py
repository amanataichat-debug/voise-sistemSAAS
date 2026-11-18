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

TOOL_NAMES = {"find_booking_slots", "create_booking", "reschedule_booking", "cancel_booking", "list_bookings"}

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
                "master": {"type": "string", "description": "Имя мастера, если клиент просит конкретного"},
                "date": {"type": "string", "description": "С какого дня искать: YYYY-MM-DD, «сегодня» или «завтра». Пусто — с сегодня"},
                "count": {"type": "integer", "description": "Сколько вариантов вернуть (по умолчанию 3)"},
            },
        },
    },
    {
        "type": "function",
        "name": "create_booking",
        "description": (
            "Онлайн-запись: записать клиента. Только после того, как клиент согласился на конкретное время. "
            "start — время из find_booking_slots. После записи подтверди одной фразой: день, время, услуга."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "start": {"type": "string", "description": "Начало, ISO по Бишкеку: 2026-11-20T15:00 (или поле start из find_booking_slots)"},
                "service": {"type": "string", "description": "Услуга словами или id"},
                "master": {"type": "string", "description": "Имя мастера (необязательно)"},
                "agent_contact_id": {"type": "string", "description": "Клиент (карточка CRM). В переписке — текущий клиент"},
                "client_name": {"type": "string", "description": "Имя клиента, если карточки нет"},
                "client_phone": {"type": "string", "description": "Телефон клиента, если карточки нет"},
                "note": {"type": "string", "description": "Комментарий к записи (пожелания клиента)"},
            },
            "required": ["start"],
        },
    },
    {
        "type": "function",
        "name": "reschedule_booking",
        "description": "Онлайн-запись: перенести запись клиента на другое время (сначала найди свободное через find_booking_slots).",
        "parameters": {
            "type": "object",
            "properties": {
                "booking_id": {"type": "string", "description": "Id записи. Пусто — ближайшая запись клиента"},
                "agent_contact_id": {"type": "string", "description": "Клиент, если id записи не знаешь"},
                "new_start": {"type": "string", "description": "Новое время, ISO по Бишкеку: 2026-11-21T12:00"},
            },
            "required": ["new_start"],
        },
    },
    {
        "type": "function",
        "name": "cancel_booking",
        "description": "Онлайн-запись: отменить запись клиента (когда он сам просит отменить).",
        "parameters": {
            "type": "object",
            "properties": {
                "booking_id": {"type": "string", "description": "Id записи. Пусто — ближайшая запись клиента"},
                "agent_contact_id": {"type": "string", "description": "Клиент, если id записи не знаешь"},
                "reason": {"type": "string", "description": "Причина отмены словами клиента"},
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
            },
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
        have = {(t.get("function") or {}).get("name") for t in tools}
        return tools + [t for t in to_chat_completions_tools(TOOLS) if t["function"]["name"] not in have]
    except Exception as e:
        logger.warning(f"[BOOKING-TOOLS] augment skipped: {e}")
        return tools


PROMPT_BLOCK = (
    "\n\n## Онлайн-запись\n"
    "Клиент хочет записаться или спрашивает про время: find_booking_slots → предложи 2–3 ближайших варианта "
    "словами → после согласия клиента на конкретное время create_booking → подтверди одной фразой (день, время, "
    "услуга). Не записывай без явного согласия на время и не выдумывай свободные окна. Перенос — найди новое время "
    "и reschedule_booking; отмена по просьбе клиента — cancel_booking. Напоминание клиенту придёт само."
)


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
            service = bs.find_service(db, user.id, None, args.get("service")) if args.get("service") else None
            res = _match_resource(db, user.id, args.get("master"))
            count = max(1, min(int(args.get("count") or 3), 8))
            slots = bs.nearest_slots(db, user, service, res.id if res else None, args.get("date"), count=count)
            db.commit()
            out = {"ok": True, "slots": slots,
                   "service": bs.service_to_dict(service) if service else None,
                   "masters": [r.name for r in bs.resources(db, user.id)]}
            if args.get("service") and service is None:
                out["note"] = "Такую услугу в каталоге не нашёл — уточни у клиента название"
            if not slots:
                out["note"] = "Свободного времени в ближайшие 2 недели нет — предложи записаться в лист ожидания или передай владельцу"
            return json.dumps(out, ensure_ascii=False)
        if tool_name == "create_booking":
            res = _match_resource(db, user.id, args.get("master"))
            b = bs.create_booking(db, user, start=args.get("start"), service_query=args.get("service"),
                                  resource_id=res.id if res else None, contact_id=_contact_for(context, args),
                                  client_name=args.get("client_name"), client_phone=args.get("client_phone"),
                                  note=args.get("note"), source="bot",
                                  channel=(context.get("call_direction") or "").replace("_inbound", "") or None)
            db.commit()
            try:
                from backend.core.spawn import spawn
                from backend.services.booking_notify import notify_owner_new_booking
                spawn(notify_owner_new_booking(user.id, b.id, by="bot"))
            except Exception:
                pass
            d = bs.booking_to_dict(b, names)
            return json.dumps({"ok": True, "booking": d,
                               "say": f"Записал: {d['label']}" + (f", {d['service']}" if d['service'] else "")
                                      + (f", мастер {d['resource']}" if d['resource'] else "")}, ensure_ascii=False)
        if tool_name == "reschedule_booking":
            b = _contact_booking(db, user, args, context)
            b = bs.update_booking(db, user, b.id, start=args.get("new_start"), source="bot")
            db.commit()
            return json.dumps({"ok": True, "booking": bs.booking_to_dict(b, names)}, ensure_ascii=False)
        if tool_name == "cancel_booking":
            b = _contact_booking(db, user, args, context)
            b = bs.cancel_booking(db, user, b.id, args.get("reason"))
            db.commit()
            return json.dumps({"ok": True, "booking": bs.booking_to_dict(b, names)}, ensure_ascii=False)
        if tool_name == "list_bookings":
            if _contact_for(context, args) and (context.get("surface") == "postcall" or args.get("agent_contact_id")):
                rows = bs.upcoming_for_contact(db, user.id, _contact_for(context, args), limit=5)
            else:
                day = bs.parse_day(args.get("date"))
                start, _ = bs.day_bounds(day)
                rows = bs.list_bookings(db, user.id, start, start + timedelta(days=max(1, min(int(args.get("days") or 1), 14))),
                                        include_cancelled=False)
            return json.dumps({"ok": True, "bookings": [bs.booking_to_dict(b, names) for b in rows]}, ensure_ascii=False)
    except bs.BookingError as e:
        db.rollback()
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
    except Exception as e:
        db.rollback()
        logger.error(f"[BOOKING-TOOLS] {tool_name} failed: {e}", exc_info=True)
        return json.dumps({"ok": False, "error": "Не удалось выполнить запись, попробуйте ещё раз"}, ensure_ascii=False)
    return json.dumps({"ok": False, "error": "unknown_tool"}, ensure_ascii=False)
