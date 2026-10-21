"""
Узнавание клиента агента по имени, компании, нише и номеру (ветка 2110-client-memory).

Зачем. Клиент вчера писал в WhatsApp, сегодня звонит с другого номера: «я Айбек,
у меня стоматология». По номеру карточку не найти, а прежний поиск
(search_contact_by_phone) смотрел в старую CRM и не видел ни контактов агента, ни
переписок. Здесь один поиск на все каналы: голосовая функция find_client
(backend/functions/find_client.py) и одноимённый инструмент оркестратора.

Как ищем — без LLM, только Postgres и Python, чтобы голос не ждал:
  * номер — основной или второй номер карточки (agent_contact_phones);
  * имя — имя в карточке и имена в мессенджерах (WhatsApp push_name, Telegram,
    Instagram); сравнение по «скелету» имени, так что «Айбек», «Aibek» и
    «Aybek» совпадают (распознавание речи пишет кириллицей, мессенджер — как
    клиент подписан);
  * компания и ниша — ILIKE по основе слова («стоматолог» найдёт и
    «стоматология», и «стоматологию») в карточке (компания, заметки, память) и в
    свежих сообщениях клиента и звонках.

Уверенность ответа решает, как агент себя ведёт: high — это он, обращайся по
имени; medium/low — уточни одним вопросом, не угадывай. Ложное «узнавание»
хуже, чем вежливый вопрос.
"""

import re
from datetime import datetime, timedelta
from typing import Any, Dict, List

from sqlalchemy import Text, cast, func
from sqlalchemy.orm import Session

from backend.core.logging import get_logger
from backend.services.agent_contact_phones import (
    contact_phone_digits, find_contact_by_phone, phone_tail,
)

logger = get_logger(__name__)

# Сообщения и звонки старше этого не ищем по нише/компании: клиент «с другого
# номера» почти всегда продолжает свежий разговор, а старые тексты дают ложные
# совпадения.
TEXT_SEARCH_DAYS = 30

SCORE_PHONE = 100
SCORE_NAME = 45
SCORE_NAME_PARTIAL = 30
SCORE_COMPANY = 35
SCORE_TEXT = 25        # ниша/компания в карточке (заметки, память)
SCORE_MESSAGES = 20    # ниша/компания в сообщениях клиента и звонках
SCORE_RECENT = 10      # карточка обновлялась за 3 дня
SCORE_FRESH = 5        # за 14 дней

_CYR_TO_LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "j",
    "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "ң": "n",
    "о": "o", "ө": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ү": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "", "ы": "i",
    "ь": "", "э": "e", "ю": "iu", "я": "ia",
}
_LAT_FOLD = (("kh", "h"), ("zh", "j"), ("dj", "j"), ("ng", "n"), ("w", "v"), ("y", "i"),
             ("q", "k"), ("x", "ks"), ("c", "k"))

_STOP_WORDS = {
    "у", "меня", "мы", "наша", "наш", "компания", "фирма", "бизнес", "это", "я", "мой",
    "моя", "по", "в", "на", "и", "с", "от", "для", "занимаюсь", "занимаемся", "сфера",
    "ниша", "клиент", "клиника", "ооо", "ип", "осоо", "компаниясы", "менин", "биз",
}


def name_skeleton(word: str) -> str:
    """Скелет имени: латиница без двойных букв и с общими заменами (Айбек → aibek)."""
    w = "".join(_CYR_TO_LAT.get(ch, ch) for ch in (word or "").lower())
    for src, dst in _LAT_FOLD:
        w = w.replace(src, dst)
    w = re.sub(r"[^a-z]", "", w)
    return re.sub(r"(.)\1+", r"\1", w)


def _words(text: str, min_len: int = 3) -> List[str]:
    return [w for w in re.findall(r"[^\W\d_]+", (text or "").lower()) if len(w) >= min_len]


def _name_match(query_name: str, candidate: str) -> int:
    """0 — нет, 2 — имя совпало, 1 — совпало началом (Айбек ↔ Айбекжан)."""
    q = [name_skeleton(w) for w in _words(query_name) if w not in _STOP_WORDS]
    c = [name_skeleton(w) for w in _words(candidate, 2)]
    q = [w for w in q if len(w) >= 3]
    c = [w for w in c if len(w) >= 2]
    if not q or not c:
        return 0
    best = 0
    for qw in q:
        for cw in c:
            if qw == cw:
                return 2
            short, long_ = sorted((qw, cw), key=len)
            if len(short) >= 4 and long_.startswith(short) and len(long_) - len(short) <= 4:
                best = 1
    return best


STEM_MAX = 8  # «стоматологическая» и «стоматологию» → «стоматол»


def _stems(text: str) -> List[str]:
    """Основы значимых слов: «стоматологическая» → «стоматол» (ищем подстрокой)."""
    out = []
    for w in _words(text, 4):
        if w in _STOP_WORDS:
            continue
        stem = w[: min(STEM_MAX, len(w) - 2)] if len(w) > 5 else w
        if stem not in out:
            out.append(stem)
    return out[:4]


def _text_hits(db: Session, agent_id, stems: List[str]) -> Dict[str, set]:
    """
    id карточек, где нашлась хотя бы половина основ фразы (клиент говорит
    своими словами: «стоматологическая клиника» ↔ «у меня стоматология»):
    в поле компании / в карточке целиком / в свежих сообщениях и звонках.
    """
    need = max(1, (len(stems) + 1) // 2)
    per_stem = [_stem_hits(db, agent_id, stem) for stem in stems]
    hits = {}
    for key in ("company", "card", "messages"):
        counts: Dict[Any, int] = {}
        for one in per_stem:
            for cid in one[key]:
                counts[cid] = counts.get(cid, 0) + 1
        hits[key] = {cid for cid, n in counts.items() if n >= need}
    return hits


def _stem_hits(db: Session, agent_id, stem: str) -> Dict[str, set]:
    """id карточек, где встречается основа: поле компании / карточка / переписка и звонки."""
    from backend.models.agent_contact import AgentContact
    from backend.models.agent_call import AgentCall
    from backend.models.agent_whatsapp import AgentWhatsAppMessage
    from backend.models.agent_telegram_account import AgentTelegramMessage
    from backend.models.agent_instagram import AgentInstagramMessage

    hits = {"company": set(), "card": set(), "messages": set()}
    like = f"%{stem}%"
    rows = db.query(AgentContact.id).filter(
        AgentContact.agent_config_id == agent_id, AgentContact.company.ilike(like),
    ).limit(200).all()
    hits["company"] = {r[0] for r in rows}
    card_text = (
        func.coalesce(AgentContact.company, "") + " " + func.coalesce(AgentContact.notes, "")
        + " " + cast(AgentContact.memory, Text)
    )
    rows = db.query(AgentContact.id).filter(
        AgentContact.agent_config_id == agent_id, card_text.ilike(like),
    ).limit(200).all()
    hits["card"] = {r[0] for r in rows}

    since = datetime.utcnow() - timedelta(days=TEXT_SEARCH_DAYS)
    queries = [
        db.query(AgentWhatsAppMessage.agent_contact_id).filter(
            AgentWhatsAppMessage.agent_config_id == agent_id,
            AgentWhatsAppMessage.direction == "inbound",
            AgentWhatsAppMessage.created_at >= since,
            AgentWhatsAppMessage.body.ilike(like)),
        db.query(AgentInstagramMessage.agent_contact_id).filter(
            AgentInstagramMessage.agent_config_id == agent_id,
            AgentInstagramMessage.direction == "inbound",
            AgentInstagramMessage.created_at >= since,
            AgentInstagramMessage.body.ilike(like)),
        db.query(AgentTelegramMessage.agent_contact_id)
        .join(AgentContact, AgentContact.id == AgentTelegramMessage.agent_contact_id)
        .filter(AgentContact.agent_config_id == agent_id,
                AgentTelegramMessage.direction == "inbound",
                AgentTelegramMessage.created_at >= since,
                AgentTelegramMessage.body.ilike(like)),
        db.query(AgentCall.agent_contact_id).filter(
            AgentCall.agent_config_id == agent_id,
            AgentCall.created_at >= since,
            AgentCall.transcript.ilike(like)),
    ]
    for q in queries:
        try:
            hits["messages"].update(r[0] for r in q.distinct().limit(200).all() if r[0])
        except Exception as e:
            logger.warning(f"[CLIENT-FIND] поиск по переписке не выполнен: {e}")
            db.rollback()
    return hits


def _messenger_names(db: Session, agent_id) -> List[tuple]:
    """(contact_id, имя) из мессенджеров: как клиент подписан в WhatsApp/Telegram/Instagram."""
    from backend.models.agent_whatsapp import AgentWhatsAppAccount, AgentWhatsAppChat
    from backend.models.agent_telegram_account import AgentTelegramAccount, AgentTelegramDialog
    from backend.models.agent_instagram import AgentInstagramConversation

    out: List[tuple] = []
    queries = [
        db.query(AgentWhatsAppChat.agent_contact_id, AgentWhatsAppChat.push_name)
        .join(AgentWhatsAppAccount, AgentWhatsAppAccount.id == AgentWhatsAppChat.account_id)
        .filter(AgentWhatsAppAccount.agent_config_id == agent_id,
                AgentWhatsAppChat.agent_contact_id.isnot(None),
                AgentWhatsAppChat.push_name.isnot(None)),
        db.query(AgentTelegramDialog.agent_contact_id, AgentTelegramDialog.tg_name)
        .join(AgentTelegramAccount, AgentTelegramAccount.id == AgentTelegramDialog.account_id)
        .filter(AgentTelegramAccount.agent_config_id == agent_id,
                AgentTelegramDialog.agent_contact_id.isnot(None)),
        db.query(AgentTelegramDialog.agent_contact_id, AgentTelegramDialog.tg_username)
        .join(AgentTelegramAccount, AgentTelegramAccount.id == AgentTelegramDialog.account_id)
        .filter(AgentTelegramAccount.agent_config_id == agent_id,
                AgentTelegramDialog.agent_contact_id.isnot(None)),
        db.query(AgentInstagramConversation.agent_contact_id, AgentInstagramConversation.ig_username)
        .filter(AgentInstagramConversation.agent_config_id == agent_id,
                AgentInstagramConversation.agent_contact_id.isnot(None)),
    ]
    for q in queries:
        try:
            out.extend((cid, name) for cid, name in q.limit(5000).all() if cid and name)
        except Exception as e:
            logger.warning(f"[CLIENT-FIND] имена из мессенджеров не прочитаны: {e}")
            db.rollback()
    return out


def search_clients(db: Session, agent_id, *, name: str = "", company: str = "",
                   niche: str = "", phone: str = "", query: str = "",
                   exclude_ids=(), limit: int = 3) -> Dict[str, Any]:
    """
    Найти клиента агента. Возвращает {"confidence": high|medium|low|none,
    "candidates": [(contact, score, [чем совпал])]} — лучшие первыми.
    exclude_ids — карточки, которые не предлагать (например, карточку самого
    звонящего, созданную только что).
    """
    from backend.models.agent_contact import AgentContact

    exclude = {str(x) for x in exclude_ids if x}
    scores: Dict[str, int] = {}
    reasons: Dict[str, List[str]] = {}
    contacts: Dict[str, Any] = {}

    def bump(cid, points, why):
        key = str(cid)
        if key in exclude:
            return
        scores[key] = scores.get(key, 0) + points
        if why not in reasons.setdefault(key, []):
            reasons[key].append(why)

    phone_hit = None
    if phone_tail(phone):
        phone_hit = find_contact_by_phone(db, agent_id, phone)
        if phone_hit is not None:
            contacts[str(phone_hit.id)] = phone_hit
            bump(phone_hit.id, SCORE_PHONE, "номер")

    if name and _words(name):
        named = db.query(AgentContact.id, AgentContact.name).filter(
            AgentContact.agent_config_id == agent_id, AgentContact.name.isnot(None),
        ).limit(20000).all()
        best: Dict[str, int] = {}
        for cid, cname in list(named) + _messenger_names(db, agent_id):
            m = _name_match(name, cname)
            if m:
                best[str(cid)] = max(best.get(str(cid), 0), m)
        for cid, m in best.items():
            bump(cid, SCORE_NAME if m == 2 else SCORE_NAME_PARTIAL, "имя" if m == 2 else "имя (похоже)")

    for text_value, label in ((company, "компания"), (niche, "ниша"), (query, "по словам клиента")):
        stems = _stems(text_value)
        if not stems:
            continue
        hits = _text_hits(db, agent_id, stems)
        for cid in hits["company"]:
            bump(cid, SCORE_COMPANY, label)
        for cid in hits["card"] - hits["company"]:
            bump(cid, SCORE_TEXT, label)
        for cid in hits["messages"] - hits["card"] - hits["company"]:
            bump(cid, SCORE_MESSAGES, label + " (в переписке)")

    if not scores:
        return {"confidence": "none", "candidates": []}

    missing = [cid for cid in scores if cid not in contacts]
    if missing:
        for c in db.query(AgentContact).filter(
            AgentContact.agent_config_id == agent_id, AgentContact.id.in_(missing),
        ).all():
            contacts[str(c.id)] = c
    now = datetime.utcnow()
    for cid, c in contacts.items():
        if cid in scores and c.updated_at:
            age = now - c.updated_at
            if age <= timedelta(days=3):
                scores[cid] += SCORE_RECENT
            elif age <= timedelta(days=14):
                scores[cid] += SCORE_FRESH

    ranked = sorted(
        (cid for cid in scores if cid in contacts),
        key=lambda cid: (scores[cid], contacts[cid].updated_at or datetime.min),
        reverse=True,
    )
    candidates = [(contacts[cid], scores[cid], reasons.get(cid, [])) for cid in ranked[:limit]]
    if not candidates:
        return {"confidence": "none", "candidates": []}

    top, top_score, top_why = candidates[0]
    second_score = candidates[1][1] if len(candidates) > 1 else 0
    has_name = any(w.startswith("имя") for w in top_why)
    has_name_full = "имя" in top_why
    has_other = any(not w.startswith("имя") for w in top_why)
    if phone_hit is not None and str(phone_hit.id) == str(top.id):
        confidence = "high"
    elif has_name and has_other and top_score - second_score >= 20:
        confidence = "high"
    elif has_name_full and top_score - second_score >= 20:
        confidence = "medium"
    else:
        confidence = "low"
    return {"confidence": confidence, "candidates": candidates}


def _trim_lines(text: str, width: int = 180) -> str:
    out = []
    for line in (text or "").splitlines():
        line = line.rstrip()
        out.append(line if len(line) <= width else line[: width - 1] + "…")
    return "\n".join(out)


def _messenger_name(db: Session, contact) -> str:
    """Как клиент подписан в мессенджере — если в карточке имени нет."""
    from backend.models.agent_whatsapp import AgentWhatsAppChat
    from backend.models.agent_telegram_account import AgentTelegramDialog
    for column, owner in ((AgentWhatsAppChat.push_name, AgentWhatsAppChat.agent_contact_id),
                          (AgentTelegramDialog.tg_name, AgentTelegramDialog.agent_contact_id)):
        try:
            row = db.query(column).filter(owner == contact.id, column.isnot(None)).first()
        except Exception:
            db.rollback()
            continue
        if row and (row[0] or "").strip():
            return row[0].strip()
    return ""


def client_card(db: Session, contact, *, with_history: bool = True, max_events: int = 8) -> Dict[str, Any]:
    """Компактная карточка клиента для голосового агента и оркестратора."""
    memory = contact.memory if isinstance(contact.memory, dict) else {}

    def mem(*keys):
        for k in keys:
            v = memory.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return ""

    card = {
        "agent_contact_id": str(contact.id),
        "name": contact.name or _messenger_name(db, contact),
        "company": contact.company or "",
        "niche": mem("niche", "industry"),
        "need": mem("direction_need", "needs"),
        "summary": mem("summary")[:400],
        "phones": ["+" + p for p in contact_phone_digits(contact)],
        "stage": contact.status,
    }
    if contact.updated_at:
        from backend.core.timezone_utils import utc_to_bishkek
        card["last_contact"] = utc_to_bishkek(contact.updated_at).strftime("%d.%m %H:%M")
    if with_history:
        try:
            from backend.services.agent_orchestrator import build_conversation_timeline
            history = build_conversation_timeline(db, contact, max_events=max_events)
            card["history"] = _trim_lines(history.strip())
        except Exception as e:
            logger.warning(f"[CLIENT-FIND] хронология не собрана: {e}")
    return {k: v for k, v in card.items() if v not in ("", None, [])}


VOICE_INSTRUCTIONS = {
    "high": ("Это он. Обратись по имени и продолжай с того, о чём говорили (история ниже). "
             "Не спрашивай заново, кто он и чем занимается."),
    "medium": ("Похоже на этого клиента, но не наверняка. Уточни ОДНИМ вопросом по истории "
               "(например: «вы писали нам про …?»). Подтвердил — вызови find_client ещё раз с "
               "уточнёнными данными и продолжай с места."),
    "low": ("Несколько похожих или совпадение слабое. Уточни одним вопросом (компания, ниша или "
            "прежний номер) и вызови find_client ещё раз. Не угадывай и не называй чужие имена."),
    "none": "В базе не найден — говори как с новым клиентом.",
}


def find_for_voice(db: Session, agent_id, args: Dict[str, Any], *, exclude_ids=()) -> Dict[str, Any]:
    """Ответ голосовой функции / инструмента оркестратора find_client."""
    found = search_clients(
        db, agent_id,
        name=str(args.get("name") or ""), company=str(args.get("company") or ""),
        niche=str(args.get("niche") or ""), phone=str(args.get("phone") or ""),
        query=str(args.get("query") or ""), exclude_ids=exclude_ids,
    )
    confidence = found["confidence"]
    candidates = found["candidates"]
    result: Dict[str, Any] = {
        "ok": True,
        "confidence": confidence,
        "instruction": VOICE_INSTRUCTIONS[confidence],
    }
    if confidence == "high":
        candidates = candidates[:1]  # узнан — чужие похожие только сбивают агента
    if candidates:
        clients = []
        for i, (contact, score, why) in enumerate(candidates):
            card = client_card(db, contact, with_history=(i == 0 and confidence in ("high", "medium")))
            card["matched_by"] = ", ".join(why)
            clients.append(card)
        result["clients"] = clients
    result["found"] = len(candidates)
    return result
