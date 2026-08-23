"""
Agent Orchestrator v2.1 — PreCall, PostCall, and Chat phases for Voksy AI Agent.
Uses OpenAI Responses API (gpt-5) with store=True for conversation continuity.
PostCall now uses AGENT_POSTCALL_TOOLS instead of JSON parsing.
ChatOrchestrator uses AGENT_CHAT_TOOLS for multi-turn dialog.

✅ v2.1 CHANGES:
- PostCall: поиск транскрипта по номеру телефона + временное окно (вместо session_id)
- PreCall:  сохраняет precall_log в agent_calls (стратегия, тон, ключевые факты)
- PostCall: сохраняет postcall_log в agent_calls (все tool calls + финальное решение)
- PostCall: задача на перезвон ВСЕГДА создаётся (это константа)
"""

import json
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from openai import AsyncOpenAI

from sqlalchemy.orm.attributes import flag_modified

from backend.core.logging import get_logger
from backend.db.session import SessionLocal
from backend.models.agent_config import AgentConfig
from backend.models.agent_contact import AgentContact
from backend.models.agent_call import AgentCall
from backend.models.task import Task, TaskStatus
from backend.models.conversation import Conversation
from backend.models.user import User
from backend.models.voximplant_child import VoximplantChildAccount
from backend.services.agent_tools import (
    AGENT_CHAT_TOOLS,
    AGENT_POSTCALL_TOOLS,
    execute_tool,
    to_chat_completions_tools,
    build_chat_tools,
    build_postcall_tools,
)
from backend.services.agent_prompts import build_orchestrator_prompt, build_time_block
from backend.services.openrouter_client import get_openrouter_client
from backend.core.pipeline_stages import stage_from_decision
from backend.services.credit_service import (
    CreditService,
    InsufficientCreditsError,
    SubscriptionExpiredError,
    SubscriptionRequiredError,
)

logger = get_logger(__name__)


# ============================================================================
# ЕДИНАЯ ХРОНОЛОГИЯ ОБЩЕНИЯ (звонки + SMS + Telegram)
# ============================================================================
# Один хронологический блок для промпта оркестратора во всех фазах (PreCall,
# PostCall, карточка входящего звонка). Каждый канал берётся из своего чистого
# источника, чтобы не задваивать входящие SMS/TG (они лежат и в AgentCall, и в
# своих таблицах):
#   • звонки  — AgentCall только channel="call" (голосовые), сниппет транскрипта;
#   • SMS     — sms_messages (обе стороны), целиком;
#   • Telegram— agent_telegram_messages (обе стороны), целиком.

TIMELINE_MAX_EVENTS = 40      # сколько последних событий кладём в ленту
TIMELINE_DAYS_WINDOW = 30     # окно по времени (дни)
TIMELINE_CALL_SNIPPET = 300   # длина сниппета транскрипта звонка в ленте

_CHANNEL_ICON = {"call": "📞", "sms": "✉️", "telegram": "✈️"}


def _timeline_call_events(db, agent_contact, exclude_call_id, since, limit) -> list:
    """События-звонки (только channel='call') для таймлайна. Best-effort."""
    try:
        q = db.query(AgentCall).filter(AgentCall.agent_contact_id == agent_contact.id)
        if since is not None:
            q = q.filter(AgentCall.created_at >= since)
        rows = q.order_by(AgentCall.created_at.desc()).limit(limit).all()
        events = []
        for c in rows:
            if exclude_call_id and str(c.id) == str(exclude_call_id):
                continue
            # Пропускаем SMS/TG-события (они придут из своих таблиц целиком).
            if c._resolve_channel() != "call":
                continue
            ts = c.started_at or c.created_at
            if not ts:
                continue
            direction = "исходящий" if (c.direction or "outbound") == "outbound" else "входящий"
            decision = c.post_call_decision or "—"
            snippet = ""
            if c.transcript and c.transcript.strip() != "(Транскрипт недоступен)":
                snippet = " ".join(c.transcript.split())
                if len(snippet) > TIMELINE_CALL_SNIPPET:
                    snippet = snippet[:TIMELINE_CALL_SNIPPET].rstrip() + "…"
            text = f"Звонок ({direction}), итог: {decision}"
            if snippet:
                text += f" — «{snippet}»"
            events.append((ts, "call", text))
        return events
    except Exception as e:
        logger.warning(f"[AGENT] timeline call events failed: {e}")
        return []


def _timeline_sms_events(db, agent_contact, since, limit) -> list:
    """События-SMS (обе стороны) для таймлайна. Best-effort."""
    try:
        from backend.services.sms_history import get_sms_thread
        if not agent_contact.user_id:
            return []
        child = db.query(VoximplantChildAccount).filter(
            VoximplantChildAccount.user_id == agent_contact.user_id
        ).first()
        if not child:
            return []
        rows = get_sms_thread(db, child.id, agent_contact.phone, limit=limit)
        events = []
        for m in rows:
            ts = m.received_at or m.created_at
            if not ts:
                continue
            if since is not None and _as_naive_utc(ts) < since:
                continue
            who = "агент → клиент" if (m.direction or "inbound") == "outbound" else "клиент → агент"
            events.append((ts, "sms", f"SMS, {who}: {(m.body or '').strip()}"))
        return events
    except Exception as e:
        logger.warning(f"[AGENT] timeline sms events failed: {e}")
        return []


def _timeline_telegram_events(db, agent_contact, since, limit) -> list:
    """События-Telegram (личный аккаунт, обе стороны) для таймлайна. Best-effort."""
    try:
        from backend.services.telegram_user_service import get_thread
        rows = get_thread(db, agent_contact.id, limit=limit)
        events = []
        for m in rows:
            ts = m.created_at
            if not ts:
                continue
            if since is not None and _as_naive_utc(ts) < since:
                continue
            who = "агент → клиент" if (m.direction or "inbound") == "outbound" else "клиент → агент"
            events.append((ts, "telegram", f"Telegram, {who}: {(m.body or '').strip()}"))
        return events
    except Exception as e:
        logger.warning(f"[AGENT] timeline telegram events failed: {e}")
        return []


def _as_naive_utc(dt):
    """К naive-UTC для единообразного сравнения (часть колонок tz-aware, часть — нет)."""
    if dt is not None and dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


def build_conversation_timeline(
    db, agent_contact, exclude_call_id=None,
    max_events=TIMELINE_MAX_EVENTS, days_window=TIMELINE_DAYS_WINDOW,
) -> str:
    """
    Единая хронология общения с контактом по всем каналам — для промпта.

    Сливает звонки (channel='call'), SMS и Telegram в один список, сортирует по
    времени, берёт последние max_events в окне days_window дней и форматирует с
    метками МСК. exclude_call_id — исключить конкретный AgentCall (текущее
    событие в PostCall или показанный отдельно последний звонок в PreCall).
    max_events/days_window по умолчанию — константы фаз; чат-тулза
    get_contact_timeline может запросить более широкое окно по требованию
    владельца. Пустая строка, если истории нет. Best-effort — не роняет промпт.
    """
    from backend.core.timezone_utils import utc_to_msk
    try:
        if not agent_contact:
            return ""
        since = datetime.utcnow() - timedelta(days=days_window) if days_window else None
        events = []
        events += _timeline_call_events(db, agent_contact, exclude_call_id, since, max_events)
        events += _timeline_sms_events(db, agent_contact, since, max_events)
        events += _timeline_telegram_events(db, agent_contact, since, max_events)
        if not events:
            return ""
        # Сортируем по времени (naive-UTC), берём последние N.
        events.sort(key=lambda e: _as_naive_utc(e[0]))
        events = events[-max_events:]
        lines = []
        for ts, channel, text in events:
            tm = utc_to_msk(_as_naive_utc(ts)).strftime("%d.%m %H:%M")
            icon = _CHANNEL_ICON.get(channel, "•")
            lines.append(f"[{tm}] {icon} {text}")
        return (
            "\n\nХРОНОЛОГИЯ ОБЩЕНИЯ С КОНТАКТОМ (все каналы, время МСК, старые → новые):\n"
            + "\n".join(lines)
        )
    except Exception as e:
        logger.warning(f"[AGENT] build_conversation_timeline failed: {e}")
        return ""


def last_call_full_block(db, agent_contact, exclude_call_id=None):
    """
    Полный транскрипт последнего голосового звонка (channel='call') для PreCall.
    Без обрезки. Возвращает кортеж (block_text, call_id): block_text — блок для
    промпта ('' если звонков не было), call_id — id показанного звонка (или None),
    чтобы исключить его из ленты хронологии. Best-effort.
    """
    try:
        if not agent_contact:
            return "", None
        rows = (
            db.query(AgentCall)
            .filter(AgentCall.agent_contact_id == agent_contact.id)
            .order_by(AgentCall.created_at.desc())
            .limit(10)
            .all()
        )
        for c in rows:
            if exclude_call_id and str(c.id) == str(exclude_call_id):
                continue
            if c._resolve_channel() != "call":
                continue
            if not c.transcript or c.transcript.strip() == "(Транскрипт недоступен)":
                continue
            day = c.created_at.strftime("%d.%m.%Y %H:%M") if c.created_at else "?"
            direction = "исходящий" if (c.direction or "outbound") == "outbound" else "входящий"
            decision = c.post_call_decision or "—"
            block = (
                f"\n\nПОСЛЕДНИЙ ЗВОНОК ПОЛНОСТЬЮ [{day}] ({direction}, итог: {decision}):\n"
                f"{c.transcript.strip()}"
            )
            return block, c.id
        return "", None
    except Exception as e:
        logger.warning(f"[AGENT] last_call_full_block failed: {e}")
        return "", None


def _extract_usage(response: dict) -> tuple:
    """Достать (prompt_tokens, completion_tokens) из ответа OpenRouter."""
    usage = response.get("usage") or {}
    return int(usage.get("prompt_tokens", 0) or 0), int(usage.get("completion_tokens", 0) or 0)


CHAT_META_PROMPT = """# РОЛЬ
Ты — AI-оркестратор системы автономных звонков Voksy AI. Ты являешься центральным мозгом системы: управляешь базой контактов, планируешь звонки, анализируешь результаты и отвечаешь владельцу бизнеса на вопросы о работе агента. Ты общаешься с пользователем через текстовый чат.

# МЫШЛЕНИЕ (думай пошагово перед каждым ответом)
1. Определи — нужен ли tool для ответа на вопрос пользователя
2. Если вопрос касается контактов / задач / звонков / статистики — ВСЕГДА вызови tool первым
3. Получи реальные данные из БД → проанализируй → ответь на их основе
4. Никогда не выдумывай данные — только из tools

# ИНСТРУМЕНТЫ И КОГДА ИХ ИСПОЛЬЗОВАТЬ
- get_agent_contacts — когда спрашивают о контактах, их статусах, количестве, списке
- get_agent_tasks — когда спрашивают о задачах, расписании, следующих звонках по контакту
- get_contact_call_history — когда спрашивают об истории звонков конкретного контакта
- get_agent_stats — когда спрашивают о статистике, результатах, эффективности агента
- create_agent_contact — когда просят добавить новый контакт в базу обзвона
- create_agent_task — когда просят запланировать звонок
  → ОБЯЗАТЕЛЬНО перед созданием: вызови get_agent_tasks(agent_contact_id=..., status_filter="scheduled")
  → Если уже есть задача в статусе scheduled — сообщи пользователю, не создавай дубль
- delete_agent_task — когда просят удалить / убрать / отменить запланированную задачу или звонок
  → Сначала вызови get_agent_tasks, чтобы найти task_id нужной задачи, затем удали по нему
  → Удаление необратимо: после него задача исчезает из календаря и не будет выполнена
- update_contact_info — когда просят обновить данные контакта (имя/компания/должность/заметки),
  например «запиши что Иванов просил перезвонить в среду» или «у него новая должность»
- search_contacts — поиск контактов по имени/телефону/компании/стадии (вместо выгрузки всех)
- get_contact_details — полная карточка одного контакта (память, заметки, факты, попытки)
- get_contacts_by_stage — разбивка контактов по стадиям воронки (счётчики + примеры)
- bulk_create_contacts — массовое добавление списка контактов одним вызовом
- delete_agent_contact — удалить контакт из базы (необратимо; только по явной просьбе)
- append_contact_note — дописать заметку, НЕ стирая старые («запиши, что…»)
- update_agent_task — перенести/переименовать существующий звонок (сначала найди task_id)
- get_upcoming_schedule — календарь ближайших звонков по всем контактам
- bulk_schedule_calls — запланировать звонки сразу группе контактов (по списку или стадии)
- trigger_immediate_call — позвонить контакту прямо сейчас (только по явной просьбе)
- snooze_contact — поставить контакт на паузу до даты (отменяет задачи; это не отказ)
- get_call_transcript — полный текст конкретного звонка (сначала agent_call_id из истории)
- get_period_report — сводный отчёт по звонкам за период (неделя/месяц)
- get_failed_calls — очередь на перезвон: кому не дозвонились

# ПОРЯДОК ВЫЗОВА
- Сначала ЧИТАЙ (get_/search_), потом ДЕЙСТВУЙ (create_/update_/delete_/trigger_).
- Не выдумывай id — бери их из результатов предыдущих вызовов.
- Перед планированием звонка всегда проверяй существующие через get_agent_tasks/get_upcoming_schedule.

# ПРАВИЛА РАБОТЫ С ЗАДАЧАМИ
- Перед созданием новой задачи — всегда проверяй существующие через get_agent_tasks
- Если уже есть SCHEDULED задача по контакту — сообщи об этом пользователю
- Система автоматически отменяет старые задачи при создании новой

# ПРАВИЛА ОТВЕТОВ
- Отвечай кратко и по делу на русском языке
- Приводи конкретные данные: имена, номера, даты, статусы
- Если данных нет в БД — скажи прямо, не додумывай
- Предлагай следующий шаг если это уместно

# КОНТЕКСТ БИЗНЕСА ПОЛЬЗОВАТЕЛЯ (ниже)
---
"""

CHAT_SUFFIX = "\n---\nВыше — контекст бизнеса пользователя. Используй его для понимания продукта, целевой аудитории и стиля общения при планировании и анализе звонков."

# ✅ v2.2: Подсказка по форматированию для Telegram-канала.
# Telegram — узкий мобильный экран и ограниченный набор разметки.
TELEGRAM_FORMAT_HINT = """

# ФОРМАТИРОВАНИЕ ОТВЕТА (Telegram)
Ты отвечаешь в Telegram — узкий мобильный экран. Соблюдай правила:
- Пиши кратко и по делу, короткими абзацами.
- Для перечислений используй маркеры «- » или цифры, НЕ таблицы.
- НЕ рисуй markdown-таблицы (| ... | ... |) — на телефоне они нечитаемы.
  Вместо таблицы дай список «Поле: значение» по каждому пункту.
- Допустим лёгкий markdown: **жирный**, *курсив*, `моноширинный`.
- Не используй заголовки решётками (#) и горизонтальные линии."""


# Rich-вариант хинта: используется для путей, отправляемых через sendRichMessage
# (Bot API rich messages). Здесь поддерживается полноценный Markdown — заголовки,
# таблицы, списки, цитаты, сворачиваемые блоки. Если клиент старый и rich не
# отрисуется, сервер откатывается на markdown_to_telegram_html, который умеет
# деградировать все эти конструкции (таблица→<pre>, заголовок→<b> и т.д.).
TELEGRAM_RICH_FORMAT_HINT = """

# ФОРМАТИРОВАНИЕ ОТВЕТА (Telegram Rich)
Твой ответ рендерится в Telegram с поддержкой богатого Markdown. Правила:
- Пиши структурно и наглядно. Можно использовать заголовки (##), списки (- / 1.),
  **жирный**, *курсив*, `код`, блоки кода ```lang.
- Для сравнения и наборов данных допустимы markdown-таблицы | ... | ... | — они
  корректно отрисовываются.
- Цитаты оформляй через «> ». Разделители «---» допустимы.
- Длинные фрагменты (полный список, детали, транскрипт) сворачивай в
  <details><summary>Заголовок</summary> … </details>, чтобы не загромождать экран.
- Не вставляй медиа по ссылкам, если пользователь об этом не просил.
- Самое важное помещай в начало ответа."""


# ============================================================================
# PRE-CALL ORCHESTRATOR
# ============================================================================

class PreCallOrchestrator:
    """Prepares call strategy using gpt-5 Responses API."""

    async def run(
        self,
        task: Task,
        agent_contact: AgentContact,
        agent_call: AgentCall,
        agent_config: AgentConfig,
        user: User,
        db
    ) -> Dict[str, Any]:
        """
        Run PreCall phase: generate first_phrase and call strategy.
        Returns dict with: first_phrase, call_strategy, tone, key_points

        Развилка по uses_hardcoded_prompt:
        - v3 (TRUE):  OpenRouter Chat Completions + захардкоженный промпт.
        - v2 (FALSE): OpenAI Responses API + сгенерированный промпт (старые агенты).
        """
        if getattr(agent_config, "uses_hardcoded_prompt", False):
            return await self._run_v3_openrouter(task, agent_contact, agent_call, agent_config, user, db)
        return await self._run_v2_responses_api(task, agent_contact, agent_call, agent_config, user, db)

    def _build_precall_input(self, task, agent_contact, db) -> str:
        memory_json = json.dumps(agent_contact.memory or {}, ensure_ascii=False)

        # Последний звонок — полным транскриптом; вся остальная история
        # (SMS, Telegram, старые звонки) — единой хронологией. Последний звонок
        # исключаем из ленты, чтобы не дублировать его же выше.
        last_call, last_call_id = last_call_full_block(db, agent_contact)
        timeline = build_conversation_timeline(db, agent_contact, exclude_call_id=last_call_id)

        return f"""ЗАДАЧА: {task.title}
ОПИСАНИЕ: {task.description or 'Нет описания'}
КОНТАКТ: {agent_contact.name or 'Неизвестный'} ({agent_contact.phone})
КОМПАНИЯ: {agent_contact.company or 'Не указана'}
ДОЛЖНОСТЬ: {agent_contact.position or 'Не указана'}
ПАМЯТЬ О КОНТАКТЕ: {memory_json}
ПОПЫТКА: {agent_contact.attempts_count + 1}{last_call}{timeline}"""

    async def _run_v3_openrouter(
        self,
        task: Task,
        agent_contact: AgentContact,
        agent_call: AgentCall,
        agent_config: AgentConfig,
        user: User,
        db
    ) -> Dict[str, Any]:
        """PreCall v3 — OpenRouter Chat Completions, JSON-стратегия, без tools."""
        logger.info(f"[AGENT-PRECALL] (v3/OpenRouter) Starting for task {task.id}, "
                    f"contact {agent_contact.name or agent_contact.phone}, model {agent_config.orchestrator_model}")

        # Pre-flight проверка подписки/кредитов (раздел 5.1)
        CreditService.precheck(db, user)

        # Статичный system + время в user-сообщении: префикс запроса
        # (tools + system) байт-в-байт одинаков между звонками → кэш провайдера.
        system_prompt = build_orchestrator_prompt(agent_config, include_time_block=False)
        base_input = self._build_precall_input(task, agent_contact, db)
        user_input = base_input + build_time_block(round_to_minutes=0) + """

Подготовь звонок. Верни ответ строго в JSON формате без markdown:
{"first_phrase": "точная первая фраза агента", "call_strategy": "краткое описание тактики", "tone": "дружелюбный/деловой/настойчивый", "key_points": ["факт1", "факт2"]}"""

        output_text = ""
        try:
            client = get_openrouter_client()
            response = await client.chat_completion(
                model=agent_config.orchestrator_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_input},
                ],
                tools=None,
                temperature=0.7,
            )
            output_text = response["choices"][0]["message"].get("content", "") or ""
            logger.info(f"[AGENT-PRECALL] (v3) Raw response: {output_text[:200]}")

            # Списываем кредиты за вызов оркестратора (раздел 5.2)
            p_tok, c_tok = _extract_usage(response)
            try:
                CreditService.charge(
                    db=db, user_id=user.id,
                    model_slug=agent_config.orchestrator_model,
                    prompt_tokens=p_tok, completion_tokens=c_tok,
                    ref_type="precall", ref_id=agent_call.id, notes="precall",
                )
            except Exception as ce:
                logger.error(f"[AGENT-PRECALL] (v3) Charge failed: {ce}", exc_info=True)

            json_text = output_text
            if "```json" in json_text:
                json_text = json_text.split("```json")[1].split("```")[0]
            elif "```" in json_text:
                json_text = json_text.split("```")[1].split("```")[0]

            result = json.loads(json_text.strip())

            agent_call.pre_call_response_id = None
            agent_call.custom_greeting = result.get("first_phrase", "")
            agent_call.call_strategy = result.get("call_strategy", "")

            agent_call.precall_log = {
                "response_id": None,
                "model": agent_config.orchestrator_model,
                "first_phrase": result.get("first_phrase", ""),
                "call_strategy": result.get("call_strategy", ""),
                "tone": result.get("tone", ""),
                "key_points": result.get("key_points", []),
                "attempts_before": agent_contact.attempts_count,
                "memory_snapshot": agent_contact.memory or {},
                "generated_at": datetime.utcnow().isoformat(),
            }

            task.custom_greeting = result.get("first_phrase", "")
            db.commit()

            logger.info(f"[AGENT-PRECALL] (v3) ✅ Success. Strategy: {result.get('call_strategy', '')[:80]}")
            return result

        except json.JSONDecodeError as e:
            logger.error(f"[AGENT-PRECALL] (v3) JSON parse error: {e}")
            agent_call.pre_call_response_id = None
            agent_call.custom_greeting = output_text[:200]
            agent_call.call_strategy = "fallback"
            agent_call.precall_log = {
                "error": "json_parse_error",
                "raw_output": output_text[:500],
                "model": agent_config.orchestrator_model,
                "generated_at": datetime.utcnow().isoformat(),
            }
            task.custom_greeting = agent_call.custom_greeting
            db.commit()
            return {"first_phrase": agent_call.custom_greeting, "call_strategy": "fallback", "tone": "деловой", "key_points": []}

        except Exception as e:
            logger.error(f"[AGENT-PRECALL] (v3) Error: {e}", exc_info=True)
            raise

    async def _run_v2_responses_api(
        self,
        task: Task,
        agent_contact: AgentContact,
        agent_call: AgentCall,
        agent_config: AgentConfig,
        user: User,
        db
    ) -> Dict[str, Any]:
        """PreCall v2 (legacy) — OpenAI Responses API + сгенерированный промпт."""
        logger.info(f"[AGENT-PRECALL] (v2/Responses) Starting for task {task.id}, contact {agent_contact.name or agent_contact.phone}")

        client = AsyncOpenAI(api_key=user.openai_api_key)

        memory_json = json.dumps(agent_contact.memory or {}, ensure_ascii=False)

        # Тот же контекст, что и в v3: последний звонок полностью + единая
        # хронология (звонки + SMS + Telegram), исключая показанный звонок.
        last_call, last_call_id = last_call_full_block(db, agent_contact)
        timeline = build_conversation_timeline(db, agent_contact, exclude_call_id=last_call_id)

        pre_call_input = f"""ЗАДАЧА: {task.title}
ОПИСАНИЕ: {task.description or 'Нет описания'}
КОНТАКТ: {agent_contact.name or 'Неизвестный'} ({agent_contact.phone})
КОМПАНИЯ: {agent_contact.company or 'Не указана'}
ДОЛЖНОСТЬ: {agent_contact.position or 'Не указана'}
ПАМЯТЬ О КОНТАКТЕ: {memory_json}
ПОПЫТКА: {agent_contact.attempts_count + 1}{last_call}{timeline}

Подготовь звонок. Верни JSON:
{{
  "first_phrase": "точная первая фраза агента",
  "call_strategy": "краткое описание тактики",
  "tone": "дружелюбный/деловой/настойчивый",
  "key_points": ["факт1", "факт2"]
}}"""

        try:
            response = await client.responses.create(
                model="gpt-5-2025-08-07",
                instructions=agent_config.orchestrator_prompt or "",
                input=pre_call_input,
                store=True,
            )

            output_text = response.output_text
            logger.info(f"[AGENT-PRECALL] Raw response: {output_text[:200]}")

            json_text = output_text
            if "```json" in json_text:
                json_text = json_text.split("```json")[1].split("```")[0]
            elif "```" in json_text:
                json_text = json_text.split("```")[1].split("```")[0]

            result = json.loads(json_text.strip())

            agent_call.pre_call_response_id = response.id
            agent_call.custom_greeting = result.get("first_phrase", "")
            agent_call.call_strategy = result.get("call_strategy", "")

            # ✅ v2.1: Сохраняем precall_log
            agent_call.precall_log = {
                "response_id": response.id,
                "model": "gpt-5-2025-08-07",
                "first_phrase": result.get("first_phrase", ""),
                "call_strategy": result.get("call_strategy", ""),
                "tone": result.get("tone", ""),
                "key_points": result.get("key_points", []),
                "attempts_before": agent_contact.attempts_count,
                "memory_snapshot": agent_contact.memory or {},
                "generated_at": datetime.utcnow().isoformat(),
            }

            task.pre_call_response_id = response.id
            task.custom_greeting = result.get("first_phrase", "")
            db.commit()

            logger.info(f"[AGENT-PRECALL] ✅ Success. Strategy: {result.get('call_strategy', '')[:80]}")
            logger.info(f"[AGENT-PRECALL] precall_log saved to agent_call {agent_call.id}")
            return result

        except json.JSONDecodeError as e:
            logger.error(f"[AGENT-PRECALL] JSON parse error: {e}")
            agent_call.pre_call_response_id = response.id if 'response' in dir() else None
            agent_call.custom_greeting = output_text[:200] if 'output_text' in dir() else ""
            agent_call.precall_log = {
                "error": "json_parse_error",
                "raw_output": output_text[:500] if 'output_text' in dir() else "",
                "generated_at": datetime.utcnow().isoformat(),
            }
            task.pre_call_response_id = agent_call.pre_call_response_id
            task.custom_greeting = agent_call.custom_greeting
            db.commit()
            return {"first_phrase": agent_call.custom_greeting, "call_strategy": "fallback", "tone": "деловой", "key_points": []}

        except Exception as e:
            logger.error(f"[AGENT-PRECALL] Error: {e}", exc_info=True)
            raise


# ============================================================================
# POST-CALL ORCHESTRATOR
# ============================================================================

class PostCallOrchestrator:
    """Analyzes call results using GPT-5 with AGENT_POSTCALL_TOOLS."""

    @staticmethod
    def _find_transcript_by_phone(
        db,
        phone: str,
        call_time: datetime,
        window_minutes_before: int = 2,
        window_minutes_after: int = 20,
    ) -> List[Conversation]:
        """
        ✅ v2.1 FIX: Поиск транскрипта по номеру телефона + временное окно.

        Проблема была:
        - agent_calls.call_session_id = "4489857542" (Voximplant History ID)
        - conversations.session_id    = "vox_abc123" (UUID из сценария)
        Они никогда не совпадали → "транскрипт недоступен".

        Решение: ищем по последним 10 цифрам номера + временному окну.
        """
        phone_suffix = phone[-10:] if len(phone) >= 10 else phone

        time_from = call_time - timedelta(minutes=window_minutes_before)
        time_to = call_time + timedelta(minutes=window_minutes_after)

        convs = db.query(Conversation).filter(
            Conversation.caller_number.like(f"%{phone_suffix}%"),
            Conversation.created_at >= time_from,
            Conversation.created_at <= time_to,
        ).order_by(Conversation.created_at.asc()).all()

        return convs

    @staticmethod
    def _claim_for_finalization(db, agent_call_id: str, allowed_statuses: List[str]) -> bool:
        """
        Атомарно «забирает» звонок под финализацию: переводит его в статус
        'finalizing', только если текущий статус входит в allowed_statuses.

        Нужно, чтобы два пути финализации (event-driven из вебхука /log и
        таймерный резервный поллер poll_and_run) не обработали один звонок
        дважды — они могут выполняться в разных воркерах Gunicorn, поэтому
        блокировка делается на уровне БД одним UPDATE ... WHERE.

        Возвращает True, если звонок удалось забрать (можно продолжать анализ).
        """
        claimed = db.query(AgentCall).filter(
            AgentCall.id == agent_call_id,
            AgentCall.status.in_(allowed_statuses),
        ).update({"status": "finalizing"}, synchronize_session=False)
        db.commit()
        return bool(claimed)

    @staticmethod
    async def poll_and_run(
        agent_call_id: str,
        agent_config_id: str,
        user_openai_key: str,
        retries: int = 20,
        delay: int = 15
    ):
        """
        Poll for conversation transcript and run PostCall analysis.
        Opens its own DB session — safe for asyncio.create_task().
        """
        logger.info(f"[AGENT-POSTCALL] Starting poll for agent_call {agent_call_id}")

        db = SessionLocal()
        try:
            agent_call = db.query(AgentCall).filter(AgentCall.id == agent_call_id).first()
            if not agent_call:
                logger.error(f"[AGENT-POSTCALL] AgentCall {agent_call_id} not found")
                return

            agent_config = db.query(AgentConfig).filter(AgentConfig.id == agent_config_id).first()
            if not agent_config:
                logger.error(f"[AGENT-POSTCALL] AgentConfig {agent_config_id} not found")
                return

            agent_contact = db.query(AgentContact).filter(
                AgentContact.id == agent_call.agent_contact_id
            ).first()
            if not agent_contact:
                logger.error(f"[AGENT-POSTCALL] AgentContact not found for call {agent_call_id}")
                return

            user = db.query(User).filter(User.id == agent_call.user_id).first()

            task = None
            if agent_call.source_task_id:
                task = db.query(Task).filter(Task.id == agent_call.source_task_id).first()

            # Poll for transcript
            transcript = None
            call_status = "no_answer"
            duration_seconds = 0

            call_time = agent_call.started_at or agent_call.created_at

            for attempt in range(retries):
                await asyncio.sleep(delay)
                logger.info(f"[AGENT-POSTCALL] Poll attempt {attempt + 1}/{retries} for call {agent_call_id}")

                db.refresh(agent_call)

                # ============================================================
                # ✅ v2.1 FIX: Ищем по номеру + временному окну
                # ============================================================
                convs = PostCallOrchestrator._find_transcript_by_phone(
                    db=db,
                    phone=agent_contact.phone,
                    call_time=call_time,
                )

                # Fallback: старый поиск по session_id
                if not convs and agent_call.call_session_id:
                    convs = db.query(Conversation).filter(
                        Conversation.session_id == agent_call.call_session_id
                    ).all()
                    if convs:
                        logger.info(f"[AGENT-POSTCALL] Found via session_id fallback")

                if convs:
                    logger.info(f"[AGENT-POSTCALL] Found {len(convs)} record(s) for {agent_contact.phone}")
                    transcript_parts = []

                    for conv in convs:
                        if conv.client_info and isinstance(conv.client_info, dict):
                            dialog = conv.client_info.get("dialog", [])
                            for turn in dialog:
                                role = turn.get("role", "unknown")
                                text = turn.get("text", "")
                                if text:
                                    label = "Агент" if role == "assistant" else "Клиент"
                                    transcript_parts.append(f"{label}: {text}")

                        if conv.duration_seconds:
                            duration_seconds = max(duration_seconds, conv.duration_seconds or 0)

                    if transcript_parts:
                        transcript = "\n".join(transcript_parts)
                        call_status = "answered"
                        logger.info(f"[AGENT-POSTCALL] ✅ Found transcript ({len(transcript_parts)} turns)")
                        break
                    else:
                        logger.info(f"[AGENT-POSTCALL] Records found but no dialog turns yet, retrying...")
                else:
                    logger.info(f"[AGENT-POSTCALL] No records for {agent_contact.phone} in time window")

            if not transcript:
                call_status = "no_transcript"
                transcript = "(Транскрипт недоступен)"
                logger.warning(f"[AGENT-POSTCALL] No transcript found after {retries} attempts")

            # ✅ Идемпотентность: забираем звонок, только если его ещё не
            #   финализировал event-driven путь (вебхук /log). Если не удалось —
            #   значит звонок уже обработан, выходим без повторного анализа.
            if not PostCallOrchestrator._claim_for_finalization(db, agent_call_id, ["calling"]):
                logger.info(f"[AGENT-POSTCALL] Call {agent_call_id} already finalized elsewhere, skipping reserve poller")
                return
            db.refresh(agent_call)

            orchestrator = PostCallOrchestrator()
            await orchestrator._analyze(
                agent_call=agent_call,
                agent_contact=agent_contact,
                agent_config=agent_config,
                user=user,
                task=task,
                transcript=transcript,
                call_status=call_status,
                duration_seconds=duration_seconds,
                openai_key=user_openai_key,
                db=db
            )

        except Exception as e:
            logger.error(f"[AGENT-POSTCALL] Fatal error: {e}", exc_info=True)
        finally:
            db.close()

    @staticmethod
    async def finalize_from_webhook(agent_call_id: str, call_direction: str = "outbound"):
        """
        ✅ Event-driven финализация звонка агента.

        Вызывается из вебхука Voximplant POST /log в момент, когда транскрипт
        звонка реально сохранён в таблице conversations. В отличие от таймерного
        poll_and_run (который сдаётся через несколько минут и не успевает за
        длинными звонками — транскрипт пишется только ПОСЛЕ окончания разговора),
        этот путь срабатывает ровно тогда, когда данные уже есть, независимо от
        длительности звонка.

        Открывает собственную сессию БД — безопасно для asyncio.create_task().
        """
        logger.info(f"[AGENT-POSTCALL] (webhook) Finalizing agent_call {agent_call_id}")

        db = SessionLocal()
        try:
            agent_call = db.query(AgentCall).filter(AgentCall.id == agent_call_id).first()
            if not agent_call:
                logger.warning(f"[AGENT-POSTCALL] (webhook) AgentCall {agent_call_id} not found")
                return

            # Уже успешно финализирован — ничего не делаем.
            if agent_call.status == "answered":
                logger.info(f"[AGENT-POSTCALL] (webhook) call {agent_call_id} already answered, skip")
                return

            agent_config = db.query(AgentConfig).filter(
                AgentConfig.id == agent_call.agent_config_id
            ).first()
            agent_contact = db.query(AgentContact).filter(
                AgentContact.id == agent_call.agent_contact_id
            ).first()
            user = db.query(User).filter(User.id == agent_call.user_id).first()

            if not agent_config or not agent_contact:
                logger.warning(f"[AGENT-POSTCALL] (webhook) config/contact missing for {agent_call_id}")
                return

            # v3-агенты оркестрируются на системном ключе; v2 — только при наличии
            # личного OpenAI-ключа юзера.
            can_orchestrate = getattr(agent_config, "uses_hardcoded_prompt", False) or (
                user and user.openai_api_key
            )
            if not can_orchestrate:
                logger.info(f"[AGENT-POSTCALL] (webhook) agent can't orchestrate, skip {agent_call_id}")
                return

            # Собираем транскрипт из уже сохранённых conversations (по номеру + времени).
            call_time = agent_call.started_at or agent_call.created_at
            convs = PostCallOrchestrator._find_transcript_by_phone(
                db=db, phone=agent_contact.phone, call_time=call_time,
            )
            if not convs and agent_call.call_session_id:
                convs = db.query(Conversation).filter(
                    Conversation.session_id == agent_call.call_session_id
                ).all()

            transcript_parts = []
            duration_seconds = 0
            for conv in convs:
                if conv.client_info and isinstance(conv.client_info, dict):
                    for turn in conv.client_info.get("dialog", []):
                        text = turn.get("text", "")
                        if text:
                            label = "Агент" if turn.get("role") == "assistant" else "Клиент"
                            transcript_parts.append(f"{label}: {text}")
                if conv.duration_seconds:
                    duration_seconds = max(duration_seconds, conv.duration_seconds or 0)

            # Диалога ещё нет — оставляем звонок резервному поллеру.
            if not transcript_parts:
                logger.info(f"[AGENT-POSTCALL] (webhook) no dialog turns yet for {agent_contact.phone}, leaving to reserve poller")
                return

            # Атомарно забираем звонок. Разрешаем забрать и 'no_answer' — это даёт
            # «апгрейд» преждевременного no_answer, если резервный поллер успел
            # пометить его так до прихода транскрипта.
            if not PostCallOrchestrator._claim_for_finalization(db, agent_call_id, ["calling", "no_answer"]):
                logger.info(f"[AGENT-POSTCALL] (webhook) call {agent_call_id} already owned/finalized, skip")
                return
            db.refresh(agent_call)

            task = None
            if agent_call.source_task_id:
                task = db.query(Task).filter(Task.id == agent_call.source_task_id).first()

            transcript = "\n".join(transcript_parts)
            orchestrator = PostCallOrchestrator()
            try:
                await orchestrator._analyze(
                    agent_call=agent_call,
                    agent_contact=agent_contact,
                    agent_config=agent_config,
                    user=user,
                    task=task,
                    transcript=transcript,
                    call_status="answered",
                    duration_seconds=duration_seconds,
                    openai_key=(user.openai_api_key or "") if user else "",
                    db=db,
                    call_direction=call_direction,
                )
                logger.info(f"[AGENT-POSTCALL] (webhook) ✅ Finalized call {agent_call_id}")
            except Exception as analyze_err:
                # Возвращаем в 'calling', чтобы резервный поллер мог повторить.
                logger.error(f"[AGENT-POSTCALL] (webhook) analyze failed: {analyze_err}", exc_info=True)
                db.query(AgentCall).filter(AgentCall.id == agent_call_id).update(
                    {"status": "calling"}, synchronize_session=False
                )
                db.commit()

        except Exception as e:
            logger.error(f"[AGENT-POSTCALL] (webhook) Fatal error: {e}", exc_info=True)
        finally:
            db.close()

    def _build_postcall_input(self, agent_call, agent_contact, transcript, call_status, duration_seconds, db, call_direction: str = "outbound") -> str:
        # Вся предыстория (звонки + SMS + Telegram) — единой хронологией, исключая
        # текущее событие (оно ниже отдельным блоком «ТЕКУЩИЙ …»).
        timeline = build_conversation_timeline(db, agent_contact, exclude_call_id=agent_call.id)

        memory_json = json.dumps(agent_contact.memory or {}, ensure_ascii=False)

        # ── Контекст направления/типа события ──
        # Для входящих (клиент сам вышел на связь) логика перезвона иная, чем для
        # исходящих. Отдельно — входящее SMS: это не звонок, а сообщение клиента.
        transcript_label = "ТЕКУЩИЙ ТРАНСКРИПТ ЗВОНКА"
        status_label = "СТАТУС ЗВОНКА"
        analyze_line = "Проанализируй звонок и выполни необходимые действия через tools:"

        is_sms = (call_direction or "").lower() == "sms_inbound"
        is_tg = (call_direction or "").lower() == "telegram_inbound"
        is_tg_out = (call_direction or "").lower() == "telegram_outbound"
        is_inbound = (call_direction or "outbound").lower() == "inbound"

        if is_tg_out:
            direction_line = (
                "СОБЫТИЕ: ЗАПЛАНИРОВАННАЯ ОТПРАВКА СООБЩЕНИЯ В TELEGRAM (личный "
                "аккаунт владельца) — наступило время написать клиенту. Это не "
                "звонок и не входящее сообщение: инициатива исходит от тебя, по "
                "задаче, поставленной ранее."
            )
            callback_rule = ""  # не используется — свой план действий ниже
            transcript_label = "ИНСТРУКЦИЯ К СООБЩЕНИЮ (что и зачем написать; это НЕ готовый текст)"
            status_label = "СТАТУС"
            analyze_line = ""
        elif is_tg:
            direction_line = (
                "СОБЫТИЕ: ВХОДЯЩЕЕ СООБЩЕНИЕ В TELEGRAM (личный аккаунт владельца) — "
                "клиент написал в Telegram, это не звонок."
            )
            callback_rule = (
                "3. Если уместно ответить клиенту — ответь в Telegram через\n"
                "   telegram_send_message (тем же каналом, которым написал клиент).\n"
                "   Пиши как живой человек, коротко и по делу, без markdown.\n"
                "   Если по сути сообщения нужен звонок (клиент просит позвонить,\n"
                "   договорились о следующем шаге) — запланируй его через\n"
                "   create_agent_task. Если договорились списаться позже — запланируй\n"
                "   отложенное сообщение через schedule_telegram_message (инструкция,\n"
                "   не готовый текст). Сам факт сообщения НЕ требует звонка."
            )
            transcript_label = "ТЕКСТ ВХОДЯЩЕГО СООБЩЕНИЯ TELEGRAM"
            status_label = "СТАТУС"
            analyze_line = "Проанализируй сообщение клиента и выполни необходимые действия через tools:"
        elif is_sms:
            direction_line = (
                "СОБЫТИЕ: ВХОДЯЩЕЕ SMS от клиента (это не звонок — клиент прислал "
                "сообщение, на которое нужно среагировать)."
            )
            callback_rule = (
                "3. Если по сути сообщения нужен звонок (клиент просит перезвонить,\n"
                "   проявил интерес, договорились о следующем шаге) — ЗАПЛАНИРУЙ его\n"
                "   через create_agent_task на подходящее время (учтутся рабочие часы).\n"
                "   Сам факт SMS НЕ требует обязательного звонка.\n"
                "   Если уместно ответить клиенту текстом — отправь SMS через send_sms."
            )
            transcript_label = "ТЕКСТ ВХОДЯЩЕГО SMS"
            status_label = "СТАТУС"
            analyze_line = "Проанализируй сообщение клиента и выполни необходимые действия через tools:"
        elif is_inbound:
            direction_line = (
                "ТИП ЗВОНКА: ВХОДЯЩИЙ — клиент позвонил сам "
                "(этот звонок инициировал не агент, а сам контакт)."
            )
            callback_rule = (
                "3. Перезвон через create_agent_task планируй ТОЛЬКО если это реально\n"
                "   нужно по сути разговора (клиент попросил перезвонить позже или\n"
                "   договорились о следующем шаге). Сам факт входящего звонка НЕ\n"
                "   является поводом для авто-перезвона."
            )
        else:
            direction_line = "ТИП ЗВОНКА: ИСХОДЯЩИЙ — звонок инициировал агент."
            callback_rule = (
                "3. Следующее касание планируется ВСЕГДА, КРОМЕ случая когда цель\n"
                "   звонка уже достигнута (тогда ничего планировать не нужно).\n"
                "   Канал выбирай по ситуации:\n"
                "   - Перезвон → create_agent_task. Если клиент ответил и цель НЕ\n"
                "     достигнута / попросил перезвонить — через разумное время (1-3 дня).\n"
                "     Если не ответил — перезвони через 24 часа.\n"
                "   - Отложенное сообщение в Telegram → schedule_telegram_message\n"
                "     (если доступен): когда договорились списаться, нужно прислать\n"
                "     детали/напоминание текстом или звонок явно неуместен. Передавай\n"
                "     инструкцию «что написать», а не готовый текст."
            )

        if is_tg_out:
            action_block = """Составь и отправь сообщение клиенту:
1. Сверься с хронологией и памятью выше: если договорённость из инструкции уже
   закрыта (клиент сам ответил, состоялся звонок, вопрос решён) и сообщение
   потеряло смысл — НЕ отправляй его, просто обнови память update_contact_memory
   с пометкой, почему отправка не потребовалась.
2. Если сообщение уместно — составь текст сам по инструкции и контексту:
   как живой человек, коротко и по делу, без markdown, без канцелярита. Учти
   тон прошлых касаний. Отправь через telegram_send_message.
3. ОБЯЗАТЕЛЬНО вызови update_contact_memory — зафиксируй, что написал (или
   почему не стал) и текущее состояние договорённости.
4. Смени стадию через move_contact_stage ТОЛЬКО если есть реальное основание.
5. Если нужен следующий шаг — запланируй его: звонок через create_agent_task
   или ещё одно отложенное сообщение через schedule_telegram_message.
6. Если владельцу важно узнать результат — send_telegram_notification."""
        else:
            action_block = f"""{analyze_line}
1. ОБЯЗАТЕЛЬНО вызови update_contact_memory — обнови память о контакте.
2. Смени стадию через move_contact_stage ТОЛЬКО если для этого есть реальное
   основание. Менять стадию каждый раз НЕ нужно:
   - цель достигнута / клиент согласился → success
   - явный отказ → rejected
   - просил больше не звонить → do_not_call
   - впервые вышли на живой контакт и продолжаем работу → active
   Если ничего по сути не изменилось (клиент ещё думает) — НЕ вызывай
   move_contact_stage, оставь контакт в текущей стадии.
{callback_rule}
4. Если нужно уведомить владельца/менеджеров (важный результат) — вызови send_telegram_notification."""

        return f"""{direction_line}
КОНТАКТ: {agent_contact.name or 'Неизвестный'} ({agent_contact.phone})
КОМПАНИЯ: {agent_contact.company or 'Не указана'}
ПАМЯТЬ О КОНТАКТЕ: {memory_json}
ВСЕГО ПОПЫТОК: {agent_contact.attempts_count or 0}{timeline or ''}

{transcript_label}:
{transcript}

{status_label}: {call_status}
ДЛИТЕЛЬНОСТЬ: {duration_seconds}s
AGENT_CONTACT_ID: {str(agent_contact.id)}

{action_block}"""

    async def run_for_sms(self, agent_call, agent_contact, agent_config, user, sms_body, db):
        """
        Прогнать входящее SMS через ту же PostCall-логику, что и звонки.

        «Транскрипт» — текст SMS (полная переписка подмешивается в промпт через
        единую хронологию build_conversation_timeline). Тулзы те же (AGENT_POSTCALL_TOOLS), поэтому агент
        может уведомить менеджеров в Telegram (send_telegram_notification),
        запланировать звонок (create_agent_task), ответить (send_sms),
        обновить память/стадию.
        """
        transcript = f'Клиент прислал SMS: "{(sms_body or "").strip()}"'
        await self._analyze(
            agent_call=agent_call,
            agent_contact=agent_contact,
            agent_config=agent_config,
            user=user,
            task=None,
            transcript=transcript,
            call_status="answered",
            duration_seconds=0,
            openai_key=(user.openai_api_key or "") if user else "",
            db=db,
            call_direction="sms_inbound",
        )

    async def run_for_telegram(self, agent_call, agent_contact, agent_config, user, message_body, db):
        """
        Прогнать входящее сообщение личного Telegram через ту же PostCall-логику,
        что звонки и SMS. «Транскрипт» — текст сообщения (полная переписка
        подмешивается через единую хронологию build_conversation_timeline).
        Ответить клиенту агент может тулзой telegram_send_message (домешивается в
        build_postcall_tools, когда аккаунт подключён).
        """
        transcript = f'Клиент написал в Telegram: "{(message_body or "").strip()}"'
        await self._analyze(
            agent_call=agent_call,
            agent_contact=agent_contact,
            agent_config=agent_config,
            user=user,
            task=None,
            transcript=transcript,
            call_status="answered",
            duration_seconds=0,
            openai_key=(user.openai_api_key or "") if user else "",
            db=db,
            call_direction="telegram_inbound",
        )

    async def run_for_scheduled_telegram(self, agent_call, agent_contact, agent_config, user, task, db):
        """
        Исполнить запланированную задачу «написать клиенту в Telegram»
        (Task.channel="telegram", ставится тулзой schedule_telegram_message).

        Один проактивный прогон той же PostCall-логики: оркестратор получает
        память контакта + единую хронологию + инструкцию из task.description,
        сам составляет текст с учётом свежего контекста и отправляет через
        telegram_send_message (или осознанно не отправляет, если договорённость
        уже закрыта). Ответ клиента потом подхватит telegram_user_poller —
        отдельный PostCall после отправки не нужен.

        Только v3 (OpenRouter): тул schedule_telegram_message домешивается лишь
        v3-агентам, а legacy v2-ветка про telegram_outbound не знает.
        """
        transcript = (task.description or "").strip() or (task.title or "").strip()
        await self._analyze_v3_openrouter(
            agent_call=agent_call,
            agent_contact=agent_contact,
            agent_config=agent_config,
            user=user,
            task=task,
            transcript=transcript,
            call_status="answered",
            duration_seconds=0,
            db=db,
            call_direction="telegram_outbound",
        )

    async def _analyze(
        self,
        agent_call: AgentCall,
        agent_contact: AgentContact,
        agent_config: AgentConfig,
        user: User,
        task: Optional[Task],
        transcript: str,
        call_status: str,
        duration_seconds: float,
        openai_key: str,
        db,
        call_direction: str = "outbound"
    ):
        """
        Run PostCall analysis. Развилка по uses_hardcoded_prompt:
        - v3 (TRUE):  OpenRouter Chat Completions + AGENT_POSTCALL_TOOLS, без цепочки.
        - v2 (FALSE): OpenAI Responses API (старые агенты).
        """
        if getattr(agent_config, "uses_hardcoded_prompt", False):
            return await self._analyze_v3_openrouter(
                agent_call, agent_contact, agent_config, user, task,
                transcript, call_status, duration_seconds, db, call_direction
            )
        return await self._analyze_v2_responses_api(
            agent_call, agent_contact, agent_config, user, task,
            transcript, call_status, duration_seconds, openai_key, db, call_direction
        )

    async def _analyze_v3_openrouter(
        self,
        agent_call: AgentCall,
        agent_contact: AgentContact,
        agent_config: AgentConfig,
        user: User,
        task: Optional[Task],
        transcript: str,
        call_status: str,
        duration_seconds: float,
        db,
        call_direction: str = "outbound"
    ):
        """PostCall v3 — OpenRouter Chat Completions, без previous_response_id."""
        logger.info(f"[AGENT-POSTCALL] (v3/OpenRouter) Analyzing call {agent_call.id}, model {agent_config.orchestrator_model}")

        # Pre-flight проверка подписки/кредитов (раздел 5.1)
        CreditService.precheck(db, user)

        # Аккумулятор токенов по всем итерациям цикла tool calls (раздел 5.2, edge case 2)
        total_prompt = 0
        total_completion = 0

        # Статичный system + время в user-сообщении: префикс запроса
        # (tools + system) байт-в-байт одинаков между звонками → кэш провайдера.
        system_prompt = build_orchestrator_prompt(agent_config, include_time_block=False)
        post_call_input = self._build_postcall_input(
            agent_call, agent_contact, transcript, call_status, duration_seconds, db, call_direction
        )
        # Подставляем стратегию PreCall в текст (симуляция цепочки). Для входящего
        # SMS/Telegram и запланированной отправки в Telegram PreCall не было —
        # блок стратегии не добавляем.
        if (call_direction or "").lower() not in ("sms_inbound", "telegram_inbound", "telegram_outbound"):
            post_call_input += f"""

СТРАТЕГИЯ КОТОРУЮ ТЫ ПЛАНИРОВАЛ ПЕРЕД ЗВОНКОМ:
Первая фраза: {agent_call.custom_greeting or '(не задана)'}
Тактика: {agent_call.call_strategy or '(не задана)'}"""
        post_call_input += build_time_block(round_to_minutes=0)

        tools = await build_postcall_tools(agent_config, db)
        tool_calls_log: List[Dict[str, Any]] = []

        context = {
            "agent_config_id": str(agent_call.agent_config_id),
            "user_id": str(agent_call.user_id),
            "user": user,
            "agent_config": agent_config,  # ← v2.2: для тулзы send_telegram_notification
        }

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": post_call_input},
        ]

        is_tg_out = (call_direction or "").lower() == "telegram_outbound"

        try:
            client = get_openrouter_client()
            post_call_decision = None
            created_task = False
            message_sent = False
            stage_moved_by_tool = False
            max_iterations = 10
            iteration = 0

            while iteration < max_iterations:
                iteration += 1
                response = await client.chat_completion(
                    model=agent_config.orchestrator_model,
                    messages=messages,
                    tools=tools,
                    temperature=0.5,
                )
                p_tok, c_tok = _extract_usage(response)
                total_prompt += p_tok
                total_completion += c_tok
                msg = response["choices"][0]["message"]
                tool_calls = msg.get("tool_calls") or []

                if not tool_calls:
                    break

                # Append assistant message with tool_calls to history
                messages.append({
                    "role": "assistant",
                    "content": msg.get("content") or "",
                    "tool_calls": tool_calls,
                })

                for tc in tool_calls:
                    fn = tc.get("function", {})
                    tool_name = fn.get("name", "")
                    try:
                        tool_args = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        tool_args = {}

                    logger.info(f"[AGENT-POSTCALL] (v3) Executing tool: {tool_name}({json.dumps(tool_args, ensure_ascii=False)[:200]})")

                    tool_entry = {
                        "tool": tool_name,
                        "args": tool_args,
                        "ts": datetime.utcnow().isoformat(),
                    }
                    result_str = await execute_tool(tool_name, tool_args, context, db)
                    try:
                        tool_entry["result"] = json.loads(result_str)
                    except Exception:
                        tool_entry["result"] = result_str
                    tool_calls_log.append(tool_entry)

                    # schedule_telegram_message — тоже follow-up задача (channel=telegram)
                    if tool_name in ("create_agent_task", "schedule_telegram_message"):
                        try:
                            result_data = json.loads(result_str)
                            if result_data.get("ok") and result_data.get("task_id"):
                                agent_call.next_task_id = result_data["task_id"]
                                created_task = True
                        except Exception:
                            pass

                    if tool_name == "telegram_send_message":
                        try:
                            if json.loads(result_str).get("ok"):
                                message_sent = True
                        except Exception:
                            pass

                    if tool_name == "move_contact_stage":
                        try:
                            if json.loads(result_str).get("ok"):
                                stage_moved_by_tool = True
                        except Exception:
                            pass

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id"),
                        "content": result_str,
                    })

            # Determine final decision (SUCCESS / NO_ANSWER / FOLLOWUP only)
            if call_status == "answered":
                post_call_decision = "FOLLOWUP" if created_task else "SUCCESS"
            else:
                post_call_decision = "NO_ANSWER"

            agent_call.transcript = transcript
            agent_call.duration_seconds = int(duration_seconds)
            agent_call.status = "answered" if call_status == "answered" else "no_answer"
            agent_call.completed_at = datetime.utcnow()
            agent_call.post_call_decision = post_call_decision

            agent_call.postcall_log = {
                "response_id": None,
                "model": agent_config.orchestrator_model,
                "call_status": call_status,
                "call_direction": call_direction,
                "duration_seconds": duration_seconds,
                "tool_calls": tool_calls_log,
                "final_decision": post_call_decision,
                "transcript_length": len(transcript),
                "analyzed_at": datetime.utcnow().isoformat(),
            }
            if is_tg_out:
                # Для UI: было ли сообщение реально отправлено (оркестратор мог
                # осознанно не отправлять, если договорённость уже закрыта).
                agent_call.postcall_log["message_sent"] = message_sent

            # Запланированная отправка в Telegram — не попытка дозвона: счётчик
            # попыток и авто-маппинг стадии (SUCCESS → success) к ней не применяем.
            # Стадию при отправке сообщения меняет только явный move_contact_stage.
            if not is_tg_out:
                agent_contact.attempts_count = (agent_contact.attempts_count or 0) + 1
                agent_contact.last_called_at = datetime.utcnow()
                # Обязательная стадия воронки: если оркестратор не двинул контакт
                # тулзой move_contact_stage — применяем детерминированный маппинг
                # от post_call_decision (стадия проставляется ВСЕГДА).
                if not stage_moved_by_tool:
                    _new_stage = stage_from_decision(post_call_decision, agent_contact.status)
                    if _new_stage:
                        agent_contact.status = _new_stage

            if task:
                task.post_call_decision = post_call_decision
                task.status = TaskStatus.COMPLETED

            flag_modified(agent_contact, 'memory')
            db.commit()
            logger.info(f"[AGENT-POSTCALL] (v3) ✅ Call {agent_call.id} completed: {post_call_decision} ({len(tool_calls_log)} tool calls), stage={agent_contact.status}")

        except Exception as e:
            logger.error(f"[AGENT-POSTCALL] (v3) Analysis error: {e}", exc_info=True)
            agent_call.postcall_log = {
                "error": str(e),
                "model": agent_config.orchestrator_model,
                "call_status": call_status,
                "call_direction": call_direction,
                "tool_calls": tool_calls_log,
                "analyzed_at": datetime.utcnow().isoformat(),
            }
            agent_call.status = "no_answer" if call_status != "answered" else "answered"
            agent_call.post_call_decision = "NO_ANSWER" if call_status != "answered" else "SUCCESS"
            agent_call.completed_at = datetime.utcnow()
            agent_call.transcript = transcript
            agent_call.duration_seconds = int(duration_seconds)
            if not is_tg_out:
                agent_contact.attempts_count = (agent_contact.attempts_count or 0) + 1
                agent_contact.last_called_at = datetime.utcnow()
            # Стадия воронки по решению — только если есть основание её менять
            # (для telegram_outbound авто-маппинг не применяем, см. выше).
            _new_stage = None if is_tg_out else stage_from_decision(agent_call.post_call_decision, agent_contact.status)
            if _new_stage:
                agent_contact.status = _new_stage
            if task:
                task.post_call_decision = agent_call.post_call_decision
                task.status = TaskStatus.COMPLETED
            flag_modified(agent_contact, 'memory')
            db.commit()

        finally:
            # Списываем кредиты за фактически потраченные токены даже при ошибке
            # посреди цепочки tool calls (раздел 5.2, edge case 3).
            if total_prompt or total_completion:
                try:
                    CreditService.charge(
                        db=db, user_id=user.id,
                        model_slug=agent_config.orchestrator_model,
                        prompt_tokens=total_prompt, completion_tokens=total_completion,
                        ref_type="postcall", ref_id=agent_call.id,
                        notes="postcall",
                    )
                except Exception as ce:
                    logger.error(f"[AGENT-POSTCALL] (v3) Charge failed: {ce}", exc_info=True)

    async def _analyze_v2_responses_api(
        self,
        agent_call: AgentCall,
        agent_contact: AgentContact,
        agent_config: AgentConfig,
        user: User,
        task: Optional[Task],
        transcript: str,
        call_status: str,
        duration_seconds: float,
        openai_key: str,
        db,
        call_direction: str = "outbound"
    ):
        """PostCall v2 (legacy) — OpenAI Responses API with AGENT_POSTCALL_TOOLS."""
        logger.info(f"[AGENT-POSTCALL] (v2/Responses) Analyzing call {agent_call.id}")

        client = AsyncOpenAI(api_key=openai_key)

        post_call_input = self._build_postcall_input(
            agent_call, agent_contact, transcript, call_status, duration_seconds, db, call_direction
        )

        # ✅ v2.1: Список для сбора всех tool calls
        tool_calls_log: List[Dict[str, Any]] = []
        postcall_response_id = None

        try:
            kwargs = {
                "model": "gpt-5-2025-08-07",
                "instructions": agent_config.orchestrator_prompt or "",
                "input": post_call_input,
                "tools": AGENT_POSTCALL_TOOLS,
                "store": True,
            }

            if agent_call.pre_call_response_id:
                kwargs["previous_response_id"] = agent_call.pre_call_response_id

            response = await client.responses.create(**kwargs)
            postcall_response_id = response.id

            context = {
                "agent_config_id": str(agent_call.agent_config_id),
                "user_id": str(agent_call.user_id),
                "user": user,
                "agent_config": agent_config,  # ← v2.2: для тулзы send_telegram_notification
            }

            post_call_decision = None
            stage_moved_by_tool = False
            while True:
                has_tool_calls = False
                tool_results = []

                for item in response.output:
                    if item.type == "function_call":
                        has_tool_calls = True
                        tool_name = item.name
                        try:
                            tool_args = json.loads(item.arguments)
                        except json.JSONDecodeError:
                            tool_args = {}

                        logger.info(f"[AGENT-POSTCALL] Executing tool: {tool_name}({json.dumps(tool_args, ensure_ascii=False)[:200]})")

                        # ✅ v2.1: Начинаем запись tool entry
                        tool_entry = {
                            "tool": tool_name,
                            "args": tool_args,
                            "ts": datetime.utcnow().isoformat(),
                        }

                        result_str = await execute_tool(tool_name, tool_args, context, db)

                        # Парсим результат для лога
                        try:
                            tool_entry["result"] = json.loads(result_str)
                        except Exception:
                            tool_entry["result"] = result_str

                        tool_calls_log.append(tool_entry)

                        if tool_name == "create_agent_task":
                            try:
                                result_data = json.loads(result_str)
                                if result_data.get("ok") and result_data.get("task_id"):
                                    agent_call.next_task_id = result_data["task_id"]
                            except Exception:
                                pass
                            if not post_call_decision:
                                post_call_decision = "FOLLOWUP"

                        if tool_name == "move_contact_stage":
                            try:
                                if json.loads(result_str).get("ok"):
                                    stage_moved_by_tool = True
                            except Exception:
                                pass

                        tool_results.append({
                            "type": "function_call_output",
                            "call_id": item.call_id,
                            "output": result_str,
                        })

                    elif item.type == "message":
                        text_content = getattr(item, "text", "") or ""
                        if not text_content:
                            for part in getattr(item, "content", []):
                                if hasattr(part, "text"):
                                    text_content += part.text
                        logger.info(f"[AGENT-POSTCALL] GPT message: {text_content[:200]}")

                if not has_tool_calls:
                    break

                response = await client.responses.create(
                    model="gpt-5-2025-08-07",
                    input=tool_results,
                    previous_response_id=response.id,
                    tools=AGENT_POSTCALL_TOOLS,
                    store=True,
                )

            # Determine final decision
            if not post_call_decision:
                if call_status == "answered":
                    post_call_decision = "SUCCESS"
                else:
                    post_call_decision = "NO_ANSWER"

            # Update AgentCall
            agent_call.transcript = transcript
            agent_call.duration_seconds = int(duration_seconds)
            agent_call.status = "answered" if call_status == "answered" else "no_answer"
            agent_call.completed_at = datetime.utcnow()
            agent_call.post_call_decision = post_call_decision

            # ✅ v2.1: Сохраняем postcall_log
            agent_call.postcall_log = {
                "response_id": postcall_response_id,
                "model": "gpt-5-2025-08-07",
                "call_status": call_status,
                "call_direction": call_direction,
                "duration_seconds": duration_seconds,
                "tool_calls": tool_calls_log,
                "final_decision": post_call_decision,
                "transcript_length": len(transcript),
                "analyzed_at": datetime.utcnow().isoformat(),
            }

            # Update AgentContact
            agent_contact.attempts_count = (agent_contact.attempts_count or 0) + 1
            agent_contact.last_called_at = datetime.utcnow()
            # Обязательная стадия воронки: если оркестратор не двинул контакт
            # тулзой move_contact_stage — применяем детерминированный маппинг.
            if not stage_moved_by_tool:
                _new_stage = stage_from_decision(post_call_decision, agent_contact.status)
                if _new_stage:
                    agent_contact.status = _new_stage

            if task:
                task.post_call_decision = post_call_decision
                task.status = TaskStatus.COMPLETED

            flag_modified(agent_contact, 'memory')
            db.commit()
            logger.info(f"[AGENT-POSTCALL] ✅ Call {agent_call.id} completed: {post_call_decision}, stage={agent_contact.status}")
            logger.info(f"[AGENT-POSTCALL] postcall_log saved: {len(tool_calls_log)} tool calls")

        except Exception as e:
            logger.error(f"[AGENT-POSTCALL] Analysis error: {e}", exc_info=True)

            # ✅ v2.1: Сохраняем ошибку в лог
            agent_call.postcall_log = {
                "error": str(e),
                "call_status": call_status,
                "call_direction": call_direction,
                "tool_calls": tool_calls_log,
                "analyzed_at": datetime.utcnow().isoformat(),
            }

            agent_call.status = "no_answer" if call_status != "answered" else "answered"
            agent_call.post_call_decision = "NO_ANSWER" if call_status != "answered" else "SUCCESS"
            agent_call.completed_at = datetime.utcnow()
            agent_call.transcript = transcript
            agent_call.duration_seconds = int(duration_seconds)
            agent_contact.attempts_count = (agent_contact.attempts_count or 0) + 1
            agent_contact.last_called_at = datetime.utcnow()
            # Стадия воронки по решению — только если есть основание её менять.
            _new_stage = stage_from_decision(agent_call.post_call_decision, agent_contact.status)
            if _new_stage:
                agent_contact.status = _new_stage
            if task:
                task.post_call_decision = agent_call.post_call_decision
                task.status = TaskStatus.COMPLETED
            flag_modified(agent_contact, 'memory')
            db.commit()


# ============================================================================
# CHAT ORCHESTRATOR
# ============================================================================

class ChatOrchestrator:
    """
    Handles text chat with the agent.
    Uses GPT-5 (Responses API) with AGENT_CHAT_TOOLS.
    Supports multi-turn dialog through previous_response_id.
    """

    @staticmethod
    def _now_ts() -> str:
        return datetime.utcnow().isoformat()

    async def run(
        self,
        message: str,
        agent_config: AgentConfig,
        user: User,
        db
    ) -> Dict[str, Any]:
        """
        Process a chat message with the agent.
        Returns dict with: reply (str), debug_log (list of log entries).

        Развилка по uses_hardcoded_prompt:
        - v3 (TRUE):  OpenRouter Chat Completions + захардкоженный промпт.
        - v2 (FALSE): OpenAI Responses API (старые агенты).
        """
        if getattr(agent_config, "uses_hardcoded_prompt", False):
            return await self._run_v3_openrouter(message, agent_config, user, db)
        return await self._run_v2_responses_api(message, agent_config, user, db)

    # ========================================================================
    # ✅ v2.2: TELEGRAM MODE
    # История берётся из telegram_history_row.history (не agent_config.chat_history),
    # в ответ возвращается только reply (без debug_log).
    # ========================================================================

    async def run_telegram(
        self,
        message: str,
        agent_config: AgentConfig,
        user: User,
        db,
        telegram_history_row,
    ) -> Dict[str, Any]:
        """
        Telegram-режим оркестратора. Развилка по uses_hardcoded_prompt:
        - v3 (TRUE):  OpenRouter Chat Completions + захардкоженный промпт.
        - v2 (FALSE): OpenAI Responses API (старые агенты).
        """
        if getattr(agent_config, "uses_hardcoded_prompt", False):
            return await self._run_telegram_v3(message, agent_config, user, db, telegram_history_row)
        return await self._run_telegram_v2(message, agent_config, user, db, telegram_history_row)

    def _persist_telegram_history(self, telegram_history_row, message: str, final_text: str, db):
        """Дописать пару user/assistant в telegram_history_row.history, обрезать до 20, commit."""
        history = list(telegram_history_row.history or [])
        history.append({"role": "user", "content": message, "ts": datetime.utcnow().isoformat()})
        history.append({"role": "assistant", "content": final_text, "ts": datetime.utcnow().isoformat()})
        telegram_history_row.history = history[-20:]
        flag_modified(telegram_history_row, "history")
        db.commit()

    async def _run_telegram_v3(
        self,
        message: str,
        agent_config: AgentConfig,
        user: User,
        db,
        telegram_history_row,
    ) -> Dict[str, Any]:
        """Telegram chat v3 — OpenRouter Chat Completions with AGENT_CHAT_TOOLS."""
        # Pre-flight проверка подписки/кредитов (раздел 5.1)
        CreditService.precheck(db, user)
        total_prompt = 0
        total_completion = 0

        system_prompt = build_orchestrator_prompt(agent_config, include_time_block=False) + TELEGRAM_RICH_FORMAT_HINT
        history = telegram_history_row.history or []

        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        for msg in history[-20:]:
            role = msg.get("role")
            content = msg.get("content")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
        # Время приклеивается к отправляемому сообщению, но в историю
        # сохраняется исходный «чистый» message (см. persist ниже).
        messages.append({"role": "user", "content": message + build_time_block(round_to_minutes=0)})

        tools = await build_chat_tools(agent_config, db)

        context = {
            "agent_config_id": str(agent_config.id),
            "user_id": str(user.id),
            "user": user,
            "agent_config": agent_config,
        }

        client = get_openrouter_client()
        final_text = ""
        max_iterations = 10
        iteration = 0

        while iteration < max_iterations:
            iteration += 1
            response = await client.chat_completion(
                model=agent_config.orchestrator_model,
                messages=messages,
                tools=tools,
                temperature=0.7,
            )
            p_tok, c_tok = _extract_usage(response)
            total_prompt += p_tok
            total_completion += c_tok
            msg = response["choices"][0]["message"]
            tool_calls = msg.get("tool_calls") or []

            if not tool_calls:
                final_text = msg.get("content") or ""
                break

            messages.append({
                "role": "assistant",
                "content": msg.get("content") or "",
                "tool_calls": tool_calls,
            })

            for tc in tool_calls:
                fn = tc.get("function", {})
                tool_name = fn.get("name", "")
                try:
                    tool_args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    tool_args = {}

                logger.info(f"[AGENT-TG-CHAT] (v3) Executing tool: {tool_name}")
                try:
                    result_str = await execute_tool(tool_name, tool_args, context, db)
                except Exception as e:
                    result_str = json.dumps({"ok": False, "error": str(e)})

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id"),
                    "content": result_str,
                })

        if not final_text:
            final_text = "Готово."

        # Списываем кредиты за весь диалоговый цикл (раздел 5.2)
        if total_prompt or total_completion:
            try:
                CreditService.charge(
                    db=db, user_id=user.id,
                    model_slug=agent_config.orchestrator_model,
                    prompt_tokens=total_prompt, completion_tokens=total_completion,
                    ref_type="telegram_chat", ref_id=agent_config.id,
                    notes=f"telegram_chat iterations: {iteration}",
                )
            except Exception as ce:
                logger.error(f"[AGENT-TG-CHAT] (v3) Charge failed: {ce}", exc_info=True)

        self._persist_telegram_history(telegram_history_row, message, final_text, db)
        return {"reply": final_text}

    async def run_telegram_stream(
        self,
        message: str,
        agent_config: AgentConfig,
        user: User,
        db,
        telegram_history_row,
    ):
        """
        Стриминговый Telegram-режим (только v3 / uses_hardcoded_prompt).
        Async-генератор событий: token / clear_partial / tool / done / error.

        Миррорит `_run_telegram_v3`, но отдаёт токены живьём — для
        sendRichMessageDraft. Списание кредитов (ref_type="telegram_chat") и
        запись истории выполняются на финале, как в `_run_telegram_v3`.
        Каждое событие token несёт накопленный `buffer`, чтобы драйвер слал
        в draft уже собранный текст.
        """
        CreditService.precheck(db, user)

        total_prompt = 0
        total_completion = 0

        system_prompt = build_orchestrator_prompt(agent_config, include_time_block=False) + TELEGRAM_RICH_FORMAT_HINT
        history = telegram_history_row.history or []

        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        for msg in history[-20:]:
            role = msg.get("role")
            content = msg.get("content")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
        # Время приклеивается к отправляемому сообщению, но в историю
        # сохраняется исходный «чистый» message (см. persist ниже).
        messages.append({"role": "user", "content": message + build_time_block(round_to_minutes=0)})

        tools = await build_chat_tools(agent_config, db)
        context = {
            "agent_config_id": str(agent_config.id),
            "user_id": str(user.id),
            "user": user,
            "agent_config": agent_config,
        }

        client = get_openrouter_client()
        final_text = ""
        max_iterations = 10
        iteration = 0

        try:
            while iteration < max_iterations:
                iteration += 1
                content_buf = ""
                tool_calls_acc: Dict[int, Dict[str, Any]] = {}
                p_tok = 0
                c_tok = 0

                async for chunk in client.chat_completion_stream(
                    model=agent_config.orchestrator_model,
                    messages=messages,
                    tools=tools,
                    temperature=0.7,
                ):
                    usage = chunk.get("usage")
                    if usage:
                        p_tok = int(usage.get("prompt_tokens", 0) or 0)
                        c_tok = int(usage.get("completion_tokens", 0) or 0)

                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}

                    content_piece = delta.get("content")
                    if content_piece:
                        content_buf += content_piece
                        yield {"type": "token", "text": content_piece, "buffer": content_buf}

                    for tc in (delta.get("tool_calls") or []):
                        idx = tc.get("index", 0)
                        acc = tool_calls_acc.get(idx)
                        if acc is None:
                            acc = {"id": None, "name": "", "arguments": ""}
                            tool_calls_acc[idx] = acc
                        if tc.get("id"):
                            acc["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            acc["name"] = fn["name"]
                        if fn.get("arguments"):
                            acc["arguments"] += fn["arguments"]

                total_prompt += p_tok
                total_completion += c_tok

                # Нет тулколлов → это финальный ответ.
                if not tool_calls_acc:
                    final_text = content_buf
                    break

                # Модель написала текст перед тулколлом — попросим стереть его.
                if content_buf.strip():
                    yield {"type": "clear_partial"}

                ordered = [tool_calls_acc[i] for i in sorted(tool_calls_acc.keys())]
                assistant_tool_calls = [
                    {
                        "id": acc["id"],
                        "type": "function",
                        "function": {"name": acc["name"], "arguments": acc["arguments"]},
                    }
                    for acc in ordered
                ]
                messages.append({
                    "role": "assistant",
                    "content": content_buf or "",
                    "tool_calls": assistant_tool_calls,
                })

                for acc in ordered:
                    tool_name = acc["name"]
                    try:
                        tool_args = json.loads(acc["arguments"] or "{}")
                    except json.JSONDecodeError:
                        tool_args = {}

                    yield {"type": "tool", "name": tool_name}
                    logger.info(f"[AGENT-TG-CHAT] (stream) Executing tool: {tool_name}")
                    try:
                        result_str = await execute_tool(tool_name, tool_args, context, db)
                    except Exception as e:
                        result_str = json.dumps({"ok": False, "error": str(e)})

                    messages.append({
                        "role": "tool",
                        "tool_call_id": acc["id"],
                        "content": result_str,
                    })

            if not final_text:
                final_text = "Готово."

            if total_prompt or total_completion:
                try:
                    CreditService.charge(
                        db=db, user_id=user.id,
                        model_slug=agent_config.orchestrator_model,
                        prompt_tokens=total_prompt, completion_tokens=total_completion,
                        ref_type="telegram_chat", ref_id=agent_config.id,
                        notes=f"telegram_chat (stream) iterations: {iteration}",
                    )
                except Exception as ce:
                    logger.error(f"[AGENT-TG-CHAT] (stream) Charge failed: {ce}", exc_info=True)

            self._persist_telegram_history(telegram_history_row, message, final_text, db)
            yield {"type": "done", "reply": final_text}

        except Exception as e:
            logger.error(f"[AGENT-TG-CHAT] (stream) error: {e}", exc_info=True)
            yield {"type": "error", "detail": str(e), "reply": final_text}

    async def _run_telegram_v2(
        self,
        message: str,
        agent_config: AgentConfig,
        user: User,
        db,
        telegram_history_row,
    ) -> Dict[str, Any]:
        """Telegram chat v2 (legacy) — OpenAI Responses API, история как input-список."""
        client = AsyncOpenAI(api_key=user.openai_api_key)
        history = telegram_history_row.history or []

        instructions = CHAT_META_PROMPT + (agent_config.orchestrator_prompt or "") + CHAT_SUFFIX + TELEGRAM_FORMAT_HINT

        input_items: List[Dict[str, Any]] = []
        for msg in history[-20:]:
            role = msg.get("role")
            content = msg.get("content")
            if role in ("user", "assistant") and content:
                input_items.append({"role": role, "content": content})
        input_items.append({"role": "user", "content": message})

        context = {
            "agent_config_id": str(agent_config.id),
            "user_id": str(user.id),
            "user": user,
            "agent_config": agent_config,
        }

        response = await client.responses.create(
            model="gpt-5-2025-08-07",
            instructions=instructions,
            input=input_items,
            tools=AGENT_CHAT_TOOLS,
            store=True,
        )

        max_iterations = 10
        iteration = 0
        while iteration < max_iterations:
            iteration += 1
            has_tool_calls = False
            tool_results = []

            for item in response.output:
                if item.type == "function_call":
                    has_tool_calls = True
                    tool_name = item.name
                    try:
                        tool_args = json.loads(item.arguments)
                    except json.JSONDecodeError:
                        tool_args = {}

                    logger.info(f"[AGENT-TG-CHAT] (v2) Executing tool: {tool_name}")
                    try:
                        result_str = await execute_tool(tool_name, tool_args, context, db)
                    except Exception as e:
                        result_str = json.dumps({"ok": False, "error": str(e)})

                    tool_results.append({
                        "type": "function_call_output",
                        "call_id": item.call_id,
                        "output": result_str,
                    })

            if not has_tool_calls:
                break

            response = await client.responses.create(
                model="gpt-5-2025-08-07",
                input=tool_results,
                previous_response_id=response.id,
                tools=AGENT_CHAT_TOOLS,
                store=True,
            )

        final_text = ""
        for item in response.output:
            if item.type == "message":
                for part in getattr(item, "content", []):
                    if hasattr(part, "text"):
                        final_text += part.text
        if not final_text:
            final_text = response.output_text or "Готово."

        self._persist_telegram_history(telegram_history_row, message, final_text, db)
        return {"reply": final_text}

    async def _run_v3_openrouter(
        self,
        message: str,
        agent_config: AgentConfig,
        user: User,
        db
    ) -> Dict[str, Any]:
        """Chat v3 — OpenRouter Chat Completions with AGENT_CHAT_TOOLS."""
        # Pre-flight проверка подписки/кредитов (раздел 5.1)
        CreditService.precheck(db, user)
        total_prompt = 0
        total_completion = 0

        debug_log: List[Dict[str, Any]] = []
        debug_log.append({"ts": self._now_ts(), "type": "user_message", "data": message})

        system_prompt = build_orchestrator_prompt(agent_config, include_time_block=False)
        history = agent_config.chat_history or []

        # Build messages from stored chat history (role/content only)
        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        for msg in history[-20:]:
            role = msg.get("role")
            content = msg.get("content")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
        # Время приклеивается к отправляемому сообщению, но в историю
        # сохраняется исходный «чистый» message (см. persist ниже).
        messages.append({"role": "user", "content": message + build_time_block(round_to_minutes=0)})

        tools = await build_chat_tools(agent_config, db)
        debug_log.append({
            "ts": self._now_ts(),
            "type": "gpt_thinking",
            "data": f"model: {agent_config.orchestrator_model}, tools: {len(tools)}, history: {len(messages) - 2} msgs",
        })

        context = {
            "agent_config_id": str(agent_config.id),
            "user_id": str(user.id),
            "user": user,
            "agent_config": agent_config,  # ← v2.2: для тулзы send_telegram_notification
        }

        client = get_openrouter_client()
        final_text = ""
        max_iterations = 10
        iteration = 0

        while iteration < max_iterations:
            iteration += 1
            response = await client.chat_completion(
                model=agent_config.orchestrator_model,
                messages=messages,
                tools=tools,
                temperature=0.7,
            )
            p_tok, c_tok = _extract_usage(response)
            total_prompt += p_tok
            total_completion += c_tok
            msg = response["choices"][0]["message"]
            tool_calls = msg.get("tool_calls") or []

            if not tool_calls:
                final_text = msg.get("content") or ""
                break

            messages.append({
                "role": "assistant",
                "content": msg.get("content") or "",
                "tool_calls": tool_calls,
            })

            for tc in tool_calls:
                fn = tc.get("function", {})
                tool_name = fn.get("name", "")
                try:
                    tool_args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    tool_args = {}

                debug_log.append({"ts": self._now_ts(), "type": "tool_call", "data": {"tool": tool_name, "args": tool_args}})
                logger.info(f"[AGENT-CHAT] (v3) Executing tool: {tool_name}")
                try:
                    result_str = await execute_tool(tool_name, tool_args, context, db)
                    try:
                        result_parsed = json.loads(result_str)
                    except (json.JSONDecodeError, TypeError):
                        result_parsed = result_str
                    debug_log.append({"ts": self._now_ts(), "type": "tool_result", "data": {"tool": tool_name, "result": result_parsed}})
                except Exception as e:
                    result_str = json.dumps({"ok": False, "error": str(e)})
                    debug_log.append({"ts": self._now_ts(), "type": "tool_error", "data": {"tool": tool_name, "error": str(e)}})

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id"),
                    "content": result_str,
                })

        if not final_text:
            final_text = "Готово."

        debug_log.append({"ts": self._now_ts(), "type": "gpt_response", "data": final_text[:500]})

        # Списываем кредиты за весь диалоговый цикл (раздел 5.2)
        if total_prompt or total_completion:
            try:
                CreditService.charge(
                    db=db, user_id=user.id,
                    model_slug=agent_config.orchestrator_model,
                    prompt_tokens=total_prompt, completion_tokens=total_completion,
                    ref_type="chat", ref_id=agent_config.id,
                    notes=f"chat iterations: {iteration}",
                )
            except Exception as ce:
                logger.error(f"[AGENT-CHAT] (v3) Charge failed: {ce}", exc_info=True)

        new_history = list(history)
        new_history.append({"role": "user", "content": message, "ts": datetime.utcnow().isoformat()})
        new_history.append({"role": "assistant", "content": final_text, "ts": datetime.utcnow().isoformat()})
        agent_config.chat_history = new_history[-20:]
        db.commit()

        return {"reply": final_text, "debug_log": debug_log}

    async def run_public(
        self,
        message: str,
        agent_config: AgentConfig,
        user: User,
        db
    ) -> Dict[str, Any]:
        """
        Публичный stateless-канал (HTTP-приём заявок, сервер-к-серверу).

        В отличие от run(): история НЕ читается и НЕ пишется — каждый запрос
        независим, личный chat_history владельца не засоряется. Использует тот
        же набор AGENT_CHAT_TOOLS, поэтому оркестратор сам решает, что сделать
        с входящим текстом (создать контакт, поставить звонок, ответить и т.д.).

        Поддерживаются только v3-агенты (uses_hardcoded_prompt + OpenRouter).
        """
        if not getattr(agent_config, "uses_hardcoded_prompt", False):
            raise ValueError("public_channel_requires_v3_agent")

        # Подписка/кредиты владельца (он же платит за обработку)
        CreditService.precheck(db, user)
        total_prompt = 0
        total_completion = 0

        system_prompt = build_orchestrator_prompt(agent_config, include_time_block=False)
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": message + build_time_block(round_to_minutes=0)},
        ]

        tools = await build_chat_tools(agent_config, db)
        context = {
            "agent_config_id": str(agent_config.id),
            "user_id": str(user.id),
            "user": user,
            "agent_config": agent_config,
        }

        client = get_openrouter_client()
        final_text = ""
        max_iterations = 10
        iteration = 0

        while iteration < max_iterations:
            iteration += 1
            response = await client.chat_completion(
                model=agent_config.orchestrator_model,
                messages=messages,
                tools=tools,
                temperature=0.7,
            )
            p_tok, c_tok = _extract_usage(response)
            total_prompt += p_tok
            total_completion += c_tok
            msg = response["choices"][0]["message"]
            tool_calls = msg.get("tool_calls") or []

            if not tool_calls:
                final_text = msg.get("content") or ""
                break

            messages.append({
                "role": "assistant",
                "content": msg.get("content") or "",
                "tool_calls": tool_calls,
            })

            for tc in tool_calls:
                fn = tc.get("function", {})
                tool_name = fn.get("name", "")
                try:
                    tool_args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    tool_args = {}

                logger.info(f"[AGENT-PUBLIC] Executing tool: {tool_name}")
                try:
                    result_str = await execute_tool(tool_name, tool_args, context, db)
                except Exception as e:
                    result_str = json.dumps({"ok": False, "error": str(e)})

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id"),
                    "content": result_str,
                })

        if not final_text:
            final_text = "Готово."

        # Списываем кредиты за весь цикл обработки
        if total_prompt or total_completion:
            try:
                CreditService.charge(
                    db=db, user_id=user.id,
                    model_slug=agent_config.orchestrator_model,
                    prompt_tokens=total_prompt, completion_tokens=total_completion,
                    ref_type="chat_public", ref_id=agent_config.id,
                    notes=f"public intake iterations: {iteration}",
                )
            except Exception as ce:
                logger.error(f"[AGENT-PUBLIC] Charge failed: {ce}", exc_info=True)

        return {"reply": final_text}

    async def run_stream(
        self,
        message: str,
        agent_config: AgentConfig,
        user: User,
        db,
    ):
        """
        Streaming version of the v3 chat loop. Async-generator of events
        (see ТЗ §3): start / tool_call / tool_result / tool_error / token /
        clear_partial / done / error.

        Mirrors `_run_v3_openrouter` but yields progress live. Only used for
        v3 (uses_hardcoded_prompt) agents — the endpoint guards that.

        NOTE: `precheck` runs BEFORE the first yield so the endpoint can map
        subscription/credit errors to HTTP 402 before the stream starts 200.
        """
        # Pre-flight проверка подписки/кредитов (раздел 5.1) — ДО первого yield.
        # Если кинет Subscription*/InsufficientCredits — пробросится наружу,
        # эндпоинт превратит в 402 до старта StreamingResponse.
        CreditService.precheck(db, user)

        total_prompt = 0
        total_completion = 0

        debug_log: List[Dict[str, Any]] = []
        debug_log.append({"ts": self._now_ts(), "type": "user_message", "data": message})

        system_prompt = build_orchestrator_prompt(agent_config, include_time_block=False)
        history = agent_config.chat_history or []

        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        for msg in history[-20:]:
            role = msg.get("role")
            content = msg.get("content")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
        # Время приклеивается к отправляемому сообщению, но в историю
        # сохраняется исходный «чистый» message (см. persist ниже).
        messages.append({"role": "user", "content": message + build_time_block(round_to_minutes=0)})

        tools = await build_chat_tools(agent_config, db)
        debug_log.append({
            "ts": self._now_ts(),
            "type": "gpt_thinking",
            "data": f"model: {agent_config.orchestrator_model}, tools: {len(tools)}, history: {len(messages) - 2} msgs",
        })

        context = {
            "agent_config_id": str(agent_config.id),
            "user_id": str(user.id),
            "user": user,
            "agent_config": agent_config,
        }

        client = get_openrouter_client()
        final_text = ""
        max_iterations = 10
        iteration = 0
        gpt_response_logged = False

        yield {"type": "start"}

        try:
            while iteration < max_iterations:
                iteration += 1

                content_buf = ""
                # tool_calls accumulator keyed by delta index
                tool_calls_acc: Dict[int, Dict[str, Any]] = {}
                p_tok = 0
                c_tok = 0

                async for chunk in client.chat_completion_stream(
                    model=agent_config.orchestrator_model,
                    messages=messages,
                    tools=tools,
                    temperature=0.7,
                ):
                    # usage обычно приходит в финальном чанке (usage.include=true).
                    # Иногда OpenRouter шлёт чанк с пустым choices и только usage.
                    usage = chunk.get("usage")
                    if usage:
                        p_tok = int(usage.get("prompt_tokens", 0) or 0)
                        c_tok = int(usage.get("completion_tokens", 0) or 0)

                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}

                    content_piece = delta.get("content")
                    if content_piece:
                        content_buf += content_piece
                        yield {"type": "token", "text": content_piece}

                    for tc in (delta.get("tool_calls") or []):
                        idx = tc.get("index", 0)
                        acc = tool_calls_acc.get(idx)
                        if acc is None:
                            acc = {"id": None, "name": "", "arguments": ""}
                            tool_calls_acc[idx] = acc
                        if tc.get("id"):
                            acc["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            acc["name"] = fn["name"]
                        if fn.get("arguments"):
                            acc["arguments"] += fn["arguments"]

                total_prompt += p_tok
                total_completion += c_tok

                # No tool calls → this iteration produced the final answer.
                if not tool_calls_acc:
                    final_text = content_buf
                    debug_log.append({"ts": self._now_ts(), "type": "gpt_response", "data": final_text[:500]})
                    gpt_response_logged = True
                    break

                # Model emitted text before calling a tool (rare). Tell the
                # front to wipe the partially-streamed text for this turn.
                if content_buf.strip():
                    yield {"type": "clear_partial"}

                # Rebuild OpenAI-style tool_calls list (ordered by index).
                ordered = [tool_calls_acc[i] for i in sorted(tool_calls_acc.keys())]
                assistant_tool_calls = [
                    {
                        "id": acc["id"],
                        "type": "function",
                        "function": {"name": acc["name"], "arguments": acc["arguments"]},
                    }
                    for acc in ordered
                ]
                messages.append({
                    "role": "assistant",
                    "content": content_buf or "",
                    "tool_calls": assistant_tool_calls,
                })

                for acc in ordered:
                    tool_name = acc["name"]
                    try:
                        tool_args = json.loads(acc["arguments"] or "{}")
                    except json.JSONDecodeError:
                        tool_args = {}

                    debug_log.append({"ts": self._now_ts(), "type": "tool_call", "data": {"tool": tool_name, "args": tool_args}})
                    yield {"type": "tool_call", "tool": tool_name, "args": tool_args}
                    logger.info(f"[AGENT-CHAT] (stream) Executing tool: {tool_name}")

                    try:
                        result_str = await execute_tool(tool_name, tool_args, context, db)
                        try:
                            result_parsed = json.loads(result_str)
                        except (json.JSONDecodeError, TypeError):
                            result_parsed = result_str
                        debug_log.append({"ts": self._now_ts(), "type": "tool_result", "data": {"tool": tool_name, "result": result_parsed}})
                        yield {"type": "tool_result", "tool": tool_name, "result": result_parsed}
                    except Exception as e:
                        result_str = json.dumps({"ok": False, "error": str(e)})
                        debug_log.append({"ts": self._now_ts(), "type": "tool_error", "data": {"tool": tool_name, "error": str(e)}})
                        yield {"type": "tool_error", "tool": tool_name, "error": str(e)}

                    messages.append({
                        "role": "tool",
                        "tool_call_id": acc["id"],
                        "content": result_str,
                    })

            if not final_text:
                final_text = "Готово."
            if not gpt_response_logged:
                debug_log.append({"ts": self._now_ts(), "type": "gpt_response", "data": final_text[:500]})

            # Списываем кредиты за весь диалоговый цикл (раздел 5.2).
            # Если usage недоступен (стрим без usage) — total_* останутся 0,
            # charge пропустится и фича не упадёт.
            if total_prompt or total_completion:
                try:
                    CreditService.charge(
                        db=db, user_id=user.id,
                        model_slug=agent_config.orchestrator_model,
                        prompt_tokens=total_prompt, completion_tokens=total_completion,
                        ref_type="chat", ref_id=agent_config.id,
                        notes=f"chat iterations: {iteration}",
                    )
                except Exception as ce:
                    logger.error(f"[AGENT-CHAT] (stream) Charge failed: {ce}", exc_info=True)

            new_history = list(history)
            new_history.append({"role": "user", "content": message, "ts": datetime.utcnow().isoformat()})
            new_history.append({"role": "assistant", "content": final_text, "ts": datetime.utcnow().isoformat()})
            agent_config.chat_history = new_history[-20:]
            db.commit()

            yield {
                "type": "done",
                "reply": final_text,
                "debug_log": debug_log,
                "timestamp": datetime.utcnow().isoformat(),
            }

        except Exception as e:
            logger.error(f"[AGENT-CHAT] (stream) error: {e}", exc_info=True)
            yield {"type": "error", "detail": f"chat_error: {e}"}

    async def _run_v2_responses_api(
        self,
        message: str,
        agent_config: AgentConfig,
        user: User,
        db
    ) -> Dict[str, Any]:
        """Chat v2 (legacy) — OpenAI Responses API."""
        client = AsyncOpenAI(api_key=user.openai_api_key)
        debug_log: List[Dict[str, Any]] = []

        debug_log.append({
            "ts": self._now_ts(),
            "type": "user_message",
            "data": message,
        })

        last_response_id = None
        history = agent_config.chat_history or []
        for msg in reversed(history):
            if msg.get("response_id"):
                last_response_id = msg["response_id"]
                break

        instructions = CHAT_META_PROMPT + (agent_config.orchestrator_prompt or "") + CHAT_SUFFIX

        kwargs = {
            "model": "gpt-5-2025-08-07",
            "instructions": instructions,
            "input": message,
            "tools": AGENT_CHAT_TOOLS,
            "store": True,
        }

        if last_response_id:
            kwargs["previous_response_id"] = last_response_id

        debug_log.append({
            "ts": self._now_ts(),
            "type": "gpt_thinking",
            "data": f"model: gpt-5-2025-08-07, previous_response_id: {last_response_id or 'None'}, tools: {len(AGENT_CHAT_TOOLS)}, instructions length: {len(instructions)}",
        })

        try:
            response = await client.responses.create(**kwargs)
        except Exception as e:
            if last_response_id and "previous_response_id" in str(e).lower():
                logger.warning(f"[AGENT-CHAT] Stale response_id, retrying without chain: {e}")
                kwargs.pop("previous_response_id", None)
                debug_log.append({
                    "ts": self._now_ts(),
                    "type": "gpt_thinking",
                    "data": "Retrying without previous_response_id (stale chain)",
                })
                response = await client.responses.create(**kwargs)
            else:
                raise

        context = {
            "agent_config_id": str(agent_config.id),
            "user_id": str(user.id),
            "user": user,
            "agent_config": agent_config,  # ← v2.2: для тулзы send_telegram_notification
        }

        max_iterations = 10
        iteration = 0
        while iteration < max_iterations:
            iteration += 1
            has_tool_calls = False
            tool_results = []

            for item in response.output:
                if item.type == "function_call":
                    has_tool_calls = True
                    tool_name = item.name
                    try:
                        tool_args = json.loads(item.arguments)
                    except json.JSONDecodeError:
                        tool_args = {}

                    debug_log.append({
                        "ts": self._now_ts(),
                        "type": "tool_call",
                        "data": {"tool": tool_name, "args": tool_args},
                    })

                    logger.info(f"[AGENT-CHAT] Executing tool: {tool_name}")
                    try:
                        result_str = await execute_tool(tool_name, tool_args, context, db)
                        try:
                            result_parsed = json.loads(result_str)
                        except (json.JSONDecodeError, TypeError):
                            result_parsed = result_str
                        debug_log.append({
                            "ts": self._now_ts(),
                            "type": "tool_result",
                            "data": {"tool": tool_name, "result": result_parsed},
                        })
                    except Exception as e:
                        result_str = json.dumps({"ok": False, "error": str(e)})
                        debug_log.append({
                            "ts": self._now_ts(),
                            "type": "tool_error",
                            "data": {"tool": tool_name, "error": str(e)},
                        })

                    tool_results.append({
                        "type": "function_call_output",
                        "call_id": item.call_id,
                        "output": result_str,
                    })

            if not has_tool_calls:
                break

            response = await client.responses.create(
                model="gpt-5-2025-08-07",
                input=tool_results,
                previous_response_id=response.id,
                tools=AGENT_CHAT_TOOLS,
                store=True,
            )

        final_text = ""
        for item in response.output:
            if item.type == "message":
                for part in getattr(item, "content", []):
                    if hasattr(part, "text"):
                        final_text += part.text

        if not final_text:
            final_text = response.output_text or "Готово."

        debug_log.append({
            "ts": self._now_ts(),
            "type": "gpt_response",
            "data": final_text[:500],
        })

        new_history = list(history)
        new_history.append({
            "role": "user",
            "content": message,
            "ts": datetime.utcnow().isoformat(),
        })
        new_history.append({
            "role": "assistant",
            "content": final_text,
            "ts": datetime.utcnow().isoformat(),
            "response_id": response.id,
        })
        agent_config.chat_history = new_history[-20:]
        db.commit()

        return {"reply": final_text, "debug_log": debug_log}


# ============================================================================
# INBOUND SMS EVENT HANDLER
# ============================================================================

async def handle_inbound_sms(sms_message_id: str):
    """
    Event-driven обработка входящего SMS.

    Вызывается из вебхука Voximplant (POST /api/telephony/webhook/sms) сразу
    после сохранения входящего SMS. Зеркалит логику входящего ЗВОНКА
    (voximplant.py): резолвит пользователя → активного агента (по номеру
    назначения = номеру агента) → контакт (по номеру отправителя, при отсутствии
    создаёт) → AgentCall(direction="inbound") → запускает тот же PostCall, что и у
    звонков, только «вместо звонка SMS». Агент сам решает: уведомить менеджеров в
    Telegram, запланировать звонок, ответить SMS, обновить память/стадию.

    Открывает собственную сессию БД — безопасно для asyncio.create_task().
    """
    from backend.models.sms_message import SmsMessage
    from backend.services.sms_history import phone_suffix

    db = SessionLocal()
    try:
        sms = db.query(SmsMessage).filter(SmsMessage.id == sms_message_id).first()
        if not sms or (sms.direction or "inbound") != "inbound":
            return

        child = db.query(VoximplantChildAccount).filter(
            VoximplantChildAccount.id == sms.child_account_id
        ).first()
        if not child:
            return

        user = db.query(User).filter(User.id == child.user_id).first()
        if not user:
            return

        # Доступ к агенту (триал/подписка) — как в планировщике.
        try:
            if not user.has_active_agent_subscription():
                logger.info(f"[AGENT-SMS] user {user.id} has no agent access, skip inbound sms {sms.id}")
                return
        except Exception:
            pass

        agents = db.query(AgentConfig).filter(
            AgentConfig.user_id == user.id,
            AgentConfig.is_active == True,
        ).all()
        if not agents:
            logger.info(f"[AGENT-SMS] no active agent for user {user.id}, skip inbound sms {sms.id}")
            return

        # Агент по номеру назначения (= номер, на который пришло SMS = номер агента);
        # fallback — первый активный агент пользователя.
        to_suf = phone_suffix(sms.to_number)
        agent = next(
            (a for a in agents if a.default_caller_id and phone_suffix(a.default_caller_id) == to_suf),
            None,
        ) or agents[0]

        # v2-агента без личного OpenAI-ключа обслужить не сможем.
        if not getattr(agent, "uses_hardcoded_prompt", False) and not user.openai_api_key:
            logger.info(f"[AGENT-SMS] v2 agent {agent.id} without OpenAI key, skip inbound sms {sms.id}")
            return

        # Контакт по номеру отправителя в пределах агента; нет — создаём (как у
        # входящего звонка).
        from_suf = phone_suffix(sms.from_number)
        contact = None
        if from_suf:
            contact = db.query(AgentContact).filter(
                AgentContact.agent_config_id == agent.id,
                AgentContact.phone.like(f"%{from_suf}"),
            ).order_by(AgentContact.created_at.desc()).first()
        if not contact:
            contact = AgentContact(
                agent_config_id=agent.id,
                user_id=user.id,
                phone=sms.from_number,
                status="new",
            )
            db.add(contact)
            db.flush()
            logger.info(f"[AGENT-SMS] 🆕 Создан AgentContact {contact.id} для входящего SMS {sms.from_number}")

        # AgentCall(direction="inbound") — на нём держится PostCall-машинерия.
        inbound_call = AgentCall(
            agent_contact_id=contact.id,
            agent_config_id=agent.id,
            user_id=user.id,
            source_task_id=None,
            call_session_id=None,
            status="calling",
            direction="inbound",
            started_at=datetime.utcnow(),
        )
        db.add(inbound_call)
        db.commit()

        logger.info(
            f"[AGENT-SMS] Inbound SMS {sms.id}: from={sms.from_number} -> agent {agent.id}, "
            f"contact={contact.id}, call={inbound_call.id}"
        )
        await PostCallOrchestrator().run_for_sms(inbound_call, contact, agent, user, sms.body, db)

    except Exception as e:
        logger.error(f"[AGENT-SMS] handle_inbound_sms error: {e}", exc_info=True)
    finally:
        db.close()


async def handle_inbound_telegram(account_id: str, agent_contact_id: str, message_body: str):
    """
    Event-driven обработка входящего сообщения личного Telegram.

    Вызывается поллером (backend/core/telegram_user_poller.py) ПОСЛЕ того, как
    он сохранил входящие в agent_telegram_messages, связал диалог с контактом и
    продвинул last_processed_msg_id (поэтому падение здесь не приводит к
    повторной обработке). Зеркалит handle_inbound_sms: проверка доступа →
    AgentCall(direction="inbound") → PostCall с call_direction="telegram_inbound"
    (агент отвечает тулзой telegram_send_message).

    Открывает собственную сессию БД — безопасно для asyncio.create_task().
    """
    from backend.models.agent_telegram_account import AgentTelegramAccount

    db = SessionLocal()
    try:
        account = db.query(AgentTelegramAccount).filter(
            AgentTelegramAccount.id == account_id
        ).first()
        if not account:
            return

        agent = db.query(AgentConfig).filter(
            AgentConfig.id == account.agent_config_id,
        ).first()
        if not agent or not agent.is_active:
            logger.info(f"[AGENT-TG-USER] agent inactive/missing for account {account_id}, skip")
            return

        user = db.query(User).filter(User.id == account.user_id).first()
        if not user:
            return

        # Доступ к агенту (триал/подписка) — как у SMS и планировщика.
        try:
            if not user.has_active_agent_subscription():
                logger.info(f"[AGENT-TG-USER] user {user.id} has no agent access, skip tg message")
                return
        except Exception:
            pass

        # v2-агента без личного OpenAI-ключа обслужить не сможем.
        if not getattr(agent, "uses_hardcoded_prompt", False) and not user.openai_api_key:
            logger.info(f"[AGENT-TG-USER] v2 agent {agent.id} without OpenAI key, skip tg message")
            return

        contact = db.query(AgentContact).filter(
            AgentContact.id == agent_contact_id,
            AgentContact.agent_config_id == agent.id,
        ).first()
        if not contact:
            return

        inbound_call = AgentCall(
            agent_contact_id=contact.id,
            agent_config_id=agent.id,
            user_id=user.id,
            source_task_id=None,
            call_session_id=None,
            status="calling",
            direction="inbound",
            started_at=datetime.utcnow(),
        )
        db.add(inbound_call)
        db.commit()

        logger.info(
            f"[AGENT-TG-USER] Inbound TG message -> agent {agent.id}, "
            f"contact={contact.id}, call={inbound_call.id}"
        )
        await PostCallOrchestrator().run_for_telegram(
            inbound_call, contact, agent, user, message_body, db
        )

    except Exception as e:
        logger.error(f"[AGENT-TG-USER] handle_inbound_telegram error: {e}", exc_info=True)
    finally:
        db.close()
