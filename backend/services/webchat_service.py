"""
Чат на сайте (Voksy Beauty, шаг 1.6в) — текстовый канал агента на странице салона
(/beauty/<slug>, /p/<slug>) и на сайте владельца (виджет booking-widget.js, data-chat).

Как устроено:
  • посетитель открывает чат → POST /api/public/chat/<slug>/start → токен (в браузере
    localStorage, у нас только sha256) и сессия webchat_sessions;
  • первое сообщение заводит карточку CRM (агент — первый агент владельца, телефон
    синтетический "web:<hex>", пока посетитель не оставит номер);
  • сообщение пишется в agent_webchat_messages и через DEBOUNCE_SECONDS (серия сообщений
    склеивается в одно, как у WhatsApp) уходит в разбор: agent_orchestrator.handle_inbound_webchat
    → тот же PostCall, что у мессенджеров (call_direction "webchat_inbound"), бот отвечает
    инструментом webchat_send_message — ответ ложится в ту же таблицу;
  • посетитель забирает новые сообщения опросом GET …/messages (раз в 2–8 с, пока окно открыто);
    пока идёт разбор, ответ помечен «печатает»;
  • оставил телефон («Представьтесь») — номер переходит в карточку, а если такой клиент уже
    есть у агента, карточка гостя склеивается с ним (agent_contact_merge): бот видит всю историю
    клиента, запись и напоминания идут на настоящую карточку.

Владелец видит чат в инбоксе (канал «Сайт») и отвечает оттуда (sent_by="owner" + пауза бота,
как в мессенджерах). Бот не смог ответить (нет кредитов, тариф, агент выключен, сбой) —
посетитель получает служебный ответ «администратор ответит здесь» (sent_by="auto"), владелец — оповещение.

Защита: лимиты на сессии и сообщения с одного IP и на агента (в памяти воркера), длина сообщения,
запись в чужую сессию невозможна (токен), посетитель видит только свою сессию.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import secrets
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from backend.core.logging import get_logger
from backend.core.timezone_utils import iso_utc

logger = get_logger(__name__)

CHANNEL = "webchat"
DIRECTION = "webchat_inbound"
GUEST_NAME = "Гость сайта"
DEFAULT_TITLE = "Администратор"
DEFAULT_GREETING = "Здравствуйте! Подскажу по услугам, ценам и свободному времени и запишу вас. Что вас интересует?"

DEBOUNCE_SECONDS = float(os.getenv("WEBCHAT_DEBOUNCE_SECONDS", "2.5") or 2.5)
# Входящее не ушло в разбор (воркер перезапустился посреди паузы) — подхватит опрос посетителя
STALE_DISPATCH_SECONDS = 20
MAX_TEXT = 2000
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,80}$")

# лимиты (в памяти воркера; на 4 воркера — грубая, но достаточная защита от пустой болтовни)
SESSIONS_PER_IP_HOUR = 12
MESSAGES_PER_IP_10MIN = 40
MESSAGES_PER_SESSION_10MIN = 25
MESSAGES_PER_AGENT_10MIN = 200

AUTO_REPLY = ("Спасибо, сообщение получили! Администратор ответит здесь в ближайшее время. "
              "Можно оставить номер — перезвоним или напишем в WhatsApp.")


class ChatError(Exception):
    def __init__(self, message: str, status: int = 400, code: str = "bad_request"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


# ───────────────────────────── лимиты ─────────────────────────────

_HITS: Dict[str, deque] = defaultdict(deque)


def _rate(key: str, limit: int, window: float, message: str) -> None:
    now = time.time()
    q = _HITS[key]
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        raise ChatError(message, 429, "rate_limited")
    q.append(now)


def _reset_limits() -> None:   # для тестов
    _HITS.clear()


# ───────────────────────────── настройки и доступность ─────────────────────────────

def chat_settings(page) -> Dict[str, Any]:
    """Настройки чата страницы (booking_pages.profile): включён, как зовут, приветствие."""
    prof = dict(getattr(page, "profile", None) or {}) if page is not None else {}
    return {
        "enabled": prof.get("chat_enabled", True) is not False,
        "title": (prof.get("chat_title") or "").strip() or DEFAULT_TITLE,
        "greeting": (prof.get("chat_greeting") or "").strip() or DEFAULT_GREETING,
    }


def agent_for(db: Session, owner):
    from backend.models.agent_config import AgentConfig
    return (db.query(AgentConfig).filter(AgentConfig.user_id == owner.id)
            .order_by(AgentConfig.created_at).first())


def availability(db: Session, page, owner) -> Tuple[bool, str, Any]:
    """(можно ли показывать чат, причина для кабинета, агент)."""
    cfg = chat_settings(page)
    if not cfg["enabled"]:
        return False, "Чат выключен в настройках страницы", None
    agent = agent_for(db, owner)
    if agent is None:
        return False, "Чтобы чат отвечал, создайте бота (раздел «Чат-бот» или «Агент»)", None
    if not agent.is_active:
        return False, "Бот выключен — включите его, и чат появится на странице", agent
    try:
        if not owner.has_agent_chat_access():
            return False, "Нужен тариф с ботом — чат появится после оплаты", agent
    except Exception:
        pass
    return True, "", agent


def public_state(db: Session, page, owner) -> Dict[str, Any]:
    """Для витрины: показывать ли чат и чем здороваться."""
    try:
        ok, _, _ = availability(db, page, owner)
    except Exception as e:
        logger.warning(f"[WEBCHAT] availability failed: {e}")
        db.rollback()
        ok = False
    cfg = chat_settings(page)
    return {"enabled": ok, "title": cfg["title"], "greeting": cfg["greeting"]}


def cabinet_state(db: Session, page, owner) -> Dict[str, Any]:
    """Для кабинета (страница салона): настройки + почему чат не виден."""
    ok, reason, agent = availability(db, page, owner)
    cfg = chat_settings(page)
    return {**cfg, "live": ok, "reason": reason, "agent_id": str(agent.id) if agent is not None else None}


def save_settings(page, data: Dict[str, Any]) -> None:
    prof = dict(page.profile or {})
    if "chat_enabled" in data:
        prof["chat_enabled"] = bool(data.get("chat_enabled"))
    if "chat_title" in data:
        prof["chat_title"] = " ".join(str(data.get("chat_title") or "").split())[:40]
    if "chat_greeting" in data:
        prof["chat_greeting"] = str(data.get("chat_greeting") or "").strip()[:300]
    page.profile = prof


# ───────────────────────────── сессия посетителя ─────────────────────────────

def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def find_session(db: Session, page, token: Optional[str]):
    from backend.models.webchat import WebChatSession
    if not token or not TOKEN_RE.match(token):
        return None
    s = db.query(WebChatSession).filter(WebChatSession.token_hash == _hash(token)).first()
    if s is None or str(s.user_id) != str(page.user_id):
        return None
    return s


def start_session(db: Session, page, owner, *, token: Optional[str] = None, origin: str = "salon",
                  ip: str = "", user_agent: str = "") -> Tuple[Any, str, bool]:
    """Продолжить сессию по токену или начать новую. → (сессия, токен, новая ли)."""
    from backend.models.webchat import WebChatSession
    ok, _, agent = availability(db, page, owner)
    if not ok:
        raise ChatError("Чат сейчас недоступен", 404, "chat_unavailable")
    s = find_session(db, page, token)
    if s is not None:
        s.last_seen_at = datetime.utcnow()
        if str(s.agent_config_id) != str(agent.id):
            s.agent_config_id = agent.id   # владелец пересоздал бота: сессия идёт к новому
        db.flush()
        return s, token, False
    _rate(f"ses:{ip}", SESSIONS_PER_IP_HOUR, 3600, "Слишком много новых чатов с этого устройства. Попробуйте позже.")
    token = secrets.token_urlsafe(24)
    s = WebChatSession(user_id=owner.id, agent_config_id=agent.id, token_hash=_hash(token),
                       page_slug=page.slug, origin=("site" if origin == "site" else "salon"),
                       ip=(ip or "")[:64], user_agent=(user_agent or "")[:255])
    db.add(s)
    db.flush()
    return s, token, True


def _is_guest_name(name: Optional[str]) -> bool:
    return not (name or "").strip() or (name or "").strip() in (GUEST_NAME, "Клиент")


def ensure_contact(db: Session, session):
    """Карточка CRM посетителя (заводится с первым сообщением)."""
    from backend.models.agent_contact import AgentContact
    if session.agent_contact_id:
        c = db.query(AgentContact).filter(AgentContact.id == session.agent_contact_id).first()
        if c is not None and str(c.agent_config_id) == str(session.agent_config_id):
            return c
    c = None
    if session.phone:
        from backend.services.agent_contact_phones import find_contact_by_phone
        c = find_contact_by_phone(db, session.agent_config_id, session.phone)
    if c is None:
        c = AgentContact(user_id=session.user_id, agent_config_id=session.agent_config_id,
                         name=(session.name or GUEST_NAME)[:255],
                         phone=session.phone or f"web:{uuid.uuid4().hex[:12]}", status="new")
        c.memory = {"source": "webchat"}
        db.add(c)
        db.flush()
    session.agent_contact_id = c.id
    db.flush()
    return c


def set_identity(db: Session, session, *, name: Optional[str] = None, phone: Optional[str] = None) -> Dict[str, Any]:
    """Посетитель представился. Телефон → в карточку; такой клиент уже есть — склеиваем с ним."""
    from backend.models.agent_contact import AgentContact
    from backend.utils.phone import normalize_phone_e164
    name = " ".join(str(name or "").split())[:120] or None
    e164 = None
    if phone:
        e164 = normalize_phone_e164(phone)
        if not e164:
            raise ChatError("Проверьте номер телефона", 400, "bad_phone")
    if name:
        session.name = name
    if e164:
        session.phone = e164
    merged = False
    contact = None
    if session.agent_contact_id:
        contact = db.query(AgentContact).filter(AgentContact.id == session.agent_contact_id).first()
    if contact is None and e164:
        contact = ensure_contact(db, session)   # ещё не писал: карточка сразу с номером (или найденная по нему)
    if contact is not None:
        if e164:
            from backend.services.agent_contact_phones import add_alt_phone, find_contact_by_phone, has_phone
            other = find_contact_by_phone(db, contact.agent_config_id, e164)
            if other is not None and str(other.id) != str(contact.id):
                from backend.services import agent_contact_merge
                db.flush()
                agent_contact_merge.merge_contacts(db, other, contact, reason="клиент оставил номер в чате на сайте")
                contact = other
                session.agent_contact_id = other.id
                merged = True
            elif not has_phone(contact, e164):
                if str(contact.phone or "").startswith("web:"):
                    contact.phone = e164
                else:
                    add_alt_phone(contact, e164)
        if name and _is_guest_name(contact.name):
            contact.name = name[:255]
    db.flush()
    return {"name": session.name, "phone": session.phone, "merged": merged}


# ───────────────────────────── сообщения ─────────────────────────────

def _who(m) -> str:
    if (m.direction or "inbound") != "outbound":
        return "client"
    return {"owner": "owner", "auto": "auto"}.get(m.sent_by or "", "bot")


def store_message(db: Session, *, agent_config_id, agent_contact_id, session_id, direction: str, body: str,
                  sent_by: Optional[str] = None, attachment_id=None):
    from backend.models.webchat import AgentWebChatMessage
    now = datetime.utcnow()
    m = AgentWebChatMessage(agent_config_id=agent_config_id, agent_contact_id=agent_contact_id,
                            session_id=session_id, direction=direction, sent_by=sent_by,
                            body=body or "", attachment_id=attachment_id, sent_at=now, created_at=now)
    db.add(m)
    db.flush()
    return m


def post_message(db: Session, session, text: str, *, ip: str = "") -> Any:
    """Сообщение посетителя. Возвращает строку сообщения; разбор запускает вызывающий (schedule_dispatch)."""
    text = (text or "").strip()
    if not text:
        raise ChatError("Пустое сообщение", 400, "empty")
    if len(text) > MAX_TEXT:
        raise ChatError(f"Сообщение длиннее {MAX_TEXT} символов — разделите его", 400, "too_long")
    _rate(f"msg-ip:{ip}", MESSAGES_PER_IP_10MIN, 600, "Слишком много сообщений. Подождите немного.")
    _rate(f"msg-s:{session.id}", MESSAGES_PER_SESSION_10MIN, 600, "Слишком много сообщений. Подождите немного.")
    _rate(f"msg-a:{session.agent_config_id}", MESSAGES_PER_AGENT_10MIN, 600, "Чат перегружен. Попробуйте позже.")
    contact = ensure_contact(db, session)
    m = store_message(db, agent_config_id=session.agent_config_id, agent_contact_id=contact.id,
                      session_id=session.id, direction="inbound", body=text)
    session.last_seen_at = datetime.utcnow()
    db.flush()
    return m


def session_for_contact(db: Session, agent_config_id, contact_id):
    """Самая свежая сессия посетителя этой карточки (туда уходит ответ бота/владельца)."""
    from backend.models.webchat import WebChatSession
    return (db.query(WebChatSession)
            .filter(WebChatSession.agent_contact_id == contact_id,
                    WebChatSession.agent_config_id == agent_config_id)
            .order_by(WebChatSession.last_seen_at.desc()).first())


def get_thread(db: Session, contact_id, limit: int = 30) -> list:
    """Переписка карточки (старые → новые) — для хронологии оркестратора и карточки."""
    from backend.models.webchat import AgentWebChatMessage
    rows = (db.query(AgentWebChatMessage).filter(AgentWebChatMessage.agent_contact_id == contact_id)
            .order_by(AgentWebChatMessage.created_at.desc()).limit(limit).all())
    return list(reversed(rows))


def has_thread(db: Session, contact_id) -> bool:
    from backend.models.webchat import AgentWebChatMessage
    return db.query(AgentWebChatMessage.id).filter(AgentWebChatMessage.agent_contact_id == contact_id).first() is not None


def _attachment(db: Session, file_id) -> Optional[Dict[str, Any]]:
    if not file_id:
        return None
    try:
        from backend.models.agent_file import AgentFile
        from backend.services import agent_media_service
        f = db.query(AgentFile).filter(AgentFile.id == file_id).first()
        if f is None:
            return None
        return {"name": f.filename, "kind": f.kind, "mime": f.mime_type,
                "url": agent_media_service.file_link(f, expires_seconds=6 * 3600)}
    except Exception as e:
        logger.warning(f"[WEBCHAT] attachment link failed: {e}")
        db.rollback()
        return None


_FILE_LINE = re.compile(r"^📎 Отправлен файл «[^»]*»( \[file_id=[^\]]+\])?( \(ссылкой\))?\n?")


def _visitor_text(body: str, has_file: bool) -> str:
    t = body or ""
    if has_file:
        t = _FILE_LINE.sub("", t)
    t = re.sub(r"\s*\[file_id=[0-9a-fA-F-]{8,}\]", "", t)
    return t.strip()


def visitor_messages(db: Session, session, after: Optional[str] = None, limit: int = 80) -> Dict[str, Any]:
    """Что видит посетитель: его сессия целиком (и прошлые сообщения карточки — нет, только этой сессии)."""
    from backend.models.webchat import AgentWebChatMessage
    q = db.query(AgentWebChatMessage).filter(AgentWebChatMessage.session_id == session.id)
    after_dt = _parse_ts(after)
    if after_dt is not None:
        q = q.filter(AgentWebChatMessage.created_at > after_dt)
    rows = q.order_by(AgentWebChatMessage.created_at.desc()).limit(limit).all()
    rows = list(reversed(rows))
    items = []
    for m in rows:
        att = _attachment(db, m.attachment_id)
        items.append({"id": str(m.id), "who": _who(m), "text": _visitor_text(m.body, att is not None),
                      "ts": iso_utc(m.created_at), "attachment": att})
    return {"items": items, "typing": typing(db, session),
            "cursor": iso_utc(rows[-1].created_at) if rows else (after or None),
            "name": session.name, "phone": session.phone}


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        raw = str(value).strip().replace("Z", "+00:00")
        if " " in raw and "+" not in raw:      # «+» из адреса без кодирования превращается в пробел
            raw = raw.replace(" ", "+")
        dt = datetime.fromisoformat(raw)
        return dt.replace(tzinfo=None) if dt.tzinfo is None else (dt - dt.utcoffset()).replace(tzinfo=None)
    except ValueError:
        return None


def _last_inbound_at(db: Session, session) -> Optional[datetime]:
    return db.execute(sql_text(
        "SELECT max(created_at) FROM agent_webchat_messages WHERE session_id = :s AND direction = 'inbound'"
    ), {"s": session.id}).scalar()


def _last_outbound_at(db: Session, session) -> Optional[datetime]:
    return db.execute(sql_text(
        "SELECT max(created_at) FROM agent_webchat_messages WHERE session_id = :s AND direction = 'outbound'"
    ), {"s": session.id}).scalar()


def typing(db: Session, session) -> bool:
    """«Печатает…»: есть неотвеченное сообщение посетителя и разбор идёт или вот-вот начнётся."""
    last_in = _last_inbound_at(db, session)
    if last_in is None:
        return False
    last_out = _last_outbound_at(db, session)
    if last_out is not None and last_out >= last_in:
        return False
    now = datetime.utcnow()
    if now - last_in < timedelta(seconds=DEBOUNCE_SECONDS + 3):
        return True
    if session.agent_contact_id:
        busy = db.execute(sql_text("SELECT ai_busy_until FROM agent_contacts WHERE id = :c"),
                          {"c": session.agent_contact_id}).scalar()
        if busy is not None and busy > now:
            return True
    return False


# ───────────────────────────── разбор ─────────────────────────────

def claim_batch(db: Session, session_id) -> Optional[Tuple[str, str]]:
    """
    Атомарно забрать новые сообщения посетителя для разбора. → (contact_id, склеенный текст) или None.
    Два воркера (опрос + отложенная задача) не отдадут одну серию дважды: строка сессии под FOR UPDATE,
    last_dispatched_at сдвигается на последнее входящее.
    """
    row = db.execute(sql_text(
        "SELECT agent_contact_id, last_dispatched_at FROM webchat_sessions WHERE id = :s FOR UPDATE"
    ), {"s": session_id}).first()
    last = db.execute(sql_text(
        "SELECT max(created_at) FROM agent_webchat_messages WHERE session_id = :s AND direction = 'inbound'"
    ), {"s": session_id}).scalar() if row is not None else None
    if row is None or row[0] is None or last is None or (row[1] is not None and row[1] >= last):
        db.rollback()
        return None
    prev = row[1]
    db.execute(sql_text("UPDATE webchat_sessions SET last_dispatched_at = :t WHERE id = :s"),
               {"t": last, "s": session_id})
    params = {"s": session_id, "last": last}
    cond = " AND created_at <= :last"
    if prev is not None:
        cond += " AND created_at > :prev"
        params["prev"] = prev
    texts = [r[0] for r in db.execute(sql_text(
        "SELECT body FROM agent_webchat_messages WHERE session_id = :s AND direction = 'inbound'"
        + cond + " ORDER BY created_at"), params).fetchall()]
    db.commit()
    if not texts:
        return None
    return str(row[0]), "\n".join(t.strip() for t in texts if (t or "").strip())


def due_for_dispatch(db: Session, session) -> bool:
    """Неразобранное входящее старше паузы склейки (подстраховка из опроса посетителя)."""
    last_in = _last_inbound_at(db, session)
    if last_in is None:
        return False
    if session.last_dispatched_at is not None and session.last_dispatched_at >= last_in:
        return False
    return datetime.utcnow() - last_in >= timedelta(seconds=STALE_DISPATCH_SECONDS)


async def dispatch_later(session_id: str, delay: float = DEBOUNCE_SECONDS) -> None:
    """Подождать паузу склейки и отдать серию сообщений разбору (фоновый loop)."""
    from backend.db.session import SessionLocal
    if delay > 0:
        await asyncio.sleep(delay)
    db = SessionLocal()
    try:
        last_in = db.execute(sql_text(
            "SELECT max(created_at) FROM agent_webchat_messages WHERE session_id = :s AND direction = 'inbound'"
        ), {"s": session_id}).scalar()
        if last_in is not None and datetime.utcnow() - last_in < timedelta(seconds=DEBOUNCE_SECONDS * 0.8):
            return   # посетитель дописывает — разбор запустит задача его последнего сообщения
        batch = claim_batch(db, session_id)
    except Exception as e:
        logger.warning(f"[WEBCHAT] claim failed for session {session_id}: {e}")
        db.rollback()
        batch = None
    finally:
        db.close()
    if batch is None:
        return
    contact_id, body = batch
    from backend.services.agent_orchestrator import handle_inbound_webchat
    await handle_inbound_webchat(session_id, contact_id, body)


def schedule_dispatch(session_id, delay: float = DEBOUNCE_SECONDS) -> None:
    """Запустить отложенный разбор в фоновом loop'е (обработчики публичного API — синхронные)."""
    try:
        from backend.core.background_loop import submit
        submit(dispatch_later(str(session_id), delay))
    except Exception as e:
        logger.warning(f"[WEBCHAT] dispatch not scheduled: {e}")


def auto_reply(db: Session, session_id, contact_id, reason: str) -> bool:
    """Бот не смог ответить — посетитель не остаётся в тишине. True — служебный ответ ушёл (тогда и владельцу
    оповещение; не чаще раза в 30 минут на сессию)."""
    from backend.models.webchat import WebChatSession
    s = db.query(WebChatSession).filter(WebChatSession.id == session_id).first()
    if s is None:
        return False
    last_in = _last_inbound_at(db, s)
    last_out = _last_outbound_at(db, s)
    if last_out is not None and last_in is not None and last_out >= last_in:
        return False
    # один служебный ответ подряд: прошлый тоже был «auto» и посетитель с тех пор писал — не повторяем
    prev_auto = db.execute(sql_text(
        "SELECT 1 FROM agent_webchat_messages WHERE session_id = :s AND sent_by = 'auto' "
        "AND created_at > now() at time zone 'utc' - interval '30 minutes' LIMIT 1"), {"s": s.id}).first()
    if prev_auto is not None:
        return False
    store_message(db, agent_config_id=s.agent_config_id, agent_contact_id=contact_id, session_id=s.id,
                  direction="outbound", body=AUTO_REPLY, sent_by="auto")
    db.commit()
    logger.warning(f"[WEBCHAT] session {s.id}: бот не ответил ({reason}) — посетителю служебный ответ")
    return True


def owner_alert_text(contact, body: str, reason: str) -> str:
    who = (contact.name or GUEST_NAME) if contact is not None else GUEST_NAME
    return (f"💬 Сообщение в чате на сайте от «{who}»: «{(body or '').strip()[:220]}»\n"
            f"Бот не ответил: {reason}. Ответьте в инбоксе (канал «Сайт»).")


# ───────────────────────────── инструмент бота ─────────────────────────────

WEBCHAT_SEND_TOOL = {
    "type": "function",
    "name": "webchat_send_message",
    "description": (
        "Ответить клиенту в чате на сайте (окно чата на странице салона или на сайте владельца). "
        "Клиент видит ответ сразу, пока окно открыто. Указывай agent_contact_id. Пиши как живой "
        "администратор: коротко, без markdown. Реплики можно разделить строкой «---» — каждая уйдёт "
        "отдельным сообщением. Номера телефона у клиента чата может не быть — попроси оставить его, "
        "когда записываешь (форма «Оставить номер» есть в окне чата)."
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

WEBCHAT_SEND_FILE_TOOL = {
    "type": "function",
    "name": "webchat_send_file",
    "description": (
        "Отправить клиенту в чат на сайте ФАЙЛ или фото (файл из list_agent_files, документ из "
        "create_document, фото товара или работы). Картинки показываются в чате, остальное — ссылкой."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent_contact_id": {"type": "string", "description": "UUID контакта агента"},
            "file_id": {"type": "string", "description": "id файла"},
            "caption": {"type": "string", "description": "Подпись"},
        },
        "required": ["agent_contact_id", "file_id"],
    },
}

TOOLS = [WEBCHAT_SEND_TOOL, WEBCHAT_SEND_FILE_TOOL]
TOOL_NAMES = frozenset(t["name"] for t in TOOLS)


def chat_live_for_agent(db: Session, agent_config) -> bool:
    """Есть ли у агента чат на сайте (страница записи с включённым чатом)."""
    from backend.models.booking import BookingPage
    if agent_config is None:
        return False
    try:
        page = db.query(BookingPage).filter(BookingPage.user_id == agent_config.user_id).first()
        if page is None or not chat_settings(page)["enabled"]:
            return False
        first = db.execute(sql_text(
            "SELECT id FROM agent_configs WHERE user_id = :u ORDER BY created_at LIMIT 1"),
            {"u": agent_config.user_id}).scalar()
        return str(first) == str(agent_config.id)
    except Exception as e:
        logger.warning(f"[WEBCHAT] chat lookup failed: {e}")
        db.rollback()
        return False


def augment(tools: list, agent_config, db: Session) -> list:
    """Домешать инструменты чата на сайте, если у агента он есть (или уже есть переписка)."""
    if not chat_live_for_agent(db, agent_config):
        try:
            from backend.models.webchat import WebChatSession
            if agent_config is None or db.query(WebChatSession.id).filter(
                    WebChatSession.agent_config_id == agent_config.id).first() is None:
                return tools
        except Exception:
            db.rollback()
            return tools
    from backend.services.agent_tools import to_chat_completions_tools
    return tools + to_chat_completions_tools(TOOLS)


async def fn_webchat_send_message(args: dict, user_id, agent_config, db: Session, *, file_row=None,
                                  sent_by: Optional[str] = None, skip_rate_limit: bool = False) -> dict:
    """Ответ в чат на сайте (бот: текст чистится; владелец из инбокса — как написал)."""
    from backend.models.agent_contact import AgentContact
    from backend.services.message_style import clean_client_text
    if agent_config is None:
        return {"ok": False, "error": "Агент не найден"}
    text = (args.get("text") or args.get("caption") or "").strip()
    if sent_by != "owner":
        text = clean_client_text(text)
    if not text and file_row is None:
        return {"ok": False, "error": "Пустой текст сообщения"}
    contact = db.query(AgentContact).filter(
        AgentContact.id == args.get("agent_contact_id"),
        AgentContact.user_id == user_id,
        AgentContact.agent_config_id == agent_config.id,
    ).first()
    if contact is None:
        return {"ok": False, "error": "Контакт не найден"}
    session = session_for_contact(db, agent_config.id, contact.id)
    if session is None:
        return {"ok": False, "error": "Клиент не писал в чат на сайте — туда ответить нельзя. Выбери его мессенджер."}
    body = text
    if file_row is not None:
        body = (f"📎 Отправлен файл «{file_row.filename}» [file_id={file_row.id}]" + (f"\n{text}" if text else ""))
    m = store_message(db, agent_config_id=agent_config.id, agent_contact_id=contact.id, session_id=session.id,
                      direction="outbound", body=body, sent_by=sent_by,
                      attachment_id=(file_row.id if file_row is not None else None))
    db.commit()
    logger.info(f"[WEBCHAT] → session {session.id} ({'file' if file_row is not None else 'text'}, {sent_by or 'bot'})")
    return {"ok": True, "to": "чат на сайте", "message_row_id": str(m.id),
            **({"sent_as": "image" if (file_row.kind or "") == "image" else "link"} if file_row is not None else {})}


async def fn_webchat_send_file(args: dict, user_id, agent_config, db: Session) -> dict:
    from backend.models.agent_file import AgentFile
    if agent_config is None:
        return {"ok": False, "error": "Агент не найден"}
    f = db.query(AgentFile).filter(AgentFile.id == args.get("file_id"),
                                   AgentFile.agent_config_id == agent_config.id).first() if args.get("file_id") else None
    if f is None or not f.storage_key:
        return {"ok": False, "error": "Файл не найден"}
    return await fn_webchat_send_message({"agent_contact_id": args.get("agent_contact_id"),
                                          "text": args.get("caption") or ""}, user_id, agent_config, db, file_row=f)
