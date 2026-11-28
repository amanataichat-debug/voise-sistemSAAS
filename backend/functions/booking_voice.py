"""
Онлайн-запись на ЗВОНКЕ (ветка 1811-niche-admin-speed): find_booking_slots и book_appointment.

Голосовой агент (GPT-Live / Gemini / каскад) записывает клиента сам, как бот в переписке:
те же правила (services/booking_service.py) — свободное время по графику мастеров, без двойной
записи. Номер клиента берётся из самого звонка (sip_calls.caller / to_number по call_id
адаптера), карточку CRM система находит или заводит сама — диктовать телефон клиенту не нужно.

На карточку голоса функции включаются В ПАМЯТИ перед звонком, только если у владельца включён
модуль «Запись» (agent_voice_context.apply_live_booking_functions).
"""

from typing import Any, Dict, Optional

from backend.core.logging import get_logger
from backend.functions.base import FunctionBase
from backend.functions.registry import register_function

logger = get_logger(__name__)


def _owner(db, assistant_config):
    from backend.models.user import User
    uid = getattr(assistant_config, "user_id", None)
    return db.query(User).filter(User.id == uid).first() if uid else None


def _call_phone(db, context: Dict[str, Any]) -> Optional[str]:
    """Номер собеседника телефонного звонка: входящий — caller, исходящий — to_number."""
    ws = context.get("websocket")
    call_id = getattr(ws, "call_id", None) or getattr(getattr(ws, "ws", None), "call_id", None)
    if not call_id:
        return None
    try:
        from backend.models.sip_gateway import SipCall
        call = db.query(SipCall).filter(SipCall.id == call_id).first()
        if call is None:
            return None
        digits = call.caller if call.direction == "inbound" else call.to_number
        return ("+" + digits) if digits and not str(digits).startswith("+") else digits
    except Exception as e:
        logger.warning(f"[VOICE-BOOKING] caller number lookup failed: {e}")
        return None


@register_function
class FindBookingSlotsFunction(FunctionBase):
    """Ближайшее свободное время для записи."""

    @classmethod
    def get_name(cls) -> str:
        return "find_booking_slots"

    @classmethod
    def get_display_name(cls) -> str:
        return "Онлайн-запись: свободное время"

    @classmethod
    def get_description(cls) -> str:
        return ("Найти ближайшее свободное время для записи: по услуге, мастеру и дню. Назови клиенту 2–3 варианта "
                "словами, не перечисляй всю сетку.")

    @classmethod
    def get_parameters(cls) -> Dict[str, Any]:
        return {"type": "object", "properties": {
            "service": {"type": "string", "description": "Услуга словами: «стрижка», «маникюр с покрытием»"},
            "master": {"type": "string", "description": "Имя мастера, если клиент просит конкретного"},
            "date": {"type": "string", "description": "С какого дня: YYYY-MM-DD, «сегодня», «завтра»"},
        }}

    @staticmethod
    async def execute(arguments: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
        from backend.db.session import SessionLocal
        from backend.services import booking_service as bs
        from backend.services.booking_tools import _match_resource, booking_enabled
        db = SessionLocal()
        try:
            user = _owner(db, (context or {}).get("assistant_config"))
            if user is None or not booking_enabled(db, user):
                return {"ok": False, "error": "Онлайн-запись выключена — предложи передать заявку администратору"}
            service = bs.find_service(db, user.id, None, arguments.get("service")) if arguments.get("service") else None
            res = _match_resource(db, user.id, arguments.get("master"))
            slots = bs.nearest_slots(db, user, service, res.id if res else None, arguments.get("date"), count=3)
            db.commit()
            note = None if slots else "Свободного времени в ближайшие 2 недели нет"
            if res is not None and not bs.bookable_online(res):
                note = f"К мастеру {res.name} записывает администратор — предложи другого мастера или передать заявку"
            return {"ok": True, "slots": [dict({"start": s["start"], "say": s["label"], "master": s["resource"]},
                                               **({"price": s["price"], "duration_min": s["duration_min"]}
                                                  if "duration_min" in s else {})) for s in slots],
                    "service": bs.service_to_dict(service) if service else None,
                    "note": note}
        except Exception as e:
            db.rollback()
            logger.error(f"[VOICE-BOOKING] slots failed: {e}", exc_info=True)
            return {"ok": False, "error": "Не получилось посмотреть расписание"}
        finally:
            db.close()


@register_function
class BookAppointmentFunction(FunctionBase):
    """Записать клиента со звонка."""

    @classmethod
    def get_name(cls) -> str:
        return "book_appointment"

    @classmethod
    def get_display_name(cls) -> str:
        return "Онлайн-запись: записать клиента"

    @classmethod
    def get_description(cls) -> str:
        return ("Записать клиента на согласованное время (start из find_booking_slots). Только после явного согласия "
                "клиента. Номер телефона клиента система возьмёт из звонка. После записи подтверди одной фразой.")

    @classmethod
    def get_parameters(cls) -> Dict[str, Any]:
        return {"type": "object", "properties": {
            "start": {"type": "string", "description": "Время из find_booking_slots (поле start)"},
            "service": {"type": "string", "description": "Услуга словами"},
            "master": {"type": "string", "description": "Имя мастера (необязательно)"},
            "client_name": {"type": "string", "description": "Как зовут клиента"},
            "note": {"type": "string", "description": "Пожелания клиента"},
        }, "required": ["start"]}

    @staticmethod
    async def execute(arguments: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
        from backend.db.session import SessionLocal
        from backend.services import booking_service as bs
        from backend.services.booking_tools import _match_resource, booking_enabled
        context = context or {}
        db = SessionLocal()
        try:
            user = _owner(db, context.get("assistant_config"))
            if user is None or not booking_enabled(db, user):
                return {"ok": False, "error": "Онлайн-запись выключена — предложи передать заявку администратору"}
            res = _match_resource(db, user.id, arguments.get("master"))
            b = bs.create_booking(db, user, start=arguments.get("start"), service_query=arguments.get("service"),
                                  resource_id=res.id if res else None, client_name=arguments.get("client_name"),
                                  client_phone=_call_phone(db, context), note=arguments.get("note"),
                                  source="call", channel="call")
            db.commit()
            try:
                from backend.core.spawn import spawn
                from backend.services.booking_notify import notify_owner_new_booking
                spawn(notify_owner_new_booking(user.id, b.id, by="call"))
            except Exception:
                pass
            names = {str(r.id): r.name for r in bs.resources(db, user.id, active_only=False)}
            d = bs.booking_to_dict(b, names)
            out = {"ok": True, "say": f"Записал: {d['label']}" + (f", {d['service']}" if d["service"] else "")
                   + (f", мастер {d['resource']}" if d["resource"] else ""), "booking_id": d["id"]}
            if d.get("prepay_amount"):
                how = bs.settings_of(bs.get_page(db, user, create=False)).get("prepay_info") or ""
                out["prepay"] = {"amount": d["prepay_amount"],
                                 "how": how or "реквизиты пришлёт администратор в мессенджер",
                                 "say": "Скажи клиенту сумму предоплаты и как её внести — запись подтвердят после оплаты"}
            return out
        except bs.BookingError as e:
            db.rollback()
            return {"ok": False, "error": str(e)}
        except Exception as e:
            db.rollback()
            logger.error(f"[VOICE-BOOKING] book failed: {e}", exc_info=True)
            return {"ok": False, "error": "Не получилось записать — предложи передать заявку администратору"}
        finally:
            db.close()


# ───────────────────── записи клиента на звонке (Voksy Beauty, шаг 2.6) ─────────────────────
# Клиент звонит (или ему звонит агент — подтверждение визита, перенос) и хочет подтвердить, перенести или
# отменить свою запись. Клиент — по номеру звонка (никаких «продиктуйте номер»: чужую запись так не отменить).
# Правила — те же, что у «Моих записей» (services/client_cabinet.py): «не позже чем за N ч», онлайн-запись,
# мастер «только через администратора». Подтверждение — сразу. Перенос и отмена — в два шага: первый вызов
# возвращает итог, второй (client_agreed=true) выполняется, только если ПОСЛЕ итога клиент что-то ответил
# (по стенограмме звонка) — модель не может отменить запись, не спросив. Журнал действий: «Голосовой агент».

import time as _time

_PENDING: Dict[tuple, Dict[str, Any]] = {}
PENDING_TTL = 900


def _socket(context: Dict[str, Any]):
    ws = (context or {}).get("websocket")
    return ws


def _call_key(context: Dict[str, Any]) -> str:
    ws = _socket(context)
    cid = getattr(ws, "call_id", None) or getattr(getattr(ws, "ws", None), "call_id", None)
    return str(cid or id(ws) or "widget")


def _user_turns(context: Dict[str, Any]) -> Optional[int]:
    """Сколько реплик клиента в стенограмме звонка (None — стенограммы нет: тест агента в браузере)."""
    ws = _socket(context)
    tr = getattr(ws, "transcript", None) or getattr(getattr(ws, "ws", None), "transcript", None)
    if tr is None or not hasattr(tr, "turns"):
        return None
    return sum(1 for t in tr.turns if t.get("role") == "user" and (t.get("text") or "").strip())


def _client(db, context: Dict[str, Any]):
    from backend.services import client_cabinet as cc
    user = _owner(db, (context or {}).get("assistant_config"))
    if user is None:
        return None, None, "Онлайн-запись выключена — предложи передать просьбу администратору"
    from backend.services.booking_tools import booking_enabled
    if not booking_enabled(db, user):
        return user, None, "Онлайн-запись выключена — предложи передать просьбу администратору"
    phone = _call_phone(db, context)
    if not phone:
        return user, None, ("Номер звонка не определился — записи клиента не найти. Предложи передать просьбу "
                            "администратору")
    cl = cc.client_for_phone(db, user, phone)
    if cl is None:
        return user, None, "Онлайн-запись в салоне не настроена — предложи передать просьбу администратору"
    return user, cl, None


def _call_hint(msg: str) -> str:
    """Отказ для голоса: правило салона — коротко почему и «передам администратору»; время не подошло — другое."""
    msg = (msg or "").replace(" — позвоните или напишите салону", "").rstrip(". ")
    if "не позже чем за" in msg or "администратор" in msg or "нельзя" in msg:
        return msg + ". Объясни это клиенту коротко и предложи передать просьбу администратору — он свяжется сам."
    return msg + ". Подбери другое время через find_booking_slots."


@register_function
class ClientBookingsFunction(FunctionBase):
    """Предстоящие записи клиента по номеру звонка."""

    @classmethod
    def get_name(cls) -> str:
        return "client_bookings"

    @classmethod
    def get_display_name(cls) -> str:
        return "Онлайн-запись: записи клиента"

    @classmethod
    def get_description(cls) -> str:
        return ("Предстоящие записи этого клиента по номеру звонка: когда, услуги, специалист, подтверждена ли, можно "
                "ли перенести или отменить. Вызывай, когда клиент хочет подтвердить, перенести или отменить запись, "
                "или звонок — про его запись.")

    @classmethod
    def get_parameters(cls) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    @staticmethod
    async def execute(arguments: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
        from backend.db.session import SessionLocal
        from backend.services import client_cabinet as cc
        db = SessionLocal()
        try:
            _user, cl, err = _client(db, context or {})
            if err:
                return {"ok": False, "error": err}
            rows = cc.upcoming_for(db, cl)
            db.rollback()
            out = [{"booking_id": v["id"], "when": v["say"],
                    "masters": sorted({(i.get("master") or {}).get("name") for i in v["items"] if i.get("master")}),
                    "confirmed": v["status"] == "confirmed", "can_move": v["can_move"], "can_cancel": v["can_cancel"],
                    **({"rule": v["change_note"]} if v.get("change_note") else {}),
                    **({"price": v["price"]} if v.get("price") else {})} for v in rows]
            return {"ok": True, "client": cl.name or None, "bookings": out,
                    **({} if out else {"note": "У клиента нет предстоящих записей по этому номеру"})}
        except Exception as e:
            db.rollback()
            logger.error(f"[VOICE-BOOKING] client bookings failed: {e}", exc_info=True)
            return {"ok": False, "error": "Не получилось посмотреть записи"}
        finally:
            db.close()


@register_function
class ChangeBookingFunction(FunctionBase):
    """Подтвердить, перенести или отменить запись клиента на звонке."""

    @classmethod
    def get_name(cls) -> str:
        return "change_booking"

    @classmethod
    def get_display_name(cls) -> str:
        return "Онлайн-запись: подтвердить, перенести, отменить"

    @classmethod
    def get_description(cls) -> str:
        return ("Подтвердить, перенести или отменить запись клиента (booking_id из client_bookings). Подтверждение — "
                "сразу, когда клиент сказал, что придёт. Перенос и отмена — в два шага: первый вызов без client_agreed "
                "проверит и вернёт итог; назови итог клиенту, дождись ответа и только при его согласии вызови ещё раз "
                "с client_agreed=true. Новое время для переноса — start из find_booking_slots.")

    @classmethod
    def get_parameters(cls) -> Dict[str, Any]:
        return {"type": "object", "properties": {
            "booking_id": {"type": "string", "description": "booking_id из client_bookings"},
            "action": {"type": "string", "enum": ["confirm", "reschedule", "cancel"],
                       "description": "confirm — придёт; reschedule — перенос; cancel — отмена"},
            "new_start": {"type": "string", "description": "Для переноса: start из find_booking_slots"},
            "reason": {"type": "string", "description": "Почему отменяет (если сказал)"},
            "client_agreed": {"type": "boolean", "description": "true — клиент согласился с названным итогом"},
        }, "required": ["booking_id", "action"]}

    @staticmethod
    async def execute(arguments: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
        from backend.db.session import SessionLocal
        from backend.services import booking_service as bs
        from backend.services import client_cabinet as cc
        context = context or {}
        args = arguments or {}
        action = args.get("action")
        bid = str(args.get("booking_id") or "")
        if action not in ("confirm", "reschedule", "cancel"):
            return {"ok": False, "error": "action: confirm, reschedule или cancel"}
        db = SessionLocal()
        try:
            _user, cl, err = _client(db, context)
            if err:
                return {"ok": False, "error": err}
            if action == "confirm":
                r = cc.confirm_for(db, cl, bid, source="call")
                return {"ok": True, "done": "confirmed", "when": r["label"]}
            if action == "reschedule" and not args.get("new_start"):
                return {"ok": False, "error": "Нужно новое время: сначала find_booking_slots, потом new_start"}
            key = (_call_key(context), bid, action, str(args.get("new_start") or ""))
            now = _time.time()
            for k in [k for k, v in _PENDING.items() if now - v["at"] > PENDING_TTL]:
                _PENDING.pop(k, None)
            pend = _PENDING.get(key)
            turns = _user_turns(context)
            if not args.get("client_agreed") or pend is None:
                # шаг 1: проверить и вернуть итог
                if action == "reschedule":
                    chk = cc.try_move(db, cl, bid, args["new_start"], source="call")
                    summary = f"перенос: {chk['was']} → {chk['now']}"
                else:
                    b = cc.own_booking(db, cl, bid)
                    parts = cc._parts(db, cl, b)
                    block = cc.change_block(parts[0], bs.settings_of(cl.page))
                    if block:
                        raise cc.ClientError(block)
                    summary = "отмена: " + cc.client_label(parts)
                    db.rollback()
                _PENDING[key] = {"at": now, "turns": turns}
                return {"ok": False, "needs_client_answer": True, "summary": summary,
                        "next": "Назови клиенту этот итог своими словами и дождись ответа. Согласился — вызови "
                                "change_booking ещё раз с теми же параметрами и client_agreed=true; нет — ничего не "
                                "меняй и спроси, как ему удобнее."}
            # шаг 2: клиент должен был ответить после итога
            if turns is not None and pend.get("turns") is not None and turns <= pend["turns"]:
                return {"ok": False, "needs_client_answer": True,
                        "error": "Клиент ещё не ответил на итог — сначала назови его и дождись ответа"}
            if turns is None and now - pend["at"] < 2:
                return {"ok": False, "needs_client_answer": True,
                        "error": "Сначала назови клиенту итог и дождись ответа"}
            _PENDING.pop(key, None)
            if action == "reschedule":
                r = cc.move(db, cl, bid, args["new_start"], source="call")
                return {"ok": True, "done": "rescheduled", "when": r["label"]}
            r = cc.cancel_for(db, cl, bid, args.get("reason"), source="call")
            return {"ok": True, "done": "cancelled", "was": r["label"],
                    "next": "Подтверди отмену одной фразой; можно предложить записаться на другое время."}
        except cc.NotFound:
            db.rollback()
            return {"ok": False, "error": "Такой записи у этого клиента нет — сверься с client_bookings"}
        except cc.ClientError as e:
            db.rollback()
            return {"ok": False, "error": _call_hint(str(e))}
        except Exception as e:
            db.rollback()
            logger.error(f"[VOICE-BOOKING] change failed: {e}", exc_info=True)
            return {"ok": False, "error": "Не получилось изменить запись — предложи передать просьбу администратору"}
        finally:
            db.close()
