"""
search_knowledge_base — поиск по базе знаний агента ДЛЯ ГОЛОСОВОГО АГЕНТА
(ветка 1610-kb-calls).

Зачем новая функция. На звонке у агента был только search_pinecone: он ищет
по документам в Pinecone и в принципе не видит ни пар «вопрос — ответ», ни
секций «о компании», ни каталога. При этом добавление любой записи в базу
включало эту функцию голосовому агенту, так что агент честно шёл искать
(≈4 с тишины в трубке через делегирующий бэкенд) и ничего не находил. Плюс у
search_pinecone нет порога близости: он всегда возвращал три куска, даже
нерелевантных, и агент сочинял ответ по ним.

Имя намеренно совпадает с инструментом оркестратора (services/agent_tools.py):
один и тот же инструмент с одним и тем же поведением в чате, в мессенджерах и
на звонке. Реализация общая — agent_knowledge_service.answer_query:
  1) записи базы в Postgres (единицы мс, без сети),
  2) документы в Pinecone (только если в записях пусто, порог KB_MIN_SCORE).

Большую часть вопросов звонка должен закрывать промпт, без всякого поиска:
знания агента вшиваются в system_prompt звонка (services/agent_voice_context.py).
Эта функция — для того, чего в промпт не влезло.
"""

from typing import Any, Dict

from backend.core.logging import get_logger
from backend.functions.base import FunctionBase
from backend.functions.registry import register_function

logger = get_logger(__name__)


@register_function
class SearchKnowledgeBaseFunction(FunctionBase):
    """Поиск по базе знаний агента обзвона: записи + документы."""

    @classmethod
    def get_name(cls) -> str:
        return "search_knowledge_base"

    @classmethod
    def get_display_name(cls) -> str:
        return "Поиск в базе знаний агента"

    @classmethod
    def get_description(cls) -> str:
        return (
            "Найти ответ в базе знаний компании: вопросы-ответы, разделы о компании, "
            "каталог и содержимое загруженных документов. Вызывай ТОЛЬКО если ответа нет "
            "прямо в твоей инструкции: то, что уже есть в инструкции, называй сразу — "
            "поиск занимает время, и клиент слышит паузу."
        )

    @classmethod
    def get_parameters(cls) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Вопрос клиента своими словами (например «есть ли рассрочка»)",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Сколько фрагментов вернуть (по умолчанию 4)",
                    "default": 4,
                },
            },
            "required": ["query"],
        }

    @staticmethod
    async def execute(arguments: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
        context = context or {}
        assistant_config = context.get("assistant_config")

        query = (arguments.get("query") or "").strip()
        if not query:
            return {"error": "Query is required"}
        try:
            top_k = int(arguments.get("top_k") or 4)
        except (TypeError, ValueError):
            top_k = 4

        from backend.db.session import SessionLocal
        from backend.services import agent_knowledge_service as kb
        from backend.services.agent_voice_context import resolve_agent_for_voice

        # Своя сессия: сессию звонка держит хендлер, и коммитить в неё из
        # функции нельзя — это чужая транзакция.
        db = SessionLocal()
        try:
            agent = resolve_agent_for_voice(db, getattr(assistant_config, "id", None))
            if agent is None:
                return {"ok": True, "total": 0, "results": [],
                        "note": "База знаний к этому ассистенту не привязана."}
            result = await kb.answer_query(
                db, agent.id, getattr(agent, "kb_namespace", None),
                query, top_k=top_k, channel="call",
            )
            logger.info(f"[VOICE-KB] search_knowledge_base «{query[:60]}» → "
                        f"{result.get('source', 'ничего')}, {result.get('total', 0)} рез.")
            return result
        except Exception as e:
            logger.error(f"[VOICE-KB] search_knowledge_base failed: {e}", exc_info=True)
            return {"error": f"Search failed: {e}"}
        finally:
            db.close()
