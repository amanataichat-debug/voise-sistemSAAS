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

# Себестоимость минуты Gemini Live (та же оценка, что у кошелька, см. CLAUDE.md) → кредиты агента
VOICE_COST_USD_PER_MIN = float(os.getenv("VOICE_COST_USD_PER_MIN_GEMINI_LIVE", "0.03") or 0.03)
# Предел длины разговора: ручка возобновления у Gemini есть, но разговор владельца — не звонок на час
MAX_SESSION_SECONDS = int(os.getenv("OWNER_VOICE_MAX_SECONDS", "1200") or 1200)
# NON_BLOCKING (по умолчанию): пока оркестратор работает, Gemini говорит «секунду, смотрю», а результат
# озвучивает, когда придёт. BLOCKING — молчит до результата (OWNER_VOICE_BLOCKING_TOOLS=1).
BLOCKING_TOOLS = os.getenv("OWNER_VOICE_BLOCKING_TOOLS", "0").strip().lower() in ("1", "true", "yes", "on")
DEFAULT_VOICE = (os.getenv("OWNER_VOICE_GEMINI_VOICE", "") or "Kore").strip()

BRIEF_MAX_CHARS = 6000
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
GEMINI_FUNCTIONS: List[Dict[str, Any]] = [
    {
        "name": "ask_agent",
        "description": (
            "Узнать в системе то, чего нет в сводке: клиент и его карточка, переписка в WhatsApp/Telegram/"
            "Instagram, звонки и их итоги, задачи и расписание, отчёт за другой период, воронка, база знаний, "
            "файлы, Gmail и Календарь. Только чтение — ничего не меняет. Пиши запрос полностью и с именами "
            "(«что писал Нурлан Асанов в WhatsApp сегодня»), а не «он»."
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

    safe("today", lambda: _brief_today(db, agent, day_start))
    safe("dialogs", lambda: _brief_dialogs(db, user, agent, today_bk))
    safe("calls", lambda: _brief_calls(db, agent, day_start, today_bk))
    safe("tasks", lambda: _brief_tasks(db, agent, now, today_bk))
    safe("funnel", lambda: _brief_funnel(db, agent))

    text = "\n\n".join(parts)
    if len(text) > BRIEF_MAX_CHARS:
        text = text[: BRIEF_MAX_CHARS - 40].rstrip() + "\n…(сводка обрезана — подробности через ask_agent)"
    return text


def _brief_today(db, agent, day_start: datetime) -> str:
    from sqlalchemy import func
    from backend.models.agent_call import AgentCall
    from backend.models.agent_contact import AgentContact
    from backend.services.agent_orchestrator import MESSAGE_DIRECTIONS

    rows = (
        db.query(AgentCall.direction, AgentCall.status, func.count(AgentCall.id))
        .filter(AgentCall.agent_config_id == agent.id, AgentCall.created_at >= day_start)
        .group_by(AgentCall.direction, AgentCall.status)
        .all()
    )
    calls_in = calls_out = answered = 0
    messages: Dict[str, int] = {}
    for direction, status, n in rows:
        d = (direction or "outbound").lower()
        if d in MESSAGE_DIRECTIONS:
            if d.endswith("_inbound"):
                ch = d.split("_", 1)[0]
                messages[ch] = messages.get(ch, 0) + int(n)
            continue
        if d == "inbound":
            calls_in += int(n)
        else:
            calls_out += int(n)
        if status == "answered":
            answered += int(n)
    new_clients = (
        db.query(func.count(AgentContact.id))
        .filter(AgentContact.agent_config_id == agent.id, AgentContact.created_at >= day_start)
        .scalar() or 0
    )
    total_msgs = sum(messages.values())
    line = (f"Сегодня: звонков {calls_in + calls_out} (входящих {calls_in}, исходящих {calls_out}, "
            f"с разговором {answered}); новых клиентов в CRM {new_clients}; обращений в мессенджерах "
            f"(разобрано агентом) {total_msgs}")
    if messages:
        line += " — " + ", ".join(f"{CHANNEL_NAMES.get(k, k)} {v}" for k, v in sorted(messages.items()))
    return line + "."


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

ЧТО ТЫ ЗНАЕШЬ
- Ниже — СВОДКА ДЕЛ на начало разговора. На «дай отчёт», «что сегодня», «кто писал», «кто звонил», «что по задачам» отвечай из неё сразу, без функций.
- Всё, чего в сводке нет (конкретный клиент целиком, вся переписка, прошлые недели, база знаний, документы, почта, календарь), — функция ask_agent. Запрос пиши полностью, с именами и периодом.
- Пока функция работает, скажи одну короткую фразу — «Секунду, смотрю» — и жди. До результата — никаких цифр, имён и выводов. Ничего не выдумывай: нет в сводке и в ответе функции — значит не знаешь, так и скажи.
- Пришёл ответ — перескажи главное своими словами, коротко.

ДЕЙСТВИЯ (написать или отправить клиенту, позвонить, поставить или перенести задачу, сменить этап, заметка, пауза бота, событие в календаре)
- Сначала одной фразой повтори, что сделаешь: кому, через какой канал, что именно, когда. Например: «Пишу Нурлану в WhatsApp, что вы перезвоните в три. Отправляю?»
- Только после явного «да» владельца вызови do_action с confirmed=true и той же командой полностью. Без «да» — не вызывай. Сказал «нет» или поправил — уточни и повтори заново.
- После выполнения коротко скажи, что сделано. Если оркестратор вернул вопрос или ошибку — передай владельцу своими словами.
- Удалять клиентов и задачи, массово рассылать и массово обзванивать голосом нельзя — скажи, что это делается текстом в чате агента.
- Отчёт или сводку самому владельцу в Telegram — send_to_telegram. Подтверждение не нужно, если он сам попросил или согласился на твоё предложение.

Ты не продавец и не консультант клиента: владельцу ничего не продаёшь и не задаёшь лишних вопросов. Не говори о себе как о модели и не называй технологии и провайдеров."""

GREETING_RULE = "Начало разговора: поздоровайся по имени одной короткой фразой и скажи, что слушаешь. Отчёт сам не начинай."


def owner_first_name(user) -> str:
    for attr in ("first_name", "full_name", "name"):
        val = (getattr(user, attr, None) or "").strip()
        if val:
            return val.split()[0]
    return ""


def greeting_text(user) -> str:
    name = owner_first_name(user)
    return f"Здравствуйте{', ' + name if name else ''}! Слушаю вас." if name else "Здравствуйте! Слушаю вас."


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
                       voice: Optional[str] = None) -> str:
    owner = owner_first_name(user) or "владелец"
    company = (getattr(agent, "brand_company_name", None) or "").strip()
    agent_name = (getattr(agent, "name", None) or "").strip()
    if not company and agent_name and agent_name != "Мой агент":
        company = agent_name
    head = VOICE_ROLE.format(owner=owner)
    parts = [head]
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
    total_prompt = total_completion = 0
    used: List[str] = []
    final_text = ""
    iteration = 0
    try:
        while iteration < MAX_ITERATIONS:
            iteration += 1
            response = await client.chat_completion(model=agent.orchestrator_model, messages=messages,
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
                CreditService.charge(db=db, user_id=user.id, model_slug=agent.orchestrator_model,
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
