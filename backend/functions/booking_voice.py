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
            return {"ok": True, "slots": [{"start": s["start"], "say": s["label"], "master": s["resource"]} for s in slots],
                    "service": bs.service_to_dict(service) if service else None,
                    "note": None if slots else "Свободного времени в ближайшие 2 недели нет"}
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
            return {"ok": True, "say": f"Записал: {d['label']}" + (f", {d['service']}" if d["service"] else "")
                    + (f", мастер {d['resource']}" if d["resource"] else ""), "booking_id": d["id"]}
        except bs.BookingError as e:
            db.rollback()
            return {"ok": False, "error": str(e)}
        except Exception as e:
            db.rollback()
            logger.error(f"[VOICE-BOOKING] book failed: {e}", exc_info=True)
            return {"ok": False, "error": "Не получилось записать — предложи передать заявку администратору"}
        finally:
            db.close()
