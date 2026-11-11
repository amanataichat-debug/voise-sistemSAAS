"""
«Сознание» агента в переписке (ветка 1111-chat-live, 11.11.2026).

Владелец о живых чатах WhatsApp: агент звучит как бот. Клиент сказал «после 18:00
позвоните» — агент переспрашивает «будет удобно в 18:00 (Бишкек)?» и ставит звонок
на завтра; клиент «я на работе / поговорю с менеджером» — агент в каждом сообщении
снова «сегодня после работы или завтра?»; согласие на звонок — «точно сейчас?».

Три части, общие для всех агентов платформы (не шаблон одного владельца):

1. Принципы переписки — в системном промпте (agent_prompts.CLIENT_BLOCK), не здесь.
2. situation() — разбор переписки КОДОМ перед ответом: последнее сообщение клиента,
   согласие на звонок, названное время, отказ «не сейчас», сколько раз агент уже
   предлагал созвон, какие вопросы уже задавал. Модель получает это готовым
   выводом («клиент согласился, время 18:00 — сделай, не переспрашивай»), а не
   догадывается сама по длинной хронологии.
3. send_refusal() — проверка в коде перед отправкой: тот же вопрос второй раз,
   «вам удобно?» после согласия, повторное предложение созвона клиенту, который
   ушёл от ответа. Отказ объясняет модели, что сделать вместо этого.

Без БД и сети: на вход — строки треда (direction, body, sent_by, sent_at/created_at).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import List, Optional

# Принципы переписки (как думать перед ответом) живут в системном промпте
# (agent_prompts.CLIENT_BLOCK) — здесь только разбор переписки кодом и проверки.

# ── распознавание ────────────────────────────────────────────────────────────

_WORD_RE = re.compile(r"[a-zа-яёңөү0-9]+", re.IGNORECASE)

_CALL_OFFER_RE = re.compile(
    r"(созвон|позвон|звонок|звякн|набер|наберу|демо|\b2\s*минут|две\s+минут|поговорить|поговорим|"
    r"послушает|услышите|чалып|чалайын)",
    re.IGNORECASE,
)
_CONSENT_RE = re.compile(
    r"^\s*(да|ага|ок|окей|ok|okay|хорошо|давайте|давай|можно|конечно|удобно|норм|согласен|"
    r"согласна|звоните|позвоните|набирайте|ооба|макул|мейли|болот|жарайт)\b"
    r"|\b(звоните|позвоните|перезвоните|набирайте|наберите|чалыңыз|чалсаңыз)\b"
    r"|\bможно\s+(сейчас|позвонить)\b|\bсейчас\s+удобно\b"
    r"|^\s*(супер|отлично|класс|го|окей|ладно)\b"
    r"|\b(давайте|давай)\s+(созвон\w*|позвон\w*|поговорим|звоните|наберите)",
    re.IGNORECASE,
)
_NEGATIVE_START_RE = re.compile(r"^\s*(нет|не\s+надо|не\s+нужно|не\s+звоните|жок|керек\s+эмес)\b",
                                re.IGNORECASE)
_DEFLECT_RE = re.compile(
    r"(на\s+работе|занят|занята|некогда|не\s+могу\s+(сейчас|говорить)|не\s+сейчас|подумаю|"
    r"подумать|посоветуюсь|поговорю\s+с|обсужу|спрошу\s+у|потом|позже\s+(напишу|свяжусь)|"
    r"сама?\s+напишу|сам\s+свяжусь|сама\s+свяжусь|ойлонуп|кийин|азыр\s+бош\s+эмес)",
    re.IGNORECASE,
)
# Клиент сам просит человека: только тогда созвон уходит менеджеру, а не агенту.
_HUMAN_RE = re.compile(
    r"(менеджер|живо\w*\s+человек|с\s+человеком|оператор|сотрудник|специалист|руководител|"
    r"директор|админ\w*|владел\w*|адам\s+менен|кызматкер)",
    re.IGNORECASE,
)
_THANKS_ONLY_RE = re.compile(r"^\s*(спасибо|благодарю|рахмат|понятно|ясно|хорошо,?\s*понятно)[\s.!]*$",
                             re.IGNORECASE)
_TIME_RE = re.compile(
    r"(\b\d{1,2}[:.]\d{2}\b|\b(после|до|в|к|около|с)\s+\d{1,2}\b|\bчерез\s+(\d+|час|полчаса|пару)|"
    r"\b(утром|вечером|днём|днем|после\s+обеда|в\s+обед|завтра|послезавтра|сегодня|"
    r"в\s+понедельник|во\s+вторник|в\s+среду|в\s+четверг|в\s+пятницу|в\s+субботу)\b)",
    re.IGNORECASE,
)
_NOW_RE = re.compile(r"\b(сейчас|прямо\s+сейчас|азыр)\b", re.IGNORECASE)
_RECONFIRM_RE = re.compile(
    r"(удобно\s*(ли|будет|сейчас|в|\?)|вам\s+удобно|будет\s+удобно|точно\s+сейчас|"
    r"подтвердите|подходит\s+ли|устроит\s+ли|вам\s+подходит|сейчас\s+можно\s*\?)",
    re.IGNORECASE,
)
_QUESTION_WORD_RE = re.compile(r"\b(как|сколько|какая|какой|какие|цена|стоимость|где|когда|что|почему|"
                               r"можно\s+ли|есть\s+ли|канча|кандай|кайда)\b", re.IGNORECASE)


def _words(text: str) -> List[str]:
    return _WORD_RE.findall((text or "").lower())


def _ts(m):
    return getattr(m, "sent_at", None) or getattr(m, "created_at", None)


def _dir(m) -> str:
    return (getattr(m, "direction", None) or "inbound").lower()


def _is_agent(m) -> bool:
    return _dir(m) == "outbound" and getattr(m, "sent_by", None) != "owner"


def _body(m) -> str:
    return (getattr(m, "body", None) or "").strip()


def _questions(text: str) -> List[str]:
    """Вопросительные предложения текста."""
    out = []
    for part in re.split(r"(?<=[?])|\n|(?<=[.!])\s", text or ""):
        part = part.strip()
        if part.endswith("?") and len(_words(part)) >= 2:
            out.append(part)
    return out


def _similar(a: str, b: str) -> bool:
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return False
    ratio = SequenceMatcher(None, " ".join(wa), " ".join(wb)).ratio()
    sa, sb = set(wa), set(wb)
    overlap = len(sa & sb) / max(1, min(len(sa), len(sb)))
    return ratio >= 0.72 or (overlap >= 0.8 and min(len(sa), len(sb)) >= 3)


def is_call_offer(text: str) -> bool:
    return bool(_CALL_OFFER_RE.search(text or "")) and "?" in (text or "")


def _clip(text: str, n: int = 160) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


# ── разбор переписки ─────────────────────────────────────────────────────────

class Situation:
    def __init__(self):
        self.client_last: str = ""          # сообщения клиента после последнего ответа агента
        self.agent_last: str = ""           # последнее сообщение агента до них
        self.consent: bool = False          # согласился на звонок / предложение
        self.time_named: Optional[str] = None
        self.wants_now: bool = False
        self.deflected: bool = False        # занят / подумаю / поговорю с …
        self.asked_question: bool = False
        self.call_offers: List[str] = []    # предложения созвона агента без согласия
        self.agent_questions: List[str] = []
        self.agent_offered_call_recently: bool = False
        self.client_mentions_call: bool = False

    @property
    def call_settled(self) -> bool:
        """Клиент согласился именно на звонок (на наше предложение или сам попросил)."""
        return self.consent and (self.agent_offered_call_recently or self.client_mentions_call)


def situation(rows, now: Optional[datetime] = None) -> Situation:
    """Разбор треда (старые → новые)."""
    s = Situation()
    rows = [m for m in (rows or []) if _body(m)]
    if not rows:
        return s
    now = now or datetime.utcnow()
    # Хвост клиента: его сообщения после последнего сообщения с нашей стороны.
    tail = []
    i = len(rows) - 1
    while i >= 0 and _dir(rows[i]) == "inbound":
        tail.append(_body(rows[i]))
        i -= 1
    tail.reverse()
    s.client_last = "\n".join(tail)
    prev_agent = [m for m in rows[: i + 1] if _is_agent(m)]
    if prev_agent:
        s.agent_last = _body(prev_agent[-1])
        s.agent_offered_call_recently = bool(_CALL_OFFER_RE.search(s.agent_last))

    client = s.client_last
    if client:
        s.client_mentions_call = bool(_CALL_OFFER_RE.search(client)) or bool(
            re.search(r"\b(звоните|позвоните|перезвоните|наберите|набирайте)\b", client, re.IGNORECASE))
        negative = bool(_NEGATIVE_START_RE.search(client))
        s.consent = bool(_CONSENT_RE.search(client)) and not negative
        m = _TIME_RE.search(client)
        if m and (s.agent_offered_call_recently or _CALL_OFFER_RE.search(client)):
            s.time_named = m.group(0).strip()
            s.consent = s.consent or not negative
        s.wants_now = bool(_NOW_RE.search(client)) and s.consent
        s.deflected = (bool(_DEFLECT_RE.search(client)) or bool(_THANKS_ONLY_RE.search(client))) \
            and not s.time_named and not (s.consent and s.agent_offered_call_recently
                                          and not _DEFLECT_RE.search(client))
        if s.deflected:
            s.consent = False
        s.asked_question = "?" in client or bool(_QUESTION_WORD_RE.search(client.split("\n")[-1]))

    # Предложения созвона и вопросы агента за последние сутки, после последнего
    # согласия клиента (согласие «обнуляет» счёт — дальше уже другой разговор).
    since = now - timedelta(hours=24)
    for m in rows:
        ts = _ts(m)
        if ts is not None and getattr(ts, "tzinfo", None) is not None:
            ts = ts.replace(tzinfo=None)
        if ts is not None and ts < since:
            continue
        body = _body(m)
        if _dir(m) == "inbound":
            if _CONSENT_RE.search(body) and not _NEGATIVE_START_RE.search(body):
                s.call_offers = []
            continue
        if not _is_agent(m):
            continue
        if is_call_offer(body):
            s.call_offers.append(body)
        s.agent_questions.extend(_questions(body))
    s.agent_questions = s.agent_questions[-6:]
    return s


def client_asked_human(rows, last_n: int = 6) -> bool:
    """В последних сообщениях клиента есть просьба о человеке / менеджере."""
    inbound = [_body(m) for m in (rows or []) if _dir(m) == "inbound" and _body(m)]
    return any(_HUMAN_RE.search(t) for t in inbound[-last_n:])


def call_ability_line(can_call: Optional[bool], reason: str = "") -> str:
    """
    Строка разбора: может ли агент сам позвонить клиенту. Один ответ на «позвоните» для всех
    тарифов: можешь — звонишь сам; не можешь — номер для звонка из базы знаний + передача владельцу.
    """
    if can_call is True:
        return (
            "- Позвонить этому клиенту ты можешь сам (номер O!, тариф с звонками). Хочет созвон или демо —"
            " звони: сейчас — trigger_immediate_call, на его время — create_agent_task. Менеджеру"
            " (notify_owner_handoff) передавай, только если клиент прямо просит человека или готов оплачивать."
        )
    if can_call is False:
        return (
            f"- Сам позвонить этому клиенту ты не можешь ({reason or 'звонок недоступен'}). Хочет созвон —"
            " дай номер для звонка из базы знаний (если он там есть) и передай владельцу notify_owner_handoff"
            " с удобным клиенту временем."
        )
    return ""


def situation_block(rows, now: Optional[datetime] = None, can_call: Optional[bool] = None,
                    call_reason: str = "") -> str:
    """Готовый вывод для события переписки (пусто — нечего подсказать)."""
    try:
        s = situation(rows, now)
    except Exception:
        return ""
    lines = []
    ability = call_ability_line(can_call, call_reason)
    if ability:
        lines.append(ability)
    if s.client_last:
        lines.append(f"- Клиент сейчас написал: «{_clip(s.client_last, 240)}».")
    if s.asked_question and not s.call_settled:
        lines.append("- В его сообщении вопрос: сначала ответь на него по сути.")
    if s.call_settled:
        when = f" Время: «{s.time_named}»." if s.time_named else ""
        now_note = " Просит сейчас — звони сейчас (trigger_immediate_call), если система разрешит." \
            if s.wants_now else ""
        lines.append(
            "- КЛИЕНТ СОГЛАСИЛСЯ на созвон." + when + now_note + " Вопрос решён: поставь звонок ровно"
            " на это время (сегодня, если оно сегодня ещё не наступило) и коротко подтверди одной фразой."
            " Не переспрашивай «удобно ли», «точно ли» и не повторяй ему его время с уточнениями."
        )
    elif s.deflected:
        if s.asked_question:
            lines.append(
                "- Клиент сейчас занят или не готов к созвону, но задал вопрос. Ответь на вопрос коротко и"
                " понятно, созвон в этом сообщении не предлагай."
            )
        else:
            lines.append(
                "- Клиент сейчас не готов (занят, подумает, посоветуется, поблагодарил). Ответь по-человечески"
                " одной фразой, без нового предложения созвона и без вопроса."
            )
    if s.call_offers and not s.call_settled:
        lines.append(
            f"- Созвон ты уже предлагал ({len(s.call_offers)} раз за сутки), последний раз:"
            f" «{_clip(s.call_offers[-1])}». Больше не предлагай, пока клиент сам не вернётся к этому."
        )
    if s.agent_questions:
        asked = "; ".join(f"«{_clip(q, 90)}»" for q in s.agent_questions[-4:])
        lines.append(f"- Ты уже спрашивал: {asked}. Эти вопросы не повторяй, даже другими словами.")
    if not lines:
        return ""
    return "\n\nРАЗБОР ПЕРЕПИСКИ (сделан системой по последним сообщениям, опирайся на него):\n" + "\n".join(lines)


# ── проверка перед отправкой ─────────────────────────────────────────────────

def send_refusal(rows, text: str, now: Optional[datetime] = None) -> Optional[str]:
    """
    Текст отказа модели или None (можно отправлять). Проверяет только то, что
    надёжно видно кодом: повтор вопроса, переспрос после согласия, повторное
    предложение созвона тому, кто ушёл от ответа или уже получил его.
    """
    text = (text or "").strip()
    if not text or not rows:
        return None
    s = situation(rows, now)
    new_questions = _questions(text)
    if not new_questions:
        return None
    for q in new_questions:
        for old in s.agent_questions:
            if _similar(q, old):
                return (
                    f"Этот вопрос ты уже задавал клиенту: «{_clip(old, 120)}». Не повторяй его, даже другими"
                    " словами. Ответь на то, что клиент написал последним, и отправь сообщение без этого"
                    " вопроса (вопрос не обязателен)."
                )
    if s.call_settled and any(_RECONFIRM_RE.search(q) for q in new_questions):
        return (
            "Клиент уже согласился на созвон" + (f" и назвал время «{s.time_named}»" if s.time_named else "")
            + ". Не переспрашивай «удобно ли» — поставь звонок (create_agent_task / trigger_immediate_call)"
            " и подтверди одной фразой без вопроса."
        )
    offer_q = [q for q in new_questions if _CALL_OFFER_RE.search(q)]
    if offer_q and not s.call_settled and (s.deflected or s.call_offers):
        why = ("клиент сейчас не готов" if s.deflected
               else f"ты уже предлагал созвон: «{_clip(s.call_offers[-1], 100)}»")
        return (
            f"Не предлагай созвон снова: {why}. Ответь на его сообщение по-человечески, без вопроса про"
            " звонок. Предложишь снова, только если клиент сам вернётся к этому."
        )
    return None


def handoff_refusal(rows, reason: str, can_call: bool) -> Optional[str]:
    """
    Передача владельцу вместо своего звонка (11.11.2026, +996 507…: клиент «давайте созвонимся»,
    агент ответил «передал живому менеджеру», хотя мог позвонить сам). Отказ, если агент может
    позвонить, повод — демо / «человек», а клиент человека не просил.
    """
    if not can_call or reason not in ("demo", "requested_human"):
        return None
    if client_asked_human(rows):
        return None
    return (
        "Клиент не просил менеджера — он хочет созвон, а позвонить ты можешь сам (номер O!). Позвони сам:"
        " сейчас — trigger_immediate_call, на названное время — create_agent_task, и коротко напиши"
        " клиенту, что звонишь. Твой звонок и есть демонстрация. Владельцу передавай, только когда"
        " клиент прямо просит человека или готов подключаться (ready_to_connect)."
    )
