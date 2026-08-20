# backend/functions/google_calendar.py
"""
Функции Google Calendar для голосового агента (через Composio).

Голосовой агент во время звонка может создать событие в календаре владельца и
найти существующие события. Подключение (OAuth) делается в дашборде агента —
здесь только исполнение под уже подключённым аккаунтом.

composio_user_id = per-agent identity (f"agent_<id>") агента-владельца голосового
ассистента — то же подключение, что использует оркестратор. Slug'и Composio
фиксированы; аргументы передаются напрямую под именами, которые ждёт Composio.
"""
from typing import Dict, Any, Optional

from backend.core.logging import get_logger
from backend.functions.base import FunctionBase
from backend.functions.registry import register_function
from backend.services import composio_service

logger = get_logger(__name__)


def _resolve_user_id(context: Dict[str, Any]) -> Optional[str]:
    """
    Агентная identity Composio (вариант A): резолвим AgentConfig по голосовому
    ассистенту и возвращаем composio_user_id агента. Контекст исполнения уже
    содержит assistant_config и db_session на всех голосовых путях.
    """
    if not context:
        return None
    ac = context.get("assistant_config")
    db = context.get("db_session")
    if ac is None or db is None:
        return None
    return composio_service.composio_user_id_for_assistant(db, ac)


@register_function
class GoogleCalendarCreateEventFunction(FunctionBase):
    """Создать событие в Google Calendar владельца."""

    SLUG = "GOOGLECALENDAR_CREATE_EVENT"

    @classmethod
    def get_name(cls) -> str:
        return "google_calendar_create_event"

    @classmethod
    def get_display_name(cls) -> str:
        return "Google Календарь: создать событие"

    @classmethod
    def get_description(cls) -> str:
        return (
            "Создать событие/встречу в Google Календаре. Используй, когда клиент "
            "договорился о встрече, консультации или звонке на конкретную дату и время."
        )

    @classmethod
    def get_parameters(cls) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Название события (например 'Консультация с Иваном')"},
                "start_datetime": {
                    "type": "string",
                    "description": "Дата и время начала в формате ISO 8601, например 2026-06-25T15:00:00",
                },
                "event_duration_minutes": {
                    "type": "integer",
                    "description": "Длительность события в минутах (по умолчанию 30)",
                },
                "description": {"type": "string", "description": "Описание/детали события (опционально)"},
                "attendees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Список email участников (опционально)",
                },
                "timezone": {"type": "string", "description": "Таймзона IANA, например Europe/Moscow (опционально)"},
                "calendar_id": {"type": "string", "description": "ID календаря (по умолчанию 'primary')"},
            },
            "required": ["summary", "start_datetime"],
        }

    @staticmethod
    async def execute(arguments: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
        user_id = _resolve_user_id(context)
        if not user_id:
            return {"success": False, "error": "Не удалось определить владельца коннектора"}

        args = dict(arguments or {})
        args.setdefault("calendar_id", "primary")
        if "event_duration_minutes" not in args:
            args["event_duration_minutes"] = 30

        logger.info(f"[GOOGLE-CALENDAR] create_event user={user_id} summary={args.get('summary')!r}")
        result = await composio_service.execute(
            GoogleCalendarCreateEventFunction.SLUG, args, user_id
        )
        return result


@register_function
class GoogleCalendarFindEventsFunction(FunctionBase):
    """Найти события в Google Calendar владельца (проверка занятости/расписания)."""

    SLUG = "GOOGLECALENDAR_FIND_EVENT"

    @classmethod
    def get_name(cls) -> str:
        return "google_calendar_find_events"

    @classmethod
    def get_display_name(cls) -> str:
        return "Google Календарь: найти события"

    @classmethod
    def get_description(cls) -> str:
        return (
            "Найти события в Google Календаре за период или по запросу. Используй, "
            "чтобы проверить занятость владельца перед тем как предложить клиенту время."
        )

    @classmethod
    def get_parameters(cls) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Текстовый поиск по событиям (опционально)"},
                "timeMin": {"type": "string", "description": "Начало периода ISO 8601 (опционально)"},
                "timeMax": {"type": "string", "description": "Конец периода ISO 8601 (опционально)"},
                "max_results": {"type": "integer", "description": "Сколько событий вернуть (по умолчанию 10)"},
                "calendar_id": {"type": "string", "description": "ID календаря (по умолчанию 'primary')"},
            },
            "required": [],
        }

    @staticmethod
    async def execute(arguments: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
        user_id = _resolve_user_id(context)
        if not user_id:
            return {"success": False, "error": "Не удалось определить владельца коннектора"}

        args = dict(arguments or {})
        args.setdefault("calendar_id", "primary")
        if "max_results" not in args:
            args["max_results"] = 10

        logger.info(f"[GOOGLE-CALENDAR] find_events user={user_id}")
        result = await composio_service.execute(
            GoogleCalendarFindEventsFunction.SLUG, args, user_id
        )
        return result
