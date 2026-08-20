"""
Единый учёт ассистентов пользователя для лимитов тарифа.

До появления этого модуля каждая страница фронтенда считала «сколько ассистентов
уже создано» по-своему (одна складывала openai+gemini, другая openai+gemini+grok
и т.д.), а бэкенд считал только строки assistant_configs. Здесь собран
единственный источник правды, которым пользуются и check_assistant_limit,
и эндпоинт /api/subscriptions/assistants-usage.

Голосовые ассистенты, созданные мастером Voicyfy Agent, в лимит НЕ входят —
они технически принадлежат агенту, а не пользователю, и определяются по
внешним ключам в agent_configs.
"""

from typing import Any, Dict, Set
from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.orm import Session

from backend.core.logging import get_logger
from backend.models.agent_config import AgentConfig
from backend.models.assistant import AssistantConfig
from backend.models.cartesia_assistant import CartesiaAssistantConfig
from backend.models.gemini_assistant import GeminiAssistantConfig
from backend.models.grok_assistant import GrokAssistantConfig
from backend.models.subscription import SubscriptionPlan
from backend.models.translate_assistant import TranslateAssistantConfig
from backend.models.yandex_assistant import YandexAssistantConfig
from backend.models.fish_assistant import FishAssistantConfig

logger = get_logger(__name__)

# ElevenLabs-агенты сознательно не учитываются: это отдельный продукт со своим
# биллингом, он никогда не входил в лимит ассистентов.

# Условная «бесконечность» для админов — фронтенд ждёт число, а не null.
UNLIMITED_ASSISTANTS = 999999


def get_agent_owned_assistant_ids(db: Session, user_id: Any) -> Set[UUID]:
    """
    ID голосовых ассистентов, созданных мастером Voicyfy Agent.

    Такие ассистенты исключаются из подсчёта лимита: пользователь их не создавал
    руками и не может создать сверх лимита, накликав агентов.
    """
    rows = db.query(
        AgentConfig.openai_assistant_id,
        AgentConfig.gemini_assistant_id,
        AgentConfig.cartesia_assistant_id,
        AgentConfig.yandex_assistant_id,
        AgentConfig.cascade_assistant_id,
        AgentConfig.fish_assistant_id,
    ).filter(AgentConfig.user_id == user_id).all()

    return {value for row in rows for value in row if value is not None}


def exclude_agent_owned(query, model, db: Session, user_id: Any, excluded: Set[UUID] = None):
    """
    Убрать из выборки голосовых ассистентов, принадлежащих агентам обзвона.

    Используется в списках ассистентов на страницах провайдеров: такой ассистент
    — внутренность агента, управляется только со страницы agent.html, и в общем
    списке ему делать нечего. Признак — внешний ключ из agent_configs, а не имя:
    пользователь волен назвать своего ассистента как угодно.

    excluded — заранее посчитанный набор ID (см. get_agent_owned_assistant_ids),
    чтобы не бегать в agent_configs отдельно на каждого провайдера.
    """
    if excluded is None:
        excluded = get_agent_owned_assistant_ids(db, user_id)
    return query.filter(model.id.notin_(excluded)) if excluded else query


def get_assistants_breakdown(db: Session, user_id: Any) -> Dict[str, int]:
    """
    Количество ассистентов пользователя по провайдерам (без ассистентов агента).

    Returns:
        Словарь вида {"openai": 2, "gemini": 0, ..., "total": 2}
    """
    excluded = get_agent_owned_assistant_ids(db, user_id)

    def count(model, *extra_filters) -> int:
        query = db.query(model).filter(model.user_id == user_id, *extra_filters)
        return exclude_agent_owned(query, model, db, user_id, excluded).count()

    breakdown = {
        "openai": count(AssistantConfig),
        "gemini": count(GeminiAssistantConfig),
        # Каскад живёт в той же таблице, что и Grok, и различается assistant_type.
        # Legacy-строки без типа считаем grok — так ни одна строка не потеряется.
        "grok": count(
            GrokAssistantConfig,
            or_(
                GrokAssistantConfig.assistant_type != "cascade",
                GrokAssistantConfig.assistant_type.is_(None),
            ),
        ),
        "cascade": count(GrokAssistantConfig, GrokAssistantConfig.assistant_type == "cascade"),
        "cartesia": count(CartesiaAssistantConfig),
        "yandex": count(YandexAssistantConfig),
        "fish": count(FishAssistantConfig),
        "translate": count(TranslateAssistantConfig),
    }
    breakdown["total"] = sum(breakdown.values())
    return breakdown


def count_user_assistants(db: Session, user_id: Any) -> int:
    """
    Общее количество ассистентов пользователя во всех провайдерах.

    Именно это число сравнивается с max_assistants тарифа.
    """
    return get_assistants_breakdown(db, user_id)["total"]


async def get_assistants_usage(db: Session, user) -> Dict[str, Any]:
    """
    Расход лимита ассистентов для UI: сколько занято, сколько доступно,
    можно ли создавать ещё.

    Правила совпадают с check_assistant_limit, чтобы кнопка «Создать» на
    фронтенде и ответ сервера никогда не расходились.
    """
    # Локальные импорты — иначе циклическая зависимость с user_service/dependencies.
    from backend.core.dependencies import PRIVILEGED_UNLIMITED_EMAILS, SPECIAL_ASSISTANT_LIMITS
    from backend.services.user_service import UserService

    breakdown = get_assistants_breakdown(db, user.id)
    used = breakdown["total"]

    # Код тарифа нужен фронтенду для матрицы доступа к разделам — отдаём его
    # всегда, включая админов, иначе разделы помечаются замком.
    plan_code = "free"
    if user.subscription_plan_id:
        plan = db.query(SubscriptionPlan).get(user.subscription_plan_id)
        if plan:
            plan_code = plan.code

    if user.is_admin or user.email in PRIVILEGED_UNLIMITED_EMAILS:
        return {
            "used": used,
            "max": UNLIMITED_ASSISTANTS,
            "unlimited": True,
            "can_create": True,
            "subscription_active": True,
            "plan_code": plan_code,
            "breakdown": breakdown,
        }

    subscription_status = await UserService.check_subscription_status(db, str(user.id))
    is_active = bool(subscription_status.get("active"))

    if user.email in SPECIAL_ASSISTANT_LIMITS:
        max_assistants = SPECIAL_ASSISTANT_LIMITS[user.email]
    else:
        max_assistants = subscription_status.get("max_assistants", 0)

    return {
        "used": used,
        "max": max_assistants,
        "unlimited": False,
        "can_create": is_active and used < max_assistants,
        "subscription_active": is_active,
        "plan_code": plan_code,
        "breakdown": breakdown,
    }
