"""
find_client — голосовой агент узнаёт клиента по имени, компании, нише или
прежнему номеру (ветка 2110-client-memory).

Сценарий: клиент вчера писал в WhatsApp, сегодня звонит с другого номера и
говорит «я Айбек, у меня стоматология». По номеру звонящего карточки нет, и
раньше агент начинал разговор с нуля. Теперь он вызывает find_client, получает
карточку клиента с историей общения по всем каналам и продолжает с места.

Реализация общая с инструментом оркестратора — services/client_recognition.py
(Postgres + сравнение имён в Python, без LLM: голос ждать не должен).

Если клиент узнан уверенно и это телефонный звонок, найденная карточка
запоминается в метаданных звонка (sip_calls.call_metadata): после звонка новый
номер добавится в неё вторым, а не появится отдельная карточка
(AgentCallFinalizer). Функция включается на карточке голоса живьём перед
звонком (agent_voice_context.apply_live_client_functions) — только на входящем
с незнакомого номера и в браузерном тесте агента.
"""

from typing import Any, Dict

from backend.core.logging import get_logger
from backend.functions.base import FunctionBase
from backend.functions.registry import register_function

logger = get_logger(__name__)


@register_function
class FindClientFunction(FunctionBase):
    """Найти клиента агента обзвона по имени / компании / нише / номеру."""

    @classmethod
    def get_name(cls) -> str:
        return "find_client"

    @classmethod
    def get_display_name(cls) -> str:
        return "Узнать клиента"

    @classmethod
    def get_description(cls) -> str:
        return (
            "Найти клиента в базе по имени, компании, нише или прежнему номеру. Вызывай СРАЗУ, "
            "как только звонящий назвал имя или компанию, или сказал, что уже писал/переписывался/"
            "звонил с другого номера. Возвращает карточку и историю общения — продолжай с места. "
            "Смотри поле instruction: при сомнении уточни одним вопросом, не угадывай."
        )

    @classmethod
    def get_parameters(cls) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Имя, как назвал клиент (например «Айбек»)"},
                "company": {"type": "string", "description": "Название компании, если назвал"},
                "niche": {"type": "string", "description": "Чем занимается: «стоматология», «салон красоты»"},
                "phone": {"type": "string", "description": "Прежний номер, с которого писал или звонил"},
            },
        }

    @staticmethod
    async def execute(arguments: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
        context = context or {}
        assistant_config = context.get("assistant_config")
        arguments = arguments or {}
        if not any(str(arguments.get(k) or "").strip() for k in ("name", "company", "niche", "phone")):
            return {"ok": False, "error": "Передай имя, компанию, нишу или прежний номер клиента."}

        from backend.db.session import SessionLocal
        from backend.services import client_recognition
        from backend.services.agent_voice_context import resolve_agent_for_voice

        # Своя сессия: сессию звонка держит хендлер, коммитить в неё из функции нельзя.
        db = SessionLocal()
        try:
            agent = resolve_agent_for_voice(db, getattr(assistant_config, "id", None))
            if agent is None:
                return {"ok": True, "confidence": "none", "found": 0,
                        "instruction": client_recognition.VOICE_INSTRUCTIONS["none"]}
            result = client_recognition.find_for_voice(db, agent.id, arguments)
            logger.info(
                f"[CLIENT-FIND] find_client {arguments} → {result.get('confidence')}, "
                f"{result.get('found', 0)} совп."
            )
            if result.get("confidence") == "high" and result.get("clients"):
                _remember_match(db, getattr(assistant_config, "sip_session_id", None),
                                result["clients"][0])
            return result
        except Exception as e:
            logger.error(f"[CLIENT-FIND] find_client failed: {e}", exc_info=True)
            return {"ok": False, "error": f"Поиск не выполнен: {e}"}
        finally:
            db.close()


def _remember_match(db, sip_call_id, client: Dict[str, Any]) -> None:
    """Запомнить узнанного клиента в метаданных звонка — для склейки после звонка."""
    if not sip_call_id:
        return  # браузерный тест: звонка нет
    try:
        import uuid
        from backend.models.sip_gateway import SipCall
        call = db.get(SipCall, uuid.UUID(str(sip_call_id)))
        if call is None:
            return
        meta = dict(call.call_metadata or {})
        meta["matched_agent_contact_id"] = client.get("agent_contact_id")
        meta["matched_contact_name"] = client.get("name") or ""
        call.call_metadata = meta
        db.commit()
        logger.info(f"[CLIENT-FIND] звонок {sip_call_id}: клиент узнан — карточка {client.get('agent_contact_id')}")
    except Exception as e:
        db.rollback()
        logger.warning(f"[CLIENT-FIND] узнанный клиент не запомнен в звонке {sip_call_id}: {e}")
