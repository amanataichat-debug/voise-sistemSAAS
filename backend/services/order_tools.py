"""
Инструменты заказов для агента (ветка 1811-niche-admin-speed): переписка (PostCall) и чат владельца.

  create_order  — оформить заказ клиента (позиции из каталога, размер/цвет, доставка или самовывоз)
  list_orders   — заказы клиента (в переписке) или открытые / за сегодня (владельцу)
  update_order  — клиент меняет адрес/время или отменяет; владелец ещё и меняет статус

Даются агенту, только если у владельца включён модуль «Заказы» (профиль бизнеса). В разборе
переписки бот работает ТОЛЬКО с заказами клиента, чьё обращение разбирает, и не меняет статус
(подтверждает, собирает и отправляет — человек). Логика — services/order_service.py.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from backend.services import order_service as osvc

logger = logging.getLogger(__name__)

TOOL_NAMES = {"create_order", "list_orders", "update_order"}

TOOLS = [
    {
        "type": "function",
        "name": "create_order",
        "description": (
            "Заказы: оформить заказ клиента. Только когда клиент выбрал, что берёт, и подтвердил заказ: перед этим "
            "повтори одной строкой — что, вариант (размер/цвет), сколько, доставка или самовывоз, адрес и время, сумма — "
            "и дождись «да». Позиции — названием из каталога (find_products) или их id. После оформления назови номер "
            "заказа и сумму одной фразой; подтверждение и отправку делает магазин."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "description": "Позиции заказа",
                    "items": {
                        "type": "object",
                        "properties": {
                            "product": {"type": "string", "description": "Название позиции из каталога или её id"},
                            "size": {"type": "string", "description": "Размер / вариант, если у позиции есть размеры"},
                            "color": {"type": "string", "description": "Цвет, если у позиции есть цвета"},
                            "qty": {"type": "integer", "description": "Количество (по умолчанию 1)"},
                        },
                        "required": ["product"],
                    },
                },
                "delivery_type": {"type": "string", "enum": ["delivery", "pickup"], "description": "Доставка или самовывоз"},
                "address": {"type": "string", "description": "Адрес доставки (для доставки обязателен)"},
                "delivery_when": {"type": "string", "description": "Когда доставить или забрать, словами клиента («сегодня после 18:00»)"},
                "payment_method": {"type": "string", "enum": ["cash", "card", "transfer", "qr"], "description": "Как клиент будет платить, если сказал"},
                "client_name": {"type": "string", "description": "Имя клиента, если карточки нет"},
                "client_phone": {"type": "string", "description": "Телефон клиента для курьера, если клиент дал"},
                "note": {"type": "string", "description": "Комментарий к заказу (пожелания клиента)"},
                "agent_contact_id": {"type": "string", "description": "Клиент (карточка CRM). В переписке — текущий клиент"},
            },
            "required": ["items"],
        },
    },
    {
        "type": "function",
        "name": "list_orders",
        "description": (
            "Заказы: статус заказов клиента (в переписке — текущего клиента: «где мой заказ?») или, владельцу, "
            "открытые заказы и заказы за сегодня."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_contact_id": {"type": "string", "description": "Только заказы этого клиента"},
                "status": {"type": "string", "description": "open — открытые, new / confirmed / packed / delivering / done / cancelled"},
                "period": {"type": "string", "enum": ["today", "yesterday", "7d", "30d"], "description": "За какой период (владельцу)"},
            },
        },
    },
    {
        "type": "function",
        "name": "update_order",
        "description": (
            "Заказы: изменить заказ. Клиент просит другой адрес или время, или отменить заказ (пока заказ не отправлен) — "
            "передай поля. Номер заказа — если клиент назвал; пусто — последний открытый заказ клиента. "
            "Статус (подтвердить, собран, отправлен, выдан) меняет только владелец по своей команде."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "order_number": {"type": "string", "description": "Номер заказа (1024) или id. Пусто — последний открытый заказ клиента"},
                "agent_contact_id": {"type": "string", "description": "Клиент, если номер заказа не знаешь"},
                "address": {"type": "string", "description": "Новый адрес доставки"},
                "delivery_when": {"type": "string", "description": "Новое время доставки / самовывоза"},
                "cancel": {"type": "boolean", "description": "Клиент отменяет заказ"},
                "reason": {"type": "string", "description": "Причина отмены словами клиента"},
                "status": {"type": "string", "enum": ["confirmed", "packed", "delivering", "done", "cancelled"],
                           "description": "Только по команде владельца: новый статус заказа"},
            },
        },
    },
]

PROMPT_BLOCK = (
    "\n\n## Заказы\n"
    "Клиент хочет купить: найди позицию (find_products), уточни размер/цвет и количество, если они есть у позиции, "
    "спроси доставку или самовывоз (адрес и удобное время). Повтори заказ одной строкой с суммой и после «да» клиента "
    "оформи create_order, затем назови номер заказа и сумму одной фразой. Не оформляй без согласия и не продавай то, "
    "чего нет в наличии, — предложи похожее. «Где мой заказ?» — list_orders. Клиент меняет адрес, время или отменяет — "
    "update_order. Подтверждает, собирает и отправляет заказ магазин; клиенту об этом придёт сообщение."
)


def augment(tools: list, agent_config, db: Session) -> list:
    """Домешать инструменты заказов (Chat Completions формат), если у владельца включены заказы."""
    try:
        from backend.models.user import User
        from backend.services.agent_tools import to_chat_completions_tools
        user = db.query(User).filter(User.id == agent_config.user_id).first()
        if not osvc.orders_enabled(user):
            return tools
        have = {(t.get("function") or {}).get("name") for t in tools}
        return tools + [t for t in to_chat_completions_tools(TOOLS) if t["function"]["name"] not in have]
    except Exception as e:
        logger.warning(f"[ORDER-TOOLS] augment skipped: {e}")
        return tools


def _contact_for(context: Dict[str, Any], args: Dict[str, Any]) -> Optional[str]:
    # В разборе переписки — только клиент этого обращения
    if context.get("surface") == "postcall" and context.get("current_contact_id"):
        return str(context["current_contact_id"])
    return args.get("agent_contact_id") or context.get("current_contact_id")


def _find_order(db: Session, user, args: Dict[str, Any], context: Dict[str, Any]):
    postcall = context.get("surface") == "postcall"
    cid = _contact_for(context, args)
    ref = str(args.get("order_number") or "").strip()
    o = None
    if ref:
        o = osvc.find_by_number(db, user.id, ref)
        if o is None:
            try:
                o = osvc.get_order(db, user.id, ref)
            except osvc.OrderError:
                o = None
        if o is None:
            raise osvc.OrderError(f"Заказ {ref} не найден")
        if postcall and cid and str(o.contact_id or "") != str(cid):
            raise osvc.OrderError("Это заказ другого клиента")
        return o
    if not cid:
        raise osvc.OrderError("Не указано, чей заказ менять — назовите номер заказа")
    rows, _ = osvc.list_orders(db, user.id, status="open", contact_id=cid, limit=1)
    if not rows:
        raise osvc.OrderError("У клиента нет открытых заказов")
    return rows[0]


def _short(o, titles) -> Dict[str, Any]:
    d = osvc.order_to_dict(o, titles)
    return {k: d[k] for k in ("number", "label", "status", "status_title", "items_text", "total", "delivery_type",
                              "address", "delivery_when", "created_label", "client_name", "paid")}


async def execute(tool_name: str, args: Dict[str, Any], context: Dict[str, Any], db: Session) -> str:
    from backend.models.user import User
    user = context.get("user")
    if user is None:
        user = db.query(User).filter(User.id == context.get("user_id")).first()
    args = args or {}
    postcall = context.get("surface") == "postcall"
    try:
        if not osvc.orders_enabled(user):
            return json.dumps({"ok": False, "error": "Заказы выключены у владельца"}, ensure_ascii=False)
        titles = osvc.status_titles(user)
        if tool_name == "create_order":
            items = [{"title": i.get("product") or i.get("title"), "entry_id": i.get("product_id") or i.get("id"),
                      "size": i.get("size"), "color": i.get("color"), "qty": i.get("qty") or 1}
                     for i in (args.get("items") or []) if isinstance(i, dict)]
            # «product» бывает id позиции из find_products
            for it in items:
                t = str(it.get("title") or "")
                if not it.get("entry_id") and len(t) == 36 and t.count("-") == 4:
                    it["entry_id"], it["title"] = t, None
            channel = (context.get("call_direction") or "").replace("_inbound", "").replace("_outbound", "") or None
            o = osvc.create_order(db, user, items=items, contact_id=_contact_for(context, args),
                                  client_name=args.get("client_name"), client_phone=args.get("client_phone"),
                                  delivery_type=args.get("delivery_type") or "delivery", address=args.get("address"),
                                  delivery_when=args.get("delivery_when"), payment_method=args.get("payment_method"),
                                  note=args.get("note"), source="bot" if postcall else "owner", channel=channel)
            db.commit()
            if postcall:
                osvc.schedule(osvc.notify_owner_new_order(user.id, o.id))
            d = _short(o, titles)
            return json.dumps({"ok": True, "order": d,
                               "say": f"Заказ {d['label']} оформлен: {d['items_text']}, сумма {osvc.money(o.total)}"},
                              ensure_ascii=False)
        if tool_name == "list_orders":
            cid = _contact_for(context, args) if (postcall or args.get("agent_contact_id")) else None
            since = until = None
            if not cid and args.get("period"):
                from backend.api.orders import _period
                since, until = _period(args.get("period"))
            rows, total = osvc.list_orders(db, user.id, status=args.get("status") or (None if cid or since else "open"),
                                           contact_id=cid, since=since, until=until, limit=10)
            return json.dumps({"ok": True, "total": total, "orders": [_short(o, titles) for o in rows]}, ensure_ascii=False)
        if tool_name == "update_order":
            o = _find_order(db, user, args, context)
            if args.get("status"):
                if postcall:
                    return json.dumps({"ok": False, "error": "Статус заказа меняет магазин, не бот"}, ensure_ascii=False)
                o, prev = osvc.set_status(db, user, o.id, args["status"], args.get("reason"))
                db.commit()
                if prev != o.status and o.status in osvc.NOTIFY_ON:
                    osvc.schedule(osvc.notify_client_status(user.id, o.id, o.status))
                return json.dumps({"ok": True, "order": _short(o, titles)}, ensure_ascii=False)
            if args.get("cancel"):
                if postcall and o.status in ("delivering", "done"):
                    return json.dumps({"ok": False, "error": "Заказ уже в пути или выдан — отменить может только магазин, "
                                                             "передай владельцу (notify_owner_handoff)"}, ensure_ascii=False)
                o, _ = osvc.set_status(db, user, o.id, "cancelled", args.get("reason") or "клиент отменил")
                db.commit()
                return json.dumps({"ok": True, "order": _short(o, titles)}, ensure_ascii=False)
            data = {k: args[k] for k in ("address", "delivery_when") if args.get(k)}
            if not data:
                return json.dumps({"ok": False, "error": "Нечего менять: укажи адрес, время или отмену"}, ensure_ascii=False)
            o = osvc.update_order(db, user, o.id, data)
            db.commit()
            return json.dumps({"ok": True, "order": _short(o, titles)}, ensure_ascii=False)
    except osvc.OrderError as e:
        db.rollback()
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
    except Exception as e:
        db.rollback()
        logger.error(f"[ORDER-TOOLS] {tool_name} failed: {e}", exc_info=True)
        return json.dumps({"ok": False, "error": "Не удалось выполнить действие с заказом, попробуйте ещё раз"}, ensure_ascii=False)
    return json.dumps({"ok": False, "error": "unknown_tool"}, ensure_ascii=False)
