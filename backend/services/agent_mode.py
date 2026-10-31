"""
Режим агента «только отвечает» (тариф Старт).

Один движок агента, два режима (User.agent_mode()):
  full     — Profi / trial / legacy agent / админ: агент действует сам;
  reactive — Старт: агент по перепискам. Отвечает клиентам в мессенджерах и на входящие звонки,
             пишет в CRM, собирает и отправляет документы (КП, счета, памятки), и МОЖЕТ ПИСАТЬ
             КЛИЕНТАМ ПЕРВЫМ в WhatsApp / Telegram / Instagram в пределах суточного лимита новых
             чатов (WhatsApp: account.daily_new_chats_limit, Telegram: почасовые лимиты
             отправки, Instagram: только существующий диалог). Звонить первым, планировать звонки,
             рассылки по базе и внешние действия нельзя. Чат владельца с оркестратором читает и
             по команде владельца отправляет сообщения клиентам (без остальных команд).

Правила применяются в двух местах: набор инструментов, который видит модель
(filter_tools), и жёсткая проверка при выполнении (check_tool) — модель может
позвать инструмент, которого нет в наборе, если он остался в истории.
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

FULL = "full"
REACTIVE = "reactive"

# Окно, в течение которого клиентское обращение (сообщение или звонок) считается
# поводом ответить. Совпадает с окном ответа WhatsApp (CHAT_REPLY_WINDOW).
REPLY_WINDOW = timedelta(hours=24)

# Инициатива агента: планирование звонков, внешние действия. Сообщения клиентам в мессенджерах
# (SEND_TOOLS, schedule_telegram_message) Старту разрешены, в том числе первым. Документы
# (create_document) тоже разрешены.
INITIATIVE_TOOLS = {
    "create_agent_task",
    "update_agent_task",
    "delete_agent_task",
    "bulk_schedule_calls",
    "trigger_immediate_call",
    "snooze_contact",
    "send_sms",
    "send_webhook",
}

# Отправка клиенту в мессенджерах. На Старте разрешена и без обращения клиента (писать первым),
# лимиты новых чатов в сутки и отправок в час держат сами функции отправки.
SEND_TOOLS = {
    "telegram_send_message",
    "telegram_send_file",
    "whatsapp_send_message",
    "whatsapp_send_file",
    "instagram_send_message",
    "instagram_send_file",
    "send_voice_message",
    "send_proposal",
}

# Чат владельца с оркестратором на Старте читает и по команде владельца пишет клиентам в
# мессенджеры (в том числе первым) и ставит отложенное сообщение в Telegram. Остальное, что
# меняет данные (звонки, задачи, рассылки, правка CRM), запрещено.
OWNER_CHAT_READONLY_ALLOWED_PREFIXES = ("get_", "search_", "find_", "list_", "read_", "check_")
OWNER_CHAT_READONLY_ALLOWED = {"get_period_report", "get_agent_stats", "notify_owner_handoff",
                               "schedule_telegram_message"} | SEND_TOOLS

REFUSAL_INITIATIVE = (
    "На вашем тарифе агент не звонит и не планирует звонки, не делает рассылки и не выполняет "
    "внешние действия. Писать клиентам в WhatsApp, Telegram и Instagram можно. "
    "Звонки и автономная работа доступны на тарифе Profi."
)
REFUSAL_FIRST_MESSAGE = (
    "На вашем тарифе агент пишет только тому клиенту, чьё обращение разбирает. "
    "Написать другому клиенту можно командой владельца в чате или на тарифе Profi."
)
REFUSAL_OWNER_CHAT = (
    "На вашем тарифе чат с оркестратором читает данные (отчёт, поиск клиента, задачи) и пишет "
    "клиентам в мессенджеры. Остальные команды (звонить, планировать звонки, менять данные) "
    "доступны на тарифе Profi."
)

PROMPT_BLOCK = """

[Режим «агент по перепискам»]
Ты работаешь на тарифе, где агент ведёт переписку с клиентами в WhatsApp, Telegram и Instagram.
- Отвечай и веди диалог; отправляй файлы из библиотеки и базы знаний.
- Писать клиенту первым в мессенджере МОЖНО: по команде владельца, для доведения клиента до сделки (напомнить, уточнить, отправить обещанное). Пиши персонально и по делу, без рассылок одинаковых сообщений: у новых чатов в сутки есть лимит, и при его исчерпании функция отправки вернёт отказ — тогда не повторяй попытку, а оформи заметку владельцу.
- Instagram первым написать нельзя: только в диалог, который уже есть.
- НЕ звони клиенту и не планируй звонки, не делай рассылки по базе.
- Документы (КП, счёт, памятку) можешь собрать сам и отправить клиенту.
- Всё, что требует звонка, оформи как заметку и пометь клиента в CRM для владельца (append_contact_note, move_contact_stage); при готовности клиента передай его владельцу (notify_owner_handoff).
- Если клиент просит звонок, коротко скажи, что передашь владельцу, и передай."""


def get_mode(user) -> Optional[str]:
    try:
        return user.agent_mode() if user is not None else None
    except Exception:
        return FULL


def is_reactive(user) -> bool:
    return get_mode(user) == REACTIVE


def mode_for_agent(db: Session, agent_config) -> Optional[str]:
    """Режим по владельцу агента; при любой ошибке — полный (не ломаем платных клиентов)."""
    try:
        from backend.models.user import User
        owner = db.query(User).filter(User.id == agent_config.user_id).first()
        return get_mode(owner) if owner is not None else FULL
    except Exception:
        return FULL


def _tool_name(tool: Dict[str, Any]) -> str:
    if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
        return tool["function"].get("name", "")
    return tool.get("name", "")


def filter_tools(tools: List[Dict[str, Any]], surface: str) -> List[Dict[str, Any]]:
    """Набор инструментов для режима reactive. surface: 'postcall' | 'owner_chat'."""
    out = []
    for tool in tools:
        name = _tool_name(tool)
        if name in INITIATIVE_TOOLS:
            continue
        if surface == "owner_chat" and not _owner_chat_allowed(name):
            continue
        out.append(tool)
    return out


def _owner_chat_allowed(name: str) -> bool:
    return name in OWNER_CHAT_READONLY_ALLOWED or name.startswith(OWNER_CHAT_READONLY_ALLOWED_PREFIXES)


def client_contacted_recently(db: Session, agent_contact_id) -> bool:
    """Был ли за последние 24 часа контакт по инициативе клиента: сообщение в любом канале или разговор по телефону."""
    if agent_contact_id is None:
        return False
    since = datetime.utcnow() - REPLY_WINDOW
    from backend.models.agent_call import AgentCall
    from backend.models.agent_instagram import AgentInstagramMessage
    from backend.models.agent_telegram_account import AgentTelegramMessage
    from backend.models.agent_whatsapp import AgentWhatsAppMessage

    for model in (AgentWhatsAppMessage, AgentTelegramMessage, AgentInstagramMessage):
        if db.query(model.id).filter(
            model.agent_contact_id == agent_contact_id,
            model.direction == "inbound",
            model.created_at >= since,
        ).first() is not None:
            return True
    # Звонок: входящий, либо исходящий, который набрал владелец и клиент взял трубку
    # (после разговора бот отправляет обещанное: КП, прайс, адрес).
    from sqlalchemy import or_
    return db.query(AgentCall.id).filter(
        AgentCall.agent_contact_id == agent_contact_id,
        AgentCall.created_at >= since,
        or_(AgentCall.direction == "inbound", AgentCall.duration_seconds > 0),
    ).first() is not None


def check_tool(user, tool_name: str, tool_args: dict, context: dict, db: Session) -> Optional[str]:
    """None — можно выполнять; иначе текст отказа для модели (режим reactive)."""
    if not is_reactive(user):
        return None
    if tool_name in INITIATIVE_TOOLS:
        return REFUSAL_INITIATIVE
    surface = context.get("surface") or "owner_chat"
    if surface == "owner_chat" and not _owner_chat_allowed(tool_name):
        return REFUSAL_OWNER_CHAT
    if tool_name in SEND_TOOLS:
        args = tool_args or {}
        current = context.get("current_contact_id")
        target = args.get("agent_contact_id")
        if not target and args.get("phone"):
            from backend.services.agent_contact_phones import find_contact_by_phone
            found = find_contact_by_phone(db, context.get("agent_config_id"), args.get("phone"))
            target = str(found.id) if found is not None else None
        if not target:
            target = current
        # В PostCall бот пишет только тому клиенту, чьё обращение разбирает; чат владельца
        # может писать любому своему клиенту. Окна «клиент писал за 24 часа» больше нет:
        # писать первым можно, лимиты новых чатов держат сами функции отправки.
        if surface == "postcall" and current and str(target) != str(current):
            return REFUSAL_FIRST_MESSAGE
    return None
