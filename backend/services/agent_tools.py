"""
Agent Tools — tool definitions and implementations for GPT-5 Responses API.
Two tool sets: AGENT_CHAT_TOOLS (user chat) and AGENT_POSTCALL_TOOLS (post-call analysis).
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import func, or_

from backend.core.logging import get_logger
from backend.models.agent_contact import AgentContact
from backend.models.agent_call import AgentCall
from backend.models.agent_config import AgentConfig
from backend.models.task import Task, TaskStatus
from backend.models.user import User
from backend.models.agent_connector import AgentConnector
from backend.services import composio_service
from backend.services import telegram_user_service
from backend.services import instagram_service
from backend.services.telegram_notification import TelegramNotificationService
from backend.core.timezone_utils import adjust_to_working_hours
from backend.core.pipeline_stages import AGENT_CONTACT_STAGE_KEYS, is_valid_stage

logger = get_logger(__name__)


# Тулза доступна и в чате, и в post-call анализе — определяем один раз.
MOVE_CONTACT_STAGE_TOOL = {
    "type": "function",
    "name": "move_contact_stage",
    "description": (
        "Перевести контакт на стадию воронки продаж. Доступные стадии: "
        "new (новый), active (в работе), success (успех — цель достигнута), "
        "rejected (явный отказ), do_not_call (просил больше не звонить). "
        "Вызывай только когда есть реальное основание сменить стадию. Если "
        "ничего по сути не изменилось (не дозвонились, клиент ещё думает) — "
        "НЕ вызывай, контакт останется в текущей стадии."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent_contact_id": {"type": "string", "description": "UUID контакта"},
            "stage": {
                "type": "string",
                "enum": AGENT_CONTACT_STAGE_KEYS,
                "description": "Ключ стадии воронки",
            },
            "reason": {"type": "string", "description": "Краткая причина перевода (опционально)"},
        },
        "required": ["agent_contact_id", "stage"],
    },
}


# Тулза доступна и в чате, и в post-call анализе — определяем один раз.
UPDATE_CONTACT_INFO_TOOL = {
    "type": "function",
    "name": "update_contact_info",
    "description": (
        "Обновить базовую информацию о контакте (имя, компанию, должность, заметки). "
        "Используй когда пользователь говорит 'запиши что...', 'обнови данные...', "
        "'у Иванова новая должность' и т.п. После звонка — когда узнал новый факт "
        "о клиенте (например должность), который стоит сохранить как заметку."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent_contact_id": {"type": "string", "description": "UUID контакта"},
            "name": {"type": "string"},
            "company": {"type": "string"},
            "position": {"type": "string"},
            "notes": {"type": "string", "description": "Свободный текст с информацией о клиенте"},
        },
        "required": ["agent_contact_id"],
    },
}


# Поиск по векторной базе знаний агента. Доступен и в чате, и в post-call.
SEARCH_KNOWLEDGE_BASE_TOOL = {
    "type": "function",
    "name": "search_knowledge_base",
    "description": (
        "Найти информацию в базе знаний компании (векторный поиск). Используй, "
        "когда нужен фактический ответ по продукту, услугам, ценам, условиям или "
        "другим деталям из материалов владельца. Не выдумывай факты — бери их из "
        "результатов поиска. Если база пуста или ничего не найдено — скажи прямо."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Поисковый запрос на русском языке"},
            "top_k": {"type": "integer", "description": "Сколько фрагментов вернуть (по умолчанию 3)"},
        },
        "required": ["query"],
    },
}


# Отправка SMS клиенту с номера агента (Voximplant). Доступна в чате и в post-call.
SEND_SMS_TOOL = {
    "type": "function",
    "name": "send_sms",
    "description": (
        "Отправить SMS клиенту с номера агента (Voximplant). Используй, когда "
        "владелец просит отправить контакту SMS, либо когда после звонка нужно "
        "продублировать клиенту важную информацию (адрес, ссылку, реквизиты, "
        "код, напоминание о встрече). Получателя укажи через agent_contact_id — "
        "номер возьмётся из карточки контакта; либо задай phone напрямую. "
        "Номер отправителя выбирается автоматически (номер агента). Текст до 500 символов."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Текст SMS (до 500 символов)"},
            "agent_contact_id": {
                "type": "string",
                "description": "UUID контакта-получателя (его телефон будет номером назначения). Либо укажи phone.",
            },
            "phone": {
                "type": "string",
                "description": "Номер получателя напрямую, если не задан agent_contact_id",
            },
        },
        "required": ["text"],
    },
}


# Отправка события на внешний вебхук (n8n/Make/Zapier/любой HTTP endpoint).
# Доступна и в чате, и в post-call. URL берётся сервером из AgentConfig.webhook_url —
# модель его НЕ передаёт (нельзя отправить на произвольный адрес).
SEND_WEBHOOK_TOOL = {
    "type": "function",
    "name": "send_webhook",
    "description": (
        "Отправить событие на внешний вебхук владельца (n8n, Make.com, Zapier "
        "или любой HTTP endpoint). URL настроен в конфигурации агента и "
        "подставляется автоматически — передавать его не нужно. Используй, когда "
        "по итогу разговора/сообщения нужно передать данные во внешнюю систему: "
        "оформить заявку, бронирование, лид, зафиксировать событие или результат "
        "звонка. Если вебхук не настроен — инструмент вернёт ошибку."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "event": {
                "type": "string",
                "description": "Код события: 'booking', 'request', 'lead', 'notification' и т.п.",
            },
            "payload": {
                "type": "object",
                "description": "Произвольные данные для отправки (имя, телефон, детали заявки и т.д.)",
            },
        },
        "required": ["event"],
    },
}


# Уведомление владельцу в Telegram-бота агента. Доступно и в чате/входящих
# сообщениях, и в post-call — чтобы оркестратор мог сигналить о важных событиях
# (горячий лид, жалоба, вопрос без ответа) сразу, а не только после звонка.
SEND_TELEGRAM_NOTIFICATION_TOOL = {
    "type": "function",
    "name": "send_telegram_notification",
    "description": (
        "Отправить уведомление владельцу бизнеса в Telegram (бот уведомлений агента). "
        "Используй при важных событиях: клиент готов купить / просит счёт, жалуется, "
        "просит живого человека, задал вопрос без ответа в материалах. "
        "Укажи в тексте: контакт (имя, телефон), суть события, что уже сделано. "
        "Не отправляй повторно одно и то же событие."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "Текст уведомления"},
        },
        "required": ["message"],
    },
}


# ============================================================================
# HELPERS
# ============================================================================

def _parse_iso_utc(value) -> Optional[datetime]:
    """
    Распарсить ISO 8601 строку времени в aware-datetime (UTC).
    Принимает суффикс 'Z' и смещения; naive-время трактуется как UTC.
    Возвращает None, если строку не удалось разобрать.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def assistant_task_kwargs(agent_config) -> dict:
    """
    Возвращает kwargs для Task с правильным FK голосового ассистента
    в зависимости от assistant_type агента (gemini / openai / cartesia / yandex /
    cascade / fish).
    Для старых агентов без assistant_type — fallback на gemini_assistant_id.
    """
    if not agent_config:
        return {}
    a_type = getattr(agent_config, "assistant_type", None)
    vid = agent_config.get_voice_assistant_id() if a_type else agent_config.gemini_assistant_id
    if a_type == "openai":
        return {"assistant_id": vid}
    if a_type == "cartesia":
        return {"cartesia_assistant_id": vid}
    if a_type == "yandex":
        return {"yandex_assistant_id": vid}
    if a_type == "cascade":
        return {"cascade_assistant_id": vid}
    if a_type == "fish":
        return {"fish_assistant_id": vid}
    # gemini (and legacy default)
    return {"gemini_assistant_id": vid}


def to_chat_completions_tools(tools: list) -> list:
    """
    Конвертирует tools из формата OpenAI Responses API (flat:
    {"type":"function","name":...,"parameters":...}) в формат Chat Completions /
    OpenRouter (nested: {"type":"function","function":{...}}).
    """
    converted = []
    for t in tools:
        if t.get("type") == "function" and "name" in t:
            converted.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("parameters", {"type": "object", "properties": {}}),
                },
            })
        else:
            converted.append(t)
    return converted


# ============================================================================
# CONNECTOR TOOLS (Composio) — динамическая надстройка над базовыми tools
# ============================================================================

def _connected_toolkits(agent_config, db: Session) -> list:
    """
    Ключи toolkit'ов (google_calendar/gmail), подключённых к агенту (status='connected').
    Пустой список, если Composio не настроен, агента нет или нет подключений.
    """
    if agent_config is None or not composio_service.is_configured():
        return []
    try:
        rows = db.query(AgentConnector).filter(
            AgentConnector.agent_config_id == agent_config.id,
            AgentConnector.status == "connected",
        ).all()
    except Exception as e:
        logger.warning(f"[AGENT-TOOLS] connector lookup failed: {e}")
        return []
    return [r.toolkit for r in rows if r.toolkit in composio_service.TOOLKIT_SLUGS]


async def _augment_with_connectors(base_tools: list, agent_config, db: Session) -> list:
    """Дописать к base_tools определения подключённых коннекторов (если есть)."""
    toolkits = _connected_toolkits(agent_config, db)
    if not toolkits:
        return base_tools
    tool_slugs = []
    for tk in toolkits:
        tool_slugs.extend(composio_service.chat_tool_slugs(tk))
    if not tool_slugs:
        return base_tools
    composio_user_id = composio_service.composio_user_id_for_agent(agent_config.id)
    connector_tools = await composio_service.get_tools(composio_user_id, tool_slugs)
    if not connector_tools:
        return base_tools
    logger.info(f"[AGENT-TOOLS] +{len(connector_tools)} connector tools for agent {agent_config.id}")
    return base_tools + connector_tools


async def build_chat_tools(agent_config, db: Session) -> list:
    """
    Tools для чата/Telegram оркестратора (Chat Completions формат): базовый
    AGENT_CHAT_TOOLS + коннекторы Composio + личный Telegram + Instagram
    (последние два — только если подключены).
    """
    tools = await _augment_with_connectors(
        to_chat_completions_tools(AGENT_CHAT_TOOLS), agent_config, db
    )
    tools = _augment_with_telegram_account(tools, agent_config, db)
    return _augment_with_instagram(tools, agent_config, db)


async def build_postcall_tools(agent_config, db: Session) -> list:
    """Tools для PostCall-анализа: AGENT_POSTCALL_TOOLS + коннекторы + личный Telegram + Instagram."""
    tools = await _augment_with_connectors(
        to_chat_completions_tools(AGENT_POSTCALL_TOOLS), agent_config, db
    )
    tools = _augment_with_telegram_account(tools, agent_config, db)
    return _augment_with_instagram(tools, agent_config, db)


async def fn_execute_connector(tool_name: str, args: dict, agent_config_id: str, db: Session) -> dict:
    """
    Исполнить инструмент коннектора (Composio) для оркестратора.
    Identity Composio — по агенту (вариант A): то же подключение, что у голосового
    агента этого же агента, изолированное от других агентов владельца.
    """
    composio_user_id = composio_service.composio_user_id_for_agent(agent_config_id)
    return await composio_service.execute(tool_name, args, composio_user_id)


# ============================================================================
# TELEGRAM USER TOOLS — личный Telegram-аккаунт агента (MTProto, Telethon).
# Домешиваются в чат и PostCall, ТОЛЬКО когда аккаунт подключён. Голосовому
# ассистенту эти функции намеренно НЕ отдаются.
# ============================================================================

TELEGRAM_SEND_MESSAGE_TOOL = {
    "type": "function",
    "name": "telegram_send_message",
    "description": (
        "Отправить клиенту сообщение в Telegram С ЛИЧНОГО аккаунта владельца. "
        "Указывай agent_contact_id (предпочтительно) и/или username. Получатель "
        "резолвится: по уже существующему диалогу → по @username → по номеру "
        "телефона контакта (только если первых двух нет; лимитировано — Telegram "
        "банит за спам незнакомым). Пиши как живой человек, без markdown."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent_contact_id": {"type": "string", "description": "UUID контакта агента"},
            "username": {"type": "string", "description": "Telegram @username получателя (если известен)"},
            "text": {"type": "string", "description": "Текст сообщения"},
        },
        "required": ["text"],
    },
}

TELEGRAM_GET_THREAD_TOOL = {
    "type": "function",
    "name": "telegram_get_thread",
    "description": (
        "Получить последние сообщения Telegram-переписки с контактом "
        "(личный аккаунт владельца) — для контекста перед ответом."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent_contact_id": {"type": "string", "description": "UUID контакта агента"},
            "limit": {"type": "integer", "description": "Сколько сообщений (по умолчанию 20)"},
        },
        "required": ["agent_contact_id"],
    },
}

SCHEDULE_TELEGRAM_MESSAGE_TOOL = {
    "type": "function",
    "name": "schedule_telegram_message",
    "description": (
        "Запланировать ОТЛОЖЕННОЕ сообщение клиенту в Telegram с личного аккаунта "
        "владельца (для немедленной отправки используй telegram_send_message). "
        "Передавай ИНСТРУКЦИЮ — что и зачем написать (цель, ключевые тезисы), а НЕ "
        "готовый текст: текст составится в момент отправки с учётом свежей "
        "переписки и памяти контакта. Время задай ОДНИМ из способов: delay_minutes "
        "— для относительного («через N минут/часов»), scheduled_at — для "
        "абсолютного («завтра в 14:00»). Рабочие часы не применяются — сообщение "
        "уйдёт ровно в назначенное время."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent_contact_id": {"type": "string", "description": "UUID контакта агента"},
            "scheduled_at": {
                "type": "string",
                "description": (
                    "Абсолютные дата и время отправки ISO 8601 UTC (например 2026-07-09T12:00:00Z). "
                    "Не используй для «через N минут» — для этого есть delay_minutes"
                ),
            },
            "delay_minutes": {
                "type": "integer",
                "description": (
                    "Через сколько минут отправить. Сервер сам вычислит точное время от "
                    "текущего момента — ВСЕГДА используй этот параметр, когда просят "
                    "написать «через N минут/часов», не вычисляй scheduled_at сам."
                ),
            },
            "title": {"type": "string", "description": "Короткое название задачи (видно владельцу в календаре)"},
            "instruction": {
                "type": "string",
                "description": (
                    "Инструкция для составления сообщения: цель, что сказать/спросить, "
                    "о чём договорились. НЕ готовый текст."
                ),
            },
        },
        "required": ["agent_contact_id", "instruction"],
    },
}

TELEGRAM_USER_TOOLS = [
    TELEGRAM_SEND_MESSAGE_TOOL,
    TELEGRAM_GET_THREAD_TOOL,
    SCHEDULE_TELEGRAM_MESSAGE_TOOL,
]


def _augment_with_telegram_account(base_tools: list, agent_config, db: Session) -> list:
    """Дописать тулзы личного Telegram, если аккаунт агента подключён."""
    if agent_config is None:
        return base_tools
    try:
        if not telegram_user_service.account_connected(db, agent_config.id):
            return base_tools
    except Exception as e:
        logger.warning(f"[AGENT-TOOLS] telegram account lookup failed: {e}")
        return base_tools
    return base_tools + to_chat_completions_tools(TELEGRAM_USER_TOOLS)


# ============================================================================
# INSTAGRAM TOOLS — DM бизнес-аккаунта Instagram (коннектор Composio).
# Домешиваются в чат и PostCall, ТОЛЬКО когда коннектор подключён. Голосовому
# ассистенту эти функции намеренно НЕ отдаются. Отправка идёт через нашу
# обёртку (а не сырой Composio-слаг), чтобы исходящие попадали в историю
# переписки (agent_instagram_messages) и в хронологию оркестратора.
# ============================================================================

INSTAGRAM_SEND_MESSAGE_TOOL = {
    "type": "function",
    "name": "instagram_send_message",
    "description": (
        "Отправить клиенту сообщение в Instagram Direct с бизнес-аккаунта "
        "владельца. Работает ТОЛЬКО как ответ в уже существующем диалоге и "
        "только в течение 24 часов после последнего сообщения клиента — писать "
        "первым Instagram не позволяет. Указывай agent_contact_id. Пиши как "
        "живой человек, коротко, без markdown."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent_contact_id": {"type": "string", "description": "UUID контакта агента"},
            "text": {"type": "string", "description": "Текст сообщения"},
        },
        "required": ["agent_contact_id", "text"],
    },
}

INSTAGRAM_GET_THREAD_TOOL = {
    "type": "function",
    "name": "instagram_get_thread",
    "description": (
        "Получить последние сообщения Instagram-переписки с контактом "
        "(DM бизнес-аккаунта владельца) — для контекста перед ответом."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent_contact_id": {"type": "string", "description": "UUID контакта агента"},
            "limit": {"type": "integer", "description": "Сколько сообщений (по умолчанию 20)"},
        },
        "required": ["agent_contact_id"],
    },
}

INSTAGRAM_TOOLS = [
    INSTAGRAM_SEND_MESSAGE_TOOL,
    INSTAGRAM_GET_THREAD_TOOL,
]


def _augment_with_instagram(base_tools: list, agent_config, db: Session) -> list:
    """Дописать тулзы Instagram, если коннектор агента подключён."""
    if agent_config is None:
        return base_tools
    try:
        if not instagram_service.connected(db, agent_config.id):
            return base_tools
    except Exception as e:
        logger.warning(f"[AGENT-TOOLS] instagram connector lookup failed: {e}")
        return base_tools
    return base_tools + to_chat_completions_tools(INSTAGRAM_TOOLS)


async def fn_instagram_send_message(args: dict, user_id: str, agent_config, db: Session) -> dict:
    """
    Отправка DM с бизнес-аккаунта Instagram владельца (через Composio).
    Только ответ в существующем треде (IGSID резолвится из
    agent_instagram_conversations); почасовой анти-спам лимит.
    """
    from backend.models.agent_instagram import AgentInstagramMessage

    if agent_config is None:
        return {"ok": False, "error": "Коннектор Instagram не подключён"}
    connector = instagram_service.connector_for_agent(db, agent_config.id)
    if connector is None:
        return {"ok": False, "error": "Коннектор Instagram не подключён"}

    text = (args.get("text") or "").strip()
    if not text:
        return {"ok": False, "error": "Пустой текст сообщения"}

    contact = db.query(AgentContact).filter(
        AgentContact.id == args.get("agent_contact_id"),
        AgentContact.user_id == user_id,
        AgentContact.agent_config_id == agent_config.id,
    ).first()
    if not contact:
        return {"ok": False, "error": "Контакт не найден"}

    conversation = instagram_service.conversation_for_contact(db, agent_config.id, contact.id)
    if conversation is None or not conversation.igsid:
        return {
            "ok": False,
            "error": ("У контакта нет Instagram-диалога — написать первым в "
                      "Instagram нельзя, клиент должен написать сам."),
        }

    hour_ago = datetime.utcnow() - timedelta(hours=1)
    sent_last_hour = db.query(AgentInstagramMessage).filter(
        AgentInstagramMessage.agent_config_id == agent_config.id,
        AgentInstagramMessage.direction == "outbound",
        AgentInstagramMessage.created_at >= hour_ago,
    ).count()
    if sent_last_hour >= instagram_service.IG_SEND_HOURLY_LIMIT:
        return {"ok": False, "error": "Достигнут почасовой лимит исходящих Instagram-сообщений"}

    composio_user_id = connector.composio_user_id or composio_service.composio_user_id_for_agent(agent_config.id)
    result = await instagram_service.send_text(composio_user_id, conversation.igsid, text)
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error")}

    instagram_service.store_message(
        db, agent_config.id, "outbound", text,
        agent_contact_id=contact.id,
        ig_conversation_id=conversation.ig_conversation_id,
        ig_message_id=result.get("message_id"),
        sent_at=datetime.utcnow(),
    )
    db.commit()

    to_label = ("@" + conversation.ig_username) if conversation.ig_username else conversation.igsid
    logger.info(f"[AGENT-TOOLS] instagram_send_message → {to_label}")
    return {"ok": True, "to": to_label}


async def fn_instagram_get_thread(args: dict, user_id: str, agent_config_id: str, db: Session) -> dict:
    """Последние сообщения Instagram-переписки с контактом."""
    contact = db.query(AgentContact).filter(
        AgentContact.id == args.get("agent_contact_id"),
        AgentContact.user_id == user_id,
        AgentContact.agent_config_id == agent_config_id,
    ).first()
    if not contact:
        return {"ok": False, "error": "Контакт не найден"}
    limit = min(int(args.get("limit") or 20), 50)
    rows = instagram_service.get_thread(db, contact.id, limit=limit)
    return {"ok": True, "messages": [m.to_dict() for m in rows]}


async def fn_telegram_send_message(args: dict, user_id: str, agent_config, db: Session) -> dict:
    """
    Отправка сообщения с личного Telegram владельца. Анти-бан меры:
    - почасовой лимит исходящих (TG_SEND_HOURLY_LIMIT);
    - резолв по номеру телефона (ImportContacts) — только когда нет диалога и
      username, и не чаще TG_PHONE_RESOLVE_HOURLY_LIMIT новых диалогов в час.
    """
    from backend.models.agent_telegram_account import (
        AgentTelegramDialog, AgentTelegramMessage,
    )

    if not telegram_user_service.is_configured():
        return {"ok": False, "error": telegram_user_service.error_human("not_configured")}
    if agent_config is None:
        return {"ok": False, "error": telegram_user_service.error_human("not_connected")}

    account = telegram_user_service.get_account_for_agent(db, agent_config.id)
    if account is None:
        return {"ok": False, "error": telegram_user_service.error_human("not_connected")}

    text = (args.get("text") or "").strip()
    if not text:
        return {"ok": False, "error": telegram_user_service.error_human("empty_text")}

    hour_ago = datetime.utcnow() - timedelta(hours=1)
    sent_last_hour = db.query(AgentTelegramMessage).filter(
        AgentTelegramMessage.account_id == account.id,
        AgentTelegramMessage.direction == "outbound",
        AgentTelegramMessage.created_at >= hour_ago,
    ).count()
    if sent_last_hour >= telegram_user_service.TG_SEND_HOURLY_LIMIT:
        return {"ok": False, "error": telegram_user_service.error_human("send_limit_reached")}

    # Резолв контакта и его диалога
    contact = None
    dialog = None
    if args.get("agent_contact_id"):
        contact = db.query(AgentContact).filter(
            AgentContact.id == args["agent_contact_id"],
            AgentContact.user_id == user_id,
            AgentContact.agent_config_id == agent_config.id,
        ).first()
        if not contact:
            return {"ok": False, "error": "Контакт не найден"}
        dialog = db.query(AgentTelegramDialog).filter(
            AgentTelegramDialog.account_id == account.id,
            AgentTelegramDialog.agent_contact_id == contact.id,
        ).first()

    peer_id = dialog.tg_peer_id if dialog else None
    username = (args.get("username") or "").strip() or (dialog.tg_username if dialog else None)
    phone = None
    if contact and contact.phone and not contact.phone.startswith("tg:"):
        phone = contact.phone

    # Телефонный резолв — только как последний фолбэк и в пределах лимита
    allow_phone = phone is not None and peer_id is None and not username
    if allow_phone:
        phone_resolves = db.query(AgentTelegramDialog).filter(
            AgentTelegramDialog.account_id == account.id,
            AgentTelegramDialog.created_via == "send_phone",
            AgentTelegramDialog.created_at >= hour_ago,
        ).count()
        if phone_resolves >= telegram_user_service.TG_PHONE_RESOLVE_HOURLY_LIMIT:
            return {"ok": False, "error": telegram_user_service.error_human("phone_resolve_limit_reached")}

    session_str = telegram_user_service.decrypt_session(account.session_encrypted)
    result = await telegram_user_service.send_message(
        session_str,
        text,
        peer_id=peer_id,
        username=username,
        phone=phone if allow_phone else None,
        contact_name=(contact.name if contact else None),
    )
    if not result.get("ok"):
        err = result.get("error") or "telegram_error"
        if err == "session_revoked":
            account.status = "error"
            account.last_error = "session_revoked"
            db.commit()
        return {"ok": False, "error": telegram_user_service.error_human(err)}

    # Upsert диалога (peer теперь известен) и сохранение сообщения в тред
    res_peer = result.get("peer_id")
    if res_peer:
        if dialog is None:
            dialog = db.query(AgentTelegramDialog).filter(
                AgentTelegramDialog.account_id == account.id,
                AgentTelegramDialog.tg_peer_id == res_peer,
            ).first()
        if dialog is None:
            dialog = AgentTelegramDialog(
                account_id=account.id,
                agent_contact_id=(contact.id if contact else None),
                tg_peer_id=res_peer,
                created_via=("send_phone" if result.get("resolved_via") == "phone" else "send_username"),
                last_processed_msg_id=result.get("tg_message_id") or 0,
            )
            db.add(dialog)
        if contact and dialog.agent_contact_id is None:
            dialog.agent_contact_id = contact.id
        if result.get("username"):
            dialog.tg_username = result["username"]
        if result.get("name"):
            dialog.tg_name = result["name"]

    telegram_user_service.store_message(
        db, account, "outbound", text,
        agent_contact_id=(contact.id if contact else (dialog.agent_contact_id if dialog else None)),
        tg_peer_id=res_peer,
        tg_message_id=result.get("tg_message_id"),
    )
    db.commit()

    to_label = result.get("name") or (f"@{result['username']}" if result.get("username") else str(res_peer))
    logger.info(f"[AGENT-TOOLS] telegram_send_message → {to_label} via {result.get('resolved_via')}")
    return {"ok": True, "to": to_label, "resolved_via": result.get("resolved_via")}


async def fn_telegram_get_thread(args: dict, user_id: str, agent_config_id: str, db: Session) -> dict:
    """Последние сообщения личной Telegram-переписки с контактом."""
    contact = db.query(AgentContact).filter(
        AgentContact.id == args.get("agent_contact_id"),
        AgentContact.user_id == user_id,
        AgentContact.agent_config_id == agent_config_id,
    ).first()
    if not contact:
        return {"ok": False, "error": "Контакт не найден"}
    limit = min(int(args.get("limit") or 20), 50)
    rows = telegram_user_service.get_thread(db, contact.id, limit=limit)
    return {"ok": True, "messages": [m.to_dict() for m in rows]}


async def fn_schedule_telegram_message(args: dict, user_id: str, agent_config, db: Session) -> dict:
    """
    Запланировать отложенное Telegram-сообщение: создаёт Task(channel="telegram").
    Текст НЕ фиксируется — в description хранится инструкция, а сообщение
    составит оркестратор в момент срабатывания задачи (см. execute_agent_task →
    PostCallOrchestrator.run_for_scheduled_telegram).
    """
    if not telegram_user_service.is_configured():
        return {"ok": False, "error": telegram_user_service.error_human("not_configured")}
    if agent_config is None or not telegram_user_service.account_connected(db, agent_config.id):
        return {"ok": False, "error": telegram_user_service.error_human("not_connected")}

    instruction = (args.get("instruction") or "").strip()
    if not instruction:
        return {"ok": False, "error": "Пустая инструкция — опиши, что нужно написать клиенту"}

    task_args = {
        "agent_contact_id": args.get("agent_contact_id"),
        "scheduled_at": args.get("scheduled_at"),
        "delay_minutes": args.get("delay_minutes"),
        "title": args.get("title"),
        "notes": instruction,
    }
    return await fn_create_agent_task(
        task_args, user_id, str(agent_config.id), db, channel="telegram"
    )


# ============================================================================
# TOOL DEFINITIONS FOR GPT-5 RESPONSES API
# ============================================================================

AGENT_CHAT_TOOLS = [
    {
        "type": "function",
        "name": "create_agent_contact",
        "description": "Создать новый контакт в базе агента для обзвона.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Имя контакта"},
                "phone": {"type": "string", "description": "Номер телефона (обязательно)"},
                "company": {"type": "string", "description": "Компания"},
                "position": {"type": "string", "description": "Должность"},
                "notes": {"type": "string", "description": "Заметки о контакте"},
            },
            "required": ["phone"],
        },
    },
    {
        "type": "function",
        "name": "create_agent_task",
        "description": (
            "Создать задачу на звонок контакту агента в указанное время. "
            "Время задай ОДНИМ из способов: delay_minutes — для относительного "
            "(«через N минут/часов»), scheduled_at — для абсолютного («завтра в 14:00»)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_contact_id": {"type": "string", "description": "UUID контакта агента"},
                "scheduled_at": {
                    "type": "string",
                    "description": (
                        "Абсолютные дата и время звонка ISO 8601 UTC (например 2026-07-09T12:00:00Z). "
                        "Не используй для «через N минут» — для этого есть delay_minutes"
                    ),
                },
                "delay_minutes": {
                    "type": "integer",
                    "description": (
                        "Через сколько минут позвонить. Сервер сам вычислит точное время от "
                        "текущего момента — ВСЕГДА используй этот параметр, когда просят "
                        "перезвонить «через N минут/часов», не вычисляй scheduled_at сам."
                    ),
                },
                "title": {"type": "string", "description": "Название задачи"},
                "notes": {"type": "string", "description": "Описание / заметки"},
            },
            "required": ["agent_contact_id", "title"],
        },
    },
    {
        "type": "function",
        "name": "get_agent_contacts",
        "description": "Получить список контактов агента.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "type": "function",
        "name": "get_contact_call_history",
        "description": "Получить историю звонков конкретного контакта агента.",
        "parameters": {
            "type": "object",
            "properties": {
                "agent_contact_id": {"type": "string", "description": "UUID контакта агента"},
            },
            "required": ["agent_contact_id"],
        },
    },
    {
        "type": "function",
        "name": "get_contact_timeline",
        "description": (
            "Получить ЕДИНУЮ хронологию всего общения с контактом по ВСЕМ каналам "
            "(звонки + SMS + Telegram) в одном списке с метками времени, старые → "
            "новые. Используй, когда владелец просит показать всю переписку/историю "
            "общения с человеком, восстановить контекст или понять, о чём "
            "договаривались — вместо раздельных get_contact_call_history / "
            "telegram_get_thread."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_contact_id": {"type": "string", "description": "UUID контакта агента"},
                "days": {"type": "integer", "description": "Окно в днях (по умолчанию 90; 0 — без ограничения)"},
                "limit": {"type": "integer", "description": "Максимум событий (по умолчанию 100)"},
            },
            "required": ["agent_contact_id"],
        },
    },
    {
        "type": "function",
        "name": "get_agent_tasks",
        "description": "Получить список задач на звонки. Использовать когда пользователь спрашивает о запланированных звонках, расписании, следующих задачах. Также вызывать ПЕРЕД созданием новой задачи чтобы проверить дубли. Для количества задач по статусам опирайся на поля status_counts и scheduled_count из ответа (точные счётчики по всей выборке), а не пересчитывай массив tasks — он ограничен лимитом.",
        "parameters": {
            "type": "object",
            "properties": {
                "agent_contact_id": {
                    "type": "string",
                    "description": "UUID контакта агента — фильтр по конкретному контакту (опционально)",
                },
                "status_filter": {
                    "type": "string",
                    "description": "Фильтр по статусу: scheduled, completed, failed, cancelled (опционально)",
                },
            },
        },
    },
    {
        "type": "function",
        "name": "get_agent_stats",
        "description": "Получить сводную статистику агента: контакты, звонки, задачи.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "type": "function",
        "name": "delete_agent_task",
        "description": "Удалить задачу на звонок по её ID. Используй когда пользователь просит удалить, убрать или отменить запланированный звонок/задачу. Сначала вызови get_agent_tasks, чтобы найти нужный task_id. Удаление необратимо — задача исчезает из календаря и не будет выполнена планировщиком.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "UUID задачи, которую нужно удалить",
                },
            },
            "required": ["task_id"],
        },
    },
    {
        "type": "function",
        "name": "search_contacts",
        "description": (
            "Найти контакты по подстроке имени/телефона/компании и/или по стадии воронки. "
            "Используй вместо get_agent_contacts, когда пользователь ищет конкретных людей "
            "('найди Иванова', 'контакты из компании X', 'покажи отказников'). "
            "Все аргументы опциональны; без аргументов вернёт последние контакты."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Подстрока для поиска по имени, телефону или компании"},
                "stage": {"type": "string", "enum": AGENT_CONTACT_STAGE_KEYS, "description": "Фильтр по стадии воронки (опционально)"},
                "company": {"type": "string", "description": "Фильтр по компании (опционально)"},
                "limit": {"type": "integer", "description": "Максимум результатов (по умолчанию 30)"},
            },
        },
    },
    {
        "type": "function",
        "name": "get_contact_details",
        "description": (
            "Получить полную карточку одного контакта: базовые поля, стадию воронки, заметки, "
            "память агента (summary, ключевые факты, лучшее время, история тона), число попыток, "
            "дату последнего звонка и краткую сводку по последним звонкам. Используй для запросов "
            "вида 'расскажи всё про Иванова', 'что мы знаем о контакте'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_contact_id": {"type": "string", "description": "UUID контакта агента"},
            },
            "required": ["agent_contact_id"],
        },
    },
    {
        "type": "function",
        "name": "get_contacts_by_stage",
        "description": (
            "Получить разбивку контактов по стадиям воронки: счётчик по каждой стадии "
            "(new/active/success/rejected/do_not_call) и небольшой пример контактов в каждой. "
            "Используй для вопросов 'как распределены контакты', 'сколько в работе/успехов/отказов', "
            "'покажи воронку'."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "bulk_create_contacts",
        "description": (
            "Создать сразу несколько контактов одним вызовом. Используй когда пользователь "
            "присылает список людей для обзвона. У каждого контакта обязателен phone. "
            "Дубли по номеру телефона (уже есть в базе) пропускаются."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "contacts": {
                    "type": "array",
                    "description": "Список контактов для создания",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "phone": {"type": "string", "description": "Номер телефона (обязательно)"},
                            "company": {"type": "string"},
                            "position": {"type": "string"},
                            "notes": {"type": "string"},
                        },
                        "required": ["phone"],
                    },
                },
            },
            "required": ["contacts"],
        },
    },
    {
        "type": "function",
        "name": "delete_agent_contact",
        "description": (
            "Удалить контакт из базы агента по его UUID. Вместе с контактом удаляется его история "
            "звонков, а запланированные задачи отвязываются. Удаление необратимо — используй только "
            "по явной просьбе пользователя ('удали контакт', 'убери из базы'). Если нужно просто "
            "перестать звонить — лучше move_contact_stage в do_not_call."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_contact_id": {"type": "string", "description": "UUID контакта агента"},
            },
            "required": ["agent_contact_id"],
        },
    },
    {
        "type": "function",
        "name": "append_contact_note",
        "description": (
            "Дописать заметку к контакту, НЕ стирая существующие заметки (в отличие от update_contact_info, "
            "который перезаписывает поле notes целиком). Каждая заметка добавляется новой строкой с датой. "
            "Используй когда узнал новый факт о клиенте и хочешь его сохранить, не теряя прежние записи."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_contact_id": {"type": "string", "description": "UUID контакта агента"},
                "note": {"type": "string", "description": "Текст заметки для добавления"},
            },
            "required": ["agent_contact_id", "note"],
        },
    },
    {
        "type": "function",
        "name": "update_agent_task",
        "description": (
            "Изменить существующую запланированную задачу на звонок: перенести время и/или поменять "
            "название/описание. Используй для 'перенеси звонок Иванову на завтра 15:00', 'переименуй задачу'. "
            "Сначала найди task_id через get_agent_tasks или get_upcoming_schedule. Время передавай в UTC. "
            "Менять можно только задачи в статусе scheduled."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "UUID задачи"},
                "scheduled_at": {"type": "string", "description": "Новое время звонка ISO 8601 (UTC), опционально"},
                "title": {"type": "string", "description": "Новое название задачи (опционально)"},
                "notes": {"type": "string", "description": "Новое описание (опционально)"},
            },
            "required": ["task_id"],
        },
    },
    {
        "type": "function",
        "name": "get_upcoming_schedule",
        "description": (
            "Получить календарь ближайших запланированных звонков по ВСЕМ контактам (а не по одному). "
            "Используй для 'что у меня на сегодня/завтра', 'какие звонки впереди', 'покажи расписание'. "
            "Возвращает задачи в статусе scheduled, отсортированные по времени."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "За сколько ближайших дней показывать (по умолчанию 7)"},
                "limit": {"type": "integer", "description": "Максимум задач (по умолчанию 50)"},
            },
        },
    },
    {
        "type": "function",
        "name": "bulk_schedule_calls",
        "description": (
            "Запланировать обзвон для группы контактов разом, расставив звонки с интервалом, начиная "
            "с указанного времени. Группу задаёшь либо списком agent_contact_ids, либо стадией воронки stage "
            "(например все 'new'). Используй для 'обзвони всех новых завтра с 10:00', 'поставь звонки этим контактам'. "
            "Время начала передавай в UTC. Звонки автоматически сдвигаются в рабочие часы агента."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_contact_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Список UUID контактов (либо это, либо stage)",
                },
                "stage": {"type": "string", "enum": AGENT_CONTACT_STAGE_KEYS, "description": "Запланировать всем контактам этой стадии (либо это, либо agent_contact_ids)"},
                "start_at": {"type": "string", "description": "Время первого звонка ISO 8601 (UTC)"},
                "interval_minutes": {"type": "integer", "description": "Интервал между звонками в минутах (по умолчанию 15)"},
                "title": {"type": "string", "description": "Название задач (по умолчанию 'Звонок агента')"},
            },
            "required": ["start_at"],
        },
    },
    {
        "type": "function",
        "name": "trigger_immediate_call",
        "description": (
            "Позвонить контакту прямо сейчас — создаёт задачу на ближайшее выполнение (планировщик подхватит "
            "её в течение ~30 секунд). В отличие от create_agent_task, НЕ сдвигает время в рабочие часы — "
            "звонок уйдёт немедленно. Используй только по явной просьбе 'позвони ему сейчас', 'набери немедленно'. "
            "Перед звонком убедись, что агент активен."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_contact_id": {"type": "string", "description": "UUID контакта агента"},
                "title": {"type": "string", "description": "Название задачи (опционально)"},
            },
            "required": ["agent_contact_id"],
        },
    },
    {
        "type": "function",
        "name": "snooze_contact",
        "description": (
            "Приостановить звонки контакту до указанной даты: отменяет все его запланированные задачи и "
            "запрещает планировать новые звонки раньше этой даты (последующие create_agent_task автоматически "
            "сдвинутся на дату окончания паузы). Используй для 'не звони Иванову до понедельника', "
            "'поставь на паузу до 15 числа'. Дату окончания паузы передавай в UTC."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_contact_id": {"type": "string", "description": "UUID контакта агента"},
                "until": {"type": "string", "description": "Дата окончания паузы ISO 8601 (UTC)"},
            },
            "required": ["agent_contact_id", "until"],
        },
    },
    {
        "type": "function",
        "name": "get_call_transcript",
        "description": (
            "Получить ПОЛНЫЙ транскрипт конкретного звонка по его UUID (get_contact_call_history отдаёт только "
            "первые 500 символов). Используй когда пользователь просит 'покажи весь разговор', 'что именно сказал клиент'. "
            "Сначала найди agent_call_id через get_contact_call_history."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_call_id": {"type": "string", "description": "UUID звонка (AgentCall)"},
            },
            "required": ["agent_call_id"],
        },
    },
    {
        "type": "function",
        "name": "get_period_report",
        "description": (
            "Сводный отчёт по звонкам за период: всего звонков, дозвонов, успехов, перезвонов, недозвонов, "
            "суммарная и средняя длительность, конверсия. Используй для 'как прошла неделя', 'отчёт за месяц', "
            "'статистика с 1 по 7 число'. Даты передавай в UTC; если не указаны — берётся последние 7 дней."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "Начало периода ISO 8601 (UTC), опционально"},
                "date_to": {"type": "string", "description": "Конец периода ISO 8601 (UTC), опционально"},
            },
        },
    },
    {
        "type": "function",
        "name": "get_failed_calls",
        "description": (
            "Получить список недозвонов и неудачных звонков как очередь на перезвон — по одному (последнему) "
            "звонку на контакт, с данными контакта. Используй для 'кому не дозвонились', 'покажи недозвоны', "
            "'кого надо перезвонить'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Максимум контактов (по умолчанию 30)"},
            },
        },
    },
    UPDATE_CONTACT_INFO_TOOL,
    MOVE_CONTACT_STAGE_TOOL,
    SEARCH_KNOWLEDGE_BASE_TOOL,
    SEND_SMS_TOOL,
    SEND_WEBHOOK_TOOL,
    SEND_TELEGRAM_NOTIFICATION_TOOL,
]


AGENT_POSTCALL_TOOLS = [
    {
        "type": "function",
        "name": "update_contact_memory",
        "description": "Обновить память агента о контакте после звонка.",
        "parameters": {
            "type": "object",
            "properties": {
                "agent_contact_id": {"type": "string", "description": "UUID контакта агента"},
                "summary": {"type": "string", "description": "Краткий итог звонка"},
                "key_facts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Новые факты о контакте",
                },
                "best_time": {"type": "string", "description": "Лучшее время для звонка или null"},
                "tone": {"type": "string", "description": "Тон разговора (дружелюбный/деловой/холодный)"},
            },
            "required": ["agent_contact_id", "summary"],
        },
    },
    {
        "type": "function",
        "name": "create_agent_task",
        "description": (
            "Создать задачу на перезвон. После исходящего звонка следующее касание "
            "планируется ВСЕГДА, кроме случая когда цель звонка уже достигнута: "
            "перезвон — этим tool, отложенное сообщение — schedule_telegram_message "
            "(если доступен). "
            "Время задай ОДНИМ из способов: delay_minutes — для относительного "
            "(«через N минут/часов»), scheduled_at — для абсолютного («завтра в 14:00»)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_contact_id": {"type": "string", "description": "UUID контакта агента"},
                "scheduled_at": {
                    "type": "string",
                    "description": (
                        "Абсолютные дата и время звонка ISO 8601 UTC (например 2026-07-09T12:00:00Z). "
                        "Не используй для «через N минут» — для этого есть delay_minutes"
                    ),
                },
                "delay_minutes": {
                    "type": "integer",
                    "description": (
                        "Через сколько минут позвонить. Сервер сам вычислит точное время от "
                        "текущего момента — ВСЕГДА используй этот параметр, когда просят "
                        "перезвонить «через N минут/часов», не вычисляй scheduled_at сам."
                    ),
                },
                "title": {"type": "string", "description": "Название задачи"},
                "notes": {"type": "string", "description": "Описание / заметки"},
            },
            "required": ["agent_contact_id", "title"],
        },
    },
    SEND_TELEGRAM_NOTIFICATION_TOOL,
    UPDATE_CONTACT_INFO_TOOL,
    MOVE_CONTACT_STAGE_TOOL,
    SEARCH_KNOWLEDGE_BASE_TOOL,
    SEND_SMS_TOOL,
    SEND_WEBHOOK_TOOL,
]


# ============================================================================
# TOOL IMPLEMENTATIONS
# ============================================================================

async def fn_create_agent_contact(args: dict, agent_config_id: str, user_id: str, db: Session) -> dict:
    contact = AgentContact(
        agent_config_id=agent_config_id,
        user_id=user_id,
        name=args.get("name"),
        phone=args["phone"],
        company=args.get("company"),
        position=args.get("position"),
        notes=args.get("notes"),
        status="new",
        memory={},
    )
    db.add(contact)
    db.commit()
    db.refresh(contact)
    logger.info(f"[AGENT-TOOLS] Created contact {contact.id} ({contact.phone})")
    return {"ok": True, "contact_id": str(contact.id), "phone": contact.phone, "name": contact.name}


async def fn_create_agent_task(args: dict, user_id: str, agent_config_id: str, db: Session, channel: str = "call") -> dict:
    """
    Создать агентскую задачу. channel="call" (дефолт) — задача на звонок,
    channel="telegram" — отложенное сообщение с личного Telegram-аккаунта
    (для него не применяются рабочие часы: писать можно в любое время).
    """
    is_telegram = channel == "telegram"
    agent_contact_id = args["agent_contact_id"]

    # Изоляция агентов: задачу можно ставить только своему контакту.
    owner = db.query(AgentContact.id).filter(
        AgentContact.id == agent_contact_id,
        AgentContact.user_id == user_id,
        AgentContact.agent_config_id == agent_config_id,
    ).first()
    if not owner:
        return {"ok": False, "error": "Contact not found"}

    # Время задачи: delay_minutes (относительное, сервер считает сам) имеет
    # приоритет над scheduled_at (абсолютное, посчитанное моделью).
    now_utc = datetime.now(timezone.utc)
    clamped_to_future = False
    scheduled_at = None
    if args.get("delay_minutes") is not None:
        try:
            scheduled_at = now_utc + timedelta(minutes=max(1, int(args["delay_minutes"])))
        except (ValueError, TypeError):
            scheduled_at = None  # битое значение → падаем в ветку scheduled_at
    if scheduled_at is None:
        try:
            scheduled_at = datetime.fromisoformat(str(args["scheduled_at"]).replace("Z", "+00:00"))
        except (KeyError, ValueError, TypeError):
            scheduled_at = datetime.utcnow() + timedelta(hours=1)
        # Клэмп: модель могла посчитать время от устаревшего значения в промпте.
        sched_aware = scheduled_at if scheduled_at.tzinfo else scheduled_at.replace(tzinfo=timezone.utc)
        if sched_aware < now_utc + timedelta(minutes=2):
            scheduled_at = now_utc + timedelta(minutes=3)
            clamped_to_future = True
            logger.info(f"[AGENT-TOOLS] scheduled_at in the past, clamped to {scheduled_at.isoformat()}")

    # Get assistant from agent_config (type-aware — gemini/openai/cartesia/yandex)
    agent_config = db.query(AgentConfig).filter(AgentConfig.id == agent_config_id).first()

    # Учитываем паузу контакта (snooze): если контакт на паузе до даты в будущем —
    # сдвигаем звонок на момент окончания паузы (раньше звонить нельзя).
    snooze_contact = db.query(AgentContact).filter(AgentContact.id == agent_contact_id).first()
    if snooze_contact and isinstance(snooze_contact.memory, dict):
        snooze_until_raw = snooze_contact.memory.get("snooze_until")
        snooze_until = _parse_iso_utc(snooze_until_raw) if snooze_until_raw else None
        if snooze_until:
            sched_aware = scheduled_at if scheduled_at.tzinfo else scheduled_at.replace(tzinfo=timezone.utc)
            if sched_aware < snooze_until:
                scheduled_at = snooze_until
                logger.info(f"[AGENT-TOOLS] Contact {agent_contact_id} snoozed until {snooze_until}, shifting task to it")

    # Унифицированная проверка рабочих часов агента (МСК) — переносим звонок
    # на ближайший рабочий день, если время выпадает на нерабочие часы.
    # Telegram-сообщения рабочими часами не ограничены.
    if agent_config is not None and not is_telegram:
        adjusted, _shifted = adjust_to_working_hours(
            scheduled_at,
            agent_config.working_hours_start,
            agent_config.working_hours_end,
        )
        scheduled_at = adjusted

    # Cancel only exact-time duplicates for this contact (same contact + same
    # scheduled_time + same channel). Tasks scheduled for other dates/times are
    # preserved, so a contact can have several upcoming calls planned at
    # different moments; звонок и telegram-сообщение на одно время — не дубли.
    existing_tasks = db.query(Task).filter(
        Task.agent_contact_id == agent_contact_id,
        Task.status == TaskStatus.SCHEDULED,
        Task.is_agent_task == True,
        Task.scheduled_time == scheduled_at,
        Task.channel == channel,
    ).all()

    cancelled_count = 0
    for existing_task in existing_tasks:
        existing_task.status = TaskStatus.CANCELLED
        cancelled_count += 1

    if cancelled_count > 0:
        logger.info(f"[AGENT-TOOLS] Cancelled {cancelled_count} duplicate SCHEDULED tasks for contact {agent_contact_id} at {scheduled_at}")

    # Create new task — route assistant to the correct Task FK by type.
    # Для telegram-задач ассистент не нужен (исполняет оркестратор, не звонилка),
    # но FK заполняем как обычно — это безвредно и упрощает конверсию в звонок.
    task = Task(
        is_agent_task=True,
        channel=channel,
        agent_contact_id=agent_contact_id,
        user_id=user_id,
        contact_id=None,
        status=TaskStatus.SCHEDULED,
        scheduled_time=scheduled_at,
        title=args.get("title") or ("Сообщение в Telegram" if is_telegram else "Звонок агента"),
        description=args.get("notes", ""),
        **assistant_task_kwargs(agent_config),
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    logger.info(f"[AGENT-TOOLS] Created agent task {task.id} (channel={channel}) for contact {agent_contact_id} at {scheduled_at}")
    result = {
        "ok": True,
        "task_id": str(task.id),
        "channel": channel,
        "scheduled_at": scheduled_at.isoformat(),
        "cancelled_duplicates": cancelled_count,
    }
    if clamped_to_future:
        result["note"] = "scheduled_at был в прошлом — время поднято до ближайшего будущего"
    return result


async def fn_update_contact_memory(args: dict, agent_config_id: str, db: Session) -> dict:
    agent_contact_id = args["agent_contact_id"]
    # Изоляция агентов: память можно обновлять только своему контакту.
    contact = db.query(AgentContact).filter(
        AgentContact.id == agent_contact_id,
        AgentContact.agent_config_id == agent_config_id,
    ).first()
    if not contact:
        return {"ok": False, "error": "Contact not found"}

    memory = contact.memory or {}

    if "summary" in args:
        memory["summary"] = args["summary"]
    if "best_time" in args and args["best_time"]:
        memory["best_time"] = args["best_time"]
    if "tone" in args:
        tone_list = memory.get("tone_history", [])
        tone_list.append(args["tone"])
        memory["tone_history"] = tone_list[-10:]
    if "key_facts" in args:
        facts = set(memory.get("key_facts", []))
        facts.update(args["key_facts"])
        memory["key_facts"] = list(facts)

    memory["attempts"] = (memory.get("attempts", 0)) + 1
    memory["last_call"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M")

    contact.memory = memory
    flag_modified(contact, 'memory')
    db.commit()
    logger.info(f"[AGENT-TOOLS] Updated memory for contact {agent_contact_id}")
    return {"ok": True, "contact_id": agent_contact_id}


async def fn_update_contact_info(args: dict, user_id: str, agent_config_id: str, db: Session) -> dict:
    """
    Обновить базовую информацию о контакте (name/company/position/notes).
    notes — то, что пользователь/агент ЯВНО записали как факт (не путать с memory).
    """
    agent_contact_id = args.get("agent_contact_id")
    if not agent_contact_id:
        return {"ok": False, "error": "agent_contact_id_required"}

    contact = db.query(AgentContact).filter(
        AgentContact.id == agent_contact_id,
        AgentContact.user_id == user_id,
        AgentContact.agent_config_id == agent_config_id,
    ).first()
    if not contact:
        return {"ok": False, "error": "Contact not found"}

    updated_fields = []
    for field in ("name", "company", "position", "notes"):
        if field in args and args[field] is not None:
            setattr(contact, field, args[field])
            updated_fields.append(field)

    if not updated_fields:
        return {"ok": False, "error": "no_fields_to_update"}

    db.commit()
    logger.info(f"[AGENT-TOOLS] Updated contact {agent_contact_id} fields: {updated_fields}")
    return {"ok": True, "contact_id": str(agent_contact_id), "updated_fields": updated_fields}


async def fn_move_contact_stage(args: dict, user_id: str, agent_config_id: str, db: Session) -> dict:
    """
    Перевести контакт на стадию воронки (status). Скоупится по user_id +
    agent_config_id, чтобы агент/чат не мог тронуть чужой контакт или контакт
    другого агента. Валидирует стадию по единому справочнику pipeline_stages.
    """
    agent_contact_id = args.get("agent_contact_id")
    stage = args.get("stage")
    if not agent_contact_id:
        return {"ok": False, "error": "agent_contact_id_required"}
    if not is_valid_stage(stage):
        return {"ok": False, "error": f"invalid_stage: {stage}"}

    contact = db.query(AgentContact).filter(
        AgentContact.id == agent_contact_id,
        AgentContact.user_id == user_id,
        AgentContact.agent_config_id == agent_config_id,
    ).first()
    if not contact:
        return {"ok": False, "error": "Contact not found"}

    old_stage = contact.status
    contact.status = stage
    db.commit()
    logger.info(
        f"[AGENT-TOOLS] Moved contact {agent_contact_id} stage {old_stage} -> {stage} "
        f"(reason: {args.get('reason', '')})"
    )
    return {"ok": True, "contact_id": str(agent_contact_id), "old_stage": old_stage, "stage": stage}


async def fn_get_agent_contacts(args: dict, user_id: str, agent_config_id: str, db: Session) -> dict:
    q = db.query(AgentContact).filter(
        AgentContact.user_id == user_id,
        AgentContact.agent_config_id == agent_config_id,
    )
    contacts = q.order_by(AgentContact.created_at.desc()).limit(50).all()
    return {
        "ok": True,
        "count": len(contacts),
        "contacts": [
            {
                "id": str(c.id),
                "name": c.name,
                "phone": c.phone,
                "company": c.company,
                "attempts_count": c.attempts_count,
                "last_called_at": c.last_called_at.isoformat() if c.last_called_at else None,
            }
            for c in contacts
        ],
    }


async def fn_get_contact_call_history(args: dict, user_id: str, agent_config_id: str, db: Session) -> dict:
    agent_contact_id = args["agent_contact_id"]

    # Изоляция агентов: историю звонков отдаём только по своему контакту.
    owner = db.query(AgentContact.id).filter(
        AgentContact.id == agent_contact_id,
        AgentContact.user_id == user_id,
        AgentContact.agent_config_id == agent_config_id,
    ).first()
    if not owner:
        return {"ok": False, "error": "Contact not found"}

    calls = (
        db.query(AgentCall)
        .filter(AgentCall.agent_contact_id == agent_contact_id)
        .order_by(AgentCall.created_at.desc())
        .limit(20)
        .all()
    )
    return {
        "ok": True,
        "count": len(calls),
        "calls": [
            {
                "id": str(c.id),
                "status": c.status,
                "post_call_decision": c.post_call_decision,
                "duration_seconds": c.duration_seconds,
                "transcript": (c.transcript[:500] if c.transcript else None),
                "started_at": c.started_at.isoformat() if c.started_at else None,
                "completed_at": c.completed_at.isoformat() if c.completed_at else None,
            }
            for c in calls
        ],
    }


async def fn_get_contact_timeline(args: dict, user_id: str, agent_config_id: str, db: Session) -> dict:
    """
    Единая хронология общения с контактом по всем каналам (звонки + SMS +
    Telegram) для чат-оркестратора. Переиспользует build_conversation_timeline
    (ленивый импорт — agent_orchestrator импортирует этот модуль).
    """
    contact = db.query(AgentContact).filter(
        AgentContact.id == args.get("agent_contact_id"),
        AgentContact.user_id == user_id,
        AgentContact.agent_config_id == agent_config_id,
    ).first()
    if not contact:
        return {"ok": False, "error": "Contact not found"}

    try:
        days = int(args.get("days")) if args.get("days") is not None else 90
    except (TypeError, ValueError):
        days = 90
    try:
        limit = min(int(args.get("limit")) if args.get("limit") is not None else 100, 200)
    except (TypeError, ValueError):
        limit = 100

    from backend.services.agent_orchestrator import build_conversation_timeline
    timeline = build_conversation_timeline(
        db, contact, max_events=limit, days_window=(days or 0)
    )
    if not timeline:
        return {"ok": True, "timeline": "", "note": "Истории общения по этому контакту пока нет."}
    return {"ok": True, "timeline": timeline.strip()}


async def fn_get_agent_tasks(args: dict, user_id: str, agent_config_id: str, db: Session) -> dict:
    """Получить задачи агента с опциональными фильтрами.

    Согласовано с эндпоинтом календаря (GET /api/agent/tasks): INNER JOIN с
    AgentContact, чтобы не считать «осиротевшие» задачи (agent_contact_id=NULL
    после ON DELETE SET NULL), которых нет в UI.

    Помимо списка задач возвращает status_counts — агрегированные счётчики по
    статусам по ВСЕЙ выборке. Это надёжный источник правды о количестве
    scheduled-задач, который не зависит от лимита выдачи строк (раньше при
    LIMIT 20 + ORDER BY scheduled_time ASC будущие scheduled-задачи отсекались
    старыми завершёнными, и агент видел «0 запланированных»).
    """
    # Базовый фильтр — общий для счётчиков и для списка строк.
    # Изоляция агентов: задачи скоупим через INNER JOIN с AgentContact по
    # agent_config_id (у Task своего agent_config_id нет — как в /api/agent/tasks).
    base_filters = [
        Task.user_id == user_id,
        Task.is_agent_task == True,
        AgentContact.agent_config_id == agent_config_id,
    ]
    if args.get("agent_contact_id"):
        base_filters.append(Task.agent_contact_id == args["agent_contact_id"])

    # Приводим строковый статус к enum (как в /api/agent/tasks). Невалидный
    # статус не роняем — просто игнорируем фильтр.
    status_filter = None
    if args.get("status_filter"):
        try:
            status_filter = TaskStatus(args["status_filter"])
        except ValueError:
            logger.warning(f"[AGENT-TOOLS] Invalid status_filter: {args['status_filter']!r}, ignoring")

    # Агрегированные счётчики по статусам по всей выборке (без лимита).
    count_q = db.query(Task.status, func.count(Task.id)).join(
        AgentContact, Task.agent_contact_id == AgentContact.id
    ).filter(*base_filters)
    if status_filter is not None:
        count_q = count_q.filter(Task.status == status_filter)
    status_counts = {
        (st.value if hasattr(st, "value") else st): cnt
        for st, cnt in count_q.group_by(Task.status).all()
    }
    total = sum(status_counts.values())

    # Список строк: scheduled-задачи первыми, затем по времени — чтобы будущие
    # запланированные звонки не отсекались лимитом.
    q = db.query(Task).join(
        AgentContact, Task.agent_contact_id == AgentContact.id
    ).filter(*base_filters)
    if status_filter is not None:
        q = q.filter(Task.status == status_filter)

    tasks = q.order_by(
        (Task.status == TaskStatus.SCHEDULED).desc(),
        Task.scheduled_time.asc(),
    ).limit(50).all()

    return {
        "ok": True,
        "count": len(tasks),
        "total": total,
        "status_counts": status_counts,
        "scheduled_count": status_counts.get(TaskStatus.SCHEDULED.value, 0),
        "tasks": [
            {
                "id": str(t.id),
                "title": t.title,
                "status": t.status.value if hasattr(t.status, "value") else t.status,
                "channel": t.channel or "call",
                "scheduled_time": t.scheduled_time.isoformat() if t.scheduled_time else None,
                "description": t.description,
                "agent_contact_id": str(t.agent_contact_id) if t.agent_contact_id else None,
            }
            for t in tasks
        ],
    }


async def fn_delete_agent_task(args: dict, user_id: str, agent_config_id: str, db: Session) -> dict:
    """Удалить задачу агента по ID.

    Hard-delete (как delete_agent_contact). Скоупится по user_id + is_agent_task
    + agent_config_id (через JOIN с AgentContact), чтобы агент не мог удалить
    чужую, не-агентскую или принадлежащую другому агенту задачу.
    """
    task_id = args.get("task_id")
    if not task_id:
        return {"ok": False, "error": "task_id is required"}

    task = db.query(Task).join(
        AgentContact, Task.agent_contact_id == AgentContact.id
    ).filter(
        Task.id == task_id,
        Task.user_id == user_id,
        Task.is_agent_task == True,
        AgentContact.agent_config_id == agent_config_id,
    ).first()

    if not task:
        logger.warning(f"[AGENT-TOOLS] delete_agent_task: task {task_id} not found for user {user_id}")
        return {"ok": False, "error": "Task not found"}

    title = task.title
    db.delete(task)
    db.commit()

    logger.info(f"[AGENT-TOOLS] Deleted agent task {task_id} ('{title}') for user {user_id}")
    return {"ok": True, "deleted": True, "task_id": str(task_id), "title": title}


async def fn_get_agent_stats(args: dict, user_id: str, agent_config_id: str, db: Session) -> dict:
    # Изоляция агентов: вся статистика считается строго по agent_config_id.
    total_contacts = db.query(func.count(AgentContact.id)).filter(
        AgentContact.agent_config_id == agent_config_id
    ).scalar() or 0

    active_contacts = db.query(func.count(AgentContact.id)).filter(
        AgentContact.agent_config_id == agent_config_id,
        AgentContact.status.notin_(["rejected", "do_not_call"]),
    ).scalar() or 0

    total_calls = db.query(func.count(AgentCall.id)).filter(
        AgentCall.agent_config_id == agent_config_id
    ).scalar() or 0

    success_calls = db.query(func.count(AgentCall.id)).filter(
        AgentCall.agent_config_id == agent_config_id,
        AgentCall.post_call_decision == "SUCCESS",
    ).scalar() or 0

    followup_calls = db.query(func.count(AgentCall.id)).filter(
        AgentCall.agent_config_id == agent_config_id,
        AgentCall.post_call_decision == "FOLLOWUP",
    ).scalar() or 0

    no_answer_calls = db.query(func.count(AgentCall.id)).filter(
        AgentCall.agent_config_id == agent_config_id,
        AgentCall.post_call_decision == "NO_ANSWER",
    ).scalar() or 0

    scheduled_tasks = db.query(func.count(Task.id)).join(
        AgentContact, Task.agent_contact_id == AgentContact.id
    ).filter(
        Task.user_id == user_id,
        Task.is_agent_task == True,
        Task.status == TaskStatus.SCHEDULED,
        AgentContact.agent_config_id == agent_config_id,
    ).scalar() or 0

    return {
        "ok": True,
        "total_contacts": total_contacts,
        "active_contacts": active_contacts,
        "total_calls": total_calls,
        "success_calls": success_calls,
        "followup_calls": followup_calls,
        "no_answer_calls": no_answer_calls,
        "scheduled_tasks": scheduled_tasks,
    }


async def fn_send_telegram_notification(args: dict, agent_config: AgentConfig, db: Session) -> dict:
    """
    v2.2: Шлёт во все chat_id из agent_config.telegram_chat_ids.
    Использует бота агента (agent_configs.telegram_bot_token), а не юзера.
    """
    from backend.services.agent_telegram_service import (
        AgentTelegramService,
        markdown_to_telegram_html,
    )

    message = args["message"]

    if not agent_config or not agent_config.has_telegram_bot():
        return {"ok": False, "error": "telegram_bot_not_configured"}

    if not agent_config.telegram_enabled:
        return {"ok": False, "error": "telegram_disabled"}

    if not agent_config.get_telegram_chat_ids_list():
        return {"ok": False, "error": "no_chat_ids_configured"}

    # Тело уведомления может быть в Markdown → конвертируем в безопасный Telegram-HTML
    body_html = markdown_to_telegram_html(message)
    text = f"🤖 <b>Voksy AI Agent</b>\n\n{body_html}"
    result = await AgentTelegramService.send_to_all_chats(agent_config, text)

    logger.info(
        f"[AGENT-TOOLS] Telegram notification: sent={result['sent']} "
        f"failed={result['failed']} total={result['total']} (agent {agent_config.id})"
    )
    return {
        "ok": result["sent"] > 0,
        "sent": result["sent"],
        "failed": result["failed"],
        "total": result["total"],
    }


async def fn_send_sms(args: dict, user_id: str, agent_config: AgentConfig, db: Session) -> dict:
    """
    Отправить SMS клиенту с номера агента через Voximplant Management API.

    Источник — agent_config.default_caller_id (номер, с которого агент звонит).
    Получатель — телефон контакта (agent_contact_id) или явно переданный phone.
    Credentials берутся из VoximplantChildAccount по user_id.
    """
    import httpx
    from backend.models.voximplant_child import VoximplantChildAccount

    text = (args.get("text") or "").strip()
    if not text:
        return {"ok": False, "error": "Текст SMS не может быть пустым"}

    # Получатель: явный phone имеет приоритет, иначе берём телефон контакта.
    to_number = (args.get("phone") or "").strip()
    if not to_number and args.get("agent_contact_id"):
        contact = db.query(AgentContact).filter(
            AgentContact.id == args["agent_contact_id"],
            AgentContact.user_id == user_id,
            AgentContact.agent_config_id == (agent_config.id if agent_config else None),
        ).first()
        if not contact:
            return {"ok": False, "error": "Contact not found"}
        to_number = (contact.phone or "").strip()
    if not to_number:
        return {"ok": False, "error": "Не указан номер получателя (phone или agent_contact_id)"}

    child = db.query(VoximplantChildAccount).filter(
        VoximplantChildAccount.user_id == user_id
    ).first()
    if not child or not child.vox_account_id or not child.vox_api_key:
        return {"ok": False, "error": "Voximplant credentials не настроены"}

    # Источник — номер агента. Та же логика, что и у исходящих звонков
    # (task_scheduler): сначала default_caller_id, иначе первый активный номер
    # аккаунта. Поэтому SMS работает в тех же случаях, что и звонки — даже если
    # default_caller_id у агента не задан.
    active_numbers = [p.phone_number for p in (child.phone_numbers or []) if getattr(p, "is_active", False)]
    from_number = (getattr(agent_config, "default_caller_id", None) or "").strip()
    if not from_number or (active_numbers and from_number not in active_numbers):
        if active_numbers:
            from_number = active_numbers[0]
    if not from_number:
        return {"ok": False, "error": "no_source_number: у агента нет активного номера-отправителя"}

    to_clean = to_number.replace("+", "")
    from_clean = from_number.replace("+", "")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.voximplant.com/platform_api/SendSmsMessage/",
                params={
                    "account_id": child.vox_account_id,
                    "api_key": child.vox_api_key,
                    "source": from_clean,
                    "destination": to_clean,
                    "sms_body": text,
                },
            )
        data = resp.json()
    except Exception as e:
        logger.error(f"[AGENT-TOOLS] send_sms error: {e}", exc_info=True)
        return {"ok": False, "error": str(e)}

    if isinstance(data, dict) and data.get("result") == 1:
        tx = data.get("transaction_id")
        logger.info(f'[AGENT-TOOLS] SMS {from_clean} → {to_clean}: "{text[:50]}" (tx: {tx})')
        # Сохраняем исходящее SMS в общий тред (sms_messages), чтобы переписка
        # была полной для контекста агента и карточки контакта.
        from backend.services.sms_history import store_outbound_sms
        store_outbound_sms(db, child.id, child.vox_account_id, from_clean, to_clean, text)
        return {"ok": True, "transaction_id": tx, "to": to_clean}

    error_msg = data.get("error", {}).get("msg", str(data)) if isinstance(data, dict) else str(data)
    logger.error(f"[AGENT-TOOLS] SMS failed {from_clean} → {to_clean}: {error_msg}")
    return {"ok": False, "error": f"Ошибка Voximplant: {error_msg}"}


async def fn_search_contacts(args: dict, user_id: str, agent_config_id: str, db: Session) -> dict:
    """Поиск контактов по подстроке (имя/телефон/компания) и/или стадии воронки."""
    q = db.query(AgentContact).filter(
        AgentContact.user_id == user_id,
        AgentContact.agent_config_id == agent_config_id,
    )

    stage = args.get("stage")
    if stage and is_valid_stage(stage):
        q = q.filter(AgentContact.status == stage)

    company = args.get("company")
    if company:
        q = q.filter(AgentContact.company.ilike(f"%{company}%"))

    query = args.get("query")
    if query:
        like = f"%{query}%"
        q = q.filter(or_(
            AgentContact.name.ilike(like),
            AgentContact.phone.ilike(like),
            AgentContact.company.ilike(like),
        ))

    try:
        limit = max(1, min(int(args.get("limit") or 30), 100))
    except (ValueError, TypeError):
        limit = 30

    contacts = q.order_by(AgentContact.created_at.desc()).limit(limit).all()
    return {
        "ok": True,
        "count": len(contacts),
        "contacts": [
            {
                "id": str(c.id),
                "name": c.name,
                "phone": c.phone,
                "company": c.company,
                "position": c.position,
                "stage": c.status,
                "attempts_count": c.attempts_count or 0,
                "last_called_at": c.last_called_at.isoformat() if c.last_called_at else None,
            }
            for c in contacts
        ],
    }


async def fn_get_contact_details(args: dict, user_id: str, agent_config_id: str, db: Session) -> dict:
    """Полная карточка контакта: поля + память + краткая сводка последних звонков."""
    agent_contact_id = args.get("agent_contact_id")
    if not agent_contact_id:
        return {"ok": False, "error": "agent_contact_id_required"}

    contact = db.query(AgentContact).filter(
        AgentContact.id == agent_contact_id,
        AgentContact.user_id == user_id,
        AgentContact.agent_config_id == agent_config_id,
    ).first()
    if not contact:
        return {"ok": False, "error": "Contact not found"}

    recent_calls = (
        db.query(AgentCall)
        .filter(AgentCall.agent_contact_id == contact.id)
        .order_by(AgentCall.created_at.desc())
        .limit(5)
        .all()
    )

    return {
        "ok": True,
        "contact": {
            "id": str(contact.id),
            "name": contact.name,
            "phone": contact.phone,
            "company": contact.company,
            "position": contact.position,
            "notes": contact.notes,
            "stage": contact.status,
            "memory": contact.memory or {},
            "attempts_count": contact.attempts_count or 0,
            "last_called_at": contact.last_called_at.isoformat() if contact.last_called_at else None,
            "created_at": contact.created_at.isoformat() if contact.created_at else None,
        },
        "recent_calls": [
            {
                "id": str(c.id),
                "status": c.status,
                "post_call_decision": c.post_call_decision,
                "duration_seconds": c.duration_seconds,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in recent_calls
        ],
    }


async def fn_get_contacts_by_stage(args: dict, user_id: str, agent_config_id: str, db: Session) -> dict:
    """Разбивка контактов по стадиям воронки: счётчики + примеры контактов."""
    rows = db.query(AgentContact.status, func.count(AgentContact.id)).filter(
        AgentContact.user_id == user_id,
        AgentContact.agent_config_id == agent_config_id,
    ).group_by(AgentContact.status).all()
    counts = {(st or "new"): cnt for st, cnt in rows}

    stages = []
    for key in AGENT_CONTACT_STAGE_KEYS:
        sample = (
            db.query(AgentContact)
            .filter(
                AgentContact.user_id == user_id,
                AgentContact.agent_config_id == agent_config_id,
                AgentContact.status == key,
            )
            .order_by(AgentContact.created_at.desc())
            .limit(5)
            .all()
        )
        stages.append({
            "stage": key,
            "count": counts.get(key, 0),
            "sample": [
                {"id": str(c.id), "name": c.name, "phone": c.phone, "company": c.company}
                for c in sample
            ],
        })

    return {
        "ok": True,
        "total": sum(counts.values()),
        "stages": stages,
    }


async def fn_bulk_create_contacts(args: dict, agent_config_id: str, user_id: str, db: Session) -> dict:
    """Массовое создание контактов. Дубли по номеру (уже в базе) пропускаются."""
    items = args.get("contacts") or []
    if not isinstance(items, list) or not items:
        return {"ok": False, "error": "contacts_required"}

    created = []
    skipped = []
    for item in items:
        if not isinstance(item, dict):
            continue
        phone = item.get("phone")
        if not phone:
            skipped.append({"phone": None, "reason": "no_phone"})
            continue

        # Дубль проверяем в пределах ТЕКУЩЕГО агента (а не всего аккаунта) —
        # один номер может вестись разными агентами одного пользователя.
        exists = db.query(AgentContact.id).filter(
            AgentContact.agent_config_id == agent_config_id,
            AgentContact.phone == phone,
        ).first()
        if exists:
            skipped.append({"phone": phone, "reason": "duplicate"})
            continue

        contact = AgentContact(
            agent_config_id=agent_config_id,
            user_id=user_id,
            name=item.get("name"),
            phone=phone,
            company=item.get("company"),
            position=item.get("position"),
            notes=item.get("notes"),
            status="new",
            memory={},
        )
        db.add(contact)
        db.flush()
        created.append({"id": str(contact.id), "phone": contact.phone, "name": contact.name})

    db.commit()
    logger.info(f"[AGENT-TOOLS] Bulk created {len(created)} contacts, skipped {len(skipped)}")
    return {
        "ok": True,
        "created_count": len(created),
        "skipped_count": len(skipped),
        "created": created,
        "skipped": skipped,
    }


async def fn_delete_agent_contact(args: dict, user_id: str, agent_config_id: str, db: Session) -> dict:
    """Удалить контакт агента (hard-delete). История звонков удаляется каскадом."""
    agent_contact_id = args.get("agent_contact_id")
    if not agent_contact_id:
        return {"ok": False, "error": "agent_contact_id_required"}

    contact = db.query(AgentContact).filter(
        AgentContact.id == agent_contact_id,
        AgentContact.user_id == user_id,
        AgentContact.agent_config_id == agent_config_id,
    ).first()
    if not contact:
        return {"ok": False, "error": "Contact not found"}

    # Отменяем запланированные задачи контакта, чтобы планировщик их не выполнил
    # после удаления (FK Task.agent_contact_id = ON DELETE SET NULL).
    db.query(Task).filter(
        Task.agent_contact_id == agent_contact_id,
        Task.status == TaskStatus.SCHEDULED,
        Task.is_agent_task == True,
    ).update({"status": TaskStatus.CANCELLED}, synchronize_session=False)

    name = contact.name or contact.phone
    db.delete(contact)
    db.commit()
    logger.info(f"[AGENT-TOOLS] Deleted agent contact {agent_contact_id} ('{name}') for user {user_id}")
    return {"ok": True, "deleted": True, "contact_id": str(agent_contact_id), "name": name}


async def fn_append_contact_note(args: dict, user_id: str, agent_config_id: str, db: Session) -> dict:
    """Дописать заметку к контакту, не стирая существующие (новая строка с датой)."""
    agent_contact_id = args.get("agent_contact_id")
    note = (args.get("note") or "").strip()
    if not agent_contact_id:
        return {"ok": False, "error": "agent_contact_id_required"}
    if not note:
        return {"ok": False, "error": "note_required"}

    contact = db.query(AgentContact).filter(
        AgentContact.id == agent_contact_id,
        AgentContact.user_id == user_id,
        AgentContact.agent_config_id == agent_config_id,
    ).first()
    if not contact:
        return {"ok": False, "error": "Contact not found"}

    stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    line = f"[{stamp}] {note}"
    contact.notes = f"{contact.notes}\n{line}" if contact.notes else line
    db.commit()
    logger.info(f"[AGENT-TOOLS] Appended note to contact {agent_contact_id}")
    return {"ok": True, "contact_id": str(agent_contact_id), "notes": contact.notes}


async def fn_update_agent_task(args: dict, user_id: str, agent_config_id: str, db: Session) -> dict:
    """Изменить запланированную задачу агента: время и/или название/описание."""
    task_id = args.get("task_id")
    if not task_id:
        return {"ok": False, "error": "task_id_required"}

    # Изоляция агентов: правим только задачи своих контактов (JOIN с AgentContact).
    task = db.query(Task).join(
        AgentContact, Task.agent_contact_id == AgentContact.id
    ).filter(
        Task.id == task_id,
        Task.user_id == user_id,
        Task.is_agent_task == True,
        AgentContact.agent_config_id == agent_config_id,
    ).first()
    if not task:
        return {"ok": False, "error": "Task not found"}
    if task.status != TaskStatus.SCHEDULED:
        return {"ok": False, "error": f"task_not_scheduled (status={task.status.value if hasattr(task.status, 'value') else task.status})"}

    updated = []
    if args.get("scheduled_at"):
        new_dt = _parse_iso_utc(args["scheduled_at"])
        if not new_dt:
            return {"ok": False, "error": "invalid_scheduled_at"}
        # Привести к рабочим часам агента (как при создании задачи).
        # Telegram-задачи рабочими часами не ограничены.
        agent_config = None
        if task.agent_contact_id:
            contact = db.query(AgentContact).filter(AgentContact.id == task.agent_contact_id).first()
            if contact and contact.agent_config_id:
                agent_config = db.query(AgentConfig).filter(AgentConfig.id == contact.agent_config_id).first()
        if agent_config is not None and (task.channel or "call") != "telegram":
            new_dt, _shifted = adjust_to_working_hours(
                new_dt, agent_config.working_hours_start, agent_config.working_hours_end
            )
        task.scheduled_time = new_dt
        updated.append("scheduled_at")

    if args.get("title") is not None:
        task.title = args["title"]
        updated.append("title")
    if args.get("notes") is not None:
        task.description = args["notes"]
        updated.append("notes")

    if not updated:
        return {"ok": False, "error": "no_fields_to_update"}

    db.commit()
    logger.info(f"[AGENT-TOOLS] Updated agent task {task_id} fields: {updated}")
    return {
        "ok": True,
        "task_id": str(task_id),
        "updated_fields": updated,
        "scheduled_at": task.scheduled_time.isoformat() if task.scheduled_time else None,
        "title": task.title,
    }


async def fn_get_upcoming_schedule(args: dict, user_id: str, agent_config_id: str, db: Session) -> dict:
    """Календарь ближайших запланированных звонков по всем контактам."""
    try:
        days = max(1, min(int(args.get("days") or 7), 90))
    except (ValueError, TypeError):
        days = 7
    try:
        limit = max(1, min(int(args.get("limit") or 50), 100))
    except (ValueError, TypeError):
        limit = 50

    now = datetime.utcnow()
    horizon = now + timedelta(days=days)

    rows = (
        db.query(Task, AgentContact)
        .join(AgentContact, Task.agent_contact_id == AgentContact.id)
        .filter(
            Task.user_id == user_id,
            Task.is_agent_task == True,
            Task.status == TaskStatus.SCHEDULED,
            Task.scheduled_time >= now,
            Task.scheduled_time <= horizon,
            AgentContact.agent_config_id == agent_config_id,
        )
        .order_by(Task.scheduled_time.asc())
        .limit(limit)
        .all()
    )

    return {
        "ok": True,
        "count": len(rows),
        "days": days,
        "tasks": [
            {
                "task_id": str(t.id),
                "title": t.title,
                "channel": t.channel or "call",
                "scheduled_time": t.scheduled_time.isoformat() if t.scheduled_time else None,
                "agent_contact_id": str(c.id),
                "contact_name": c.name,
                "contact_phone": c.phone,
            }
            for t, c in rows
        ],
    }


async def fn_bulk_schedule_calls(args: dict, user_id: str, agent_config_id: str, db: Session) -> dict:
    """Запланировать звонки группе контактов с интервалом, начиная со start_at."""
    start_dt = _parse_iso_utc(args.get("start_at"))
    if not start_dt:
        return {"ok": False, "error": "invalid_or_missing_start_at"}

    try:
        interval = max(1, min(int(args.get("interval_minutes") or 15), 1440))
    except (ValueError, TypeError):
        interval = 15

    title = args.get("title") or "Звонок агента"

    # Резолвим целевые контакты: явный список или по стадии.
    ids = args.get("agent_contact_ids")
    stage = args.get("stage")
    cq = db.query(AgentContact).filter(
        AgentContact.user_id == user_id,
        AgentContact.agent_config_id == agent_config_id,
    )
    if ids:
        cq = cq.filter(AgentContact.id.in_(ids))
    elif stage and is_valid_stage(stage):
        cq = cq.filter(AgentContact.status == stage)
    else:
        return {"ok": False, "error": "provide_agent_contact_ids_or_valid_stage"}

    contacts = cq.order_by(AgentContact.created_at.asc()).all()
    if not contacts:
        return {"ok": False, "error": "no_contacts_matched"}

    agent_config = db.query(AgentConfig).filter(AgentConfig.id == agent_config_id).first()
    task_kwargs = assistant_task_kwargs(agent_config)

    scheduled = []
    for i, contact in enumerate(contacts):
        slot = start_dt + timedelta(minutes=interval * i)

        # Уважаем паузу контакта (snooze).
        if isinstance(contact.memory, dict):
            snooze_until = _parse_iso_utc(contact.memory.get("snooze_until"))
            if snooze_until and slot < snooze_until:
                slot = snooze_until

        # Рабочие часы агента.
        if agent_config is not None:
            slot, _shifted = adjust_to_working_hours(
                slot, agent_config.working_hours_start, agent_config.working_hours_end
            )

        task = Task(
            is_agent_task=True,
            agent_contact_id=contact.id,
            user_id=user_id,
            contact_id=None,
            status=TaskStatus.SCHEDULED,
            scheduled_time=slot,
            title=title,
            description=args.get("notes", ""),
            **task_kwargs,
        )
        db.add(task)
        db.flush()
        scheduled.append({
            "task_id": str(task.id),
            "agent_contact_id": str(contact.id),
            "contact_name": contact.name or contact.phone,
            "scheduled_at": slot.isoformat(),
        })

    db.commit()
    logger.info(f"[AGENT-TOOLS] Bulk scheduled {len(scheduled)} calls for user {user_id}")
    return {"ok": True, "scheduled_count": len(scheduled), "tasks": scheduled}


async def fn_trigger_immediate_call(args: dict, user_id: str, agent_config_id: str, db: Session) -> dict:
    """Создать задачу на немедленный звонок (без сдвига в рабочие часы)."""
    agent_contact_id = args.get("agent_contact_id")
    if not agent_contact_id:
        return {"ok": False, "error": "agent_contact_id_required"}

    contact = db.query(AgentContact).filter(
        AgentContact.id == agent_contact_id,
        AgentContact.user_id == user_id,
        AgentContact.agent_config_id == agent_config_id,
    ).first()
    if not contact:
        return {"ok": False, "error": "Contact not found"}

    agent_config = db.query(AgentConfig).filter(AgentConfig.id == agent_config_id).first()
    if agent_config is not None and not agent_config.is_active:
        return {"ok": False, "error": "agent_inactive", "hint": "Активируйте агента, иначе планировщик не выполнит звонок."}

    # Немедленно: ставим задачу на текущий момент, рабочие часы НЕ применяем —
    # пользователь явно просит позвонить сейчас. Планировщик подхватит её за ~30с.
    task = Task(
        is_agent_task=True,
        agent_contact_id=contact.id,
        user_id=user_id,
        contact_id=None,
        status=TaskStatus.SCHEDULED,
        scheduled_time=datetime.now(timezone.utc),
        title=args.get("title") or "Немедленный звонок",
        description="",
        **assistant_task_kwargs(agent_config),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    logger.info(f"[AGENT-TOOLS] Triggered immediate call task {task.id} for contact {agent_contact_id}")
    return {
        "ok": True,
        "task_id": str(task.id),
        "agent_contact_id": str(contact.id),
        "contact_name": contact.name or contact.phone,
        "note": "Звонок поставлен в очередь, планировщик выполнит его в течение ~30 секунд.",
    }


async def fn_snooze_contact(args: dict, user_id: str, agent_config_id: str, db: Session) -> dict:
    """Поставить контакт на паузу до даты: отменить задачи + запретить ранние звонки."""
    agent_contact_id = args.get("agent_contact_id")
    until = _parse_iso_utc(args.get("until"))
    if not agent_contact_id:
        return {"ok": False, "error": "agent_contact_id_required"}
    if not until:
        return {"ok": False, "error": "invalid_or_missing_until"}

    contact = db.query(AgentContact).filter(
        AgentContact.id == agent_contact_id,
        AgentContact.user_id == user_id,
        AgentContact.agent_config_id == agent_config_id,
    ).first()
    if not contact:
        return {"ok": False, "error": "Contact not found"}

    # Отменяем все запланированные задачи контакта.
    cancelled = db.query(Task).filter(
        Task.agent_contact_id == agent_contact_id,
        Task.status == TaskStatus.SCHEDULED,
        Task.is_agent_task == True,
    ).update({"status": TaskStatus.CANCELLED}, synchronize_session=False)

    # Запоминаем паузу в памяти контакта — её уважает create_agent_task / bulk_schedule_calls.
    memory = dict(contact.memory or {})
    memory["snooze_until"] = until.isoformat()
    contact.memory = memory
    flag_modified(contact, "memory")
    db.commit()
    logger.info(f"[AGENT-TOOLS] Snoozed contact {agent_contact_id} until {until}, cancelled {cancelled} tasks")
    return {
        "ok": True,
        "contact_id": str(agent_contact_id),
        "snooze_until": until.isoformat(),
        "cancelled_tasks": cancelled,
    }


async def fn_get_call_transcript(args: dict, user_id: str, agent_config_id: str, db: Session) -> dict:
    """Полный транскрипт конкретного звонка."""
    agent_call_id = args.get("agent_call_id")
    if not agent_call_id:
        return {"ok": False, "error": "agent_call_id_required"}

    call = db.query(AgentCall).filter(
        AgentCall.id == agent_call_id,
        AgentCall.user_id == user_id,
        AgentCall.agent_config_id == agent_config_id,
    ).first()
    if not call:
        return {"ok": False, "error": "Call not found"}

    return {
        "ok": True,
        "call": {
            "id": str(call.id),
            "agent_contact_id": str(call.agent_contact_id) if call.agent_contact_id else None,
            "status": call.status,
            "post_call_decision": call.post_call_decision,
            "duration_seconds": call.duration_seconds,
            "started_at": call.started_at.isoformat() if call.started_at else None,
            "completed_at": call.completed_at.isoformat() if call.completed_at else None,
            "transcript": call.transcript or "(транскрипт недоступен)",
        },
    }


async def fn_get_period_report(args: dict, user_id: str, agent_config_id: str, db: Session) -> dict:
    """Сводный отчёт по звонкам за период (по умолчанию последние 7 дней)."""
    date_to = _parse_iso_utc(args.get("date_to")) or datetime.now(timezone.utc)
    date_from = _parse_iso_utc(args.get("date_from")) or (date_to - timedelta(days=7))
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    # Колонка created_at — naive UTC, сравниваем с naive границами.
    df = date_from.replace(tzinfo=None)
    dt = date_to.replace(tzinfo=None)

    calls = (
        db.query(AgentCall)
        .filter(
            AgentCall.user_id == user_id,
            AgentCall.agent_config_id == agent_config_id,
            AgentCall.created_at >= df,
            AgentCall.created_at <= dt,
        )
        .all()
    )

    total = len(calls)
    answered = sum(1 for c in calls if c.status == "answered")
    success = sum(1 for c in calls if c.post_call_decision == "SUCCESS")
    followup = sum(1 for c in calls if c.post_call_decision == "FOLLOWUP")
    no_answer = sum(1 for c in calls if c.post_call_decision == "NO_ANSWER" or c.status in ("no_answer", "failed"))
    total_duration = sum(int(c.duration_seconds or 0) for c in calls)
    avg_duration = round(total_duration / answered) if answered else 0
    conversion = round(success / answered * 100, 1) if answered else 0.0

    return {
        "ok": True,
        "period": {"from": date_from.isoformat(), "to": date_to.isoformat()},
        "total_calls": total,
        "answered": answered,
        "success": success,
        "followup": followup,
        "no_answer": no_answer,
        "total_duration_seconds": total_duration,
        "avg_duration_seconds": avg_duration,
        "conversion_percent": conversion,
    }


async def fn_get_failed_calls(args: dict, user_id: str, agent_config_id: str, db: Session) -> dict:
    """Очередь на перезвон: последний неудачный/недозвон по каждому контакту."""
    try:
        limit = max(1, min(int(args.get("limit") or 30), 100))
    except (ValueError, TypeError):
        limit = 30

    calls = (
        db.query(AgentCall)
        .filter(
            AgentCall.user_id == user_id,
            AgentCall.agent_config_id == agent_config_id,
            or_(
                AgentCall.status.in_(["no_answer", "failed"]),
                AgentCall.post_call_decision == "NO_ANSWER",
            ),
        )
        .order_by(AgentCall.created_at.desc())
        .limit(300)
        .all()
    )

    seen = set()
    result = []
    for c in calls:
        cid = c.agent_contact_id
        if cid in seen:
            continue
        seen.add(cid)
        contact = db.query(AgentContact).filter(AgentContact.id == cid).first() if cid else None
        result.append({
            "agent_call_id": str(c.id),
            "agent_contact_id": str(cid) if cid else None,
            "contact_name": (contact.name if contact else None),
            "contact_phone": (contact.phone if contact else None),
            "status": c.status,
            "post_call_decision": c.post_call_decision,
            "last_attempt_at": c.created_at.isoformat() if c.created_at else None,
            "attempts_count": (contact.attempts_count if contact else None),
        })
        if len(result) >= limit:
            break

    return {"ok": True, "count": len(result), "contacts": result}


async def fn_search_knowledge_base(args: dict, agent_config, db: Session) -> dict:
    """
    Векторный поиск по базе знаний агента.

    Namespace берётся из agent_config.kb_namespace. Эмбеддинги считаются на
    системном ключе OPENAI_API_KEY (оркестратор v3 работает на кредитах, а не на
    личном ключе юзера).
    """
    import os
    from backend.services.pinecone_service import PineconeService

    query = (args.get("query") or "").strip()
    top_k = int(args.get("top_k") or 3)

    if not query:
        return {"ok": False, "error": "Пустой поисковый запрос"}

    namespace = getattr(agent_config, "kb_namespace", None) if agent_config else None
    if not namespace:
        return {"ok": False, "error": "База знаний не создана для этого агента"}

    openai_api_key = os.environ.get("OPENAI_API_KEY")
    if not openai_api_key:
        return {"ok": False, "error": "OPENAI_API_KEY не настроен на сервере"}

    try:
        matches = await PineconeService.search(
            query=query, namespace=namespace, api_key=openai_api_key, top_k=top_k,
        )
    except Exception as e:
        logger.error(f"[AGENT-TOOLS] knowledge base search failed: {e}", exc_info=True)
        return {"ok": False, "error": f"Ошибка поиска: {e}"}

    results = [{"text": m.get("text", ""), "score": m.get("score")} for m in matches]
    return {"ok": True, "query": query, "total": len(results), "results": results}


async def fn_send_webhook(args: dict, agent_config, db: Session) -> dict:
    """
    Отправить событие на внешний вебхук агента (n8n/Make/Zapier/любой HTTP endpoint).

    URL берётся ТОЛЬКО из agent_config.webhook_url — модель его не передаёт.
    Тело запроса: {event, data, agent_id, agent_name}. Best-effort: ошибки сети
    не роняют оркестратор, а возвращаются как {"ok": false, ...}.
    """
    import asyncio
    import aiohttp

    url = (getattr(agent_config, "webhook_url", None) or "").strip() if agent_config else ""
    if not url:
        return {"ok": False, "error": "webhook_url_not_configured"}

    event = (args.get("event") or "").strip() or "default_event"
    payload = args.get("payload")
    if not isinstance(payload, dict):
        payload = {} if payload is None else {"value": payload}

    data = {
        "event": event,
        "data": payload,
        "agent_id": str(agent_config.id),
        "agent_name": agent_config.name,
    }

    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=data) as response:
                response_text = await response.text()
                logger.info(f"[AGENT-TOOLS] Webhook sent: event={event} status={response.status} (agent {agent_config.id})")
                return {
                    "ok": 200 <= response.status < 300,
                    "status": response.status,
                    "event": event,
                    "response": response_text[:200],
                }
    except asyncio.TimeoutError:
        logger.error(f"[AGENT-TOOLS] Webhook timeout: {url}")
        return {"ok": False, "error": "webhook_timeout"}
    except Exception as e:
        logger.error(f"[AGENT-TOOLS] send_webhook error: {e}", exc_info=True)
        return {"ok": False, "error": str(e)}


# ============================================================================
# DISPATCHER
# ============================================================================

_TOOL_MAP = {
    "search_knowledge_base": "fn_search_knowledge_base",
    "create_agent_contact": "fn_create_agent_contact",
    "create_agent_task": "fn_create_agent_task",
    "update_contact_memory": "fn_update_contact_memory",
    "update_contact_info": "fn_update_contact_info",
    "move_contact_stage": "fn_move_contact_stage",
    "get_agent_contacts": "fn_get_agent_contacts",
    "get_contact_call_history": "fn_get_contact_call_history",
    "get_agent_tasks": "fn_get_agent_tasks",
    "delete_agent_task": "fn_delete_agent_task",
    "get_agent_stats": "fn_get_agent_stats",
    "send_telegram_notification": "fn_send_telegram_notification",
    "send_sms": "fn_send_sms",
    "send_webhook": "fn_send_webhook",
    "search_contacts": "fn_search_contacts",
    "get_contact_details": "fn_get_contact_details",
    "get_contacts_by_stage": "fn_get_contacts_by_stage",
    "bulk_create_contacts": "fn_bulk_create_contacts",
    "delete_agent_contact": "fn_delete_agent_contact",
    "append_contact_note": "fn_append_contact_note",
    "update_agent_task": "fn_update_agent_task",
    "get_upcoming_schedule": "fn_get_upcoming_schedule",
    "bulk_schedule_calls": "fn_bulk_schedule_calls",
    "trigger_immediate_call": "fn_trigger_immediate_call",
    "snooze_contact": "fn_snooze_contact",
    "get_call_transcript": "fn_get_call_transcript",
    "get_period_report": "fn_get_period_report",
    "get_failed_calls": "fn_get_failed_calls",
}


async def execute_tool(tool_name: str, tool_args: dict, context: dict, db: Session) -> str:
    """
    Execute an agent tool by name.

    context must contain: agent_config_id, user_id, user (User object)
    Returns JSON string with result.
    """
    agent_config_id = context.get("agent_config_id")
    user_id = context.get("user_id")
    user = context.get("user")

    try:
        if tool_name == "create_agent_contact":
            result = await fn_create_agent_contact(tool_args, agent_config_id, user_id, db)
        elif tool_name == "create_agent_task":
            result = await fn_create_agent_task(tool_args, user_id, agent_config_id, db)
        elif tool_name == "update_contact_memory":
            result = await fn_update_contact_memory(tool_args, agent_config_id, db)
        elif tool_name == "update_contact_info":
            result = await fn_update_contact_info(tool_args, user_id, agent_config_id, db)
        elif tool_name == "move_contact_stage":
            result = await fn_move_contact_stage(tool_args, user_id, agent_config_id, db)
        elif tool_name == "get_agent_contacts":
            result = await fn_get_agent_contacts(tool_args, user_id, agent_config_id, db)
        elif tool_name == "get_contact_call_history":
            result = await fn_get_contact_call_history(tool_args, user_id, agent_config_id, db)
        elif tool_name == "get_contact_timeline":
            result = await fn_get_contact_timeline(tool_args, user_id, agent_config_id, db)
        elif tool_name == "get_agent_tasks":
            result = await fn_get_agent_tasks(tool_args, user_id, agent_config_id, db)
        elif tool_name == "delete_agent_task":
            result = await fn_delete_agent_task(tool_args, user_id, agent_config_id, db)
        elif tool_name == "get_agent_stats":
            result = await fn_get_agent_stats(tool_args, user_id, agent_config_id, db)
        elif tool_name == "send_telegram_notification":
            result = await fn_send_telegram_notification(tool_args, context.get("agent_config"), db)
        elif tool_name == "send_sms":
            agent_config = context.get("agent_config")
            if agent_config is None and agent_config_id:
                agent_config = db.query(AgentConfig).filter(AgentConfig.id == agent_config_id).first()
            result = await fn_send_sms(tool_args, user_id, agent_config, db)
        elif tool_name == "search_contacts":
            result = await fn_search_contacts(tool_args, user_id, agent_config_id, db)
        elif tool_name == "get_contact_details":
            result = await fn_get_contact_details(tool_args, user_id, agent_config_id, db)
        elif tool_name == "get_contacts_by_stage":
            result = await fn_get_contacts_by_stage(tool_args, user_id, agent_config_id, db)
        elif tool_name == "bulk_create_contacts":
            result = await fn_bulk_create_contacts(tool_args, agent_config_id, user_id, db)
        elif tool_name == "delete_agent_contact":
            result = await fn_delete_agent_contact(tool_args, user_id, agent_config_id, db)
        elif tool_name == "append_contact_note":
            result = await fn_append_contact_note(tool_args, user_id, agent_config_id, db)
        elif tool_name == "update_agent_task":
            result = await fn_update_agent_task(tool_args, user_id, agent_config_id, db)
        elif tool_name == "get_upcoming_schedule":
            result = await fn_get_upcoming_schedule(tool_args, user_id, agent_config_id, db)
        elif tool_name == "bulk_schedule_calls":
            result = await fn_bulk_schedule_calls(tool_args, user_id, agent_config_id, db)
        elif tool_name == "trigger_immediate_call":
            result = await fn_trigger_immediate_call(tool_args, user_id, agent_config_id, db)
        elif tool_name == "snooze_contact":
            result = await fn_snooze_contact(tool_args, user_id, agent_config_id, db)
        elif tool_name == "get_call_transcript":
            result = await fn_get_call_transcript(tool_args, user_id, agent_config_id, db)
        elif tool_name == "get_period_report":
            result = await fn_get_period_report(tool_args, user_id, agent_config_id, db)
        elif tool_name == "get_failed_calls":
            result = await fn_get_failed_calls(tool_args, user_id, agent_config_id, db)
        elif tool_name == "search_knowledge_base":
            agent_config = context.get("agent_config")
            if agent_config is None and agent_config_id:
                agent_config = db.query(AgentConfig).filter(AgentConfig.id == agent_config_id).first()
            result = await fn_search_knowledge_base(tool_args, agent_config, db)
        elif tool_name == "send_webhook":
            agent_config = context.get("agent_config")
            if agent_config is None and agent_config_id:
                agent_config = db.query(AgentConfig).filter(AgentConfig.id == agent_config_id).first()
            result = await fn_send_webhook(tool_args, agent_config, db)
        elif tool_name == "telegram_send_message":
            agent_config = context.get("agent_config")
            if agent_config is None and agent_config_id:
                agent_config = db.query(AgentConfig).filter(AgentConfig.id == agent_config_id).first()
            result = await fn_telegram_send_message(tool_args, user_id, agent_config, db)
        elif tool_name == "telegram_get_thread":
            result = await fn_telegram_get_thread(tool_args, user_id, agent_config_id, db)
        elif tool_name == "schedule_telegram_message":
            agent_config = context.get("agent_config")
            if agent_config is None and agent_config_id:
                agent_config = db.query(AgentConfig).filter(AgentConfig.id == agent_config_id).first()
            result = await fn_schedule_telegram_message(tool_args, user_id, agent_config, db)
        elif tool_name == "instagram_send_message":
            agent_config = context.get("agent_config")
            if agent_config is None and agent_config_id:
                agent_config = db.query(AgentConfig).filter(AgentConfig.id == agent_config_id).first()
            result = await fn_instagram_send_message(tool_args, user_id, agent_config, db)
        elif tool_name == "instagram_get_thread":
            result = await fn_instagram_get_thread(tool_args, user_id, agent_config_id, db)
        elif composio_service.is_composio_tool(tool_name):
            result = await fn_execute_connector(tool_name, tool_args, agent_config_id, db)
        else:
            result = {"ok": False, "error": f"Unknown tool: {tool_name}"}

        return json.dumps(result, ensure_ascii=False, default=str)

    except Exception as e:
        logger.error(f"[AGENT-TOOLS] Error executing {tool_name}: {e}", exc_info=True)
        return json.dumps({"ok": False, "error": str(e)})
