# backend/functions/gmail.py
"""
Функции Gmail для голосового агента (через Composio).

Голосовой агент во время звонка может отправить письмо с почты владельца
(например, выслать клиенту КП, реквизиты, подтверждение) и при необходимости
прочитать последние письма. Подключение (OAuth) — в дашборде агента; здесь
только исполнение под уже подключённым аккаунтом.
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
class GmailSendEmailFunction(FunctionBase):
    """Отправить письмо с Gmail владельца."""

    SLUG = "GMAIL_SEND_EMAIL"

    @classmethod
    def get_name(cls) -> str:
        return "gmail_send_email"

    @classmethod
    def get_display_name(cls) -> str:
        return "Gmail: отправить письмо"

    @classmethod
    def get_description(cls) -> str:
        return (
            "Отправить email с почты владельца. Используй, когда клиент просит "
            "выслать информацию на почту (КП, реквизиты, ссылку, подтверждение встречи)."
        )

    @classmethod
    def get_parameters(cls) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "recipient_email": {"type": "string", "description": "Email получателя"},
                "subject": {"type": "string", "description": "Тема письма"},
                "body": {"type": "string", "description": "Текст письма"},
                "is_html": {"type": "boolean", "description": "True, если body в HTML (по умолчанию false)"},
                "cc": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Список email для копии (опционально)",
                },
            },
            "required": ["recipient_email", "body"],
        }

    @staticmethod
    async def execute(arguments: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
        user_id = _resolve_user_id(context)
        if not user_id:
            return {"success": False, "error": "Не удалось определить владельца коннектора"}

        args = dict(arguments or {})
        if not args.get("subject"):
            args["subject"] = "Сообщение"

        logger.info(f"[GMAIL] send_email user={user_id} to={args.get('recipient_email')!r}")
        result = await composio_service.execute(
            GmailSendEmailFunction.SLUG, args, user_id
        )
        return result


@register_function
class GmailFetchEmailsFunction(FunctionBase):
    """Прочитать последние письма из Gmail владельца."""

    SLUG = "GMAIL_FETCH_EMAILS"

    @classmethod
    def get_name(cls) -> str:
        return "gmail_fetch_emails"

    @classmethod
    def get_display_name(cls) -> str:
        return "Gmail: прочитать письма"

    @classmethod
    def get_description(cls) -> str:
        return (
            "Получить последние письма из почты владельца, опционально по поисковому "
            "запросу. Используй, когда нужно свериться с перепиской по клиенту."
        )

    @classmethod
    def get_parameters(cls) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Поисковый запрос Gmail (например from:client@mail.com)"},
                "max_results": {"type": "integer", "description": "Сколько писем вернуть (по умолчанию 5)"},
            },
            "required": [],
        }

    @staticmethod
    async def execute(arguments: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
        user_id = _resolve_user_id(context)
        if not user_id:
            return {"success": False, "error": "Не удалось определить владельца коннектора"}

        args = dict(arguments or {})
        if "max_results" not in args:
            args["max_results"] = 5

        logger.info(f"[GMAIL] fetch_emails user={user_id}")
        result = await composio_service.execute(
            GmailFetchEmailsFunction.SLUG, args, user_id
        )
        return result
