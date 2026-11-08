"""
Голосовой режим оркестратора (ветка 0811-voice-jarvis): владелец разговаривает со своим агентом
голосом, как с Gemini Live в телефоне. Голос и разговор — Gemini Live (backend/websockets/
handler_owner_voice.py), знания и действия — ТОТ ЖЕ оркестратор, что в текстовом чате агента
(те же инструменты agent_tools, тот же промпт, кредиты, ограничения тарифа).

Здесь — всё, что не про звук:
- сводка дел на начало разговора (build_brief): на «дай отчёт / кто писал / что сегодня» голос
  отвечает сразу, без похода к оркестратору (поход ≈ 3–10 с);
- промпт голосового помощника (build_voice_prompt) и три функции Gemini (GEMINI_FUNCTIONS):
  ask_agent — узнать (оркестратор только с инструментами чтения),
  do_action — сделать (оркестратор с инструментами действий, только после «да» владельца),
  send_to_telegram — отправить отчёт самому владельцу в Telegram-бота агента;
- разделение инструментов оркестратора (classify_tool): удалить клиентов/задачи, массовые
  рассылки и звонки голосом нельзя — только текстом;
- проверка подтверждения в коде (action_refusal): модель могла бы «услышать да» сама, поэтому
  действие выполняется, только если последняя реплика владельца — короткое «да / отправляй / ооба…»;
- цена минуты голоса в кредитах агента и запись разговора в историю текстового чата.
"""
from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from backend.core.logging import get_logger

logger = get_logger(__name__)

BISHKEK_OFFSET = timedelta(hours=6)
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
WEEKDAYS_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

# Себестоимость минуты Gemini Live (та же оценка, что у кошелька, см. CLAUDE.md) → кредиты агента
VOICE_COST_USD_PER_MIN = float(os.getenv("VOICE_COST_USD_PER_MIN_GEMINI_LIVE", "0.03") or 0.03)
# Предел длины разговора: ручка возобновления у Gemini есть, но разговор владельца — не звонок на час
MAX_SESSION_SECONDS = int(os.getenv("OWNER_VOICE_MAX_SECONDS", "1200") or 1200)
# NON_BLOCKING (по умолчанию): пока оркестратор работает, Gemini говорит «секунду, смотрю», а результат
# озвучивает, когда придёт. BLOCKING — молчит до результата (OWNER_VOICE_BLOCKING_TOOLS=1).
BLOCKING_TOOLS = os.getenv("OWNER_VOICE_BLOCKING_TOOLS", "0").strip().lower() in ("1", "true", "yes", "on")
DEFAULT_VOICE = (os.getenv("OWNER_VOICE_GEMINI_VOICE", "") or "Kore").strip()
# Модель оркестратора для голосовых ВОПРОСОВ (ask_agent): нужна скорость, ответ слушают вживую. Пусто —
# модель агента. Действия (do_action) всегда идут на модели агента: там важнее точность, чем секунды.
READ_MODEL = (os.getenv("OWNER_VOICE_ORCHESTRATOR_MODEL", "google/gemini-3.6-flash") or "").strip()

BRIEF_MAX_CHARS = 7000
MAX_ITERATIONS = 8
MAX_ACTIONS_PER_SESSION = 20
ANSWER_MAX_CHARS = 2500

# ---------------------------------------------------------------- инструменты оркестратора
READ_PREFIXES = ("get_", "search_", "find_", "list_", "read_", "check_")
READ_EXTRA = {"get_period_report", "get_agent_stats"}
# Голосом нельзя: необратимое и массовое. Делается текстом в чате агента.
VOICE_FORBIDDEN = {
    "delete_agent_contact",
    "delete_agent_task",
    "bulk_create_contacts",
    "bulk_schedule_calls",
    "merge_contacts",
    "send_webhook",
    "send_sms",
}
# Коннекторы (Composio): чтение — FIND/FETCH/GET, удаление — запрещено, остальное — действие
_CONNECTOR_READ = re.compile(r"_(FIND|FETCH|GET|LIST|SEARCH)(_|$)")
_CONNECTOR_FORBIDDEN = re.compile(r"_(DELETE|REMOVE|TRASH)(_|$)")


def classify_tool(name: str) -> str:
    """'read' — можно без подтверждения, 'action' — только после «да», 'forbidden' — голосом нельзя."""
    name = (name or "").strip()
    if not name:
        return "forbidden"
    if name in VOICE_FORBIDDEN:
        return "forbidden"
    if name.isupper() or (name.upper() == name and "_" in name):
        if _CONNECTOR_FORBIDDEN.search(name):
            return "forbidden"
        return "read" if _CONNECTOR_READ.search(name) else "action"
    if name in READ_EXTRA or name.startswith(READ_PREFIXES) or name.endswith("_get_thread"):
        return "read"
    return "action"


def _tool_name(tool: Dict[str, Any]) -> str:
    fn = tool.get("function") if isinstance(tool, dict) else None
    if isinstance(fn, dict):
        return fn.get("name") or ""
    return (tool or {}).get("name") or ""


def tools_for(kind: str, tools: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """kind='read' — только чтение; kind='action' — чтение + действия (без запрещённых голосом)."""
    allowed = {"read"} if kind == "read" else {"read", "action"}
    return [t for t in tools if classify_tool(_tool_name(t)) in allowed]


# ---------------------------------------------------------------- подтверждение действия
# «Да» по смыслу. Сильные — подтверждение в начале любой фразы («да, и добавь про скидку»).
# Мягкие (повелительные «давай / отправляй / напиши») — только в коротком ответе: «давай напиши Нурлану,
# что перезвоню» — это новая команда, а не «да» на повтор действия.
_STRONG_YES = {
    "да", "ага", "угу", "конечно", "ок", "окей", "ok", "okay", "верно", "правильно", "подтверждаю", "именно",
    "точно", "yes", "ооба", "оба", "макул", "туура", "албетте",
}
_SOFT_YES = {
    "давай", "давайте", "отправляй", "отправляйте", "отправь", "отправьте", "звони", "звоните", "позвони", "делай",
    "делайте", "сделай", "хорошо", "пиши", "пишите", "напиши", "ставь", "поставь", "можно", "го", "жарайт",
    "жарайсың", "жибер", "жибериңиз", "мейли", "болот", "ну",
}
_YES = _STRONG_YES | _SOFT_YES
_NO = {
    "нет", "не", "неа", "стоп", "отмена", "отмени", "отменить", "подожди", "погоди", "подождите", "стой",
    "жок", "токто", "токтот", "күт", "no",
}
_NO_PHRASES = ("не надо", "не отправляй", "не звони", "не нужно", "керек эмес", "жибербе")
_WORD = re.compile(r"[a-zа-яёөүңәіұқғһ]+", re.IGNORECASE)
SHORT_REPLY_WORDS = 4


def owner_confirmation(text: Optional[str]) -> Optional[str]:
    """'yes' — владелец подтвердил, 'no' — отказался, None — ни то ни другое (или пусто)."""
    low = (text or "").strip().lower().replace("ё", "е")
    if not low:
        return None
    if any(p in low for p in _NO_PHRASES):
        return "no"
    words = _WORD.findall(low)
    if not words:
        return None
    if words[0] in _NO:
        return "no"
    if words[0] in _STRONG_YES:
        return "yes"
    if len(words) <= SHORT_REPLY_WORDS:
        has_no = any(w in _NO for w in words)
        if has_no:
            return "no"
        if any(w in _YES for w in words if w != "ну"):
            return "yes"
    return None


def action_refusal(confirmed: Any, last_owner_text: Optional[str]) -> Optional[str]:
    """None — действие можно выполнять; иначе текст для модели, почему нет."""
    verdict = owner_confirmation(last_owner_text)
    if verdict == "no":
        return ("Владелец отказался или попросил подождать — действие НЕ выполнено. Ничего не отправляй, "
                "спроси, что изменить.")
    if confirmed is not True and str(confirmed).lower() not in ("true", "1", "yes"):
        return ("Действие не выполнено: сначала одной фразой повтори, что сделаешь (кому, где, что), спроси "
                "«Отправляю?» и дождись «да». Потом вызови do_action с confirmed=true.")
    if verdict != "yes":
        return ("Действие не выполнено: в последней реплике владельца нет подтверждения. Повтори действие "
                "одной фразой и дождись явного «да».")
    return None


# ---------------------------------------------------------------- функции голосового помощника (Gemini)
# Быстрые (FAST_FUNCTIONS) читают базу напрямую и отвечают за доли секунды — BLOCKING: модель молча ждёт
# результат и сразу говорит по нему. Медленные (поход к оркестратору ≈ 3–10 с) — NON_BLOCKING: модель
# говорит «секунду, смотрю», а результат озвучивает, когда он придёт (scheduling WHEN_IDLE).
_PERIOD_DESC = ("today — сегодня, yesterday — вчера, week — последние 7 дней, month — последние 30 дней, "
                "this_month — с 1-го числа, custom — даты date_from/date_to")
GEMINI_FUNCTIONS: List[Dict[str, Any]] = [
    {
        "name": "get_stats",
        "behavior": "BLOCKING",
        "description": (
            "Цифры за период: звонки (входящие, исходящие, с разговором, минуты), сообщения клиентов и сколько "
            "клиентов написало, ответы агента, новые клиенты, по дням. Для «сколько звонков вчера», «как прошла "
            "неделя», «сколько написали за месяц». Отвечает мгновенно."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "period": {"type": "string", "enum": ["today", "yesterday", "week", "month", "this_month", "custom"],
                           "description": _PERIOD_DESC},
                "date_from": {"type": "string", "description": "Для custom: первая дата, ГГГГ-ММ-ДД"},
                "date_to": {"type": "string", "description": "Для custom: последняя дата включительно, ГГГГ-ММ-ДД"},
            },
            "required": ["period"],
        },
    },
    {
        "name": "client_info",
        "behavior": "BLOCKING",
        "description": (
            "Всё о клиенте по имени, телефону или компании: карточка, этап воронки, что агент о нём знает, "
            "последние сообщения во всех мессенджерах и звонки с итогами. Для «что писал Нурлан», «кто такой "
            "Айбек», «чем кончился звонок с …». Отвечает мгновенно."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Имя, телефон или компания клиента"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_dialogs",
        "behavior": "BLOCKING",
        "description": (
            "Список переписок: кто ждёт ответа, непрочитанные, где бот на паузе, или все последние. Для «кто ждёт "
            "ответа», «кто писал в WhatsApp», «где бот молчит». Отвечает мгновенно."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "folder": {"type": "string", "enum": ["waiting", "unread", "paused", "all"],
                           "description": "waiting — ждут ответа, unread — непрочитанные, paused — бот на паузе, all — все"},
                "channel": {"type": "string", "enum": ["whatsapp", "telegram", "instagram", "any"],
                            "description": "Канал или any"},
            },
            "required": ["folder"],
        },
    },
    {
        "name": "get_schedule",
        "behavior": "BLOCKING",
        "description": "Запланированные звонки, сообщения и напоминания на ближайшие дни. Отвечает мгновенно.",
        "parameters": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "На сколько дней вперёд, 1–14"},
            },
            "required": ["days"],
        },
    },
    {
        "name": "ask_agent",
        "behavior": "NON_BLOCKING",
        "description": (
            "Сложный вопрос, на который быстрые функции не отвечают: анализ, сравнение, база знаний, файлы, "
            "Gmail и Календарь, стенограмма звонка, советы по клиентам. Работает несколько секунд — скажи "
            "«секунду, смотрю». Только чтение. Пиши запрос полностью и с именами, а не «он»."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Полный вопрос владельца, с именами и периодом"},
            },
            "required": ["question"],
        },
    },
    {
        "name": "do_action",
        "behavior": "NON_BLOCKING",
        "description": (
            "Выполнить команду владельца: написать или отправить клиенту (WhatsApp/Telegram/Instagram, "
            "голосовое, файл, КП), позвонить клиенту, поставить или перенести задачу/напоминание, сменить этап "
            "воронки, дописать заметку, поставить бота на паузу, создать событие в календаре. Вызывай ТОЛЬКО "
            "после того, как одной фразой повторил действие и владелец ответил «да». Удалять и массово "
            "рассылать/обзванивать нельзя."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string",
                            "description": "Команда полностью: кому, через какой канал, что именно, когда"},
                "confirmed": {"type": "boolean",
                              "description": "true — владелец только что ответил «да» на твой повтор действия"},
            },
            "required": ["command", "confirmed"],
        },
    },
    {
        "name": "send_to_telegram",
        "behavior": "BLOCKING",
        "description": (
            "Отправить самому владельцу в его Telegram (бот агента) отчёт, сводку или список, который он "
            "попросил прислать или согласился получить. Текст — готовое сообщение: коротко, по пунктам, с "
            "именами и цифрами."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Текст сообщения владельцу"},
            },
            "required": ["text"],
        },
    },
]
FAST_FUNCTIONS = {"get_stats", "client_info", "list_dialogs", "get_schedule"}
FUNCTION_NAMES = [f["name"] for f in GEMINI_FUNCTIONS]


# ---------------------------------------------------------------- сводка дел
def _bishkek(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt + BISHKEK_OFFSET


def _hm(dt: Optional[datetime]) -> str:
    b = _bishkek(dt)
    return b.strftime("%H:%M") if b else "?"


def _day_label(dt: Optional[datetime], today_bk: datetime) -> str:
    b = _bishkek(dt)
    if b is None:
        return "?"
    if b.date() == today_bk.date():
        return f"сегодня {b:%H:%M}"
    if b.date() == (today_bk - timedelta(days=1)).date():
        return f"вчера {b:%H:%M}"
    if b.date() == (today_bk + timedelta(days=1)).date():
        return f"завтра {b:%H:%M}"
    return f"{b:%d.%m} {b:%H:%M}"


def _clip(text: Optional[str], n: int) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


CHANNEL_NAMES = {"whatsapp": "WhatsApp", "telegram": "Telegram", "instagram": "Instagram", "sms": "SMS"}


def _section(title: str, rows: List[str]) -> str:
    return title + ":\n" + "\n".join(f"- {r}" for r in rows) if rows else ""


def build_brief(db, user, agent, now: Optional[datetime] = None) -> str:
    """Сводка на начало разговора. Каждый блок — отдельно: сбой одного не роняет остальные."""
    now = now or datetime.utcnow()
    today_bk = now + BISHKEK_OFFSET
    day_start = (today_bk.replace(hour=0, minute=0, second=0, microsecond=0) - BISHKEK_OFFSET)
    parts: List[str] = [f"Сейчас в Бишкеке {WEEKDAYS[today_bk.weekday()]}, {today_bk:%d.%m.%Y}, {today_bk:%H:%M}."]

    def safe(name: str, fn):
        try:
            text = fn()
            if text:
                parts.append(text)
        except Exception as exc:  # одна часть сводки не должна ронять разговор
            logger.warning(f"[OWNER-VOICE] brief part {name} failed: {exc}")
            try:
                db.rollback()
            except Exception:
                pass

    safe("today", lambda: _brief_today(db, user, agent, day_start, now))
    safe("week", lambda: _brief_week(db, user, agent, day_start, now))
    safe("dialogs", lambda: _brief_dialogs(db, user, agent, today_bk))
    safe("calls", lambda: _brief_calls(db, agent, day_start, today_bk))
    safe("tasks", lambda: _brief_tasks(db, agent, now, today_bk))
    safe("funnel", lambda: _brief_funnel(db, agent))

    text = "\n\n".join(parts)
    if len(text) > BRIEF_MAX_CHARS:
        text = text[: BRIEF_MAX_CHARS - 40].rstrip() + "\n…(сводка обрезана — подробности через ask_agent)"
    return text


def _brief_today(db, user, agent, day_start: datetime, now: datetime) -> str:
    days = activity_by_day(db, user, agent, day_start, now + timedelta(minutes=1))
    t = _sum_days(days)
    t["writers"] = count_writers(db, user, agent, day_start, now + timedelta(minutes=1))
    return "Сегодня: " + _stats_sentence(t) + "."


def _brief_week(db, user, agent, day_start: datetime, now: datetime) -> str:
    since = day_start - timedelta(days=6)
    days = activity_by_day(db, user, agent, since, now + timedelta(minutes=1))
    rows = []
    for i in range(6, -1, -1):
        d = (day_start - timedelta(days=i) + BISHKEK_OFFSET).date()
        st = days.get(d) or _empty_day()
        label = "сегодня" if i == 0 else "вчера" if i == 1 else f"{WEEKDAYS_SHORT[d.weekday()]} {d:%d.%m}"
        rows.append(f"{label}: {_stats_sentence(st, short=True)}")
    t = _sum_days(days)
    t["writers"] = count_writers(db, user, agent, since, now + timedelta(minutes=1))
    return _section("По дням за неделю (вопросы «вчера», «за неделю» — отсюда, без функций)", rows) + \
        "\nИтого за 7 дней: " + _stats_sentence(t) + "."


def _brief_dialogs(db, user, agent, today_bk: datetime) -> str:
    from backend.services import inbox_service

    data = inbox_service.list_dialogs(db, user, agent_id=agent.id, limit=10)
    items = data.get("items") or []
    counts = data.get("counts") or {}
    rows = []
    for it in items:
        last_at = it.get("last_at")
        try:
            dt = datetime.fromisoformat(str(last_at).replace("Z", "+00:00")) if last_at else None
        except ValueError:
            dt = None
        who = {"client": "клиент", "bot": "агент", "owner": "вы"}.get(it.get("last_by") or "", it.get("last_by") or "")
        state = "ждёт ответа" if it.get("waiting") else f"последним писал {who}" if who else ""
        flags = []
        if it.get("paused"):
            flags.append("бот на паузе")
        if it.get("unread"):
            flags.append(f"непрочитанных {it['unread']}")
        chans = ", ".join(CHANNEL_NAMES.get(c, c) for c in (it.get("channels") or []))
        rows.append(
            f"{it.get('name')}{' (' + it['company'] + ')' if it.get('company') else ''} — {chans}, "
            f"{_day_label(dt, today_bk)}, {state}{'; ' + ', '.join(flags) if flags else ''}: "
            f"«{_clip(it.get('preview'), 140)}» [contact_id={it.get('contact_id')}]"
        )
    head = (f"Переписка: диалогов {counts.get('all', 0)}, ждут ответа {counts.get('waiting', 0)}, "
            f"с непрочитанными {counts.get('unread', 0)}, бот на паузе {counts.get('paused', 0)}"
            + (f", чатов WhatsApp без карточки {counts['no_card']}" if counts.get("no_card") else "") + ".")
    return head + ("\n" + _section("Последние диалоги (свежие сверху)", rows) if rows else "")


def _brief_calls(db, agent, day_start: datetime, today_bk: datetime) -> str:
    from backend.models.agent_call import AgentCall
    from backend.models.agent_contact import AgentContact
    from backend.services.agent_orchestrator import MESSAGE_DIRECTIONS

    since = day_start - timedelta(days=1)
    rows = (
        db.query(AgentCall, AgentContact)
        .outerjoin(AgentContact, AgentContact.id == AgentCall.agent_contact_id)
        .filter(AgentCall.agent_config_id == agent.id, AgentCall.created_at >= since,
                ~AgentCall.direction.in_(list(MESSAGE_DIRECTIONS)))
        .order_by(AgentCall.created_at.desc())
        .limit(8)
        .all()
    )
    out = []
    for call, contact in rows:
        name = (contact.name or contact.phone) if contact is not None else "неизвестный"
        kind = "входящий" if (call.direction or "") == "inbound" else "исходящий"
        dur = int(call.duration_seconds or 0)
        dur_txt = f", {dur // 60} мин {dur % 60} с" if dur else ""
        state = {"answered": "был разговор", "no_answer": "не ответил", "failed": "не соединился"}.get(
            call.status or "", call.status or "")
        summary = _clip(call.call_result, 220)
        out.append(f"{name} — {kind}, {_day_label(call.created_at, today_bk)}{dur_txt}, {state}"
                   + (f": {summary}" if summary else ""))
    return _section("Последние звонки (за вчера и сегодня)", out)


def _brief_tasks(db, agent, now: datetime, today_bk: datetime) -> str:
    from backend.models.agent_contact import AgentContact
    from backend.models.task import Task, TaskStatus

    horizon = now + timedelta(hours=36)
    rows = (
        db.query(Task, AgentContact)
        .join(AgentContact, AgentContact.id == Task.agent_contact_id)
        .filter(AgentContact.agent_config_id == agent.id, Task.status == TaskStatus.SCHEDULED,
                Task.scheduled_time <= horizon)
        .order_by(Task.scheduled_time.asc())
        .limit(8)
        .all()
    )
    out = []
    for task, contact in rows:
        what = {"call": "звонок", "telegram": "сообщение в Telegram", "owner": "напоминание вам"}.get(
            task.channel or "call", task.channel or "")
        who = "вы" if (getattr(task, "created_by", None) == "owner") else "агент"
        out.append(f"{_day_label(task.scheduled_time, today_bk)} — {what}: {contact.name or contact.phone}"
                   f" ({_clip(task.title, 80)}; поставил {who})")
    return _section("Ближайшие задачи (до завтрашнего вечера)", out)


def _brief_funnel(db, agent) -> str:
    from sqlalchemy import func
    from backend.core.pipeline_stages import get_agent_stages
    from backend.models.agent_contact import AgentContact

    counts = dict(
        db.query(AgentContact.status, func.count(AgentContact.id))
        .filter(AgentContact.agent_config_id == agent.id)
        .group_by(AgentContact.status)
        .all()
    )
    if not counts:
        return ""
    labels = {s["key"]: s["label"] for s in get_agent_stages(agent)}
    total = sum(int(v) for v in counts.values())
    parts = [f"{labels.get(k, k)} {int(v)}" for k, v in counts.items() if v]
    return f"Воронка CRM: всего клиентов {total} — " + ", ".join(parts) + "."


# ---------------------------------------------------------------- активность по дням (сводка и get_stats)
def _empty_day() -> Dict[str, int]:
    return {"calls_in": 0, "calls_out": 0, "answered": 0, "talk_sec": 0, "msgs_in": 0, "writers": 0,
            "bot_out": 0, "owner_out": 0, "new_clients": 0}


def _sum_days(days: Dict[Any, Dict[str, int]]) -> Dict[str, int]:
    total = _empty_day()
    for st in days.values():
        for k, v in st.items():
            total[k] = total.get(k, 0) + int(v or 0)
    total["writers"] = 0   # уникальных за период считает count_writers, сумма по дням тут неверна
    return total


def _stats_sentence(st: Dict[str, int], short: bool = False) -> str:
    calls = st["calls_in"] + st["calls_out"]
    talk_min = round(st["talk_sec"] / 60)
    if short:
        return (f"звонков {calls} (входящих {st['calls_in']}, с разговором {st['answered']}), "
                f"сообщений от клиентов {st['msgs_in']}, новых клиентов {st['new_clients']}")
    out = (f"звонков {calls} (входящих {st['calls_in']}, исходящих {st['calls_out']}, с разговором "
           f"{st['answered']}, разговоров {talk_min} мин); сообщений от клиентов {st['msgs_in']}")
    if st.get("writers"):
        out += f" от {st['writers']} клиентов"
    out += (f", ответов агента {st['bot_out']}, ваших ответов {st['owner_out']}; "
            f"новых клиентов в CRM {st['new_clients']}")
    return out


def _bk_day_expr(column):
    from sqlalchemy import func, text
    return func.date(column + text("interval '6 hours'"))


def activity_by_day(db, user, agent, since: datetime, until: datetime) -> Dict[Any, Dict[str, int]]:
    """Звонки, сообщения и новые клиенты по дням (дата по Бишкеку); since/until — naive UTC."""
    from sqlalchemy import func, text
    from backend.models.agent_call import AgentCall
    from backend.models.agent_contact import AgentContact
    from backend.services import inbox_service
    from backend.services.agent_orchestrator import MESSAGE_DIRECTIONS

    days: Dict[Any, Dict[str, int]] = {}

    def day(d):
        return days.setdefault(d, _empty_day())

    day_col = _bk_day_expr(AgentCall.created_at)
    for d, direction, status, n, secs in (
        db.query(day_col, AgentCall.direction, AgentCall.status, func.count(AgentCall.id),
                 func.coalesce(func.sum(AgentCall.duration_seconds), 0))
        .filter(AgentCall.agent_config_id == agent.id, AgentCall.created_at >= since, AgentCall.created_at < until,
                ~AgentCall.direction.in_(list(MESSAGE_DIRECTIONS)))
        .group_by(day_col, AgentCall.direction, AgentCall.status)
        .all()
    ):
        st = day(d)
        if (direction or "outbound") == "inbound":
            st["calls_in"] += int(n)
        else:
            st["calls_out"] += int(n)
        if status == "answered":
            st["answered"] += int(n)
            st["talk_sec"] += int(secs or 0)

    union = " UNION ALL ".join(inbox_service._UNION_PARTS.values())
    sql = (
        "WITH m AS (" + union + ") "
        "SELECT date(m.created_at + interval '6 hours') AS d, "
        "count(*) FILTER (WHERE m.direction = 'inbound') AS msgs_in, "
        "count(DISTINCT m.cid) FILTER (WHERE m.direction = 'inbound') AS writers, "
        "count(*) FILTER (WHERE m.direction = 'outbound' AND COALESCE(m.sent_by, '') <> 'owner') AS bot_out, "
        "count(*) FILTER (WHERE m.direction = 'outbound' AND m.sent_by = 'owner') AS owner_out "
        "FROM m JOIN agent_contacts c ON c.id = m.cid "
        "WHERE c.agent_config_id = :aid AND m.created_at >= :since AND m.created_at < :until "
        "GROUP BY 1"
    )
    for r in db.execute(text(sql), {"uid": str(user.id), "aid": str(agent.id), "since": since, "until": until}):
        st = day(r.d)
        st["msgs_in"] += int(r.msgs_in or 0)
        st["writers"] += int(r.writers or 0)
        st["bot_out"] += int(r.bot_out or 0)
        st["owner_out"] += int(r.owner_out or 0)

    nc_col = _bk_day_expr(AgentContact.created_at)
    for d, n in (
        db.query(nc_col, func.count(AgentContact.id))
        .filter(AgentContact.agent_config_id == agent.id, AgentContact.created_at >= since,
                AgentContact.created_at < until)
        .group_by(nc_col).all()
    ):
        day(d)["new_clients"] += int(n)
    return days


def count_writers(db, user, agent, since: datetime, until: datetime) -> int:
    """Сколько разных клиентов написало за период (во всех мессенджерах)."""
    from sqlalchemy import text
    from backend.services import inbox_service

    union = " UNION ALL ".join(inbox_service._UNION_PARTS.values())
    sql = ("WITH m AS (" + union + ") SELECT count(DISTINCT m.cid) FROM m JOIN agent_contacts c ON c.id = m.cid "
           "WHERE c.agent_config_id = :aid AND m.direction = 'inbound' AND m.created_at >= :since "
           "AND m.created_at < :until")
    return int(db.execute(text(sql), {"uid": str(user.id), "aid": str(agent.id), "since": since,
                                      "until": until}).scalar() or 0)


def period_range(period: str, date_from: Optional[str] = None, date_to: Optional[str] = None,
                 now: Optional[datetime] = None):
    """(since_utc, until_utc, подпись) для периода в датах Бишкека."""
    now = now or datetime.utcnow()
    today = (now + BISHKEK_OFFSET).date()
    period = (period or "today").strip().lower()

    def utc(d):
        return datetime(d.year, d.month, d.day) - BISHKEK_OFFSET

    if period == "yesterday":
        a = b = today - timedelta(days=1)
        label = f"вчера, {a:%d.%m}"
    elif period == "week":
        a, b = today - timedelta(days=6), today
        label = f"последние 7 дней ({a:%d.%m}–{b:%d.%m})"
    elif period == "month":
        a, b = today - timedelta(days=29), today
        label = f"последние 30 дней ({a:%d.%m}–{b:%d.%m})"
    elif period == "this_month":
        a, b = today.replace(day=1), today
        label = f"этот месяц ({a:%d.%m}–{b:%d.%m})"
    elif period == "custom":
        try:
            a = datetime.strptime((date_from or "").strip()[:10], "%Y-%m-%d").date()
            b = datetime.strptime((date_to or date_from or "").strip()[:10], "%Y-%m-%d").date()
        except ValueError:
            a = b = today
        if a > b:
            a, b = b, a
        b = min(b, today)
        a = max(a, b - timedelta(days=366))
        label = f"{a:%d.%m.%Y}" if a == b else f"{a:%d.%m.%Y}–{b:%d.%m.%Y}"
    else:
        a = b = today
        label = f"сегодня, {a:%d.%m}"
    return utc(a), utc(b) + timedelta(days=1), label, a, b


def fast_get_stats(db, user, agent, args: Dict[str, Any]) -> Dict[str, Any]:
    since, until, label, a, b = period_range(args.get("period"), args.get("date_from"), args.get("date_to"))
    days = activity_by_day(db, user, agent, since, until)
    total = _sum_days(days)
    total["writers"] = count_writers(db, user, agent, since, until)
    calls = total["calls_in"] + total["calls_out"]
    result: Dict[str, Any] = {
        "ok": True,
        "period": label,
        "summary": _stats_sentence(total),
        "calls": {"total": calls, "incoming": total["calls_in"], "outgoing": total["calls_out"],
                  "with_talk": total["answered"], "talk_minutes": round(total["talk_sec"] / 60)},
        "messages": {"from_clients": total["msgs_in"], "clients_wrote": total["writers"],
                     "agent_replies": total["bot_out"], "your_replies": total["owner_out"]},
        "new_clients": total["new_clients"],
    }
    n_days = (b - a).days + 1
    if 1 < n_days <= 31:
        result["by_day"] = []
        for i in range(n_days):
            d = a + timedelta(days=i)
            st = days.get(d) or _empty_day()
            result["by_day"].append({"day": f"{WEEKDAYS_SHORT[d.weekday()]} {d:%d.%m}",
                                     "calls": st["calls_in"] + st["calls_out"], "with_talk": st["answered"],
                                     "messages": st["msgs_in"], "new_clients": st["new_clients"]})
    result["card"] = {
        "type": "stats", "title": label[:1].upper() + label[1:],
        "tiles": [
            {"label": "Звонки", "value": calls, "sub": f"с разговором {total['answered']}"},
            {"label": "Сообщения", "value": total["msgs_in"], "sub": f"от {total['writers']} клиентов"},
            {"label": "Новые клиенты", "value": total["new_clients"], "sub": "в CRM"},
            {"label": "Разговоры", "value": f"{round(total['talk_sec'] / 60)} мин", "sub": f"входящих {total['calls_in']}"},
        ],
        "bars": [{"label": x["day"][-5:], "value": x["calls"] + x["messages"]} for x in result.get("by_day", [])],
    }
    return result


def _stage_label(agent, key: Optional[str]) -> str:
    try:
        from backend.core.pipeline_stages import get_agent_stages
        labels = {s["key"]: s["label"] for s in get_agent_stages(agent)}
        return labels.get(key or "", key or "")
    except Exception:
        return key or ""


def find_clients(db, user, agent, query: str, limit: int = 5) -> list:
    from sqlalchemy import or_
    from backend.models.agent_contact import AgentContact

    q = (query or "").strip()
    if not q:
        return []
    if sum(ch.isdigit() for ch in q) >= 7:
        try:
            from backend.services.agent_contact_phones import find_contacts_by_phone_any
            found, _tail = find_contacts_by_phone_any(db, user.id, agent.id, q, limit=limit)
            if found:
                return list(found)[:limit]
        except Exception as exc:
            logger.warning(f"[OWNER-VOICE] phone search failed: {exc}")
            db.rollback()
    words = [w for w in re.split(r"\s+", q) if len(w) >= 2][:3] or [q]
    conds = []
    for w in words:
        like = f"%{w}%"
        conds.append(or_(AgentContact.name.ilike(like), AgentContact.company.ilike(like),
                         AgentContact.phone.ilike(like), AgentContact.tags.ilike(like)))
    base = db.query(AgentContact).filter(AgentContact.agent_config_id == agent.id)
    rows = base.filter(*conds).order_by(AgentContact.updated_at.desc()).limit(limit).all()
    if not rows and len(words) > 1:   # «Нурлан Асанов» → хотя бы по одному слову
        rows = base.filter(or_(*conds)).order_by(AgentContact.updated_at.desc()).limit(limit).all()
    return rows


_WHO = {"client": "клиент", "bot": "агент", "owner": "вы"}
_OUTCOMES = {"answered": "был разговор", "no_answer": "не ответил", "failed": "не соединился", "busy": "занято",
             "completed": "был разговор", "missed": "пропущен", "scheduled": "запланирован", "calling": "идёт"}


def _memory_brief(memory: Any, limit: int = 700) -> str:
    if not isinstance(memory, dict) or not memory:
        return ""
    skip = {"nurture", "reengage", "lost_by", "whatsapp_phone"}
    parts = []
    for k, v in memory.items():
        if k in skip or v in (None, "", [], {}):
            continue
        val = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        parts.append(f"{k}: {_clip(val, 200)}")
    return _clip("; ".join(parts), limit)


def fast_client_info(db, user, agent, args: Dict[str, Any]) -> Dict[str, Any]:
    from backend.models.agent_call import AgentCall
    from backend.services import inbox_service
    from backend.services.agent_orchestrator import MESSAGE_DIRECTIONS

    query = (args.get("query") or "").strip()
    rows = find_clients(db, user, agent, query)
    if not rows:
        return {"ok": False, "message": f"Клиента «{query}» в CRM не нашёл. Уточни имя или номер."}
    c = rows[0]
    now_bk = datetime.utcnow() + BISHKEK_OFFSET
    feed = []
    try:
        items = inbox_service.timeline(db, c, limit=10).get("items") or []
    except Exception as exc:
        logger.warning(f"[OWNER-VOICE] timeline failed: {exc}")
        db.rollback()
        items = []
    for it in items[-10:]:
        try:
            ts = datetime.fromisoformat(str(it.get("ts")).replace("Z", "+00:00")) if it.get("ts") else None
        except ValueError:
            ts = None
        when = _day_label(ts, now_bk)
        if it.get("type") == "call":
            if (it.get("direction") or "") in MESSAGE_DIRECTIONS:
                continue   # разбор переписки агентом — не звонок, сами сообщения уже в ленте
            dur = int(it.get("duration") or 0)
            outcome = str(it.get("outcome") or it.get("status") or "")
            feed.append(f"{when} · звонок {'входящий' if it.get('direction') == 'inbound' else 'исходящий'}"
                        f"{f', {dur // 60} мин {dur % 60} с' if dur else ''}"
                        f"{' · ' + _OUTCOMES.get(outcome, outcome) if outcome else ''}")
        else:
            who = _WHO.get(it.get("who") or "", it.get("who") or "")
            text = _clip(it.get("text") or ("файл" if it.get("attachment") else ""), 220)
            feed.append(f"{when} · {CHANNEL_NAMES.get(it.get('channel'), it.get('channel'))} · {who}"
                        f"{' (голосовое)' if it.get('voice') else ''}: «{text}»")
    calls = []
    for call in (db.query(AgentCall).filter(AgentCall.agent_contact_id == c.id,
                                            ~AgentCall.direction.in_(list(MESSAGE_DIRECTIONS)))
                 .order_by(AgentCall.created_at.desc()).limit(3).all()):
        if call.call_result:
            calls.append(f"{_day_label(call.created_at, now_bk)}: {_clip(call.call_result, 260)}")
    from backend.services import agent_pause
    client = {
        "name": c.name or c.phone, "company": c.company or None, "phone": inbox_service.real_phone(c.phone),
        "stage": _stage_label(agent, c.status), "in_crm_since": _day_label(c.created_at, now_bk),
        "deal_amount": float(c.deal_amount) if getattr(c, "deal_amount", None) is not None else None,
        "next_step": c.next_step_text or None, "notes": _clip(c.notes, 400) or None,
        "agent_knows": _memory_brief(c.memory) or None,
        "bot_paused": bool(agent_pause.is_paused(c, datetime.utcnow())),
        "recent": feed, "call_results": calls,
    }
    out = {"ok": True, "client": {k: v for k, v in client.items() if v not in (None, [], "")}}
    if len(rows) > 1:
        out["other_matches"] = [r.name or r.phone for r in rows[1:4]]
        out["note"] = "Есть ещё похожие клиенты — если владелец имел в виду другого, уточни."
    out["card"] = {"type": "client", "title": client["name"], "subtitle": " · ".join(
        x for x in (client.get("company"), client["stage"], client.get("phone")) if x),
        "lines": feed[-4:]}
    return out


def fast_list_dialogs(db, user, agent, args: Dict[str, Any]) -> Dict[str, Any]:
    from backend.services import inbox_service

    folder = args.get("folder") if args.get("folder") in ("waiting", "unread", "paused", "all") else "waiting"
    channel = args.get("channel") if args.get("channel") in ("whatsapp", "telegram", "instagram") else None
    data = inbox_service.list_dialogs(db, user, folder=folder, channel=channel, agent_id=agent.id, limit=8)
    now_bk = datetime.utcnow() + BISHKEK_OFFSET
    items = []
    for it in data.get("items") or []:
        try:
            dt = datetime.fromisoformat(str(it.get("last_at")).replace("Z", "+00:00")) if it.get("last_at") else None
        except ValueError:
            dt = None
        items.append({
            "name": it.get("name"), "channels": [CHANNEL_NAMES.get(x, x) for x in it.get("channels") or []],
            "when": _day_label(dt, now_bk), "last_from": _WHO.get(it.get("last_by") or "", ""),
            "text": _clip(it.get("preview"), 160), "waiting": bool(it.get("waiting")),
            "bot_paused": bool(it.get("paused")),
        })
    counts = data.get("counts") or {}
    titles = {"waiting": "Ждут ответа", "unread": "Непрочитанные", "paused": "Бот на паузе", "all": "Последние диалоги"}
    return {"ok": True, "folder": titles[folder], "counts": {"waiting": counts.get("waiting", 0),
                                                             "unread": counts.get("unread", 0),
                                                             "paused": counts.get("paused", 0)},
            "dialogs": items,
            "card": {"type": "list", "title": titles[folder],
                     "items": [{"title": x["name"], "meta": f"{', '.join(x['channels'])} · {x['when']}",
                                "text": x["text"]} for x in items[:6]]}}


def fast_get_schedule(db, user, agent, args: Dict[str, Any]) -> Dict[str, Any]:
    from backend.models.agent_contact import AgentContact
    from backend.models.task import Task, TaskStatus

    try:
        days = max(1, min(int(args.get("days") or 1), 14))
    except (TypeError, ValueError):
        days = 1
    now = datetime.utcnow()
    now_bk = now + BISHKEK_OFFSET
    end_bk = now_bk.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=days)
    rows = (
        db.query(Task, AgentContact)
        .join(AgentContact, AgentContact.id == Task.agent_contact_id)
        .filter(AgentContact.agent_config_id == agent.id, Task.status == TaskStatus.SCHEDULED,
                Task.scheduled_time <= end_bk - BISHKEK_OFFSET)
        .order_by(Task.scheduled_time.asc()).limit(20).all()
    )
    items = []
    for task, contact in rows:
        what = {"call": "звонок", "telegram": "сообщение в Telegram", "owner": "напоминание вам"}.get(
            task.channel or "call", task.channel or "")
        items.append({"when": _day_label(task.scheduled_time, now_bk), "what": what,
                      "client": contact.name or contact.phone, "title": _clip(task.title, 100),
                      "by": "вы" if getattr(task, "created_by", None) == "owner" else "агент"})
    return {"ok": True, "days": days, "count": len(items), "items": items,
            "card": {"type": "list", "title": f"Запланировано ({len(items)})",
                     "items": [{"title": f"{x['when']} · {x['what']}", "meta": x["client"], "text": x["title"]}
                               for x in items[:6]]}}


def dashboard_cards(db, user, agent, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Карточки на экран в начале разговора: сегодня (+ график недели), ждут ответа, воронка.
    Владелец сразу видит картину дня, пока агент здоровается. Сбой одной карточки не мешает остальным."""
    from backend.services import inbox_service

    now = now or datetime.utcnow()
    today_bk = now + BISHKEK_OFFSET
    day_start = today_bk.replace(hour=0, minute=0, second=0, microsecond=0) - BISHKEK_OFFSET
    until = now + timedelta(minutes=1)
    cards: List[Dict[str, Any]] = []
    waiting_total = 0
    waiting_items: List[Dict[str, Any]] = []

    def safe(name, fn):
        try:
            return fn()
        except Exception as exc:
            logger.warning(f"[OWNER-VOICE] dashboard {name} failed: {exc}")
            try:
                db.rollback()
            except Exception:
                pass
            return None

    data = safe("dialogs", lambda: inbox_service.list_dialogs(db, user, folder="waiting", agent_id=agent.id, limit=4))
    if data:
        waiting_total = int((data.get("counts") or {}).get("waiting") or 0)
        for it in data.get("items") or []:
            try:
                dt = datetime.fromisoformat(str(it.get("last_at")).replace("Z", "+00:00")) if it.get("last_at") else None
            except ValueError:
                dt = None
            waiting_items.append({"title": it.get("name"),
                                  "meta": f"{', '.join(CHANNEL_NAMES.get(c, c) for c in it.get('channels') or [])} · "
                                          f"{_day_label(dt, today_bk)}",
                                  "text": _clip(it.get("preview"), 110),
                                  "channel": (it.get("channels") or [None])[0]})

    def today_card():
        days = activity_by_day(db, user, agent, day_start - timedelta(days=6), until)
        t = days.get(today_bk.date()) or _empty_day()
        writers = count_writers(db, user, agent, day_start, until)
        bars = []
        for i in range(6, -1, -1):
            d = (today_bk - timedelta(days=i)).date()
            st = days.get(d) or _empty_day()
            bars.append({"label": "сег" if i == 0 else WEEKDAYS_SHORT[d.weekday()],
                         "value": st["calls_in"] + st["calls_out"] + st["msgs_in"]})
        return {
            "type": "stats", "title": "Сегодня", "subtitle": f"{WEEKDAYS[today_bk.weekday()]}, {today_bk:%d.%m}",
            "tiles": [
                {"label": "Звонки", "value": t["calls_in"] + t["calls_out"], "sub": f"с разговором {t['answered']}"},
                {"label": "Сообщения", "value": t["msgs_in"], "sub": f"от {writers} клиентов"},
                {"label": "Новые клиенты", "value": t["new_clients"], "sub": "в CRM"},
                {"label": "Ждут ответа", "value": waiting_total, "sub": "диалогов", "accent": bool(waiting_total)},
            ],
            "bars": bars, "bars_title": "Обращения за 7 дней",
        }

    card = safe("today", today_card)
    if card:
        cards.append(card)
    if waiting_items:
        cards.append({"type": "list", "title": f"Ждут ответа ({waiting_total})", "items": waiting_items})

    def funnel_card():
        from sqlalchemy import func
        from backend.core.pipeline_stages import get_agent_stages
        from backend.models.agent_contact import AgentContact

        counts = dict(db.query(AgentContact.status, func.count(AgentContact.id))
                      .filter(AgentContact.agent_config_id == agent.id).group_by(AgentContact.status).all())
        if not counts:
            return None
        stages = []
        known = set()
        for st in get_agent_stages(agent):
            known.add(st["key"])
            stages.append({"label": st["label"], "value": int(counts.get(st["key"], 0))})
        other = sum(int(v) for k, v in counts.items() if k not in known)
        if other:
            stages.append({"label": "Другое", "value": other})
        return {"type": "funnel", "title": "Воронка CRM", "subtitle": f"всего клиентов {sum(int(v) for v in counts.values())}",
                "stages": stages}

    card = safe("funnel", funnel_card)
    if card:
        cards.append(card)
    return cards


def save_prefs(user_id: str, agent_id: str, prefs: Dict[str, Any]) -> bool:
    """Сохранить настройки голосового режима владельца (своя сессия БД — вызывается из потока)."""
    from backend.db.session import SessionLocal
    from backend.models.agent_config import AgentConfig

    db = SessionLocal()
    try:
        agent = db.query(AgentConfig).filter(AgentConfig.id == agent_id, AgentConfig.user_id == user_id).first()
        if agent is None:
            return False
        agent.owner_voice_prefs = clean_prefs(prefs)
        db.commit()
        return True
    except Exception as exc:
        logger.error(f"[OWNER-VOICE] save prefs failed: {exc}")
        try:
            db.rollback()
        except Exception:
            pass
        return False
    finally:
        db.close()


def build_session_context(db, user, agent) -> Dict[str, Any]:
    """Всё для начала разговора одним заходом в поток: сводка в промпт и карточки на экран."""
    return {"brief": build_brief(db, user, agent), "cards": dashboard_cards(db, user, agent)}


FAST_IMPL = {
    "get_stats": fast_get_stats,
    "client_info": fast_client_info,
    "list_dialogs": fast_list_dialogs,
    "get_schedule": fast_get_schedule,
}


def run_fast_function(name: str, args: Dict[str, Any], user_id: str, agent_id: str) -> Dict[str, Any]:
    """Быстрая функция в отдельном потоке (asyncio.to_thread): своя сессия БД, звук не стоит."""
    from backend.db.session import SessionLocal
    from backend.models.agent_config import AgentConfig
    from backend.models.user import User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        agent = db.query(AgentConfig).filter(AgentConfig.id == agent_id, AgentConfig.user_id == user_id).first()
        if user is None or agent is None:
            return {"ok": False, "error": "Агент недоступен"}
        return FAST_IMPL[name](db, user, agent, args if isinstance(args, dict) else {})
    except Exception as exc:
        logger.error(f"[OWNER-VOICE] fast {name} failed: {exc}", exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        return {"ok": False, "error": "Не получилось достать данные — попробуй ask_agent"}
    finally:
        db.close()


# ---------------------------------------------------------------- промпт голосового помощника
VOICE_ROLE = """ТЫ — ГОЛОСОВОЙ ПОМОЩНИК ВЛАДЕЛЬЦА БИЗНЕСА. С тобой говорит {owner} — владелец, а не клиент. Ты тот же агент Voksy AI, который ведёт его клиентов, CRM, звонки и переписку, только сейчас — голосом: толковый помощник-референт, который в курсе всех дел компании и сразу отвечает по сути.

КАК ГОВОРИТЬ
- Естественно, тепло и уверенно, как живой ассистент. Без канцелярита и без «отличный вопрос».
- Коротко: на простой вопрос — одна-две фразы. Отчёт — три-шесть коротких фраз: сначала главные цифры, потом самое важное (кто ждёт ответа, горячие клиенты, что сорвалось), не перечисляй всё подряд.
- Цифры, время и даты говори как в речи: «двенадцать обращений», «в половине третьего», «вчера вечером».
- Никаких списков с номерами, «во-первых», markdown, ссылок и id вслух.
- Язык — тот, на котором говорит владелец (русский или кыргызский). Перешёл — сразу переходи, без комментариев.
- Перебили — сразу замолчи и слушай. Владелец говорит — не вставляй ни слова.
- Не понял или не расслышал — коротко переспроси, не отвечай наугад.
- Если в ответе много деталей (список клиентов, отчёт), один раз предложи: «Отправить подробно в Telegram?» Не в каждой реплике.

ЧТО ТЫ ЗНАЕШЬ И КАК УЗНАЁШЬ
- Ниже — СВОДКА ДЕЛ на начало разговора: сегодня, по дням за неделю, последние диалоги и звонки, задачи, воронка. На «дай отчёт», «что сегодня», «сколько звонков вчера», «как прошла неделя», «кто писал», «что по задачам» отвечай из неё сразу, без функций.
- Быстрые функции отвечают мгновенно и ПОКАЗЫВАЮТ владельцу карточку на экране (цифры, графики, клиента, список) — ничего не говори перед ними: get_stats — цифры за период; client_info — всё о конкретном клиенте, его переписка и звонки; list_dialogs — кто ждёт ответа, непрочитанные; get_schedule — что запланировано.
- Владелец любит видеть цифры: на вопрос о цифрах за любой период, о клиенте, о переписке или о планах — вызывай быструю функцию, даже если ответ есть в сводке (сводка — чтобы ты не ошибся и знал общую картину). На «дай отчёт» / «что нового» — get_stats за сегодня и list_dialogs «ждут ответа», потом расскажи главное.
- Сложное (анализ, советы, база знаний, документы, почта, календарь, стенограмма звонка) — ask_agent: он думает несколько секунд, поэтому скажи одну короткую фразу — «Секунду, смотрю» — и жди. До результата — никаких цифр, имён и выводов.
- Ничего не выдумывай: нет в сводке и в ответе функции — значит не знаешь, так и скажи.
- Пришёл ответ — перескажи главное своими словами, коротко. На экране владелец видит карточку с цифрами — не зачитывай всё подряд, назови главное.

ДЕЙСТВИЯ (написать или отправить клиенту, позвонить, поставить или перенести задачу, сменить этап, заметка, пауза бота, событие в календаре)
- Сначала одной фразой повтори, что сделаешь: кому, через какой канал, что именно, когда. Например: «Пишу Нурлану в WhatsApp, что вы перезвоните в три. Отправляю?»
- Только после явного «да» владельца вызови do_action с confirmed=true и той же командой полностью. Без «да» — не вызывай. Сказал «нет» или поправил — уточни и повтори заново.
- После выполнения коротко скажи, что сделано. Если оркестратор вернул вопрос или ошибку — передай владельцу своими словами.
- Удалять клиентов и задачи, массово рассылать и массово обзванивать голосом нельзя — скажи, что это делается текстом в чате агента.
- Отчёт или сводку самому владельцу в Telegram — send_to_telegram. Подтверждение не нужно, если он сам попросил или согласился на твоё предложение.

Ты не продавец и не консультант клиента: владельцу ничего не продаёшь и не задаёшь лишних вопросов. Не говори о себе как о модели и не называй технологии и провайдеров."""

GREETING_RULE = "Начало разговора: поздоровайся одной короткой фразой так, как указано в обращении, и скажи, что слушаешь. Отчёт сам не начинай."

# ---------------------------------------------------------------- как обращаться к владельцу
ADDRESS_MODES = ("name", "chef", "boss", "custom")
FORMALITY = ("vy", "ty")
_ADDRESS_WORDS = {"chef": "шеф", "boss": "босс"}


def clean_prefs(raw: Any) -> Dict[str, Any]:
    """Настройки голосового режима владельца: только известные поля и значения."""
    raw = raw if isinstance(raw, dict) else {}
    out: Dict[str, Any] = {}
    mode = str(raw.get("address") or "name").strip().lower()
    out["address"] = mode if mode in ADDRESS_MODES else "name"
    text = re.sub(r"[\r\n\t]+", " ", str(raw.get("address_text") or "")).strip()
    text = re.sub(r"[«»\"<>{}\[\]]", "", text)[:40]
    if text:
        out["address_text"] = text
    if out["address"] == "custom" and not text:
        out["address"] = "name"
    formality = str(raw.get("formality") or "vy").strip().lower()
    out["formality"] = formality if formality in FORMALITY else "vy"
    voice = str(raw.get("voice") or "").strip()
    if voice and re.fullmatch(r"[A-Za-z]{2,20}", voice):
        out["voice"] = voice
    return out


def address_word(prefs: Optional[Dict[str, Any]], user) -> str:
    """Как называть владельца: «Аман», «шеф», «босс» или своё слово ('' — имя неизвестно)."""
    p = clean_prefs(prefs)
    if p["address"] in _ADDRESS_WORDS:
        return _ADDRESS_WORDS[p["address"]]
    if p["address"] == "custom":
        return p.get("address_text") or ""
    return p.get("address_text") or owner_first_name(user)


def address_note(prefs: Optional[Dict[str, Any]], user) -> str:
    p = clean_prefs(prefs)
    word = address_word(p, user)
    tone = ("на «ты», дружески и по-свойски, но с уважением" if p["formality"] == "ty"
            else "на «вы», тепло и уважительно")
    if p["address"] in ("chef", "boss", "custom") and word:
        who = f"Обращайся к владельцу «{word}» — так он попросил; по имени не называй."
    elif word:
        who = f"Обращайся к владельцу по имени — «{word}»."
    else:
        who = "Имя владельца неизвестно — обращайся без имени."
    return f"ОБРАЩЕНИЕ: {who} Говори с ним {tone}. Обращение вставляй естественно, не в каждой фразе."


def greeting_text(user, prefs: Optional[Dict[str, Any]] = None) -> str:
    p = clean_prefs(prefs)
    word = address_word(p, user)
    if p["formality"] == "ty":
        return f"Привет, {word}! Слушаю." if word else "Привет! Слушаю."
    if p["address"] in ("chef", "boss", "custom") and word:
        return f"Слушаю вас, {word}!"
    return f"Здравствуйте, {word}! Слушаю вас." if word else "Здравствуйте! Слушаю вас."


def address_change_text(prefs: Dict[str, Any], user) -> str:
    """Реплика для модели посреди разговора: владелец сменил обращение в настройках."""
    p = clean_prefs(prefs)
    word = address_word(p, user)
    tone = "на «ты»" if p["formality"] == "ty" else "на «вы»"
    called = f"«{word}»" if word else "без имени"
    return (f"(Настройка от владельца, не вопрос.) С этого момента обращайся ко мне {called} и {tone}. "
            "Подтверди одной короткой фразой в новом обращении и жди вопроса.")


def owner_first_name(user) -> str:
    for attr in ("first_name", "full_name", "name"):
        val = (getattr(user, attr, None) or "").strip()
        if val:
            return val.split()[0]
    return ""


def _history_block(history: Optional[List[Dict[str, Any]]], limit: int = 8) -> str:
    rows = []
    for msg in (history or [])[-limit:]:
        role = msg.get("role")
        content = _clip(msg.get("content"), 400)
        if role in ("user", "assistant") and content:
            rows.append(f"{'Владелец' if role == 'user' else 'Агент'}: {content}")
    return ("ПОСЛЕДНИЕ СООБЩЕНИЯ В ЧАТЕ С АГЕНТОМ (контекст, если владелец продолжает тему):\n" + "\n".join(rows)) if rows else ""


MALE_VOICES = {"puck", "charon", "fenrir", "orus"}


def voice_gender_note(voice: Optional[str]) -> str:
    if not voice:
        return ""
    if voice.strip().lower() in MALE_VOICES:
        return "Твой голос мужской: о себе говори в мужском роде («я посмотрел», «я отправил»)."
    return "Твой голос женский: о себе говори в женском роде («я посмотрела», «я отправила»)."


def build_voice_prompt(user, agent, brief: str, history: Optional[List[Dict[str, Any]]] = None,
                       voice: Optional[str] = None, prefs: Optional[Dict[str, Any]] = None) -> str:
    owner = owner_first_name(user) or "владелец"
    company = (getattr(agent, "brand_company_name", None) or "").strip()
    agent_name = (getattr(agent, "name", None) or "").strip()
    if not company and agent_name and agent_name != "Мой агент":
        company = agent_name
    head = VOICE_ROLE.format(owner=owner)
    parts = [head, address_note(prefs, user)]
    gender = voice_gender_note(voice)
    if gender:
        parts.append(gender)
    if company:
        parts.append(f"Агент и компания: {company}.")
    parts.append("СВОДКА ДЕЛ:\n" + (brief or "Сводка недоступна — данные узнавай через ask_agent."))
    hist = _history_block(history)
    if hist:
        parts.append(hist)
    parts.append(GREETING_RULE)
    return "\n\n".join(parts)


# ---------------------------------------------------------------- оркестратор по запросу голоса
VOICE_TASK_NOTE_READ = """

[ГОЛОСОВОЙ РЕЖИМ — ВОПРОС]
Запрос пришёл из голосового разговора с владельцем; твой ответ перескажет голосовой помощник. У тебя только инструменты чтения — ничего не меняй и не обещай сделать. Ответь фактами: имена, время, цифры, что писал клиент, чем кончился звонок. Без markdown, таблиц и ссылок, до 1500 символов. Длинный список — главное и «ещё N». Если данных нет — так и скажи, без догадок."""

VOICE_TASK_NOTE_ACTION = """

[ГОЛОСОВОЙ РЕЖИМ — ДЕЙСТВИЕ]
Владелец голосом поручил это действие и подтвердил его словом «да». Выполни его своими инструментами и коротко ответь, что сделано: кому, через какой канал, что ушло, на какое время. Если команда неоднозначна (несколько клиентов с таким именем, неясно что писать, нет канала) — НЕ выполняй, а верни, что нужно уточнить. Удалять, склеивать карточки и массово рассылать/звонить голосом нельзя. Без markdown, до 800 символов."""


def _voice_context(pairs: Optional[List[Dict[str, str]]], limit: int = 6) -> str:
    rows = []
    for p in (pairs or [])[-limit:]:
        if p.get("user"):
            rows.append(f"Владелец: {_clip(p['user'], 300)}")
        if p.get("assistant"):
            rows.append(f"Помощник: {_clip(p['assistant'], 300)}")
    return ("\n\nКонтекст голосового разговора (последние реплики):\n" + "\n".join(rows)) if rows else ""


async def run_orchestrator_task(kind: str, request: str, agent, user, db,
                                voice_pairs: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
    """Один запрос голоса к оркестратору. kind: 'read' | 'action'. Кредиты — как у текстового чата."""
    from backend.services.agent_orchestrator import _extract_usage
    from backend.services.agent_prompts import build_orchestrator_prompt, build_time_block
    from backend.services.agent_tools import build_chat_tools, execute_tool
    from backend.services.credit_service import CreditService
    from backend.services.openrouter_client import get_openrouter_client

    request = (request or "").strip()
    if not request:
        return {"ok": False, "error": "Пустой запрос"}
    CreditService.precheck(db, user)

    note = VOICE_TASK_NOTE_READ if kind == "read" else VOICE_TASK_NOTE_ACTION
    system_prompt = build_orchestrator_prompt(agent, include_time_block=False, db=db) + note
    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for msg in (agent.chat_history or [])[-10:]:
        if msg.get("role") in ("user", "assistant") and msg.get("content"):
            messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": request + _voice_context(voice_pairs)
                     + build_time_block(round_to_minutes=0)})

    tools = tools_for(kind, await build_chat_tools(agent, db))
    allowed = {_tool_name(t) for t in tools}
    context = {"agent_config_id": str(agent.id), "user_id": str(user.id), "user": user,
               "agent_config": agent, "surface": "owner_chat"}

    client = get_openrouter_client()
    model = agent.orchestrator_model
    if kind == "read" and READ_MODEL:
        try:
            from backend.services.agent_models import is_valid_model
            if is_valid_model(READ_MODEL):
                model = READ_MODEL
        except Exception:
            pass
    total_prompt = total_completion = 0
    used: List[str] = []
    final_text = ""
    iteration = 0
    try:
        while iteration < MAX_ITERATIONS:
            iteration += 1
            response = await client.chat_completion(model=model, messages=messages,
                                                    tools=tools or None, temperature=0.4)
            p_tok, c_tok = _extract_usage(response)
            total_prompt += p_tok
            total_completion += c_tok
            msg = response["choices"][0]["message"]
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                final_text = msg.get("content") or ""
                break
            messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": tool_calls})
            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                if not isinstance(args, dict):
                    args = {}
                if name not in allowed:
                    # модель не видела этот инструмент в наборе — отказ, а не выполнение (страховка)
                    verdict = classify_tool(name)
                    result_str = json.dumps({"ok": False, "error": (
                        "Голосом это нельзя — только текстом в чате агента." if verdict == "forbidden" else
                        "Это действие, а запрос — вопрос: ничего не меняй, только ответь.")}, ensure_ascii=False)
                else:
                    used.append(name)
                    logger.info(f"[OWNER-VOICE] {kind}: tool {name}")
                    try:
                        result_str = await execute_tool(name, args, context, db)
                    except Exception as exc:
                        result_str = json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
                messages.append({"role": "tool", "tool_call_id": tc.get("id"), "content": result_str})
        if not final_text:
            final_text = "Не успел собрать ответ — попробуйте спросить точнее." if kind == "read" else "Готово."
    finally:
        if total_prompt or total_completion:
            try:
                CreditService.charge(db=db, user_id=user.id, model_slug=model,
                                     prompt_tokens=total_prompt, completion_tokens=total_completion,
                                     ref_type="voice_chat", ref_id=agent.id,
                                     notes=f"voice {kind}: iterations {iteration}")
            except Exception as exc:
                logger.error(f"[OWNER-VOICE] charge failed: {exc}", exc_info=True)
    return {"ok": True, "answer": _clip_answer(final_text), "tools": used}


def _clip_answer(text: str) -> str:
    text = re.sub(r"[*_`#>|]+", "", text or "").strip()
    return text if len(text) <= ANSWER_MAX_CHARS else text[: ANSWER_MAX_CHARS - 1].rstrip() + "…"


async def send_owner_telegram(text: str, agent, user, db) -> Dict[str, Any]:
    """Отчёт самому владельцу — тем же инструментом, что у оркестратора (бот уведомлений агента)."""
    from backend.services.agent_tools import execute_tool

    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "Пустой текст"}
    context = {"agent_config_id": str(agent.id), "user_id": str(user.id), "user": user,
               "agent_config": agent, "surface": "owner_chat"}
    raw = await execute_tool("send_telegram_notification", {"message": text[:3800]}, context, db)
    try:
        result = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        result = {"ok": True, "result": raw}
    if isinstance(result, dict) and result.get("ok") is False:
        return {"ok": False, "error": result.get("error") or "не отправлено",
                "hint": "Telegram-бот агента не подключён или выключен — его подключают в окне «Telegram-бот»."}
    return {"ok": True, "message": "Отправлено в Telegram"}


# ---------------------------------------------------------------- деньги и история
def voice_credits(seconds: float) -> int:
    """Кредиты за разговор: себестоимость минуты × курс кредитов (как у прочих расходов агента)."""
    from backend.services.agent_models import _CREDITS_PER_USD

    if not seconds or seconds <= 0:
        return 0
    return max(1, math.ceil(seconds / 60.0 * VOICE_COST_USD_PER_MIN * _CREDITS_PER_USD))


def charge_voice_minutes(db, user, agent, seconds: float) -> int:
    from backend.services.credit_service import CreditService

    amount = voice_credits(seconds)
    if amount <= 0:
        return 0
    try:
        CreditService.charge_amount(db=db, user_id=user.id, amount=amount, ref_type="voice_live",
                                    ref_id=agent.id, model_slug="gemini-live",
                                    notes=f"голосовой режим: {int(seconds)} с")
    except Exception as exc:
        logger.error(f"[OWNER-VOICE] voice charge failed: {exc}", exc_info=True)
        return 0
    return amount


def append_voice_history(db, agent, pairs: List[Dict[str, str]]) -> int:
    """Разговор голосом — в историю текстового чата (одной парой), чтобы чат продолжал тему."""
    lines_user, lines_bot = [], []
    for p in pairs or []:
        if (p.get("user") or "").strip():
            lines_user.append(p["user"].strip())
        if (p.get("assistant") or "").strip():
            lines_bot.append(p["assistant"].strip())
    if not (lines_user or lines_bot):
        return 0
    now = datetime.utcnow().isoformat()
    history = list(agent.chat_history or [])
    history.append({"role": "user", "content": "🎙 (голосом) " + _clip(" / ".join(lines_user), 1500), "ts": now,
                    "voice": True})
    history.append({"role": "assistant", "content": "🎙 (голосом) " + _clip(" / ".join(lines_bot), 2500), "ts": now,
                    "voice": True})
    agent.chat_history = history[-20:]
    db.commit()
    return len(pairs)


# ---------------------------------------------------------------- Telegram Mini App: вход без пароля
# Mini App открывается из бота агента. Telegram подписывает данные пользователя ключом бота (initData),
# мы проверяем подпись и выдаём короткий токен ТОЛЬКО для голосового сокета этого агента — не токен
# кабинета: даже утёкший, он не открывает ни API, ни CRM.
VOICE_TOKEN_TTL = 30 * 60
WEBAPP_MAX_AGE = 24 * 3600


def _b64u(data: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64u_dec(text: str) -> bytes:
    import base64
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _token_key() -> bytes:
    from backend.core.config import settings
    return ("owner-voice:" + str(settings.JWT_SECRET_KEY or "")).encode()


def make_voice_token(user_id, agent_id, ttl: int = VOICE_TOKEN_TTL, now: Optional[float] = None) -> str:
    import hashlib
    import hmac
    import time as _time
    body = _b64u(json.dumps({"u": str(user_id), "a": str(agent_id),
                             "e": int((now or _time.time()) + ttl)}, separators=(",", ":")).encode())
    sig = _b64u(hmac.new(_token_key(), ("ov1." + body).encode(), hashlib.sha256).digest())
    return f"ov1.{body}.{sig}"


def read_voice_token(token: str, now: Optional[float] = None) -> Optional[Dict[str, str]]:
    """{'user_id', 'agent_id'} или None (подделка, другой формат, истёк)."""
    import hashlib
    import hmac
    import time as _time
    try:
        prefix, body, sig = (token or "").split(".")
        if prefix != "ov1":
            return None
        good = _b64u(hmac.new(_token_key(), ("ov1." + body).encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(good, sig):
            return None
        data = json.loads(_b64u_dec(body))
        if int(data.get("e") or 0) < (now or _time.time()):
            return None
        return {"user_id": str(data["u"]), "agent_id": str(data["a"])}
    except Exception:
        return None


def verify_webapp_init_data(init_data: str, bot_token: str, max_age: int = WEBAPP_MAX_AGE,
                            now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Проверка initData Telegram Mini App (core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app).
    Возвращает разобранные поля (user — словарь) или None."""
    import hashlib
    import hmac
    import time as _time
    from urllib.parse import parse_qsl

    if not init_data or not bot_token:
        return None
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return None
    fields = dict(pairs)
    received = fields.pop("hash", "")
    if not received:
        return None
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received):
        return None
    try:
        auth_date = int(fields.get("auth_date") or 0)
    except ValueError:
        return None
    if not auth_date or (now or _time.time()) - auth_date > max_age:
        return None
    try:
        fields["user"] = json.loads(fields.get("user") or "{}")
    except ValueError:
        return None
    if not isinstance(fields["user"], dict) or not fields["user"].get("id"):
        return None
    return fields


def webapp_url(agent_id) -> str:
    from backend.core.config import settings
    base = (getattr(settings, "HOST_URL", "") or "").rstrip("/")
    return f"{base}/static/voice-live.html?agent={agent_id}"
