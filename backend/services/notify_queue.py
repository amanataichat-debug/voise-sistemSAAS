"""
Очередь отправок клиентам (Voksy Beauty, шаг 2.2).

Любое автоматическое сообщение клиенту сначала встаёт в notification_queue (enqueue), потом его отправляет
process() из фонового цикла напоминаний (раз в минуту, booking_notify):

  • одно сообщение — один раз: idem_key уникален (ON CONFLICT DO NOTHING), сколько бы воркеров ни планировали;
  • забирает строку ровно один воркер: UPDATE … FOR UPDATE SKIP LOCKED → status «sending» с арендой на 5 минут
    (упавший воркер — строка вернётся в очередь);
  • окна времени по Бишкеку: по записи (service) — 08:00–21:00, поздравления и приглашения (marketing) — 10:00–20:00;
    вне окна ждём утра, а если к утру смысла нет (expires_at — напоминание после начала визита) — пропуск;
  • канал — где клиент писал последним, при сбое пробуем остальные доступные (WhatsApp → Telegram → Instagram →
    чат на сайте); не доставилось ни в один — повтор через 5, 20, 60 минут, потом «не доставлено»; мессенджера у
    клиента нет — «пропущено» (SMS не работают);
  • лимиты: не больше SEND_BURST сообщений салона за 10 минут (защита номера WhatsApp от бана), рассылок
    (marketing) — не больше max_per_day в день на салон и одному клиенту не чаще раза в gap_days дней;
  • согласие: отказавшимся от рассылок (agent_contacts.marketing_opt_out_at), «не беспокоить» и заблокированным
    как спам поздравления и приглашения не идут; заблокированным как спам — вообще ничего.

Отправку делает SENDER (по умолчанию deliver) — в тестах подменяется.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.services import booking_service as bs

logger = logging.getLogger(__name__)

SERVICE_WINDOW = (8, 21)          # часы Бишкека [8:00, 21:00)
MARKETING_WINDOW = (10, 20)
RETRY_MINUTES = (5, 20, 60)
MAX_ATTEMPTS = len(RETRY_MINUTES) + 1
LEASE = timedelta(minutes=5)
SEND_BURST = 30                   # сообщений салона за 10 минут
BURST_WINDOW = timedelta(minutes=10)
DEFAULT_MAX_PER_DAY = 50
DEFAULT_GAP_DAYS = 3
CHANNEL_ORDER = ("whatsapp", "telegram", "instagram", "webchat")
CHANNEL_TITLES = {"whatsapp": "WhatsApp", "telegram": "Telegram", "instagram": "Instagram", "webchat": "Чат на сайте"}
STATUS_TITLES = {"pending": "В очереди", "sending": "Отправляется", "sent": "Отправлено", "failed": "Не доставлено",
                 "skipped": "Пропущено", "cancelled": "Отменено"}


# ───────────────────────────── постановка ─────────────────────────────

def enqueue(db: Session, *, user_id, contact_id, rule: str, text_: str, idem_key: str, kind: str = "service",
            lang: Optional[str] = None, booking_id=None, send_after: Optional[datetime] = None,
            expires_at: Optional[datetime] = None, meta: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Встать в очередь. Уже есть такой idem_key — ничего (None). Коммит — на вызывающем."""
    from sqlalchemy.dialects.postgresql import insert
    from backend.models.automation import NotificationQueue
    if not (text_ or "").strip() or not contact_id:
        return None
    row = {
        "id": uuid.uuid4(), "user_id": _uuid(user_id), "contact_id": _uuid(contact_id), "rule": rule[:24],
        "kind": kind if kind in ("service", "marketing") else "service", "lang": lang, "text": text_.strip()[:4000],
        "status": "pending", "attempts": 0, "send_after": send_after or bs.now_utc(), "expires_at": expires_at,
        "idem_key": idem_key[:160], "meta": meta or None, "booking_id": _uuid(booking_id) if booking_id else None,
    }
    res = db.execute(insert(NotificationQueue.__table__).values(**row)
                     .on_conflict_do_nothing(index_elements=["idem_key"]).returning(NotificationQueue.__table__.c.id))
    got = res.first()
    return str(got[0]) if got else None


def _uuid(v):
    return v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))


# ───────────────────────────── отправка ─────────────────────────────

async def send_via(db: Session, user, contact, message: str, channel: str, agent=None) -> bool:
    """Текст клиенту в конкретный канал — теми же функциями, что у бота (лимиты бота не действуют)."""
    from backend.models.agent_config import AgentConfig
    from backend.services import agent_tools
    if agent is None:
        agent = db.query(AgentConfig).filter(AgentConfig.id == contact.agent_config_id).first()
    if agent is None:
        return False
    args = {"agent_contact_id": str(contact.id), "text": message}
    try:
        if channel == "whatsapp":
            res = await agent_tools.fn_whatsapp_send_message(args, user.id, agent, db, skip_rate_limit=True, typing_ms=0)
        elif channel == "telegram":
            res = await agent_tools.fn_telegram_send_message(args, user.id, agent, db, skip_rate_limit=True, typing_seconds=0)
        elif channel == "instagram":
            res = await agent_tools.fn_instagram_send_message(args, user.id, agent, db, skip_rate_limit=True)
        elif channel == "webchat":
            from backend.services import webchat_service
            res = await webchat_service.fn_webchat_send_message(args, user.id, agent, db, skip_rate_limit=True)
        else:
            return False
    except Exception as e:
        logger.warning(f"[QUEUE] send {channel} failed: {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return False
    return bool(res and res.get("ok"))


async def deliver(db: Session, user, contact, message: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """(доставлено, канал, ошибка). Сначала канал, где клиент писал последним, потом остальные доступные."""
    from backend.models.agent_config import AgentConfig
    from backend.services import inbox_service
    agent = db.query(AgentConfig).filter(AgentConfig.id == contact.agent_config_id).first()
    if agent is None:
        return False, None, "no_channel"
    avail = inbox_service.send_channels(db, contact, agent)
    first = inbox_service.default_channel(db, contact, avail)
    order = ([first] if first else []) + [c for c in CHANNEL_ORDER if c != first and (avail.get(c) or {}).get("can_send")]
    if not order:
        return False, None, "no_channel"
    tried = []
    for ch in order:
        if await send_via(db, user, contact, message, ch, agent=agent):
            return True, ch, None
        tried.append(CHANNEL_TITLES.get(ch, ch))
    return False, None, "Не отправилось: " + ", ".join(tried)


SENDER = deliver


def _window_ok(kind: str, now: datetime) -> bool:
    lo, hi = MARKETING_WINDOW if kind == "marketing" else SERVICE_WINDOW
    return lo <= bs.to_local(now).hour < hi


def next_window(kind: str, now: datetime) -> datetime:
    """Ближайшее начало окна отправки (сегодня или завтра) — UTC."""
    lo, hi = MARKETING_WINDOW if kind == "marketing" else SERVICE_WINDOW
    loc = bs.to_local(now)
    day = loc.date() if loc.hour < lo else loc.date() + timedelta(days=1)
    return datetime.combine(day, time(lo, 0), bs.BISHKEK).astimezone(timezone.utc)


def _spam_blocked(contact) -> bool:
    """Бот с клиентом выключен навсегда (спам или владелец так решил) — автоматически ничего не шлём."""
    until = getattr(contact, "ai_paused_until", None)
    return until is not None and until.year >= 9999


def _limits(db: Session, user_id) -> Dict[str, int]:
    from backend.services import automations
    return automations.limits_for(db, user_id)


def _claim(db: Session, now: datetime, limit: int) -> list:
    db.execute(text("UPDATE notification_queue SET status = 'pending', lease_until = NULL "
                    "WHERE status = 'sending' AND lease_until < :now"), {"now": now})
    rows = db.execute(text("""
        UPDATE notification_queue SET status = 'sending', lease_until = :lease, attempts = attempts + 1
        WHERE id IN (SELECT id FROM notification_queue WHERE status = 'pending' AND send_after <= :now
                     ORDER BY send_after LIMIT :lim FOR UPDATE SKIP LOCKED)
        RETURNING id
    """), {"now": now, "lease": now + LEASE, "lim": int(limit)}).fetchall()
    db.commit()
    return [r[0] for r in rows]


def _set(db: Session, item, status: str, *, error: Optional[str] = None, send_after: Optional[datetime] = None,
         refund_attempt: bool = False) -> None:
    item.status = status
    item.lease_until = None
    if error is not None:
        item.last_error = error[:500]
    if send_after is not None:
        item.send_after = send_after
    if refund_attempt:
        item.attempts = max(0, (item.attempts or 1) - 1)
    db.commit()


async def process(db: Session, now: Optional[datetime] = None, limit: int = 60) -> int:
    """Отправить, что пора. Возвращает, сколько сообщений ушло."""
    from backend.models.agent_contact import AgentContact
    from backend.models.automation import NotificationQueue
    from backend.models.user import User
    now = now or bs.now_utc()
    try:
        ids = _claim(db, now, limit)
    except Exception as e:
        db.rollback()
        logger.warning(f"[QUEUE] claim failed: {e}")
        return 0
    sent = 0
    users: Dict[str, Any] = {}
    for qid in ids:
        item = db.query(NotificationQueue).filter(NotificationQueue.id == qid).first()
        if item is None:
            continue
        try:
            sent += 1 if await _process_one(db, item, now, users, AgentContact, User) else 0
        except Exception as e:
            db.rollback()
            logger.error(f"[QUEUE] item {qid} failed: {e}", exc_info=True)
            try:
                item = db.query(NotificationQueue).filter(NotificationQueue.id == qid).first()
                if item is not None and item.status == "sending":
                    _retry_or_fail(db, item, now, f"Ошибка: {e}")
            except Exception:
                db.rollback()
    return sent


def _retry_or_fail(db: Session, item, now: datetime, error: str) -> None:
    if (item.attempts or 0) < MAX_ATTEMPTS:
        wait = RETRY_MINUTES[min(len(RETRY_MINUTES) - 1, max(0, (item.attempts or 1) - 1))]
        nxt = now + timedelta(minutes=wait)
        if item.expires_at is not None and nxt >= item.expires_at:
            _set(db, item, "failed", error=error)
            return
        _set(db, item, "pending", error=error, send_after=nxt)
    else:
        _set(db, item, "failed", error=error)


async def _process_one(db: Session, item, now: datetime, users: Dict[str, Any], AgentContact, User) -> bool:
    if item.expires_at is not None and now >= item.expires_at:
        _set(db, item, "skipped", error="Опоздало: время для этого сообщения прошло")
        return False
    contact = db.query(AgentContact).filter(AgentContact.id == item.contact_id).first() if item.contact_id else None
    if contact is None:
        _set(db, item, "skipped", error="Клиент удалён")
        return False
    if _spam_blocked(contact):
        _set(db, item, "skipped", error="Бот с клиентом выключен навсегда")
        return False
    marketing = item.kind == "marketing"
    if marketing and (contact.marketing_opt_out_at is not None or contact.status == "do_not_call"):
        _set(db, item, "skipped", error="Клиент отказался от рассылок")
        return False
    if not _window_ok(item.kind, now):
        nxt = next_window(item.kind, now)
        if item.expires_at is not None and nxt >= item.expires_at:
            _set(db, item, "skipped", error="Не успели в часы отправки")
        else:
            _set(db, item, "pending", send_after=nxt, refund_attempt=True)
        return False
    uid = str(item.user_id)
    if uid not in users:
        users[uid] = db.query(User).filter(User.id == item.user_id).first()
    user = users[uid]
    if user is None or not user.has_agent_chat_access():
        _set(db, item, "skipped", error="Нет доступа к боту по тарифу")
        return False
    burst = db.execute(text("SELECT count(*) FROM notification_queue WHERE user_id = :u AND status = 'sent' "
                            "AND sent_at > :t"), {"u": uid, "t": now - BURST_WINDOW}).scalar() or 0
    if burst >= SEND_BURST:
        _set(db, item, "pending", send_after=now + timedelta(minutes=10), refund_attempt=True)
        return False
    if marketing:
        lim = _limits(db, item.user_id)
        recent = db.execute(text(
            "SELECT 1 FROM notification_queue WHERE contact_id = :c AND kind = 'marketing' AND status = 'sent' "
            "AND sent_at > :t AND id <> :id LIMIT 1"),
            {"c": str(contact.id), "t": now - timedelta(days=lim["gap_days"]), "id": str(item.id)}).first()
        if recent:
            _set(db, item, "skipped", error=f"Этому клиенту уже писали за последние {lim['gap_days']} дн.")
            return False
        day0 = datetime.combine(bs.to_local(now).date(), time(0, 0), bs.BISHKEK).astimezone(timezone.utc)
        today = db.execute(text("SELECT count(*) FROM notification_queue WHERE user_id = :u AND kind = 'marketing' "
                                "AND status = 'sent' AND sent_at >= :d"), {"u": uid, "d": day0}).scalar() or 0
        if today >= lim["max_per_day"]:
            _set(db, item, "pending", send_after=next_window("marketing", day0 + timedelta(days=1)), refund_attempt=True,
                 error=f"Дневной лимит рассылок ({lim['max_per_day']}) — отправим завтра")
            return False
    ok, channel, err = await SENDER(db, user, contact, item.text)
    if ok:
        item.channel = channel
        item.sent_at = now
        item.last_error = None
        _after_sent(db, item)
        _set(db, item, "sent")
        return True
    if err == "no_channel":
        _set(db, item, "skipped", error="У клиента нет мессенджера, куда можно написать")
        return False
    _retry_or_fail(db, item, now, err or "Не отправилось")
    return False


def _after_sent(db: Session, item) -> None:
    """Что отметить после доставки: просьба об оценке → review_asked_at у записи."""
    if item.rule == "review" and item.booking_id:
        db.execute(text("UPDATE bookings SET review_asked_at = :t WHERE id = :id AND review_asked_at IS NULL"),
                   {"t": bs.now_utc(), "id": str(item.booking_id)})


# ───────────────────────────── журнал отправок ─────────────────────────────

def listing(db: Session, user_id, *, status: Optional[str] = None, rule: Optional[str] = None,
            contact_id: Optional[str] = None, before: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
    from backend.models.agent_contact import AgentContact
    from backend.models.automation import NotificationQueue
    q = db.query(NotificationQueue, AgentContact.name, AgentContact.phone).outerjoin(
        AgentContact, AgentContact.id == NotificationQueue.contact_id).filter(NotificationQueue.user_id == _uuid(user_id))
    if status == "problems":
        q = q.filter(NotificationQueue.status.in_(("failed", "skipped")))
    elif status in STATUS_TITLES:
        q = q.filter(NotificationQueue.status == status)
    if rule:
        q = q.filter(NotificationQueue.rule == rule)
    if contact_id:
        try:
            q = q.filter(NotificationQueue.contact_id == _uuid(contact_id))
        except ValueError:
            return {"items": [], "next": None}
    if before:
        # курсор «ISO|id»: строки одной транзакции имеют одинаковое время — без id они бы терялись или повторялись
        ts_raw, _, id_raw = before.replace(" ", "+").partition("|")
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            from sqlalchemy import and_, or_
            if id_raw:
                q = q.filter(or_(NotificationQueue.created_at < ts,
                                 and_(NotificationQueue.created_at == ts, NotificationQueue.id < _uuid(id_raw))))
            else:
                q = q.filter(NotificationQueue.created_at < ts)
        except ValueError:
            pass
    rows = (q.order_by(NotificationQueue.created_at.desc(), NotificationQueue.id.desc())
            .limit(max(1, min(limit, 200)) + 1).all())
    more = len(rows) > limit
    rows = rows[:limit]
    from backend.services.salon_clients import real_phone
    items = []
    for it, name, phone in rows:
        items.append({
            "id": str(it.id), "rule": it.rule, "kind": it.kind, "lang": it.lang, "text": it.text, "status": it.status,
            "status_title": STATUS_TITLES.get(it.status, it.status), "attempts": it.attempts,
            "channel": it.channel, "channel_title": CHANNEL_TITLES.get(it.channel or "", None),
            "error": it.last_error, "contact_id": str(it.contact_id) if it.contact_id else None,
            "client": name, "phone": real_phone(phone),
            "created_at": it.created_at.isoformat() if it.created_at else None,
            "send_after": it.send_after.isoformat() if it.send_after else None,
            "sent_at": it.sent_at.isoformat() if it.sent_at else None,
            "booking_id": str(it.booking_id) if it.booking_id else None,
        })
    return {"items": items, "next": (items[-1]["created_at"] + "|" + items[-1]["id"]) if more and items else None}


def stats(db: Session, user_id, days: int = 30) -> Dict[str, Dict[str, int]]:
    rows = db.execute(text("SELECT rule, status, count(*) FROM notification_queue WHERE user_id = :u AND created_at > :t "
                           "GROUP BY 1, 2"), {"u": str(user_id), "t": bs.now_utc() - timedelta(days=days)}).fetchall()
    out: Dict[str, Dict[str, int]] = {}
    for rule, status, n in rows:
        out.setdefault(rule, {})[status] = int(n)
    return out


def retry(db: Session, user_id, item_id: str) -> bool:
    res = db.execute(text("UPDATE notification_queue SET status = 'pending', attempts = 0, send_after = now(), "
                          "last_error = NULL, lease_until = NULL WHERE id = :id AND user_id = :u "
                          "AND status IN ('failed', 'skipped', 'cancelled') RETURNING id"),
                     {"id": item_id, "u": str(user_id)}).first()
    db.commit()
    return res is not None


def cancel(db: Session, user_id, item_id: str) -> bool:
    res = db.execute(text("UPDATE notification_queue SET status = 'cancelled', lease_until = NULL WHERE id = :id "
                          "AND user_id = :u AND status = 'pending' RETURNING id"),
                     {"id": item_id, "u": str(user_id)}).first()
    db.commit()
    return res is not None


_last_cleanup = 0.0


def cleanup(db: Session, keep_days: int = 180, force: bool = False) -> int:
    """Журнал отправок старше keep_days — удалить (порцией, не чаще раза в час на воркер)."""
    global _last_cleanup
    import time as _t
    if not force and _t.monotonic() - _last_cleanup < 3600:
        return 0
    _last_cleanup = _t.monotonic()
    res = db.execute(text("DELETE FROM notification_queue WHERE id IN (SELECT id FROM notification_queue "
                          "WHERE created_at < :t LIMIT 2000)"), {"t": bs.now_utc() - timedelta(days=keep_days)})
    db.commit()
    return res.rowcount or 0
