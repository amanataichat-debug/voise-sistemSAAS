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
from backend.services import whatsapp_service
from backend.services import chat_split
from backend.services import agent_pause
from backend.services import agent_voice_service
from backend.services.telegram_notification import TelegramNotificationService
from backend.core.timezone_utils import adjust_to_working_hours
from backend.core.pipeline_stages import (
    AGENT_CONTACT_STAGE_KEYS, is_valid_stage,
    get_agent_stages, is_valid_stage_for_agent,
)

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
        "Найти информацию в базе знаний компании: вопросы-ответы, секции о компании, "
        "каталог и ЗАГРУЖЕННЫЕ ВЛАДЕЛЬЦЕМ ФАЙЛЫ (PDF, DOCX, XLSX, TXT, Markdown, "
        "сканированные документы). Это основной инструмент поиска по всему, что "
        "лежит в базе знаний. "
        "КОГДА ВЫЗЫВАТЬ: ОБЯЗАТЕЛЬНО перед тем, как сказать клиенту «не знаю», "
        "«уточню», «не могу предоставить данные» — ЛЮБОЙ фактический вопрос про "
        "компанию (услуги, доступ, условия, документы, сроки, политика, адреса, "
        "контакты, сертификация, партнёры и т.п.) → сначала поиск. "
        "Отвечать сразу (без поиска) можно, только если ответ ЯВНО есть в разделе "
        "«ЗНАНИЯ О КОМПАНИИ» в твоей системной инструкции. "
        "Переформулируй `query` разными словами, если первый поиск пустой "
        "(«тестовая симка» → «тестовый номер», → «демо», → «попробовать звонок»). "
        "Не выдумывай: только после 1–2 попыток с разными формулировками и всё равно "
        "пусто — скажи «уточню у специалиста»."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Что ищем, словами клиента или переформулированное"},
            "top_k": {"type": "integer", "description": "Сколько фрагментов вернуть (по умолчанию 4, увеличь до 8 для широких тем)"},
        },
        "required": ["query"],
    },
}


# Подбор позиций каталога (kind=product в базе знаний). Быстрый поиск в
# Postgres + фильтр по цене: векторный поиск по числам («до 3000») врёт.
FIND_PRODUCTS_TOOL = {
    "type": "function",
    "name": "find_products",
    "description": (
        "Поиск по каталогу товаров и услуг владельца (позиции с ценами, фото, "
        "характеристиками). Вызывай ВСЕГДА, когда клиент спрашивает про что-то, "
        "что мы можем предоставить: товар, услугу, модель, пакет, тариф, доступ, "
        "наличие, цену, тестовый номер, демо, подключение. Даже если вопрос звучит "
        "неявно («у вас есть ...?», «можете показать ...?», «что из услуг ...?», "
        "«как попробовать?») — всё равно вызывай. "
        "Если в системной инструкции уже есть блок «ПРАЙС» — короткие вопросы про "
        "цену/название можно отвечать по нему сразу, без вызова. Но если клиент просит "
        "фото, подробности, характеристики, фильтр по цене — вызывай обязательно. "
        "В ответе: цена, описание, id фото/видео — отправь их клиенту тулзой "
        "whatsapp_send_file / telegram_send_file / instagram_send_file. "
        "Если первая попытка пуста — переформулируй query ещё 1–2 раза синонимами. "
        "Товары вне каталога не придумывай: когда и переформулировки не помогли, "
        "попробуй search_knowledge_base по общей базе; если и там пусто — скажи "
        "«уточню у специалиста»."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Что ищет клиент: услуга, модель, тип товара, сервис"},
            "max_price": {"type": "number", "description": "Верхняя граница цены, если клиент её назвал"},
            "min_price": {"type": "number", "description": "Нижняя граница цены"},
            "limit": {"type": "integer", "description": "Сколько позиций вернуть (по умолчанию 5, можно 10 для широких запросов)"},
        },
        "required": [],
    },
}

# Порог близости для Pinecone живёт в сервисе базы знаний: его применяют и чат,
# и голосовой агент на звонке (ветка 1610-kb-calls), поэтому значение одно.
from backend.services.agent_knowledge_service import KB_MIN_SCORE  # noqa: E402


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


# Проверка оператора по номеру клиента. Телефонный транк пропускает исходящие
# ТОЛЬКО на номера оператора O! (национальные префиксы 050 и 070). Остальные
# операторы (Beeline, Megacom) мы не наберём — агент должен это узнать ДО того,
# как пообещает клиенту звонок, иначе получится «сейчас позвоню → не позвоним».
# Доступно и в чате, и в post-call: решение «звонить/не звонить» принимается в
# обоих контекстах.
CHECK_PHONE_OPERATOR_TOOL = {
    "type": "function",
    "name": "check_phone_operator",
    "description": (
        "Проверить, можно ли позвонить клиенту с нашей телефонии. "
        "Мы умеем звонить ТОЛЬКО на оператор O! (префиксы 050 и 070 в "
        "кыргызском национальном формате; международно 996 500 и 996 700). "
        "ОБЯЗАТЕЛЬНО зови этот инструмент ПРЕЖДЕ ЧЕМ пообещать клиенту «сейчас "
        "позвоню» или назначить звонок: Beeline/Megacom мы не наберём. "
        "Ответ: can_call=true (O! — можно звонить), can_call=false (другой "
        "оператор — не звонить, предложить переписку или входящий звонок на "
        "наш номер) с полем operator и note с объяснением."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "phone": {
                "type": "string",
                "description": "Номер клиента в любом формате: +996…, 996…, 0…, с пробелами или без",
            },
            "agent_contact_id": {
                "type": "string",
                "description": "UUID контакта агента, если номер нужно взять из карточки (альтернатива phone)",
            },
        },
        "required": [],
    },
}


# Узнавание клиента и его номера (ветка 2110-client-memory) — в чате и в PostCall.
FIND_CLIENT_TOOL = {
    "type": "function",
    "name": "find_client",
    "description": (
        "Найти клиента в базе агента по имени, компании, нише или номеру (основному или второму), "
        "в том числе по именам в мессенджерах и свежей переписке. Используй, когда клиент пишет или "
        "звонит с нового номера и называет себя («я Айбек, писал вам вчера», «звонил с другого "
        "номера»). Ответ: confidence (high — это он; medium/low — не наверняка) и карточки с id. "
        "Нашёл того же человека в другой карточке — склей их merge_contacts."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Имя клиента"},
            "company": {"type": "string", "description": "Компания"},
            "niche": {"type": "string", "description": "Чем занимается («стоматология»)"},
            "phone": {"type": "string", "description": "Номер, который назвал клиент"},
            "exclude_contact_id": {
                "type": "string",
                "description": "Не показывать эту карточку (например, текущую — созданную для нового номера)",
            },
        },
        "required": [],
    },
}

MERGE_CONTACTS_TOOL = {
    "type": "function",
    "name": "merge_contacts",
    "description": (
        "Склеить две карточки ОДНОГО клиента в одну (необратимо): звонки, записи, переписки, "
        "задачи и номера лишней карточки переходят в основную, лишняя удаляется. Вызывай только "
        "когда точно один человек: клиент сам назвал прежний номер, или find_client вернул "
        "confidence=high. Основная — карточка с историей (старая), лишняя — новая, созданная "
        "для незнакомого номера. После склейки работай с основной карточкой (main_contact_id)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "main_contact_id": {"type": "string", "description": "UUID основной карточки (остаётся)"},
            "duplicate_contact_id": {"type": "string", "description": "UUID лишней карточки (удаляется)"},
            "reason": {"type": "string", "description": "Почему это один клиент — одной фразой"},
        },
        "required": ["main_contact_id", "duplicate_contact_id", "reason"],
    },
}

ADD_CONTACT_PHONE_TOOL = {
    "type": "function",
    "name": "add_contact_phone",
    "description": (
        "Добавить клиенту второй номер в карточку. Когда клиент в переписке или на звонке дал "
        "другой номер («позвоните на 0700…», «мой второй номер …») — запиши его этим инструментом. "
        "use_for_calls=true — звонить на этот номер (например, WhatsApp на Beeline, а звонить "
        "можно только на O!). call_now=true — сразу и позвонить на него, если клиент просит "
        "позвонить сейчас (действуют обычные правила: только O!, окно звонков, не больше "
        "одного недозвона в день). Если номер уже у другой карточки — ответ подскажет склейку."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent_contact_id": {"type": "string", "description": "UUID карточки клиента"},
            "phone": {"type": "string", "description": "Номер в любом формате: 0700 123 456, +996700123456"},
            "use_for_calls": {"type": "boolean", "description": "Звонить клиенту на этот номер"},
            "call_now": {"type": "boolean", "description": "Сразу позвонить на этот номер"},
        },
        "required": ["agent_contact_id", "phone"],
    },
}


# Тёплая передача клиента админу (ветка 1810-anri-sales-logic). Отдельная
# тулза вместо произвольного send_telegram_notification: гарантирует формат
# карточки «Имя | ниша | нужны [входящие/исходящие] | объём | обсудили | готов
# к [...] | контакт», тянет недостающие поля из CRM автоматически. Нужно,
# чтобы владелец получал один и тот же читаемый дайджест и в чате, и после
# звонка — без «а что это за клиент, где его данные».
NOTIFY_OWNER_HANDOFF_TOOL = {
    "type": "function",
    "name": "notify_owner_handoff",
    "description": (
        "Передать клиента админу: собрать карточку из CRM и отправить в "
        "Telegram-бот владельца. Используй, когда клиент: (1) готов "
        "подключаться (`ready_to_connect`), (2) просит живого человека "
        "(`requested_human`), (3) просит демонстрацию (`demo`), (4) жалоба "
        "или нестандартный случай (`escalation`). Если по клиенту за этот "
        "же эпизод уже отправлял `send_telegram_notification` или "
        "`notify_owner_handoff` — не дублируй."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent_contact_id": {"type": "string", "description": "UUID контакта"},
            "reason": {
                "type": "string",
                "enum": ["ready_to_connect", "requested_human", "demo", "escalation"],
                "description": "Код события для шапки карточки",
            },
            "summary": {
                "type": "string",
                "description": (
                    "Короткий (1-2 строки) контекст: что обсудили, какая боль, "
                    "что ждёт от админа. Если пусто — соберётся из memory контакта."
                ),
            },
            "scheduled_at": {
                "type": "string",
                "description": (
                    "ISO 8601 UTC времени, когда владелец должен связаться с "
                    "клиентом / подключить его, если клиент назвал удобное время "
                    "или оно согласовано. Передавай ВСЕГДА, когда есть конкретное "
                    "время: по нему создаётся задача в «Ближайшие задачи» владельца "
                    "(напоминание ему самому), а для встречи/демо — ещё и событие "
                    "в Google Календаре."
                ),
            },
        },
        "required": ["agent_contact_id", "reason"],
    },
}


# Единый инструмент «отправить коммерческое предложение» (ветка 1810-anri-sales-logic).
# Критичная цепочка после звонка: собрать КП → отправить в тот же канал →
# зафиксировать в CRM → поставить напоминание через 60–90 мин → уведомить
# владельца. 4–5 отдельных шагов легко забыть по одному; эта тулза делает их
# атомарно. Если нужны тонкие настройки (своё расписание напоминания, другой
# канал) — Анри может перейти на пару create_document + send_file + create_agent_task.
SEND_PROPOSAL_TOOL = {
    "type": "function",
    "name": "send_proposal",
    "description": (
        "Собрать коммерческое предложение (КП) PDF, отправить его клиенту в "
        "последний активный канал, записать в CRM, поставить напоминание через "
        "60–90 минут и уведомить владельца — одной командой. "
        "ВАЖНО: вызывай эту тулзу после звонка (PostCall), когда клиент не "
        "отказался явно, или когда в чате владелец просит отправить КП конкретному "
        "клиенту. В поле `content` клади готовый текст КП в markdown (разделы, "
        "выгоды, следующий шаг, контакты). Платформа сама соберёт PDF фирменного "
        "стиля агента, выберет канал по последней активности клиента, запланирует "
        "напоминание и напишет владельцу одной строкой."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent_contact_id": {"type": "string", "description": "UUID контакта"},
            "content": {
                "type": "string",
                "description": (
                    "Текст КП в markdown. Пиши под нишу клиента и его запрос (ниша, "
                    "объём, что именно его интересует — входящие/исходящие/всё). "
                    "Не копируй весь прайс, оставь только релевантные блоки."
                ),
            },
            "title": {
                "type": "string",
                "description": "Заголовок документа (например «Коммерческое предложение Voksy AI для [компании]»)",
            },
            "channel": {
                "type": "string",
                "enum": ["telegram", "whatsapp", "instagram"],
                "description": (
                    "В какой канал отправить КП. По умолчанию — последний активный у клиента."
                ),
            },
            "caption": {
                "type": "string",
                "description": "Короткая подпись к файлу (например «[Имя], как договаривались — коммерческое»)",
            },
            "reminder_text": {
                "type": "string",
                "description": (
                    "Текст напоминания через 60–90 минут. По умолчанию: «[Имя], получилось глянуть "
                    "предложение? Что скажете?»"
                ),
            },
            "owner_note": {
                "type": "string",
                "description": (
                    "Короткая строка владельцу в Telegram (одна фраза с итогом). По умолчанию "
                    "соберётся из данных контакта."
                ),
            },
        },
        "required": ["agent_contact_id", "content"],
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
    cascade / fish / eleven).
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
    if a_type == "eleven":
        return {"eleven_assistant_id": vid}
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


def _patch_move_stage_tool_for_agent(tools: list, agent_config) -> list:
    """
    Подменяет move_contact_stage на вариант с актуальными стадиями агента.
    У владельца может быть кастомный набор (CRM v2, agent_configs.pipeline_stages).
    Без этого агент не знал бы про новые стадии и ругался бы в enum.
    """
    stages = get_agent_stages(agent_config)
    if not stages:
        return tools
    keys = [s["key"] for s in stages]
    # Короткое описание со списком стадий для подсказки модели
    desc_stages = ", ".join([f"{s['key']} ({s['label']})" for s in stages])
    out = []
    for t in tools:
        # Chat Completions формат: {type, function: {name, description, parameters}}.
        fn = t.get("function") if isinstance(t, dict) else None
        if fn and fn.get("name") == "move_contact_stage":
            # Копируем, чтобы не мутировать общий MOVE_CONTACT_STAGE_TOOL
            new_fn = dict(fn)
            new_fn["description"] = (
                "Перевести контакт на стадию воронки продаж. "
                f"Доступные стадии этого агента: {desc_stages}. "
                "Вызывай только когда есть реальное основание сменить стадию. Если "
                "ничего по сути не изменилось (не дозвонились, клиент ещё думает) — "
                "НЕ вызывай."
            )
            params = dict(new_fn.get("parameters") or {})
            props = dict(params.get("properties") or {})
            props["stage"] = {
                "type": "string",
                "enum": keys,
                "description": "Ключ стадии воронки из списка выше",
            }
            params["properties"] = props
            new_fn["parameters"] = params
            out.append({**t, "function": new_fn})
        else:
            out.append(t)
    return out


async def build_chat_tools(agent_config, db: Session) -> list:
    """
    Tools для чата/Telegram оркестратора (Chat Completions формат): базовый
    AGENT_CHAT_TOOLS + коннекторы Composio + личный Telegram + Instagram +
    WhatsApp (последние три — только если подключены). move_contact_stage
    подменяется на актуальные стадии агента (кастомные CRM v2).
    """
    tools = await _augment_with_connectors(
        to_chat_completions_tools(AGENT_CHAT_TOOLS), agent_config, db
    )
    tools = _augment_with_files(tools, agent_config)
    tools = _augment_with_telegram_account(tools, agent_config, db)
    tools = _augment_with_instagram(tools, agent_config, db)
    tools = _augment_with_whatsapp(tools, agent_config, db)
    tools = _augment_with_voice(tools, agent_config, db)
    return _patch_move_stage_tool_for_agent(tools, agent_config)


async def build_postcall_tools(agent_config, db: Session) -> list:
    """Tools для PostCall-анализа: AGENT_POSTCALL_TOOLS + коннекторы + файлы + личный Telegram + Instagram + WhatsApp + подмена move_contact_stage под кастомные стадии."""
    tools = await _augment_with_connectors(
        to_chat_completions_tools(AGENT_POSTCALL_TOOLS), agent_config, db
    )
    tools = _augment_with_files(tools, agent_config)
    tools = _augment_with_telegram_account(tools, agent_config, db)
    tools = _augment_with_instagram(tools, agent_config, db)
    tools = _augment_with_whatsapp(tools, agent_config, db)
    tools = _augment_with_voice(tools, agent_config, db)
    return _patch_move_stage_tool_for_agent(tools, agent_config)


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
        "банит за спам незнакомым). Пиши как живой человек, без markdown. "
        "Реплики разделяй строкой «---» — каждая уйдёт отдельным сообщением."
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

TELEGRAM_SEND_FILE_TOOL = {
    "type": "function",
    "name": "telegram_send_file",
    "description": (
        "Отправить клиенту ФАЙЛ в Telegram с личного аккаунта владельца: файл из "
        "библиотеки агента (прайс, презентация, договор — list_agent_files), "
        "созданный тобой документ (create_document) или вложение клиента. "
        "Картинки уходят как фото, видео — видео, остальное — документом. Так же "
        "отправляются фото и видео товара: id берутся из media_file_ids в ответе "
        "find_products. caption — короткая подпись (необязательно). Получатель резолвится так же, как в "
        "telegram_send_message."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent_contact_id": {"type": "string", "description": "UUID контакта агента"},
            "username": {"type": "string", "description": "Telegram @username получателя (если известен)"},
            "file_id": {"type": "string", "description": "id файла из list_agent_files / create_document"},
            "caption": {"type": "string", "description": "Подпись к файлу"},
        },
        "required": ["file_id"],
    },
}

TELEGRAM_USER_TOOLS = [
    TELEGRAM_SEND_MESSAGE_TOOL,
    TELEGRAM_SEND_FILE_TOOL,
    TELEGRAM_GET_THREAD_TOOL,
    SCHEDULE_TELEGRAM_MESSAGE_TOOL,
]


# ============================================================================
# ГОЛОСОВЫЕ СООБЩЕНИЯ (ElevenLabs) — личный Telegram и WhatsApp.
# Тулза домешивается в чат и PostCall, когда хотя бы в одном из этих каналов
# режим voice_reply_mode не «never» и у агента есть голос (см.
# agent_voice_service.resolve_voice). Instagram голосовые не поддерживает.
# ============================================================================

SEND_VOICE_MESSAGE_TOOL = {
    "type": "function",
    "name": "send_voice_message",
    "description": (
        "Отправить клиенту ГОЛОСОВОЕ сообщение (озвучка голосом агента) в личный Telegram или "
        "WhatsApp. Пиши так, как живой человек спокойно наговаривает голосовое на телефон: "
        "на языке клиента (русский или кыргызский — чистый литературный, без диалекта и без "
        "смешения языков), простыми понятными словами, без канцелярита и терминов; "
        "разговорно, неторопливо, связными фразами, с паузами «…» и лёгкими раздумьями («так…», "
        "«смотрите…», «ну вот»), без «алло», без дикторских и рекламных оборотов, без ссылок, "
        "эмодзи, списков и markdown. До ~400 символов (≈30 секунд). Можно 1–3 звуковые метки в "
        "квадратных скобках: [softly], [calm], [thoughtful], [warmly], [short pause], [long pause], "
        "[exhales], [sighs], [chuckles], [hesitates] — например «[softly] Так, смотрите… [short "
        "pause] доставка у нас завтра». Данные, которые удобно прочитать или скопировать (адрес, "
        "цены, номера, ссылки, список вопросов), отправь ОТДЕЛЬНЫМ текстом тем же каналом — "
        "только сами данные, НЕ пересказывая голосовое. Режим «в ответ на голосовое» — голосом "
        "можно, только если последнее сообщение клиента было голосовым."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent_contact_id": {"type": "string", "description": "UUID контакта агента"},
            "text": {"type": "string", "description": "Что произнести (на языке клиента)"},
            "channel": {"type": "string", "enum": ["telegram", "whatsapp"],
                        "description": "Канал; по умолчанию — тот, где клиент писал последним"},
        },
        "required": ["agent_contact_id", "text"],
    },
}


def _voice_channels(agent_config, db: Session) -> dict:
    """{channel: account} подключённых каналов, где голосовые разрешены настройкой."""
    out = {}
    try:
        if telegram_user_service.account_connected(db, agent_config.id):
            acc = telegram_user_service.get_account_for_agent(db, agent_config.id)
            if acc is not None and agent_voice_service.voice_reply_mode(acc) != "never":
                out["telegram"] = acc
    except Exception as e:
        logger.warning(f"[AGENT-TOOLS] voice: telegram lookup failed: {e}")
    try:
        if whatsapp_service.connected(db, agent_config.id):
            acc = whatsapp_service.get_account_for_agent(db, agent_config.id)
            if acc is not None and agent_voice_service.voice_reply_mode(acc) != "never":
                out["whatsapp"] = acc
    except Exception as e:
        logger.warning(f"[AGENT-TOOLS] voice: whatsapp lookup failed: {e}")
    return out


def _augment_with_voice(base_tools: list, agent_config, db: Session) -> list:
    if agent_config is None or not agent_voice_service.any_engine_configured():
        return base_tools
    try:
        if not _voice_channels(agent_config, db) or agent_voice_service.resolve_voice(db, agent_config) is None:
            return base_tools
    except Exception as e:
        logger.warning(f"[AGENT-TOOLS] voice tool check failed: {e}")
        return base_tools
    return base_tools + to_chat_completions_tools([SEND_VOICE_MESSAGE_TOOL])


def _repeats_voice(tool_name: str, tool_args: dict, db: Session) -> bool:
    """Текстовое сообщение клиенту почти повторяет голосовое, только что отправленное ему."""
    contact_id = tool_args.get("agent_contact_id")
    text = tool_args.get("text") or ""
    if not contact_id or not text:
        return False
    try:
        channel = "telegram" if tool_name.startswith("telegram") else "whatsapp"
        return agent_voice_service.repeats_recent_voice(_thread_for(channel, db, contact_id), text)
    except Exception as e:
        logger.warning(f"[AGENT-TOOLS] repeat check failed: {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return False


def _thread_for(channel: str, db: Session, contact_id) -> list:
    if channel == "telegram":
        return telegram_user_service.get_thread(db, contact_id, limit=10)
    return whatsapp_service.get_thread(db, contact_id, limit=10)


def _last_inbound_at(rows):
    for m in reversed(list(rows or [])):
        if (m.direction or "inbound") == "inbound":
            return getattr(m, "sent_at", None) or m.created_at
    return None


async def fn_send_voice_message(args: dict, user_id: str, agent_config, db: Session) -> dict:
    from backend.core.config import settings as _settings

    if agent_config is None:
        return {"ok": False, "error": "Агент не найден"}
    text = (args.get("text") or "").strip()
    spoken = agent_voice_service.strip_tags(text)
    if not spoken:
        return {"ok": False, "error": "Пустой текст голосового"}
    if len(spoken) > _settings.AGENT_VOICE_MAX_CHARS:
        return {"ok": False, "error": (
            f"Слишком длинно для голосового ({len(spoken)} симв.). Сократи до ~400 символов, "
            "подробности отправь текстом.")}
    contact = db.query(AgentContact).filter(
        AgentContact.id == args.get("agent_contact_id"),
        AgentContact.user_id == user_id,
        AgentContact.agent_config_id == agent_config.id,
    ).first()
    if not contact:
        return {"ok": False, "error": "Контакт не найден"}

    channels = _voice_channels(agent_config, db)
    if not channels:
        return {"ok": False, "error": "Голосовые ответы выключены в настройках Telegram и WhatsApp — ответь текстом"}
    channel = args.get("channel")
    if channel and channel not in ("telegram", "whatsapp"):
        return {"ok": False, "error": "Голосовые можно отправить только в Telegram или WhatsApp"}
    threads = {ch: _thread_for(ch, db, contact.id) for ch in channels}
    if not channel:
        dated = [(ch, _last_inbound_at(rows)) for ch, rows in threads.items()]
        dated = [x for x in dated if x[1] is not None]
        channel = max(dated, key=lambda x: x[1])[0] if dated else next(iter(channels))
    if channel not in channels:
        return {"ok": False, "error": f"Голосовые в {channel} выключены в настройках или канал не подключён — ответь текстом"}
    account = channels[channel]
    if agent_voice_service.voice_reply_mode(account) == "on_voice" \
            and not agent_voice_service.last_inbound_is_voice(threads.get(channel)):
        return {"ok": False, "error": (
            "Голосом отвечаем только на голосовые клиента (настройка канала) — ответь текстом")}

    voice = agent_voice_service.resolve_voice(db, agent_config)
    if voice is None:
        return {"ok": False, "error": "У агента не выбран голос для голосовых сообщений — ответь текстом"}
    audio = await agent_voice_service.synthesize(text, voice)
    if not audio.get("ok"):
        return {"ok": False, "error": f"Не удалось озвучить ({audio.get('error')}) — ответь текстом"}

    send_args = {"agent_contact_id": str(contact.id), "text": spoken}
    if channel == "telegram":
        result = await fn_telegram_send_message(send_args, user_id, agent_config, db, voice=audio)
    else:
        result = await fn_whatsapp_send_message(send_args, user_id, agent_config, db, voice=audio)
    if not result.get("ok"):
        return result
    credits = agent_voice_service.charge(db, contact.user_id or user_id, spoken, ref_id=contact.id,
                                         engine=voice.get("engine") or "eleven",
                                         seconds=audio.get("seconds"))
    logger.info(f"[AGENT-TOOLS] send_voice_message → {channel} contact={contact.id} engine={voice.get('engine')} "
                f"{len(spoken)} chars, {audio.get('duration')}s, ogg={audio.get('is_ogg')}, credits={credits}")
    return dict(result, channel=channel, sent_as=("voice" if audio.get("is_ogg") or channel == "whatsapp" else "audio"),
                duration_seconds=audio.get("duration") or None)


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
        "живой человек, коротко, без markdown. Реплики разделяй строкой «---» "
        "— каждая уйдёт отдельным сообщением."
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

INSTAGRAM_SEND_FILE_TOOL = {
    "type": "function",
    "name": "instagram_send_file",
    "description": (
        "Отправить клиенту ФАЙЛ в Instagram Direct (файл из list_agent_files, "
        "документ из create_document, фото товара из media_file_ids в ответе "
        "find_products или вложение клиента). Картинки уходят "
        "фото; PDF, документы, таблицы и аудио Instagram напрямую не принимает — "
        "они уйдут ссылкой на скачивание (действует 7 дней). Те же ограничения, "
        "что у instagram_send_message: только ответ клиенту в течение 24 часов."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent_contact_id": {"type": "string", "description": "UUID контакта агента"},
            "file_id": {"type": "string", "description": "id файла из list_agent_files / create_document"},
            "caption": {"type": "string", "description": "Подпись / сопроводительный текст"},
        },
        "required": ["agent_contact_id", "file_id"],
    },
}

INSTAGRAM_TOOLS = [
    INSTAGRAM_SEND_MESSAGE_TOOL,
    INSTAGRAM_SEND_FILE_TOOL,
    INSTAGRAM_GET_THREAD_TOOL,
]


# ============================================================================
# WHATSAPP TOOLS — номер WhatsApp владельца (Evolution API, неофициально).
# Домешиваются в чат и PostCall, ТОЛЬКО когда номер подключён. Голосовому
# ассистенту эти функции намеренно НЕ отдаются. В отличие от Instagram, писать
# первым можно (по номеру телефона контакта), но с лимитами: новых чатов в
# сутки (account.daily_new_chats_limit) и исходящих в час — защита от бана.
# ============================================================================

WHATSAPP_SEND_MESSAGE_TOOL = {
    "type": "function",
    "name": "whatsapp_send_message",
    "description": (
        "Отправить клиенту сообщение в WhatsApp с номера владельца. Можно "
        "отвечать в существующем чате и писать ПЕРВЫМ — по номеру телефона "
        "контакта. Укажи agent_contact_id (предпочтительно) или phone (если "
        "контакта ещё нет — он будет создан). Новых чатов в сутки — ограниченное "
        "число (защита номера от бана): не рассылай одинаковые сообщения по "
        "холодной базе, пиши персонально и по делу. Пиши как живой человек, "
        "коротко, без markdown. Реплики разделяй строкой «---» — каждая уйдёт "
        "отдельным сообщением."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent_contact_id": {"type": "string", "description": "UUID контакта агента"},
            "phone": {"type": "string", "description": "Номер телефона получателя (если нет agent_contact_id), например +996700123456"},
            "text": {"type": "string", "description": "Текст сообщения"},
        },
        "required": ["text"],
    },
}

WHATSAPP_SEND_FILE_TOOL = {
    "type": "function",
    "name": "whatsapp_send_file",
    "description": (
        "Отправить клиенту ФАЙЛ в WhatsApp (файл из list_agent_files, документ "
        "из create_document, фото/видео товара из media_file_ids в ответе "
        "find_products или вложение клиента). Картинки и видео уходят медиа, "
        "остальное — документом. caption — подпись (необязательно). "
        "Получатель и лимиты — как у whatsapp_send_message."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent_contact_id": {"type": "string", "description": "UUID контакта агента"},
            "phone": {"type": "string", "description": "Номер телефона получателя (если нет agent_contact_id)"},
            "file_id": {"type": "string", "description": "id файла из list_agent_files / create_document"},
            "caption": {"type": "string", "description": "Подпись к файлу"},
        },
        "required": ["file_id"],
    },
}

WHATSAPP_GET_THREAD_TOOL = {
    "type": "function",
    "name": "whatsapp_get_thread",
    "description": (
        "Получить последние сообщения WhatsApp-переписки с контактом — для "
        "контекста перед ответом."
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

WHATSAPP_TOOLS = [
    WHATSAPP_SEND_MESSAGE_TOOL,
    WHATSAPP_SEND_FILE_TOOL,
    WHATSAPP_GET_THREAD_TOOL,
]


# ============================================================================
# FILE TOOLS — файлы агента (agent_files): библиотека владельца, вложения
# клиентов (уже распознанные: голос → текст, фото → OCR) и документы, которые
# агент собирает сам. Доступны всегда; отправка клиенту — telegram_send_file /
# instagram_send_file / whatsapp_send_file (домешиваются вместе с тулзами своего канала).
# ============================================================================

LIST_AGENT_FILES_TOOL = {
    "type": "function",
    "name": "list_agent_files",
    "description": (
        "Список файлов агента: библиотека владельца (прайсы, презентации, "
        "договоры — с описанием, когда их отправлять), документы, созданные "
        "тобой, и вложения от клиентов (голосовые, фото, документы). Вызывай, "
        "когда клиент просит прайс/каталог/договор или нужно найти присланный "
        "клиентом файл.\n"
        "Коммерческое предложение, счёт и расчёт отсюда НЕ пересылай: такой "
        "документ сделан под другого клиента и под его условия. Для нового "
        "клиента всегда собирай свежий через create_document."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "enum": ["library", "generated", "inbound", "all"],
                "description": "library — библиотека владельца (по умолчанию), generated — созданные агентом, inbound — от клиентов, all — все",
            },
            "agent_contact_id": {"type": "string", "description": "Только файлы этого контакта (для inbound/generated)"},
            "limit": {"type": "integer", "description": "Сколько файлов (по умолчанию 30)"},
        },
    },
}

READ_AGENT_FILE_TOOL = {
    "type": "function",
    "name": "read_agent_file",
    "description": (
        "Прочитать содержимое файла: полный текст документа, расшифровку "
        "голосового, распознанный текст и описание картинки. Для длинных "
        "документов читай частями через offset."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "file_id": {"type": "string", "description": "id файла"},
            "offset": {"type": "integer", "description": "С какого символа читать (по умолчанию 0)"},
            "max_chars": {"type": "integer", "description": "Сколько символов (по умолчанию 8000, максимум 20000)"},
        },
        "required": ["file_id"],
    },
}

CREATE_DOCUMENT_TOOL = {
    "type": "function",
    "name": "create_document",
    "description": (
        "Создать документ для клиента или владельца: коммерческое предложение, "
        "счёт, расчёт, памятку, таблицу. Форматы: pdf (по умолчанию), docx, "
        "xlsx, csv, txt. PDF собирается по фирменному стилю бизнеса (шапка с "
        "логотипом и цветом, оформленные секции, таблица, блок призыва) — тебе "
        "оформление задавать не нужно, только содержание.\n"
        "content — текст: строки с «# » — заголовки секций, «- » — пункты списка, "
        "«1. » — нумерованные шаги, **жирный** внутри строки. table — таблица "
        "(первая строка — заголовки), для КП это прайс; обязательна для xlsx/csv.\n"
        "Преимущества оформляй карточками: пункты вида «- **Короткий заголовок** — "
        "пояснение одной фразой» (от двух подряд) рисуются плитками с галочками, "
        "это выглядит заметно лучше обычного списка.\n"
        "image_file_ids — id картинок из list_agent_files (фото товара, объекта, "
        "примеров работ), до 4 штук: они встанут в документ. cover — титульная "
        "страница с крупным логотипом; по умолчанию решается само (у большого КП "
        "и презентации — есть, у короткой памятки — нет).\n"
        "Коммерческое предложение пиши ПОД КЛИЕНТА, а не шаблоном: секция о его "
        "задаче — словами из вашего разговора (что он просил, какой объём, что "
        "его беспокоит), затем что предлагаем именно под эту задачу, затем "
        "стоимость (таблицей), затем секция «Следующий шаг» — она рисуется "
        "выделенным блоком. Два разных клиента не должны получить одинаковый "
        "текст. Не выдумывай цены и условия — бери их из базы знаний и "
        "библиотеки файлов; если цены нет, напиши, что рассчитаете после "
        "уточнения, и не придумывай цифру.\n"
        "Возвращает file_id — отправь его клиенту через telegram_send_file / "
        "instagram_send_file / whatsapp_send_file."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Заголовок и имя файла"},
            "content": {"type": "string", "description": "Текст документа"},
            "format": {"type": "string", "enum": ["pdf", "docx", "xlsx", "csv", "txt"]},
            "table": {
                "type": "array",
                "description": "Таблица: массив строк, каждая — массив ячеек; первая строка — заголовки",
                "items": {"type": "array", "items": {"type": "string"}},
            },
            "agent_contact_id": {"type": "string", "description": "UUID контакта, для которого документ (если есть) — его имя попадёт в шапку «Для: …»"},
            "kind_label": {"type": "string", "description": "Тип документа в углу шапки: «Коммерческое предложение», «Счёт», «Памятка»"},
            "image_file_ids": {
                "type": "array",
                "description": "id картинок из list_agent_files (до 4) — фото товара, объекта, примеров работ",
                "items": {"type": "string"},
            },
            "cover": {"type": "boolean", "description": "Титульная страница с логотипом. Не передавай — решится само"},
        },
        "required": ["title"],
    },
}

AGENT_FILE_TOOLS = [
    LIST_AGENT_FILES_TOOL,
    READ_AGENT_FILE_TOOL,
    CREATE_DOCUMENT_TOOL,
]


def _augment_with_files(base_tools: list, agent_config) -> list:
    """Дописать тулзы файлов агента (не зависят от коннекторов)."""
    if agent_config is None:
        return base_tools
    return base_tools + to_chat_completions_tools(AGENT_FILE_TOOLS)


def _get_agent_file(db: Session, agent_config_id, file_id):
    """Файл агента по id (только свой агент) или None."""
    from backend.models.agent_file import AgentFile
    try:
        import uuid as _uuid
        fid = _uuid.UUID(str(file_id))
    except (ValueError, TypeError):
        return None
    return db.query(AgentFile).filter(
        AgentFile.id == fid,
        AgentFile.agent_config_id == agent_config_id,
    ).first()


async def fn_list_agent_files(args: dict, agent_config_id: str, db: Session) -> dict:
    from backend.models.agent_file import AgentFile
    source = (args.get("source") or "library").lower()
    q = db.query(AgentFile).filter(AgentFile.agent_config_id == agent_config_id)
    if source in ("library", "generated", "inbound"):
        q = q.filter(AgentFile.source == source)
    if args.get("agent_contact_id"):
        q = q.filter(AgentFile.agent_contact_id == args["agent_contact_id"])
    limit = min(int(args.get("limit") or 30), 100)
    rows = q.order_by(AgentFile.created_at.desc()).limit(limit).all()
    files = []
    for r in rows:
        item = {
            "file_id": str(r.id),
            "source": r.source,
            "kind": r.kind,
            "filename": r.filename,
            "title": r.title,
            "description": r.description,
            "status": r.status,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        if r.agent_contact_id:
            item["agent_contact_id"] = str(r.agent_contact_id)
        if r.extracted_text:
            item["text_preview"] = r.extracted_text[:200]
        files.append(item)
    return {"ok": True, "count": len(files), "files": files}


async def fn_read_agent_file(args: dict, agent_config_id: str, db: Session) -> dict:
    row = _get_agent_file(db, agent_config_id, args.get("file_id"))
    if row is None:
        return {"ok": False, "error": "Файл не найден"}
    text = row.extracted_text or ""
    offset = max(0, int(args.get("offset") or 0))
    max_chars = min(max(500, int(args.get("max_chars") or 8000)), 20000)
    chunk = text[offset:offset + max_chars]
    out = {
        "ok": True,
        "file_id": str(row.id),
        "filename": row.filename,
        "kind": row.kind,
        "status": row.status,
        "total_chars": len(text),
        "offset": offset,
        "text": chunk,
    }
    if offset + max_chars < len(text):
        out["next_offset"] = offset + max_chars
    if not text:
        out["note"] = f"Текста нет ({row.error or row.status})"
    return out


async def _document_brand(agent_config_id: str, db: Session) -> dict:
    """
    Фирменный стиль для PDF из карточки агента: название, цвет, контакты,
    байты логотипа. Логотип лежит обычным файлом агента (agent_files), поэтому
    грузится и удаляется теми же ручками, что и остальная библиотека.
    Ничего не заполнено → пустой бренд: документ оформится нейтрально.
    """
    from backend.models.agent_file import AgentFile
    from backend.services import agent_media_service

    cfg = db.query(AgentConfig).filter(AgentConfig.id == agent_config_id).first()
    if cfg is None:
        return {}
    brand = {
        "company_name": (cfg.brand_company_name or "").strip() or None,
        "color": (cfg.brand_color or "").strip() or None,
        "contacts": (cfg.brand_contacts or "").strip() or None,
    }
    if cfg.brand_logo_file_id:
        logo = db.query(AgentFile).filter(
            AgentFile.id == cfg.brand_logo_file_id,
            AgentFile.agent_config_id == agent_config_id,
        ).first()
        if logo is not None:
            try:
                brand["logo_bytes"] = await agent_media_service.load_bytes(logo)
            except Exception as e:
                logger.warning(f"[DOC] логотип {cfg.brand_logo_file_id} не загружен: {e}")
    return brand


async def _document_images(file_ids, agent_config_id: str, db: Session) -> list:
    """
    Картинки для документа: файлы библиотеки агента (фото товара, объекта).
    Не картинки и чужие файлы молча пропускаем — документ важнее, чем ошибка
    из-за одной неудачной ссылки. Не больше 4, чтобы КП не превратилось в альбом.
    """
    from backend.models.agent_file import AgentFile
    from backend.services import agent_media_service

    if not isinstance(file_ids, list):
        return []
    out = []
    for fid in file_ids[:4]:
        row = db.query(AgentFile).filter(
            AgentFile.id == str(fid),
            AgentFile.agent_config_id == agent_config_id,
        ).first()
        # mime_type, а не mime: из-за опечатки картинки в КП (image_file_ids)
        # падали с AttributeError и в документ не попадали никогда.
        if row is None or not (row.mime_type or "").startswith("image/"):
            continue
        try:
            data = await agent_media_service.load_bytes(row)
        except Exception as e:
            logger.warning(f"[DOC] картинка {fid} не загружена: {e}")
            continue
        if data:
            out.append({"bytes": data, "caption": (row.title or "").strip() or None})
    return out


async def fn_create_document(args: dict, user_id: str, agent_config_id: str, db: Session) -> dict:
    from backend.services import agent_media_service
    contact_id = args.get("agent_contact_id") or None
    client_name = None
    if contact_id:
        contact = db.query(AgentContact).filter(
            AgentContact.id == contact_id,
            AgentContact.agent_config_id == agent_config_id,
        ).first()
        if contact is None:
            contact_id = None
        else:
            client_name = (contact.name or "").strip() or None
    table = args.get("table")
    if table is not None and not isinstance(table, list):
        table = None
    images = await _document_images(args.get("image_file_ids"), agent_config_id, db)
    cover = args.get("cover")
    try:
        row = await agent_media_service.create_document(
            db,
            user_id=user_id,
            agent_config_id=agent_config_id,
            title=(args.get("title") or "Документ").strip(),
            content=args.get("content") or "",
            fmt=args.get("format") or "pdf",
            table=table,
            agent_contact_id=contact_id,
            brand=await _document_brand(agent_config_id, db),
            client_name=client_name,
            kind_label=(args.get("kind_label") or "").strip(),
            images=images,
            cover=cover if isinstance(cover, bool) else None,
        )
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    if not row.storage_key:
        return {"ok": False, "error": "Хранилище файлов (R2) не настроено — документ не сохранён"}
    return {"ok": True, "file_id": str(row.id), "filename": row.filename, "size_bytes": row.size_bytes}


async def fn_telegram_send_file(args: dict, user_id: str, agent_config, db: Session) -> dict:
    from backend.services import agent_media_service
    if agent_config is None:
        return {"ok": False, "error": telegram_user_service.error_human("not_connected")}
    row = _get_agent_file(db, agent_config.id, args.get("file_id"))
    if row is None:
        return {"ok": False, "error": "Файл не найден"}
    data = await agent_media_service.load_bytes(row)
    if not data:
        return {"ok": False, "error": "Файл недоступен в хранилище"}
    send_args = dict(args, text=(args.get("caption") or "").strip())
    return await fn_telegram_send_message(send_args, user_id, agent_config, db, file_row=row, file_bytes=data)


async def fn_instagram_send_file(args: dict, user_id: str, agent_config, db: Session) -> dict:
    if agent_config is None:
        return {"ok": False, "error": "Коннектор Instagram не подключён"}
    row = _get_agent_file(db, agent_config.id, args.get("file_id"))
    if row is None:
        return {"ok": False, "error": "Файл не найден"}
    if not row.storage_key:
        return {"ok": False, "error": "Файл недоступен в хранилище"}
    send_args = dict(args, text=(args.get("caption") or "").strip())
    return await fn_instagram_send_message(send_args, user_id, agent_config, db, file_row=row)


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


# Ответ клиенту, который сам написал за последние сутки, банами почти не грозит —
# для него большой лимит; «написать первым» остаётся на строгих лимитах канала.
# Плюс предохранитель от зацикливания (например, переписки с чужим ботом).
CHAT_REPLY_WINDOW = timedelta(hours=24)
CHAT_PER_CONTACT_LIMIT = 30
CHAT_PER_CONTACT_WINDOW = timedelta(minutes=10)
CHAT_PER_CONTACT_ERROR = ("Слишком много сообщений этому клиенту за 10 минут — пауза "
                          "(защита от спама и зацикливания)")


def _chat_limit_hit(db: Session, model, scope: list, contact_id, first_limit: int,
                    reply_limit: int, sent_filter=None) -> Optional[str]:
    """None — можно слать; "hourly" — почасовой лимит; "per_contact" — предохранитель."""
    now = datetime.utcnow()
    is_reply = contact_id is not None and db.query(model.id).filter(
        *scope, model.agent_contact_id == contact_id, model.direction == "inbound",
        model.created_at >= now - CHAT_REPLY_WINDOW,
    ).first() is not None
    sent = db.query(model).filter(*scope, model.direction == "outbound")
    if sent_filter is not None:
        sent = sent.filter(sent_filter)
    if sent.filter(model.created_at >= now - timedelta(hours=1)).count() >= (reply_limit if is_reply else first_limit):
        return "hourly"
    if contact_id is not None and sent.filter(
        model.agent_contact_id == contact_id, model.created_at >= now - CHAT_PER_CONTACT_WINDOW,
    ).count() >= CHAT_PER_CONTACT_LIMIT:
        return "per_contact"
    return None


# Streak-лимит (ветка 1810-anri-sales-logic): «не больше 2 исходящих в
# бишкекский день подряд без ответа клиента». В ритме касаний Анри
# день 1 допускает до 2 сообщений, день 2 — 1 сообщение + 1 звонок, и т.д.;
# за один календарный день не нужно слать клиенту третье подряд, пока он
# молчит. Как только клиент ответит в тот же день — лимит сбрасывается.
# skip_rate_limit (от chat_split для многочастных сообщений) эту проверку
# тоже пропускает, иначе части одного сообщения считались бы по отдельности.
OUTBOUND_STREAK_LIMIT = 2
OUTBOUND_STREAK_HINT = (
    "Клиент сегодня не отвечает — ему уже ушло {count} сообщений подряд в этом канале. "
    "Не отправляй третье: подожди ответа или смени канал. Если это запланированное "
    "напоминание — перенеси на завтра через create_agent_task."
)


def _today_outbound_streak(db: Session, model, agent_contact_id, extra_filters=()) -> int:
    """Сколько исходящих ушло клиенту сегодня (Бишкек) без ответа после них.

    Считаем outbound за сегодняшний бишкекский день. Если в этом же дне есть
    более поздний inbound, он сбрасывает счётчик (клиент ответил — агенту снова
    можно до двух исходящих). Возвращает 0, если контакт не задан или ничего
    не было.
    """
    if agent_contact_id is None:
        return 0
    from backend.core.timezone_utils import BISHKEK
    today_start_local = datetime.now(BISHKEK).replace(hour=0, minute=0, second=0, microsecond=0)
    today_start_utc = today_start_local.astimezone(timezone.utc).replace(tzinfo=None)
    base = db.query(model).filter(
        model.agent_contact_id == agent_contact_id,
        model.created_at >= today_start_utc,
        *extra_filters,
    )
    last_inbound = base.filter(model.direction == "inbound").order_by(model.created_at.desc()).first()
    q = base.filter(model.direction == "outbound")
    if last_inbound is not None:
        q = q.filter(model.created_at > last_inbound.created_at)
    return q.count()


def _streak_block(count: int) -> Optional[dict]:
    """None, если слать можно; dict-отказ, если streak уже достиг лимита."""
    if count < OUTBOUND_STREAK_LIMIT:
        return None
    return {
        "ok": False,
        "error": "streak_without_reply",
        "hint": OUTBOUND_STREAK_HINT.format(count=count),
    }


async def fn_instagram_send_message(args: dict, user_id: str, agent_config, db: Session,
                                    file_row=None, skip_rate_limit: bool = False) -> dict:
    """
    Отправка DM с бизнес-аккаунта Instagram владельца (через Composio).
    Только ответ в существующем треде (IGSID резолвится из
    agent_instagram_conversations); почасовой анти-спам лимит.
    С file_row отправляет файл (instagram_send_file): картинку — как фото,
    остальное — ссылкой на скачивание (Composio-тулкит шлёт только текст и
    изображения); text — подпись.
    """
    from backend.models.agent_instagram import AgentInstagramMessage

    if agent_config is None:
        return {"ok": False, "error": "Коннектор Instagram не подключён"}
    connector = instagram_service.connector_for_agent(db, agent_config.id)
    if connector is None:
        return {"ok": False, "error": "Коннектор Instagram не подключён"}

    text = (args.get("text") or "").strip()
    if not text and file_row is None:
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

    if not skip_rate_limit:
        streak = _today_outbound_streak(
            db, AgentInstagramMessage, contact.id,
            extra_filters=(AgentInstagramMessage.agent_config_id == agent_config.id,),
        )
        blocked = _streak_block(streak)
        if blocked is not None:
            return blocked

    hit = None if skip_rate_limit else _chat_limit_hit(
        db, AgentInstagramMessage, [AgentInstagramMessage.agent_config_id == agent_config.id], contact.id,
        instagram_service.IG_SEND_HOURLY_LIMIT, instagram_service.IG_SEND_HOURLY_LIMIT,
    )
    if hit == "hourly":
        return {"ok": False, "error": "Достигнут почасовой лимит исходящих Instagram-сообщений"}
    if hit == "per_contact":
        return {"ok": False, "error": CHAT_PER_CONTACT_ERROR}

    composio_user_id = connector.composio_user_id or composio_service.composio_user_id_for_agent(agent_config.id)
    body = text
    sent_as = None
    if file_row is None:
        result = await instagram_service.send_text(composio_user_id, conversation.igsid, text)
    else:
        from backend.services import agent_media_service
        if file_row.kind == "image":
            link = agent_media_service.file_link(file_row, expires_seconds=3600)
            if not link:
                return {"ok": False, "error": "Файл недоступен в хранилище"}
            result = await instagram_service.send_image(composio_user_id, conversation.igsid, link)
            if result.get("ok") and text:
                await instagram_service.send_text(composio_user_id, conversation.igsid, text)
            sent_as = "image"
        else:
            link = agent_media_service.file_link(file_row)
            if not link:
                return {"ok": False, "error": "Файл недоступен в хранилище"}
            msg_text = (text + "\n\n" if text else "") + f"{file_row.filename}: {link}"
            result = await instagram_service.send_text(composio_user_id, conversation.igsid, msg_text)
            sent_as = "link"
        body = (f"📎 Отправлен файл «{file_row.filename}» [file_id={file_row.id}]"
                + (" (ссылкой)" if sent_as == "link" else "")
                + (f"\n{text}" if text else ""))
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error")}

    instagram_service.store_message(
        db, agent_config.id, "outbound", body,
        agent_contact_id=contact.id,
        ig_conversation_id=conversation.ig_conversation_id,
        ig_message_id=result.get("message_id"),
        sent_at=datetime.utcnow(),
        attachment_id=(file_row.id if file_row is not None else None),
    )
    db.commit()

    to_label = ("@" + conversation.ig_username) if conversation.ig_username else conversation.igsid
    logger.info(f"[AGENT-TOOLS] instagram_send_{'file' if file_row is not None else 'message'} → {to_label}")
    out = {"ok": True, "to": to_label}
    if sent_as:
        out["sent_as"] = sent_as
    return out


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


def _augment_with_whatsapp(base_tools: list, agent_config, db: Session) -> list:
    """Дописать тулзы WhatsApp, если номер агента подключён."""
    if agent_config is None:
        return base_tools
    try:
        if not whatsapp_service.connected(db, agent_config.id):
            return base_tools
    except Exception as e:
        logger.warning(f"[AGENT-TOOLS] whatsapp account lookup failed: {e}")
        return base_tools
    return base_tools + to_chat_completions_tools(WHATSAPP_TOOLS)


async def fn_whatsapp_send_message(args: dict, user_id: str, agent_config, db: Session,
                                   file_row=None, skip_rate_limit: bool = False,
                                   typing_ms: Optional[int] = None, voice: Optional[dict] = None) -> dict:
    """
    Отправка в WhatsApp с номера владельца (Evolution API). Получатель:
    существующий чат контакта → номер телефона контакта / phone (новый чат).
    Анти-бан: почасовой лимит (WA_SEND_HOURLY_LIMIT, ответ на свежее входящее —
    WA_REPLY_HOURLY_LIMIT), предохранитель на одного клиента, лимит новых
    чатов в сутки (account.daily_new_chats_limit), проверка, что номер есть в
    WhatsApp, пауза «печатает…» перед отправкой. С file_row отправляет файл
    (whatsapp_send_file), text — подпись.
    """
    from backend.models.agent_whatsapp import AgentWhatsAppChat, AgentWhatsAppMessage
    from backend.utils.phone import normalize_phone_e164

    err = lambda code: {"ok": False, "error": whatsapp_service.error_human(code)}  # noqa: E731
    if not whatsapp_service.is_configured():
        return err("not_configured")
    if agent_config is None:
        return err("not_connected")
    account = whatsapp_service.get_account_for_agent(db, agent_config.id)
    if account is None:
        return err("not_connected")

    text = (args.get("text") or "").strip()
    if not text and file_row is None:
        return err("empty_text")

    # Контакт: по id, иначе по номеру (создаём, если такого нет)
    contact = None
    phone_arg = normalize_phone_e164(args.get("phone")) if args.get("phone") else None
    if args.get("agent_contact_id"):
        contact = db.query(AgentContact).filter(
            AgentContact.id == args["agent_contact_id"],
            AgentContact.user_id == user_id,
            AgentContact.agent_config_id == agent_config.id,
        ).first()
        if not contact:
            return {"ok": False, "error": "Контакт не найден"}
    elif phone_arg:
        contact = whatsapp_service.match_contact_by_phone(db, agent_config.id, phone_arg)
        if contact is None:
            contact = AgentContact(
                agent_config_id=agent_config.id, user_id=user_id,
                phone=phone_arg, status="new",
            )
            db.add(contact)
            db.flush()
    else:
        return {"ok": False, "error": "Укажи agent_contact_id или phone"}

    if not skip_rate_limit:
        streak = _today_outbound_streak(
            db, AgentWhatsAppMessage, contact.id,
            extra_filters=(AgentWhatsAppMessage.account_id == account.id,),
        )
        blocked = _streak_block(streak)
        if blocked is not None:
            return blocked

    hit = None if skip_rate_limit else _chat_limit_hit(
        db, AgentWhatsAppMessage, [AgentWhatsAppMessage.account_id == account.id], contact.id,
        whatsapp_service.WA_SEND_HOURLY_LIMIT, whatsapp_service.WA_REPLY_HOURLY_LIMIT,
        sent_filter=AgentWhatsAppMessage.wa_message_id.isnot(None),
    )
    if hit == "hourly":
        return err("send_limit_reached")
    if hit == "per_contact":
        return {"ok": False, "error": CHAT_PER_CONTACT_ERROR}

    chat = whatsapp_service.chat_for_contact(db, account.id, contact.id)
    if chat is not None:
        to = chat.remote_jid
    else:
        raw_phone = phone_arg or contact.phone or ""
        if raw_phone.startswith(("tg:", "ig:", "wa:")):
            raw_phone = ""
        digits = whatsapp_service.phone_digits(normalize_phone_e164(raw_phone) or raw_phone)
        if len(digits) < 8:
            return err("no_phone")
        # Новый чат (агент пишет первым) — суточный лимит
        day_ago = datetime.utcnow() - timedelta(days=1)
        new_chats = db.query(AgentWhatsAppChat).filter(
            AgentWhatsAppChat.account_id == account.id,
            AgentWhatsAppChat.created_via == "send",
            AgentWhatsAppChat.created_at >= day_ago,
        ).count()
        if new_chats >= int(account.daily_new_chats_limit or 0):
            return err("new_chat_limit_reached")
        check = await whatsapp_service.check_number(account.instance_name, digits)
        if not check.get("ok"):
            return err(check.get("error") or "whatsapp_error")
        if not check.get("exists"):
            return err("not_on_whatsapp")
        to = check.get("jid") or f"{digits}{whatsapp_service.USER_JID_SUFFIX}"
        chat = db.query(AgentWhatsAppChat).filter(
            AgentWhatsAppChat.account_id == account.id,
            AgentWhatsAppChat.remote_jid == to,
        ).first()
        if chat is None:
            chat = AgentWhatsAppChat(
                account_id=account.id, remote_jid=to, phone=digits,
                agent_contact_id=contact.id, created_via="send",
            )
            db.add(chat)
        elif chat.agent_contact_id is None:
            chat.agent_contact_id = contact.id
        db.flush()

    if voice is not None:
        import base64 as _b64
        result = await whatsapp_service.send_audio(
            account.instance_name, to, data_b64=_b64.b64encode(voice["data"]).decode(),
        )
        body = agent_voice_service.VOICE_BODY_PREFIX + text
    elif file_row is None:
        result = await whatsapp_service.send_text(account.instance_name, to, text, delay_ms=typing_ms)
        body = text
    else:
        import base64 as _b64
        from backend.services import agent_media_service
        data = await agent_media_service.load_bytes(file_row)
        if not data:
            return {"ok": False, "error": "Файл недоступен в хранилище"}
        result = await whatsapp_service.send_media(
            account.instance_name, to,
            data_b64=_b64.b64encode(data).decode(), mime=file_row.mime_type or "",
            filename=file_row.filename, kind=file_row.kind or "document", caption=text,
        )
        body = (f"📎 Отправлен файл «{file_row.filename}» [file_id={file_row.id}]"
                + (f"\n{text}" if text else ""))
    if not result.get("ok"):
        db.rollback()
        code = result.get("error") or "whatsapp_error"
        if code == "not_connected":
            account = whatsapp_service.get_account_for_agent(db, agent_config.id, require_connected=False)
            if account is not None:
                account.last_error = "send_failed_not_connected"
                db.commit()
        return err(code)

    whatsapp_service.store_message(
        db, account, "outbound", body,
        chat=chat, agent_contact_id=contact.id,
        wa_message_id=result.get("message_id"), sent_at=datetime.utcnow(),
        attachment_id=(file_row.id if file_row is not None else None),
    )
    db.commit()

    to_label = contact.name or ("+" + chat.phone if chat.phone else to)
    logger.info(f"[AGENT-TOOLS] whatsapp_send_{'file' if file_row is not None else 'message'} → {to_label}")
    return {"ok": True, "to": to_label, "agent_contact_id": str(contact.id)}


async def fn_whatsapp_send_file(args: dict, user_id: str, agent_config, db: Session) -> dict:
    if agent_config is None:
        return {"ok": False, "error": whatsapp_service.error_human("not_connected")}
    row = _get_agent_file(db, agent_config.id, args.get("file_id"))
    if row is None:
        return {"ok": False, "error": "Файл не найден"}
    send_args = dict(args, text=(args.get("caption") or "").strip())
    return await fn_whatsapp_send_message(send_args, user_id, agent_config, db, file_row=row)


async def fn_whatsapp_get_thread(args: dict, user_id: str, agent_config_id: str, db: Session) -> dict:
    """Последние сообщения WhatsApp-переписки с контактом."""
    contact = db.query(AgentContact).filter(
        AgentContact.id == args.get("agent_contact_id"),
        AgentContact.user_id == user_id,
        AgentContact.agent_config_id == agent_config_id,
    ).first()
    if not contact:
        return {"ok": False, "error": "Контакт не найден"}
    limit = min(int(args.get("limit") or 20), 50)
    rows = whatsapp_service.get_thread(db, contact.id, limit=limit)
    return {"ok": True, "messages": [m.to_dict() for m in rows]}


async def fn_telegram_send_message(args: dict, user_id: str, agent_config, db: Session,
                                   file_row=None, file_bytes: Optional[bytes] = None,
                                   skip_rate_limit: bool = False,
                                   typing_seconds: Optional[float] = None,
                                   voice: Optional[dict] = None) -> dict:
    """
    Отправка сообщения с личного Telegram владельца. Анти-бан меры:
    - почасовой лимит (TG_SEND_HOURLY_LIMIT, ответ на свежее входящее —
      TG_REPLY_HOURLY_LIMIT) и предохранитель на одного клиента;
    - резолв по номеру телефона (ImportContacts) — только когда нет диалога и
      username, и не чаще TG_PHONE_RESOLVE_HOURLY_LIMIT новых диалогов в час.
    С file_row/file_bytes отправляет файл (text — подпись); так работает
    telegram_send_file.
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
    if not text and file_bytes is None:
        return {"ok": False, "error": telegram_user_service.error_human("empty_text")}

    hour_ago = datetime.utcnow() - timedelta(hours=1)

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

    if not skip_rate_limit and contact is not None:
        streak = _today_outbound_streak(
            db, AgentTelegramMessage, contact.id,
            extra_filters=(AgentTelegramMessage.account_id == account.id,),
        )
        blocked = _streak_block(streak)
        if blocked is not None:
            return blocked

    hit = None if skip_rate_limit else _chat_limit_hit(
        db, AgentTelegramMessage, [AgentTelegramMessage.account_id == account.id],
        contact.id if contact else None,
        telegram_user_service.TG_SEND_HOURLY_LIMIT, telegram_user_service.TG_REPLY_HOURLY_LIMIT,
    )
    if hit == "hourly":
        return {"ok": False, "error": telegram_user_service.error_human("send_limit_reached")}
    if hit == "per_contact":
        return {"ok": False, "error": CHAT_PER_CONTACT_ERROR}

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
        file_bytes=(voice["data"] if voice is not None else file_bytes),
        file_name=(voice["filename"] if voice is not None else (file_row.filename if file_row is not None else None)),
        force_document=(file_row is None or file_row.kind != "image"),
        typing_seconds=typing_seconds,
        voice_note=bool(voice is not None and voice.get("is_ogg")),
        voice_duration=int((voice or {}).get("duration") or 0),
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

    body = text
    if voice is not None:
        body = agent_voice_service.VOICE_BODY_PREFIX + text
    elif file_row is not None:
        body = (f"📎 Отправлен файл «{file_row.filename}» [file_id={file_row.id}]"
                + (f"\n{text}" if text else ""))
    telegram_user_service.store_message(
        db, account, "outbound", body,
        agent_contact_id=(contact.id if contact else (dialog.agent_contact_id if dialog else None)),
        tg_peer_id=res_peer,
        tg_message_id=result.get("tg_message_id"),
        attachment_id=(file_row.id if file_row is not None else None),
    )
    db.commit()

    to_label = result.get("name") or (f"@{result['username']}" if result.get("username") else str(res_peer))
    what = f"file {file_row.filename}" if file_row is not None else "message"
    logger.info(f"[AGENT-TOOLS] telegram_send_{'file' if file_row is not None else 'message'} ({what}) → {to_label} via {result.get('resolved_via')}")
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
# send_proposal: атомарная цепочка «собрать КП → отправить → зафиксировать →
# напомнить → сказать владельцу». Ветка 1810-anri-sales-logic.
# ============================================================================

import random as _random


def _detect_recent_channel(db: Session, agent_config_id: str, agent_contact_id) -> Optional[str]:
    """Какой канал клиент использовал последним. Возвращает telegram/whatsapp/instagram или None.

    Берём MAX(created_at) по каждому из трёх треходов и выбираем самый свежий.
    Если ни одного сообщения нет (контакт заведён вручную владельцем) — None.
    """
    from backend.models.agent_telegram_account import AgentTelegramMessage
    from backend.models.agent_whatsapp import AgentWhatsAppMessage
    from backend.models.agent_instagram import AgentInstagramMessage

    best_channel = None
    best_ts = None
    for channel, model in (
        ("telegram", AgentTelegramMessage),
        ("whatsapp", AgentWhatsAppMessage),
        ("instagram", AgentInstagramMessage),
    ):
        row = db.query(model.created_at).filter(
            model.agent_contact_id == agent_contact_id,
        ).order_by(model.created_at.desc()).first()
        if row is None or row[0] is None:
            continue
        ts = row[0]
        if best_ts is None or ts > best_ts:
            best_ts = ts
            best_channel = channel
    return best_channel


async def _send_proposal_file(channel: str, args: dict, user_id: str, agent_config, db: Session) -> dict:
    """Отправить готовый PDF КП в выбранный канал. Прокси над *_send_file."""
    if channel == "telegram":
        return await fn_telegram_send_file(args, user_id, agent_config, db)
    if channel == "whatsapp":
        return await fn_whatsapp_send_file(args, user_id, agent_config, db)
    if channel == "instagram":
        return await fn_instagram_send_file(args, user_id, agent_config, db)
    return {"ok": False, "error": f"Неподдерживаемый канал: {channel}"}


def _proposal_reminder_delay_min() -> int:
    """Случайный интервал 60–90 минут, чтобы напоминание выглядело живым."""
    return _random.randint(60, 90)


def _clamp_reminder_to_working_hours(delay_min: int) -> int:
    """Если `сейчас + delay` вылетает за 20:00 Бишкек — ставим на завтра 10:00.

    Возвращает новое число минут от текущего момента. Внутри окна — исходный delay.
    """
    from backend.core.timezone_utils import BISHKEK
    now_local = datetime.now(BISHKEK)
    target_local = now_local + timedelta(minutes=delay_min)
    if 9 <= target_local.hour < 20:
        return delay_min
    # За окном: ставим на следующее 10:00 Бишкек
    tomorrow_10 = (now_local + timedelta(days=1 if now_local.hour >= 20 else 0)).replace(
        hour=10, minute=0, second=0, microsecond=0
    )
    if tomorrow_10 <= now_local:
        tomorrow_10 = tomorrow_10 + timedelta(days=1)
    delta_min = int((tomorrow_10 - now_local).total_seconds() // 60)
    return max(delta_min, 10)


async def _auto_create_calendar_event(
    agent_config,
    db: Session,
    *,
    summary: str,
    start_utc: datetime,
    duration_minutes: int = 30,
    description: str = "",
) -> Optional[dict]:
    """Best-effort автозапись события в Google Календарь владельца.

    Используется, чтобы у владельца в календаре появлялись все действия
    агента: запланированные звонки, встречи «клиент просит человека». Если
    коннектор google_calendar не подключён — тихо выходим (это не ошибка,
    просто у агента нет календаря).

    Время переводим в Asia/Bishkek, Composio получает wall-time + timezone,
    чтобы событие появилось ровно на бишкекском часе, без драмы с UTC.
    """
    if agent_config is None:
        return None
    if "google_calendar" not in _connected_toolkits(agent_config, db):
        return None
    from backend.core.timezone_utils import BISHKEK
    if start_utc.tzinfo is None:
        start_utc = start_utc.replace(tzinfo=timezone.utc)
    local = start_utc.astimezone(BISHKEK)
    event_args = {
        "summary": summary,
        "start_datetime": local.strftime("%Y-%m-%dT%H:%M:%S"),
        "event_duration_minutes": max(10, int(duration_minutes)),
        "timezone": "Asia/Bishkek",
        "calendar_id": "primary",
    }
    if description:
        event_args["description"] = description.strip()
    try:
        composio_user_id = composio_service.composio_user_id_for_agent(agent_config.id)
        result = await composio_service.execute(
            "GOOGLECALENDAR_CREATE_EVENT", event_args, composio_user_id
        )
        logger.info(
            f"[AGENT-TOOLS] calendar event '{summary}' at {event_args['start_datetime']} Bishkek"
        )
        return result if isinstance(result, dict) else None
    except Exception as e:
        logger.warning(f"[AGENT-TOOLS] calendar auto-insert failed: {e}")
        return None


_HANDOFF_REASON_LABEL = {
    "ready_to_connect": "ГОТОВ ПОДКЛЮЧАТЬСЯ",
    "requested_human": "ПРОСИТ ЧЕЛОВЕКА",
    "demo": "ПРОСИТ ДЕМОНСТРАЦИЮ",
    "escalation": "НЕСТАНДАРТНЫЙ СЛУЧАЙ / ЖАЛОБА",
}

# Заголовок платформенной задачи-напоминания владельцу (B3). Это НЕ звонок
# агента, а дело самого владельца: позвонить клиенту / подключить / разобрать.
_HANDOFF_TASK_TITLE = {
    "ready_to_connect": "Подключить клиента",
    "requested_human": "Звонок владельца клиенту",
    "demo": "Демонстрация клиенту",
    "escalation": "Разобрать обращение клиента",
}


async def fn_notify_owner_handoff(args: dict, user_id: str, agent_config_id: str, db: Session) -> dict:
    """Собрать карточку клиента из CRM и уведомить владельца о передаче."""
    agent_contact_id = args.get("agent_contact_id")
    reason = (args.get("reason") or "").strip()
    if not agent_contact_id or reason not in _HANDOFF_REASON_LABEL:
        return {"ok": False, "error": "Нужны agent_contact_id и валидный reason"}

    agent_config = db.query(AgentConfig).filter(AgentConfig.id == agent_config_id).first()
    if agent_config is None:
        return {"ok": False, "error": "Агент не найден"}

    contact = db.query(AgentContact).filter(
        AgentContact.id == agent_contact_id,
        AgentContact.user_id == user_id,
        AgentContact.agent_config_id == agent_config_id,
    ).first()
    if contact is None:
        return {"ok": False, "error": "Контакт не найден"}

    memory = contact.memory if isinstance(contact.memory, dict) else {}
    niche = (memory.get("niche") or memory.get("industry") or "").strip()
    volume = (memory.get("call_volume_per_day") or memory.get("volume") or "").strip() \
        if isinstance(memory.get("call_volume_per_day") or memory.get("volume"), str) else ""
    need = (memory.get("direction_need") or memory.get("needs") or "").strip() \
        if isinstance(memory.get("direction_need") or memory.get("needs"), str) else ""
    summary = (args.get("summary") or "").strip() or (memory.get("summary") or "").strip()

    name = (contact.name or "—").strip()
    phone = (contact.phone or "—").strip()
    company = (contact.company or "").strip()

    lines = [f"📌 {_HANDOFF_REASON_LABEL[reason]}"]
    header = name
    if company:
        header += f" ({company})"
    lines.append(header)
    if niche:
        lines.append(f"Ниша: {niche}")
    if need:
        lines.append(f"Нужно: {need}")
    if volume:
        lines.append(f"Объём: {volume}")
    if summary:
        lines.append(f"Контекст: {summary}")
    if args.get("scheduled_at"):
        lines.append(f"Назначено: {args['scheduled_at']}")
    lines.append(f"Контакт: {phone}")

    message = "\n".join(lines)
    try:
        await fn_send_telegram_notification({"message": message}, agent_config, db)
        sent = True
    except Exception as e:
        logger.warning(f"[AGENT-TOOLS] notify_owner_handoff: send failed: {e}")
        sent = False

    # Клиент назвал время → парсим один раз: под платформенную задачу и под
    # событие в календаре.
    scheduled_raw = args.get("scheduled_at")
    scheduled_utc = _parse_iso_utc(scheduled_raw) if scheduled_raw else None

    # B3 (карта): платформенная задача в «Ближайшие задачи». Раньше handoff слал
    # только Telegram + Google Календарь, а в интерфейсе платформы задача не
    # появлялась — список «Ближайшие задачи» оставался пустым. Теперь при
    # передаче с назначенным временем создаём Task с channel="owner": это НЕ
    # звонок агента, планировщик такие задачи НЕ исполняет (см.
    # task_scheduler.check_and_execute_tasks), а напоминание владельцу самому
    # разобраться с клиентом (позвонить / подключить / провести демо).
    owner_task_created = False
    if sent and scheduled_utc is not None:
        try:
            handoff_task = Task(
                is_agent_task=True,
                channel="owner",
                agent_contact_id=contact.id,
                user_id=user_id,
                contact_id=None,
                status=TaskStatus.SCHEDULED,
                scheduled_time=scheduled_utc,
                title=_HANDOFF_TASK_TITLE.get(reason, "Передача клиента владельцу"),
                description=message,
            )
            db.add(handoff_task)
            db.commit()
            db.refresh(handoff_task)
            owner_task_created = True
            logger.info(
                f"[AGENT-TOOLS] notify_owner_handoff → owner task {handoff_task.id} "
                f"contact={agent_contact_id} at {scheduled_utc.isoformat()}"
            )
        except Exception as e:
            db.rollback()
            logger.warning(f"[AGENT-TOOLS] notify_owner_handoff: owner task failed: {e}")

    # Автозапись встречи в Google Календарь, если клиент назвал время, а повод
    # явно требует встречу владельца с клиентом. Best-effort — не блокирует
    # уведомление владельцу.
    calendar_event = None
    if sent and scheduled_utc is not None and reason in ("requested_human", "demo"):
        cal_summary = f"👤 {_HANDOFF_REASON_LABEL[reason]}: {name}"
        if company:
            cal_summary += f" ({company})"
        cal_desc_parts = [f"Контакт: {phone}"]
        if niche:
            cal_desc_parts.append(f"Ниша: {niche}")
        if summary:
            cal_desc_parts.append(f"Контекст: {summary}")
        calendar_event = await _auto_create_calendar_event(
            agent_config, db,
            summary=cal_summary, start_utc=scheduled_utc, duration_minutes=60,
            description="\n".join(cal_desc_parts),
        )

    logger.info(
        f"[AGENT-TOOLS] notify_owner_handoff reason={reason} contact={agent_contact_id} sent={sent} "
        f"task={owner_task_created} calendar={bool(calendar_event)}"
    )
    return {
        "ok": sent,
        "reason": reason,
        "message": message,
        "contact_id": str(agent_contact_id),
        "task_created": owner_task_created,
        "calendar_event_created": bool(calendar_event),
    }


async def fn_send_proposal(args: dict, user_id: str, agent_config_id: str, db: Session) -> dict:
    """
    Атомарная цепочка после звонка: собрать КП → отправить → записать → напомнить → владельцу.
    Пять шагов в одной тулзе, чтобы ни один не потерялся.
    """
    agent_contact_id = args.get("agent_contact_id")
    content = (args.get("content") or "").strip()
    if not agent_contact_id or not content:
        return {"ok": False, "error": "agent_contact_id и content обязательны"}

    agent_config = db.query(AgentConfig).filter(AgentConfig.id == agent_config_id).first()
    if agent_config is None:
        return {"ok": False, "error": "Агент не найден"}

    contact = db.query(AgentContact).filter(
        AgentContact.id == agent_contact_id,
        AgentContact.user_id == user_id,
        AgentContact.agent_config_id == agent_config_id,
    ).first()
    if contact is None:
        return {"ok": False, "error": "Контакт не найден"}

    client_name = (contact.name or contact.phone or "клиент").strip()
    niche = ""
    if isinstance(contact.memory, dict):
        niche = (contact.memory.get("niche") or contact.memory.get("summary") or "").strip()

    # Канал — явный > автодетект > telegram как минимально болезненный fallback.
    channel = (args.get("channel") or "").strip().lower() or None
    if channel not in ("telegram", "whatsapp", "instagram"):
        channel = _detect_recent_channel(db, agent_config_id, agent_contact_id) or "telegram"

    # 1. Собираем КП (всё та же fn_create_document — фирстиль, обложка, таблицы).
    title = (args.get("title") or f"Коммерческое предложение Voksy AI для {client_name}").strip()
    doc_result = await fn_create_document(
        {
            "agent_contact_id": agent_contact_id,
            "title": title,
            "content": content,
            "format": "pdf",
            "kind_label": "Коммерческое предложение",
            "cover": True,
        },
        user_id, agent_config_id, db,
    )
    if not doc_result.get("ok"):
        return {"ok": False, "step": "create_document", "error": doc_result.get("error")}
    file_id = doc_result["file_id"]

    # 2. Отправка в выбранный канал.
    caption = (args.get("caption") or f"{client_name}, как договаривались — коммерческое предложение.").strip()
    send_args = {
        "agent_contact_id": agent_contact_id,
        "file_id": file_id,
        "caption": caption,
    }
    send_result = await _send_proposal_file(channel, send_args, user_id, agent_config, db)
    if not send_result.get("ok"):
        return {
            "ok": False, "step": "send_file", "channel": channel,
            "file_id": file_id, "error": send_result.get("error"),
            "hint": "КП собрано, но отправить в канал не удалось. Попробуй другой канал вручную.",
        }

    # 3. Записываем в memory: подстадия voronki + метки для дальнейших сценариев.
    memory = dict(contact.memory or {})
    memory["funnel_stage"] = "proposal_sent"
    memory["proposal_sent_at"] = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()
    memory["proposal_file_id"] = file_id
    memory["proposal_channel"] = channel
    contact.memory = memory
    flag_modified(contact, "memory")
    db.commit()

    # 4. Напоминание 60–90 мин, с переносом на 10:00 следующего дня, если за окно.
    reminder_delay = _clamp_reminder_to_working_hours(_proposal_reminder_delay_min())
    reminder_text = (args.get("reminder_text") or "").strip() or (
        f"Напомни {client_name}: «Получилось глянуть предложение? Что скажете?» "
        "Будь коротким, без давления. Если клиент ответил сомнениями — отработай возражение."
    )
    reminder_scheduled = False
    reminder_result = None
    reminder_note = None
    tg_account_ok = (
        telegram_user_service.is_configured()
        and telegram_user_service.account_connected(db, agent_config_id)
    )
    if tg_account_ok and channel == "telegram":
        reminder_result = await fn_create_agent_task(
            {
                "agent_contact_id": agent_contact_id,
                "delay_minutes": reminder_delay,
                "title": "Напоминание после КП",
                "notes": reminder_text,
            },
            user_id, agent_config_id, db, channel="telegram",
        )
        reminder_scheduled = reminder_result.get("ok", False)
    if not reminder_scheduled:
        reminder_note = (
            f"Напоминание не поставлено автоматически (канал={channel}, telegram-аккаунт "
            f"{'подключён' if tg_account_ok else 'не подключён'}). "
            f"Поставь вручную через {reminder_delay} мин: create_agent_task(channel='telegram', notes=...)."
        )

    # 5. Короткая строка владельцу в Telegram-бота.
    owner_note = (args.get("owner_note") or "").strip()
    if not owner_note:
        parts = [client_name]
        if niche:
            parts.append(niche)
        parts.append(f"КП отправлено ({channel})")
        if reminder_scheduled:
            parts.append(f"напомню через {reminder_delay} мин")
        else:
            parts.append("напоминание не запланировано")
        owner_note = " | ".join(parts)
    try:
        await fn_send_telegram_notification({"message": owner_note}, agent_config, db)
        owner_notified = True
    except Exception as e:
        logger.warning(f"[AGENT-TOOLS] send_proposal: owner notify failed: {e}")
        owner_notified = False

    logger.info(
        f"[AGENT-TOOLS] send_proposal → contact={agent_contact_id} channel={channel} "
        f"file={file_id} reminder_in_min={reminder_delay if reminder_scheduled else 'manual'}"
    )
    return {
        "ok": True,
        "file_id": file_id,
        "channel": channel,
        "memory_stage": "proposal_sent",
        "reminder_scheduled": reminder_scheduled,
        "reminder_delay_minutes": reminder_delay,
        "reminder_task_id": (reminder_result or {}).get("task_id") if reminder_scheduled else None,
        "reminder_note": reminder_note,
        "owner_notified": owner_notified,
    }


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
            "(«через N минут/часов»), scheduled_at — для абсолютного («завтра в 14:00»). "
            "У клиента всегда одна запланированная задача на звонок: новая заменяет прежнюю "
            "(в ответе — что заменено)."
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
    FIND_PRODUCTS_TOOL,
    SEND_SMS_TOOL,
    SEND_WEBHOOK_TOOL,
    SEND_TELEGRAM_NOTIFICATION_TOOL,
    {
        "type": "function",
        "name": "pause_agent_for_contact",
        "description": (
            "Остановить агента в ПЕРЕПИСКЕ с клиентом (Telegram, WhatsApp, Instagram сразу): "
            "агент перестаёт отвечать этому клиенту, сообщения клиента сохраняются. Звонки не "
            "затрагиваются. Используй для «останови агента для Айбека на час», «не отвечай "
            "Петрову, я сам с ним переписываюсь». Клиента указывай agent_contact_id или query "
            "(имя / номер / @username). Без срока — до команды «верни агента»."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_contact_id": {"type": "string", "description": "UUID контакта агента"},
                "query": {"type": "string", "description": "Имя, номер телефона или @username клиента, если id неизвестен"},
                "minutes": {"type": "integer", "description": "На сколько минут (60 — час, 1440 — сутки). Не указывай для паузы без срока"},
                "forever": {"type": "boolean", "description": "true — до ручного возврата (по умолчанию, если срок не назван)"},
            },
        },
    },
    {
        "type": "function",
        "name": "resume_agent_for_contact",
        "description": (
            "Вернуть агента в переписку с клиентом (снять паузу): агент ответит на следующее "
            "сообщение клиента и увидит всю переписку, включая сообщения владельца. Для «верни "
            "агента к Айбеку», «пусть агент снова отвечает Петрову»."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_contact_id": {"type": "string", "description": "UUID контакта агента"},
                "query": {"type": "string", "description": "Имя, номер телефона или @username клиента, если id неизвестен"},
            },
        },
    },
    CHECK_PHONE_OPERATOR_TOOL,
    SEND_PROPOSAL_TOOL,
    NOTIFY_OWNER_HANDOFF_TOOL,
    FIND_CLIENT_TOOL,
    MERGE_CONTACTS_TOOL,
    ADD_CONTACT_PHONE_TOOL,
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
                "niche": {
                    "type": "string",
                    "description": (
                        "Чем занимается клиент, 1–3 слова («стоматология», «салон красоты»). "
                        "Запиши, как только узнал: по нише агент узнаёт клиента, когда тот "
                        "звонит с другого номера"
                    ),
                },
                "need": {"type": "string", "description": "Запрос клиента одной фразой (что хочет)"},
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
            "(«через N минут/часов»), scheduled_at — для абсолютного («завтра в 14:00»). "
            "У клиента всегда одна запланированная задача на звонок: новая заменяет прежнюю "
            "(в ответе — что заменено), удалять старую не нужно. После недозвона сегодня "
            "второй звонок на сегодня система не поставит — ставь на завтра."
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
    FIND_PRODUCTS_TOOL,
    SEND_SMS_TOOL,
    SEND_WEBHOOK_TOOL,
    CHECK_PHONE_OPERATOR_TOOL,
    SEND_PROPOSAL_TOOL,
    NOTIFY_OWNER_HANDOFF_TOOL,
    FIND_CLIENT_TOOL,
    MERGE_CONTACTS_TOOL,
    ADD_CONTACT_PHONE_TOOL,
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
        sched_aware = scheduled_at if scheduled_at.tzinfo else scheduled_at.replace(tzinfo=timezone.utc)
        # Звонок на время, которое уже прошло, — ошибка модели во времени или
        # часовом поясе, а не просьба «позвонить сейчас». Раньше такой звонок молча
        # ставился на «через 3 минуты» (живой тест 21.10.2026: вторая задача на
        # 14:38 сразу после недозвона). Теперь отказ с текущим временем — модель
        # пересчитает. Пара минут назад — это «сейчас», их по-прежнему поднимаем.
        if channel == "call" and sched_aware < now_utc - CALL_PAST_TOLERANCE:
            from backend.core.timezone_utils import BISHKEK as _BISHKEK
            logger.info(
                f"[AGENT-TOOLS] create_agent_task: отказ — время звонка {sched_aware.isoformat()} "
                f"уже прошло (контакт {agent_contact_id})"
            )
            return {
                "ok": False,
                "error": "scheduled_in_past",
                "hint": (
                    f"Время {sched_aware.astimezone(_BISHKEK).strftime('%d.%m %H:%M')} по Бишкеку уже прошло. "
                    f"Сейчас {now_utc.astimezone(_BISHKEK).strftime('%d.%m %H:%M')} по Бишкеку "
                    f"({now_utc.strftime('%Y-%m-%dT%H:%MZ')}). Пересчитай scheduled_at (UTC) "
                    "или передай delay_minutes."
                ),
            }
        # Клэмп: модель могла посчитать время от устаревшего значения в промпте.
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

    # Унифицированная проверка рабочих часов агента (Бишкек) — переносим звонок
    # на ближайший рабочий день, если время выпадает на нерабочие часы.
    # Telegram-сообщения рабочими часами не ограничены.
    if agent_config is not None and not is_telegram:
        adjusted, _shifted = adjust_to_working_hours(
            scheduled_at,
            agent_config.working_hours_start,
            agent_config.working_hours_end,
        )
        scheduled_at = adjusted

    # Guard-ы для звонков (ветка 1810-anri-sales-logic): O! + окно 09–20 Бишкек +
    # «не больше одного звонка в день на клиента». Если время вне окна —
    # сдвинем на ближайший момент внутри; если оператор не наш или сегодня уже
    # есть звонок — отдадим Анри понятный отказ с hint.
    if channel == "call":
        guard_contact = snooze_contact if snooze_contact is not None else db.query(AgentContact).filter(
            AgentContact.id == agent_contact_id
        ).first()
        if guard_contact is None:
            return {"ok": False, "error": "Contact not found"}
        # Проверка идёт по ИТОГОВОМУ времени (после клэмпа, паузы и окна): клэмп
        # «сейчас + 3 мин» больше не обходит запрет второго звонка в день.
        guard = _check_call_guards(guard_contact, scheduled_at, allow_shift=True,
                                   window=call_window(agent_config), db=db, user_id=user_id)
        if not guard.get("ok"):
            logger.info(
                f"[AGENT-TOOLS] create_agent_task: отказ ({guard.get('error')}) — звонок контакту "
                f"{agent_contact_id} на {scheduled_at.isoformat()}"
            )
            return guard
        scheduled_at = guard["scheduled_utc"]

    replaced = []
    if channel == "call":
        # Одна запланированная задача на звонок у клиента: новая — последнее
        # решение агента, прежние звонки этого клиента отменяются (раньше их могло
        # копиться сколько угодно, удалить ошибочную PostCall не мог).
        replaced = _replace_scheduled_calls(db, agent_contact_id)
        cancelled_count = len(replaced)
    else:
        # Telegram-сообщения и напоминания владельцу: отменяем только дубль на
        # то же самое время, остальные запланированные остаются.
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

    # Автозапись в Google Календарь (ветка 1810-anri-sales-logic): владельцу
    # нужно видеть всю работу агента в календаре. Делаем best-effort только
    # для звонков, чтобы не захламлять календарь десятками «отложенное
    # сообщение в Telegram».
    calendar_event = None
    if channel == "call":
        cal_contact = snooze_contact if snooze_contact is not None else db.query(AgentContact).filter(
            AgentContact.id == agent_contact_id
        ).first()
        cal_name = (cal_contact.name if cal_contact else None) or (cal_contact.phone if cal_contact else "") or "клиент"
        cal_niche = ""
        if cal_contact and isinstance(cal_contact.memory, dict):
            cal_niche = str(cal_contact.memory.get("niche") or cal_contact.memory.get("industry") or "").strip()
        cal_summary = f"📞 Звонок агента: {cal_name}" + (f" ({cal_niche})" if cal_niche else "")
        cal_desc_parts = []
        if args.get("title") and args.get("title") != "Звонок агента":
            cal_desc_parts.append(args["title"])
        if args.get("notes"):
            cal_desc_parts.append(args["notes"])
        if cal_contact and cal_contact.phone:
            cal_desc_parts.append(f"Контакт: {cal_contact.phone}")
        calendar_event = await _auto_create_calendar_event(
            agent_config, db,
            summary=cal_summary, start_utc=scheduled_at, duration_minutes=30,
            description="\n".join(cal_desc_parts),
        )

    result = {
        "ok": True,
        "task_id": str(task.id),
        "channel": channel,
        "scheduled_at": scheduled_at.isoformat(),
        "cancelled_duplicates": cancelled_count,
    }
    if clamped_to_future:
        result["note"] = "scheduled_at был в прошлом — время поднято до ближайшего будущего"
    if replaced:
        result["replaced"] = _replaced_text(scheduled_at, replaced)
    if calendar_event is not None:
        result["calendar_event_created"] = True
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
    # Ниша и запрос — их читают узнавание клиента (find_client) и блок
    # «знакомый клиент» на входящем звонке (memory.niche / direction_need).
    if str(args.get("niche") or "").strip():
        memory["niche"] = str(args["niche"]).strip()[:120]
    if str(args.get("need") or "").strip():
        memory["direction_need"] = str(args["need"]).strip()[:300]

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

    # Валидация стадии — с учётом кастомных стадий агента (CRM v2).
    # agent_config нужен ДО этой проверки, чтобы read его pipeline_stages.
    from backend.models.agent_config import AgentConfig
    agent_config = db.query(AgentConfig).filter(AgentConfig.id == agent_config_id).first()
    if not is_valid_stage_for_agent(agent_config, stage):
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
            AgentContact.alt_phones.ilike(like),
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

        # Одна запланированная задача на звонок у клиента — прежние отменяем.
        _replace_scheduled_calls(db, contact.id)
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


# Защиты звонков (ветка 1810-anri-sales-logic): O! + окно 09–20 Бишкек + лимит
# «один звонок в день на клиента». До этой ветки оркестратор мог обещать звонок
# в полночь / на Beeline / повторный после «сброса» — но дотрагивался до правила
# только через промпт. Теперь любое планирование звонка проходит через этот
# shared-guard (чтобы в trigger_immediate_call и в create_agent_task(channel=call)
# логика была единой).
CALL_WINDOW_START_HOUR = 9   # 09:00 Бишкек — по умолчанию, если окно агента не задано
CALL_WINDOW_END_HOUR = 20    # 20:00 Бишкек (не включительно)


def call_window(agent_config) -> Optional[tuple]:
    """Окно звонков агента (Бишкек): (start, end) часов, end не включительно.

    Берётся из настроек агента (раздел «Звонки»: working_hours_start/end и
    переключатель call_window_enabled). None — ограничение выключено, агент
    звонит в любое время. Нет агента / битые значения — 09–20 по умолчанию.
    """
    if agent_config is None:
        return (CALL_WINDOW_START_HOUR, CALL_WINDOW_END_HOUR)
    if getattr(agent_config, "call_window_enabled", True) is False:
        return None
    try:
        start = int(agent_config.working_hours_start)
        end = int(agent_config.working_hours_end)
    except (TypeError, ValueError):
        return (CALL_WINDOW_START_HOUR, CALL_WINDOW_END_HOUR)
    if not (0 <= start < end <= 24):
        return (CALL_WINDOW_START_HOUR, CALL_WINDOW_END_HOUR)
    return (start, end)


def call_window_text(window: Optional[tuple]) -> str:
    """«с 09:00 до 20:00» / «в любое время» — для подсказок агенту."""
    if window is None:
        return "в любое время"
    return f"с {window[0]:02d}:00 до {window[1]:02d}:00 по Бишкеку"


def _bishkek_now() -> datetime:
    from backend.core.timezone_utils import now_bishkek
    return now_bishkek()


def _check_call_guards(contact: AgentContact, scheduled_utc: datetime, *, allow_shift: bool = True,
                       window="default", db: Optional[Session] = None, user_id=None) -> dict:
    """
    Проверить, можно ли ставить звонок на этот момент.

    Возвращает dict:
      - {"ok": True, "scheduled_utc": datetime}  — звонок разрешён (возможно, со сдвигом)
      - {"ok": False, "error": "...", "hint": "..."}  — отказ с объяснением

    Правила (все — Бишкек):
      * оператор клиента — O! (050 / 070);
      * окно звонков агента (call_window: настройка «Звонки», по умолчанию 09–20;
        None — без ограничения по времени);
      * один звонок в день на клиента (last_called_at < сегодня-Бишкек);
      * недозвон сегодня (по факту, строки sip_calls; нужен db) — на этот день
        звонок больше не ставим, пока клиент сам не вышел на связь.

    allow_shift=True: если время вне окна, предложим ближайший момент внутри окна
    и вернём его в scheduled_utc. False — просто ошибка «вне окна».
    """
    from backend.models.sip_gateway import is_o_number, normalize_sip_number
    from backend.core.timezone_utils import BISHKEK

    # --- оператор ---
    # Номер для звонка: «номер для звонков» карточки → основной на O! → второй на O!
    # (ветка 2110-client-memory: WhatsApp клиента на Beeline, звонить — на O!).
    from backend.services.agent_contact_phones import dial_number
    phone = dial_number(contact) if contact else ""
    if not phone:
        return {"ok": False, "error": "no_phone", "hint": "У контакта не задан номер телефона."}
    digits = normalize_sip_number(phone)
    if not is_o_number(digits):
        return {
            "ok": False,
            "error": "other_operator",
            "hint": (
                "У клиента нет номера O! (мы звоним только на префиксы 050 и 070). "
                "Попроси номер O! и запиши его add_contact_phone(use_for_calls=true), "
                "или предложи переписку / входящий звонок на наш номер."
            ),
            "phone": phone,
        }

    # --- окно звонков агента (Бишкек) ---
    if window == "default":
        window = (CALL_WINDOW_START_HOUR, CALL_WINDOW_END_HOUR)
    if scheduled_utc.tzinfo is None:
        scheduled_utc = scheduled_utc.replace(tzinfo=timezone.utc)
    local = scheduled_utc.astimezone(BISHKEK)
    if window is not None and (local.hour < window[0] or local.hour >= window[1]):
        w_start = window[0]
        if not allow_shift:
            return {
                "ok": False,
                "error": "outside_window",
                "hint": (
                    f"Сейчас звонить нельзя: звонки агента {call_window_text(window)} "
                    "(настройка владельца). Предложи клиенту время в этом окне или продолжай переписку."
                ),
                "scheduled_local": local.strftime("%Y-%m-%d %H:%M"),
            }
        # Сдвигаем на ближайший момент внутри окна (сегодня к началу, если ещё рано,
        # иначе завтра к началу).
        if local.hour < w_start:
            shifted_local = local.replace(hour=w_start, minute=0, second=0, microsecond=0)
        else:
            shifted_local = (local + timedelta(days=1)).replace(hour=w_start, minute=0, second=0, microsecond=0)
        scheduled_utc = shifted_local.astimezone(timezone.utc)
        local = shifted_local

    # --- один звонок в день ---
    today_local = local.date()
    last_called = contact.last_called_at
    if last_called is not None:
        if last_called.tzinfo is None:
            last_called = last_called.replace(tzinfo=timezone.utc)
        if last_called.astimezone(BISHKEK).date() == today_local:
            return {
                "ok": False,
                "error": "already_called_today",
                "hint": (
                    "Сегодня этому клиенту уже звонили — не больше одного звонка в день. "
                    f"Ближайший разрешённый звонок: завтра {call_window_text(window)}."
                ),
                "last_called_at": last_called.astimezone(BISHKEK).strftime("%Y-%m-%d %H:%M"),
            }

    # --- недозвон сегодня (по факту) ---
    # last_called_at PostCall пишет в конце анализа, поэтому пока модель разбирает
    # недозвон, проверка выше видит вчерашнюю дату. Строка sip_calls есть с момента
    # постановки звонка в очередь.
    if db is not None and today_local <= _bishkek_now().date():
        from backend.services.agent_contact_phones import missed_call_today
        missed = missed_call_today(db, contact, user_id or contact.user_id, today_local)
        if missed is not None:
            when = missed.created_at
            if when is not None and when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            return {
                "ok": False,
                "error": "already_called_today",
                "hint": (
                    "Сегодня этому клиенту уже звонили и не дозвонились — второй звонок в тот же день "
                    f"не ставим. Ближайший разрешённый звонок: завтра {call_window_text(window)}."
                ),
                "last_called_at": when.astimezone(BISHKEK).strftime("%Y-%m-%d %H:%M") if when else None,
            }

    return {"ok": True, "scheduled_utc": scheduled_utc}


# Насколько «в прошлом» может быть время звонка, чтобы считаться «сейчас»
# (create_agent_task поднимет его до «через 3 минуты»); раньше — отказ.
CALL_PAST_TOLERANCE = timedelta(minutes=5)


def _replace_scheduled_calls(db: Session, contact_id, keep_task_id=None) -> list:
    """
    Отменить запланированные звонки клиента: у клиента остаётся одна задача на
    звонок — та, что ставится сейчас. Telegram-задачи и напоминания владельцу не
    трогает. Возвращает отменённые задачи (для ответа модели и лога).
    """
    q = db.query(Task).filter(
        Task.agent_contact_id == contact_id,
        Task.status == TaskStatus.SCHEDULED,
        Task.is_agent_task == True,
        Task.channel == "call",
    )
    if keep_task_id is not None:
        q = q.filter(Task.id != keep_task_id)
    tasks = q.order_by(Task.scheduled_time.asc()).all()
    for t in tasks:
        t.status = TaskStatus.CANCELLED
        logger.info(
            f"[AGENT-TOOLS] Отменён прежний звонок {t.id} на {t.scheduled_time} (контакт {contact_id}): "
            "у клиента остаётся одна запланированная задача на звонок"
        )
    return tasks


def _replaced_text(new_utc: datetime, replaced: list) -> str:
    """«звонок на 22.10 14:37 заменил звонок на 21.10 14:38» (Бишкек)."""
    from backend.core.timezone_utils import BISHKEK

    def fmt(dt):
        if dt is None:
            return "?"
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(BISHKEK).strftime("%d.%m %H:%M")

    old = ", ".join(f"звонок на {fmt(t.scheduled_time)}" for t in replaced)
    return f"звонок на {fmt(new_utc)} заменил {old} (у клиента всегда одна запланированная задача на звонок)"


async def fn_check_phone_operator(args: dict, user_id: str, agent_config_id: str, db: Session) -> dict:
    """Проверить номер клиента: оператор O! → можно звонить."""
    from backend.models.sip_gateway import is_o_number, normalize_sip_number, O_MOBILE_PREFIXES

    phone = (args.get("phone") or "").strip()
    agent_contact_id = args.get("agent_contact_id")
    if not phone and agent_contact_id:
        contact = db.query(AgentContact).filter(
            AgentContact.id == agent_contact_id,
            AgentContact.user_id == user_id,
            AgentContact.agent_config_id == agent_config_id,
        ).first()
        if contact and contact.phone:
            from backend.services.agent_contact_phones import dial_number
            phone = dial_number(contact)  # номер, на который агент и позвонит
    if not phone:
        return {"ok": False, "error": "phone_required", "hint": "Передай phone или agent_contact_id."}

    digits = normalize_sip_number(phone)
    if len(digits) != 12 or not digits.startswith("996"):
        return {
            "ok": True,
            "can_call": False,
            "operator": "unknown",
            "phone": phone,
            "note": (
                "Номер не похож на кыргызский (ждём 12 цифр, начинающихся с 996). "
                "Уточни у клиента полный номер; звонить не предлагай."
            ),
        }
    national = "0" + digits[3:]
    prefix = national[:3]
    if is_o_number(digits):
        return {
            "ok": True,
            "can_call": True,
            "operator": "O!",
            "prefix": prefix,
            "phone": phone,
            "note": (
                "Номер оператора O! — можем позвонить (в окно звонков агента из его настроек; "
                "вне окна trigger_immediate_call откажет с подсказкой)."
            ),
        }
    return {
        "ok": True,
        "can_call": False,
        "operator": "other",
        "prefix": prefix,
        "phone": phone,
        "note": (
            f"Префикс {prefix} не относится к O! (мы умеем звонить только на {', '.join(O_MOBILE_PREFIXES)}). "
            "Скажи клиенту, что звонить мы можем только на O!, и предложи продолжить переписку "
            "или входящий звонок на наш номер (его дай из базы знаний)."
        ),
    }


async def fn_trigger_immediate_call(args: dict, user_id: str, agent_config_id: str, db: Session) -> dict:
    """Создать задачу на немедленный звонок (без сдвига в рабочие часы).

    Guard-ы (ветка 1810-anri-sales-logic): O! + окно 09–20 Бишкек + лимит одного
    звонка в день на контакт. Отказ возвращает понятный hint, чтобы оркестратор
    мог корректно ответить клиенту.
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

    agent_config = db.query(AgentConfig).filter(AgentConfig.id == agent_config_id).first()
    if agent_config is not None and not agent_config.is_active:
        return {"ok": False, "error": "agent_inactive", "hint": "Активируйте агента, иначе планировщик не выполнит звонок."}

    now_utc = datetime.now(timezone.utc)
    guard = _check_call_guards(contact, now_utc, allow_shift=False, window=call_window(agent_config),
                               db=db, user_id=user_id)
    if not guard.get("ok"):
        logger.info(
            f"[AGENT-TOOLS] trigger_immediate_call: отказ ({guard.get('error')}) — контакт {agent_contact_id}"
        )
        return guard

    # Немедленный звонок — последнее решение: прежние запланированные звонки
    # клиента отменяются (одна задача на звонок у клиента).
    replaced = _replace_scheduled_calls(db, contact.id)

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
    result = {
        "ok": True,
        "task_id": str(task.id),
        "agent_contact_id": str(contact.id),
        "contact_name": contact.name or contact.phone,
        "note": "Звонок поставлен в очередь, планировщик выполнит его в течение ~30 секунд.",
    }
    if replaced:
        result["replaced"] = _replaced_text(now_utc, replaced)
    return result


# ---------------------------------------------------------------------------
# Узнавание клиента, второй номер, склейка карточек (ветка 2110-client-memory)
# ---------------------------------------------------------------------------

def _own_contact(db: Session, contact_id, user_id, agent_config_id):
    if not contact_id:
        return None
    try:
        return db.query(AgentContact).filter(
            AgentContact.id == contact_id,
            AgentContact.user_id == user_id,
            AgentContact.agent_config_id == agent_config_id,
        ).first()
    except Exception:
        db.rollback()  # кривой UUID от модели
        return None


async def fn_find_client(args: dict, user_id: str, agent_config_id: str, db: Session) -> dict:
    """Найти клиента по имени / компании / нише / номеру — та же реализация, что у голоса."""
    from backend.services import client_recognition
    if not any(str(args.get(k) or "").strip() for k in ("name", "company", "niche", "phone")):
        return {"ok": False, "error": "Передай имя, компанию, нишу или номер клиента."}
    result = client_recognition.find_for_voice(
        db, agent_config_id, args, exclude_ids=[args.get("exclude_contact_id")],
    )
    logger.info(f"[AGENT-TOOLS] find_client {args} → {result.get('confidence')}, {result.get('found', 0)} совп.")
    return result


async def fn_merge_contacts(args: dict, user_id: str, agent_config_id: str, db: Session,
                            agent_config=None) -> dict:
    """Склеить лишнюю карточку клиента в основную (необратимо, с записью в лог и владельцу)."""
    from backend.services.agent_contact_merge import merge_contacts, notify_owner
    main = _own_contact(db, args.get("main_contact_id"), user_id, agent_config_id)
    extra = _own_contact(db, args.get("duplicate_contact_id"), user_id, agent_config_id)
    if main is None or extra is None:
        return {"ok": False, "error": "Contact not found",
                "hint": "Нужны id двух карточек этого агента (возьми из find_client / search_contacts)."}
    if main.id == extra.id:
        return {"ok": False, "error": "same_contact", "hint": "Это одна и та же карточка — склеивать нечего."}
    reason = str(args.get("reason") or "").strip()
    result = merge_contacts(db, main, extra, reason=reason)
    notify_owner(agent_config or db.query(AgentConfig).filter(AgentConfig.id == agent_config_id).first(),
                 result["owner_line"])
    return result


async def fn_add_contact_phone(args: dict, user_id: str, agent_config_id: str, db: Session) -> dict:
    """Второй номер в карточку клиента; по желанию — звонить на него (и сразу позвонить)."""
    from backend.models.sip_gateway import is_o_number
    from backend.services.agent_contact_phones import (
        add_alt_phone, find_contact_by_phone, has_phone, phone_digits,
    )
    contact = _own_contact(db, args.get("agent_contact_id"), user_id, agent_config_id)
    if contact is None:
        return {"ok": False, "error": "Contact not found"}
    digits = phone_digits(args.get("phone"))
    if len(digits) != 12 or not digits.startswith("996"):
        return {"ok": False, "error": "bad_phone",
                "hint": "Номер не похож на кыргызский (ждём 0XXX XXX XXX или +996…). Уточни у клиента."}

    other = find_contact_by_phone(db, agent_config_id, digits)
    if other is not None and other.id != contact.id:
        return {
            "ok": False,
            "error": "phone_belongs_to_other_contact",
            "other_contact_id": str(other.id),
            "other_contact_name": other.name or other.phone,
            "hint": (
                "Этот номер уже есть в другой карточке. Если это тот же человек — склей карточки "
                f"merge_contacts(main_contact_id=<карточка с историей>, duplicate_contact_id=<лишняя>)."
            ),
        }

    added = add_alt_phone(contact, digits)
    can_call = is_o_number(digits)
    use_for_calls = bool(args.get("use_for_calls") or args.get("call_now"))
    if use_for_calls and can_call:
        contact.call_phone = digits
    db.commit()
    logger.info(
        f"[AGENT-TOOLS] add_contact_phone: контакт {contact.id} +{digits} "
        f"({'добавлен' if added else 'уже был'}{', звонить на него' if use_for_calls and can_call else ''})"
    )
    result = {
        "ok": True,
        "agent_contact_id": str(contact.id),
        "phone": "+" + digits,
        "added": added,
        "already_known": not added and has_phone(contact, digits),
        "can_call": can_call,
        "call_phone": ("+" + contact.call_phone) if contact.call_phone else None,
    }
    if use_for_calls and not can_call:
        result["hint"] = ("Номер записан, но он не на O! — звонить на него мы не можем. "
                          "Попроси номер O! (050 / 070) или продолжай переписку.")
    if args.get("call_now") and can_call:
        result["call"] = await fn_trigger_immediate_call(
            {"agent_contact_id": str(contact.id), "title": "Звонок на номер, который дал клиент"},
            user_id, agent_config_id, db,
        )
    return result


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


def _find_contact_for_pause(args: dict, user_id: str, agent_config_id: str, db: Session):
    """Контакт по agent_contact_id или query → (contact, error_dict|None)."""
    q = db.query(AgentContact).filter(
        AgentContact.user_id == user_id,
        AgentContact.agent_config_id == agent_config_id,
    )
    if args.get("agent_contact_id"):
        try:
            contact = q.filter(AgentContact.id == args["agent_contact_id"]).first()
        except Exception:
            db.rollback()
            contact = None
        if contact:
            return contact, None
        if not args.get("query"):
            return None, {"ok": False, "error": "Контакт не найден"}
    query = (args.get("query") or "").strip().lstrip("@")
    if not query:
        return None, {"ok": False, "error": "Укажи agent_contact_id или query (имя/номер клиента)"}
    digits = "".join(ch for ch in query if ch.isdigit())
    conds = [AgentContact.name.ilike(f"%{query}%")]
    if len(digits) >= 5:
        conds.append(AgentContact.phone.like(f"%{digits[-9:]}%"))
    else:
        conds.append(AgentContact.phone.ilike(f"%{query}%"))
    found = q.filter(or_(*conds)).order_by(AgentContact.updated_at.desc()).limit(6).all()
    if not found:
        return None, {"ok": False, "error": f"Клиент «{query}» не найден среди контактов агента"}
    if len(found) > 1:
        exact = [c for c in found if (c.name or "").strip().lower() == query.lower()]
        if len(exact) == 1:
            return exact[0], None
        return None, {
            "ok": False,
            "error": "Нашлось несколько клиентов — уточни, кого именно, и повтори с agent_contact_id",
            "candidates": [{"agent_contact_id": str(c.id), "name": c.name, "phone": c.phone} for c in found],
        }
    return found[0], None


async def fn_pause_agent_for_contact(args: dict, user_id: str, agent_config_id: str, db: Session) -> dict:
    contact, err = _find_contact_for_pause(args, user_id, agent_config_id, db)
    if err:
        return err
    minutes = args.get("minutes")
    try:
        minutes = int(minutes) if minutes not in (None, "") else None
    except (TypeError, ValueError):
        minutes = None
    if args.get("forever") or not minutes or minutes <= 0:
        minutes = -1
    agent_pause.set_pause(contact, minutes, "command")
    db.commit()
    return {
        "ok": True,
        "agent_contact_id": str(contact.id),
        "name": contact.name or contact.phone,
        "status": agent_pause.describe(contact),
    }


async def fn_resume_agent_for_contact(args: dict, user_id: str, agent_config_id: str, db: Session) -> dict:
    contact, err = _find_contact_for_pause(args, user_id, agent_config_id, db)
    if err:
        return err
    was = agent_pause.describe(contact)
    agent_pause.clear_pause(contact)
    db.commit()
    return {
        "ok": True,
        "agent_contact_id": str(contact.id),
        "name": contact.name or contact.phone,
        "was": was,
        "status": "агент снова отвечает клиенту (ответит на его следующее сообщение)",
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


async def fn_search_knowledge_base(args: dict, agent_config, db: Session,
                                   channel: Optional[str] = None) -> dict:
    """
    Поиск по базе знаний агента для оркестратора (чат, мессенджеры, разбор звонка).

    Слои и порог близости — в agent_knowledge_service.answer_query: записи базы
    в Postgres (единицы мс, без сети), документы в Pinecone только если в
    записях пусто. Тот же код вызывает голосовой агент на звонке
    (backend/functions/search_knowledge_base.py), поэтому поведение в чате и в
    трубке одинаковое — до ветки 1610-kb-calls это были две разные реализации,
    и на звонке работала та, что не видела ни пар «вопрос — ответ», ни каталога.
    """
    from backend.services import agent_knowledge_service as kb

    return await kb.answer_query(
        db,
        getattr(agent_config, "id", None) if agent_config else None,
        getattr(agent_config, "kb_namespace", None) if agent_config else None,
        (args.get("query") or "").strip(),
        top_k=int(args.get("top_k") or 4),
        channel=channel,
    )


async def fn_find_products(args: dict, agent_config, db: Session) -> dict:
    """
    Подбор позиций каталога для оркестратора.

    Реализация общая с голосовым агентом (backend/functions/find_products.py) —
    agent_knowledge_service.answer_products. Цена фильтруется в SQL: «наушники
    до 3000» — это число, а по числам вектора врут.
    """
    from backend.services import agent_knowledge_service as kb

    return await kb.answer_products(
        db,
        getattr(agent_config, "id", None) if agent_config else None,
        query=(args.get("query") or "").strip(),
        max_price=args.get("max_price"),
        min_price=args.get("min_price"),
        limit=args.get("limit") or 5,
    )


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
    "find_products": "fn_find_products",
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
    "pause_agent_for_contact": "fn_pause_agent_for_contact",
    "resume_agent_for_contact": "fn_resume_agent_for_contact",
    "get_call_transcript": "fn_get_call_transcript",
    "get_period_report": "fn_get_period_report",
    "get_failed_calls": "fn_get_failed_calls",
    "find_client": "fn_find_client",
    "merge_contacts": "fn_merge_contacts",
    "add_contact_phone": "fn_add_contact_phone",
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
        elif tool_name == "check_phone_operator":
            result = await fn_check_phone_operator(tool_args, user_id, agent_config_id, db)
        elif tool_name == "find_client":
            result = await fn_find_client(tool_args, user_id, agent_config_id, db)
        elif tool_name == "merge_contacts":
            result = await fn_merge_contacts(tool_args, user_id, agent_config_id, db,
                                             agent_config=context.get("agent_config"))
        elif tool_name == "add_contact_phone":
            result = await fn_add_contact_phone(tool_args, user_id, agent_config_id, db)
        elif tool_name == "send_proposal":
            result = await fn_send_proposal(tool_args, user_id, agent_config_id, db)
        elif tool_name == "notify_owner_handoff":
            result = await fn_notify_owner_handoff(tool_args, user_id, agent_config_id, db)
        elif tool_name == "snooze_contact":
            result = await fn_snooze_contact(tool_args, user_id, agent_config_id, db)
        elif tool_name == "pause_agent_for_contact":
            result = await fn_pause_agent_for_contact(tool_args, user_id, agent_config_id, db)
        elif tool_name == "resume_agent_for_contact":
            result = await fn_resume_agent_for_contact(tool_args, user_id, agent_config_id, db)
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
            result = await fn_search_knowledge_base(tool_args, agent_config, db,
                                                    channel=context.get("channel"))
        elif tool_name == "find_products":
            agent_config = context.get("agent_config")
            if agent_config is None and agent_config_id:
                agent_config = db.query(AgentConfig).filter(AgentConfig.id == agent_config_id).first()
            result = await fn_find_products(tool_args, agent_config, db)
        elif tool_name == "send_webhook":
            agent_config = context.get("agent_config")
            if agent_config is None and agent_config_id:
                agent_config = db.query(AgentConfig).filter(AgentConfig.id == agent_config_id).first()
            result = await fn_send_webhook(tool_args, agent_config, db)
        elif tool_name in ("telegram_send_message", "whatsapp_send_message") \
                and _repeats_voice(tool_name, tool_args, db):
            result = {"ok": False, "error": (
                "Этот текст повторяет только что отправленное голосовое — не дублируй его. "
                "Отправь текстом только дополнение: адрес, цены, номера, ссылки или короткий "
                "список того, что нужно от клиента. Если дополнять нечего — ничего не отправляй.")}
        elif tool_name == "telegram_send_message":
            agent_config = context.get("agent_config")
            if agent_config is None and agent_config_id:
                agent_config = db.query(AgentConfig).filter(AgentConfig.id == agent_config_id).first()
            result = await chat_split.send_in_parts(
                lambda a, i, skip: fn_telegram_send_message(
                    a, user_id, agent_config, db, skip_rate_limit=skip,
                    typing_seconds=chat_split.typing_seconds(a.get("text")) if i else None,
                ),
                tool_args, pause_between=False,
            )
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
            result = await chat_split.send_in_parts(
                lambda a, i, skip: fn_instagram_send_message(a, user_id, agent_config, db, skip_rate_limit=skip),
                tool_args, pause_between=True,
            )
        elif tool_name == "instagram_get_thread":
            result = await fn_instagram_get_thread(tool_args, user_id, agent_config_id, db)
        elif tool_name in ("whatsapp_send_message", "whatsapp_send_file"):
            agent_config = context.get("agent_config")
            if agent_config is None and agent_config_id:
                agent_config = db.query(AgentConfig).filter(AgentConfig.id == agent_config_id).first()
            if tool_name == "whatsapp_send_message":
                result = await chat_split.send_in_parts(
                    lambda a, i, skip: fn_whatsapp_send_message(
                        a, user_id, agent_config, db, skip_rate_limit=skip,
                        typing_ms=int(chat_split.typing_seconds(a.get("text")) * 1000) if i else None,
                    ),
                    tool_args, pause_between=False,
                )
            else:
                result = await fn_whatsapp_send_file(tool_args, user_id, agent_config, db)
        elif tool_name == "send_voice_message":
            agent_config = context.get("agent_config")
            if agent_config is None and agent_config_id:
                agent_config = db.query(AgentConfig).filter(AgentConfig.id == agent_config_id).first()
            result = await fn_send_voice_message(tool_args, user_id, agent_config, db)
        elif tool_name == "whatsapp_get_thread":
            result = await fn_whatsapp_get_thread(tool_args, user_id, agent_config_id, db)
        elif tool_name in ("telegram_send_file", "instagram_send_file"):
            agent_config = context.get("agent_config")
            if agent_config is None and agent_config_id:
                agent_config = db.query(AgentConfig).filter(AgentConfig.id == agent_config_id).first()
            fn = fn_telegram_send_file if tool_name == "telegram_send_file" else fn_instagram_send_file
            result = await fn(tool_args, user_id, agent_config, db)
        elif tool_name == "list_agent_files":
            result = await fn_list_agent_files(tool_args, agent_config_id, db)
        elif tool_name == "read_agent_file":
            result = await fn_read_agent_file(tool_args, agent_config_id, db)
        elif tool_name == "create_document":
            result = await fn_create_document(tool_args, user_id, agent_config_id, db)
        elif composio_service.is_composio_tool(tool_name):
            result = await fn_execute_connector(tool_name, tool_args, agent_config_id, db)
        else:
            result = {"ok": False, "error": f"Unknown tool: {tool_name}"}

        return json.dumps(result, ensure_ascii=False, default=str)

    except Exception as e:
        logger.error(f"[AGENT-TOOLS] Error executing {tool_name}: {e}", exc_info=True)
        # Упавший commit оставляет сессию в состоянии PendingRollback: без отката
        # следующее же обращение оркестратора к БД роняет весь чат, а модель так
        # и не узнаёт об ошибке инструмента.
        try:
            db.rollback()
        except Exception as rb_err:
            logger.error(f"[AGENT-TOOLS] Rollback after {tool_name} failed: {rb_err}")
        return json.dumps({"ok": False, "error": str(e)})
