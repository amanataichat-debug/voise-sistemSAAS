"""
find_products — каталог агента ДЛЯ ГОЛОСОВОГО АГЕНТА (ветка 1610-kb-calls).

До этой ветки каталог существовал только для оркестратора (чат и мессенджеры):
на звонке у агента такой функции не было вообще, поэтому на вопрос «а есть
что-нибудь до 30 тысяч» он мог только пообещать уточнить.

Цены агент теперь знает наизусть — короткий прайс уходит в промпт звонка
(services/agent_voice_context.build_price_block), поэтому называть цену он
должен сразу, без вызова функции. Эта функция нужна, когда прайса мало:
подбор по цене («до 3000»), характеристики позиции, наличие, а также id фото —
чтобы отправить их клиенту в мессенджер после звонка.

Имя совпадает с инструментом оркестратора (services/agent_tools.py), реализация
общая — agent_knowledge_service.answer_products. Фильтр по цене идёт в SQL:
по числам векторный поиск врёт.
"""

from typing import Any, Dict

from backend.core.logging import get_logger
from backend.functions.base import FunctionBase
from backend.functions.registry import register_function

logger = get_logger(__name__)


@register_function
class FindProductsFunction(FunctionBase):
    """Подбор позиций каталога агента по запросу и цене."""

    @classmethod
    def get_name(cls) -> str:
        return "find_products"

    @classmethod
    def get_display_name(cls) -> str:
        return "Каталог агента (подбор позиции)"

    @classmethod
    def get_description(cls) -> str:
        return (
            "Подобрать позиции из каталога компании: по названию, по характеристикам или "
            "по цене («до 30 000»). Возвращает название, цену, наличие, характеристики и id "
            "фото. Цены из прайса в твоей инструкции называй сразу, без этой функции — "
            "вызывай её, когда нужен подбор, характеристики или фото."
        )

    @classmethod
    def get_parameters(cls) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Что ищет клиент: название, модель, категория",
                },
                "max_price": {
                    "type": "number",
                    "description": "Максимальная цена, если клиент назвал бюджет",
                },
                "min_price": {
                    "type": "number",
                    "description": "Минимальная цена, если клиент задал нижнюю границу",
                },
                "limit": {
                    "type": "integer",
                    "description": "Сколько позиций вернуть (по умолчанию 5, максимум 10)",
                    "default": 5,
                },
            },
        }

    @staticmethod
    async def execute(arguments: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
        context = context or {}
        assistant_config = context.get("assistant_config")

        def _num(value):
            try:
                return float(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        from backend.db.session import SessionLocal
        from backend.services import agent_knowledge_service as kb
        from backend.services.agent_voice_context import resolve_agent_for_functions

        # Своя сессия: сессию звонка держит хендлер (см. search_knowledge_base).
        db = SessionLocal()
        try:
            agent = resolve_agent_for_functions(db, assistant_config)
            if agent is None:
                return {"ok": True, "total": 0, "results": [],
                        "note": "Каталог к этому ассистенту не привязан."}
            result = await kb.answer_products(
                db, agent.id,
                query=(arguments.get("query") or "").strip(),
                max_price=_num(arguments.get("max_price")),
                min_price=_num(arguments.get("min_price")),
                limit=arguments.get("limit") or 5,
                channel="call",
            )
            logger.info(f"[VOICE-KB] find_products «{(arguments.get('query') or '')[:40]}» → "
                        f"{result.get('total', 0)} позиций")
            return result
        except Exception as e:
            logger.error(f"[VOICE-KB] find_products failed: {e}", exc_info=True)
            return {"error": f"Search failed: {e}"}
        finally:
            db.close()
