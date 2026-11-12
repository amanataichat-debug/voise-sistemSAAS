"""
Инбокс (ветка 0411-inbox): один экран для переписки с клиентами во всех каналах.

Данных своих почти нет — инбокс объединяет то, что уже пишут каналы:
  agent_whatsapp_messages  (Evolution API, webhook)
  agent_telegram_messages  (личный Telegram, поллер Telethon)
  agent_instagram_messages (DM бизнес-аккаунта, поллер Composio)
и звонки агента (agent_calls + sip_calls с записью).

Один диалог = один КЛИЕНТ (agent_contacts), а не один канал: Айбек пишет и в
WhatsApp, и в Telegram — это одна строка списка и одна лента.

Отправка владельца идёт теми же функциями, что у агента (fn_*_send_message в
agent_tools), но с sent_by="owner" и без анти-спам лимитов агента (их задача —
сдерживать робота, а не человека; суточный лимит НОВЫХ чатов WhatsApp остаётся —
он бережёт номер от бана). Сообщение сохраняется с id канала сразу после
отправки, поэтому webhook/поллер узнают его и второй строкой не запишут.

После сообщения владельца бот молчит с этим клиентом во всех каналах
(agent_pause.on_owner_message): по умолчанию — настройка канала (30 мин),
либо срок, выбранный в окне ввода («на 2 часа», «навсегда»).
"""

import asyncio
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from backend.core.logging import get_logger
from backend.core.timezone_utils import iso_utc

logger = get_logger(__name__)

CHANNELS = ("whatsapp", "telegram", "instagram")
CHANNEL_LABELS = {"whatsapp": "WhatsApp", "telegram": "Telegram", "instagram": "Instagram"}

# Языки помощника (перевод исходящих и входящих). Владелец выбирает в настройках,
# какие показывать в меню «Язык».
LANGUAGES = [
    ("ru", "Русский"), ("ky", "Кыргызский"), ("en", "Английский"), ("zh", "Китайский"),
    ("tr", "Турецкий"), ("kk", "Казахский"), ("uz", "Узбекский"), ("tg", "Таджикский"),
    ("ar", "Арабский"), ("de", "Немецкий"), ("fr", "Французский"), ("es", "Испанский"),
    ("ko", "Корейский"), ("ja", "Японский"), ("fa", "Персидский"), ("hi", "Хинди"),
]
LANGUAGE_NAMES = dict(LANGUAGES)

# Сколько бот молчит после ответа владельца: "channel" — настройка канала,
# минуты или -1 (до ручного включения).
PAUSE_OPTIONS = ("channel", 30, 60, 120, 240, 480, 1440, -1)

TONES = ("neutral", "friendly", "formal")

DEFAULT_SETTINGS: Dict[str, Any] = {
    "channels": {"whatsapp": True, "instagram": True, "telegram": True},
    "my_language": "ru",
    "languages": ["ru", "ky", "en", "zh", "tr"],
    "auto_translate_incoming": False,
    "default_pause": "channel",
    "tone": "neutral",
    "enter_to_send": True,
    "sound": True,
    "desktop_notify": False,
}

# Помощник: быстрая многоязычная модель (кыргызский, китайский) через OpenRouter.
import os
ASSIST_MODEL = os.getenv("INBOX_ASSIST_MODEL", "google/gemini-3.6-flash")
ASSIST_MAX_CHARS = 4000

# Непрочитанные для диалога, который владелец ни разу не открывал: только свежие
# входящие (иначе в первый день вся история была бы «непрочитанной»).
UNREAD_DEFAULT_WINDOW = timedelta(hours=24)

# Instagram (Meta) пропускает ответ бизнеса только в течение 24 ч после
# последнего сообщения клиента.
IG_REPLY_WINDOW = timedelta(hours=24)

FILE_ID_RE = re.compile(r"\s*\[file_id=[^\]]*\]")


# ============================================================================
# НАСТРОЙКИ
# ============================================================================

def get_settings(db: Session, user_id) -> Dict[str, Any]:
    from backend.models.inbox import InboxSettings
    row = db.query(InboxSettings).filter(InboxSettings.user_id == user_id).first()
    data = dict(DEFAULT_SETTINGS)
    data["channels"] = dict(DEFAULT_SETTINGS["channels"])
    if row is not None and isinstance(row.data, dict):
        for k, v in row.data.items():
            if k == "channels" and isinstance(v, dict):
                data["channels"].update({c: bool(v[c]) for c in CHANNELS if c in v})
            elif k in DEFAULT_SETTINGS:
                data[k] = v
    return data


def clean_settings(patch: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    """Проверить и наложить изменения. Неверное значение → ValueError(код)."""
    out = dict(current)
    out["channels"] = dict(current.get("channels") or {})
    for k, v in (patch or {}).items():
        if v is None or k not in DEFAULT_SETTINGS:
            continue
        if k == "channels":
            if not isinstance(v, dict):
                raise ValueError("invalid_channels")
            out["channels"].update({c: bool(v[c]) for c in CHANNELS if c in v})
        elif k == "my_language":
            if v not in LANGUAGE_NAMES:
                raise ValueError("invalid_language")
            out[k] = v
        elif k == "languages":
            if not isinstance(v, list) or not v or any(x not in LANGUAGE_NAMES for x in v):
                raise ValueError("invalid_languages")
            out[k] = list(dict.fromkeys(v))[:12]
        elif k == "default_pause":
            if v not in PAUSE_OPTIONS:
                raise ValueError("invalid_pause")
            out[k] = v
        elif k == "tone":
            if v not in TONES:
                raise ValueError("invalid_tone")
            out[k] = v
        else:  # булевы переключатели
            out[k] = bool(v)
    return out


def save_settings(db: Session, user_id, patch: Dict[str, Any]) -> Dict[str, Any]:
    from backend.models.inbox import InboxSettings
    data = clean_settings(patch, get_settings(db, user_id))
    row = db.query(InboxSettings).filter(InboxSettings.user_id == user_id).first()
    if row is None:
        row = InboxSettings(user_id=user_id, data=data)
        db.add(row)
    else:
        row.data = data
    db.commit()
    return data


# ============================================================================
# АГЕНТЫ И КАНАЛЫ
# ============================================================================

def user_agents(db: Session, user) -> list:
    from backend.models.agent_config import AgentConfig
    return (
        db.query(AgentConfig)
        .filter(AgentConfig.user_id == user.id)
        .order_by(AgentConfig.created_at.asc())
        .all()
    )


def _pause_value(row) -> int:
    from backend.services import agent_pause
    return agent_pause.owner_pause_minutes(row)


def channel_status(db: Session, agent) -> Dict[str, Dict[str, Any]]:
    """Состояние каналов агента: подключён ли, как отвечает бот, пауза после владельца."""
    from backend.models.agent_connector import AgentConnector
    from backend.services import telegram_user_service, whatsapp_service

    out: Dict[str, Dict[str, Any]] = {}

    wa = whatsapp_service.get_account_for_agent(db, agent.id, require_connected=False)
    out["whatsapp"] = {
        "connected": bool(wa is not None and wa.is_connected() and whatsapp_service.is_configured()),
        "exists": wa is not None,
        "account": (("+" + wa.wa_phone) if wa is not None and wa.wa_phone else None),
        "auto_reply_enabled": bool(wa.auto_reply_enabled) if wa is not None else None,
        "reply_scope": (wa.reply_scope or "contacts") if wa is not None else None,
        "owner_pause_minutes": _pause_value(wa) if wa is not None else 30,
        "reply_delay_seconds": int(wa.reply_delay_seconds or 15) if wa is not None else 15,
        "status": wa.status if wa is not None else None,
    }

    tg = telegram_user_service.get_account_for_agent(db, agent.id, require_connected=False)
    out["telegram"] = {
        "connected": bool(tg is not None and tg.is_connected() and telegram_user_service.is_configured()),
        "exists": tg is not None,
        "account": ((("@" + tg.tg_username) if tg.tg_username else tg.tg_first_name) if tg is not None else None),
        "auto_reply_enabled": bool(tg.auto_reply_enabled) if tg is not None else None,
        "reply_scope": (tg.reply_scope or "contacts") if tg is not None else None,
        "owner_pause_minutes": _pause_value(tg) if tg is not None else 30,
        "reply_delay_seconds": int(tg.reply_delay_seconds or 15) if tg is not None else 15,
        "status": tg.status if tg is not None else None,
    }

    ig = db.query(AgentConnector).filter(
        AgentConnector.agent_config_id == agent.id,
        AgentConnector.toolkit == "instagram",
    ).first()
    out["instagram"] = {
        "connected": bool(ig is not None and ig.status == "connected"),
        "exists": ig is not None,
        "account": None,
        "auto_reply_enabled": None,
        "reply_scope": None,
        "owner_pause_minutes": _pause_value(ig) if ig is not None else 30,
        "reply_delay_seconds": int(getattr(ig, "reply_delay_seconds", None) or 15) if ig is not None else 15,
        "status": ig.status if ig is not None else None,
    }
    return out


def pause_row_for(db: Session, agent_config_id, channel: str):
    """Строка подключения канала — источник настройки owner_pause_minutes."""
    from backend.models.agent_connector import AgentConnector
    from backend.services import telegram_user_service, whatsapp_service
    if channel == "whatsapp":
        return whatsapp_service.get_account_for_agent(db, agent_config_id, require_connected=False)
    if channel == "telegram":
        return telegram_user_service.get_account_for_agent(db, agent_config_id, require_connected=False)
    if channel == "instagram":
        return db.query(AgentConnector).filter(
            AgentConnector.agent_config_id == agent_config_id,
            AgentConnector.toolkit == "instagram",
        ).first()
    return None


# ============================================================================
# СПИСОК ДИАЛОГОВ
# ============================================================================

_UNION_PARTS = {
    "whatsapp": (
        "SELECT w.agent_contact_id AS cid, 'whatsapp' AS ch, w.direction AS direction, "
        "w.sent_by AS sent_by, w.body AS body, w.created_at AS created_at "
        "FROM agent_whatsapp_messages w JOIN agent_contacts c0 ON c0.id = w.agent_contact_id "
        "WHERE c0.user_id = :uid"
    ),
    "telegram": (
        "SELECT t.agent_contact_id AS cid, 'telegram' AS ch, t.direction AS direction, "
        "t.sent_by AS sent_by, t.body AS body, t.created_at AS created_at "
        "FROM agent_telegram_messages t JOIN agent_contacts c0 ON c0.id = t.agent_contact_id "
        "WHERE c0.user_id = :uid"
    ),
    "instagram": (
        "SELECT i.agent_contact_id AS cid, 'instagram' AS ch, i.direction AS direction, "
        "i.sent_by AS sent_by, i.body AS body, i.created_at AS created_at "
        "FROM agent_instagram_messages i JOIN agent_contacts c0 ON c0.id = i.agent_contact_id "
        "WHERE c0.user_id = :uid"
    ),
}

FOLDERS = ("all", "waiting", "unread", "paused", "no_card")


def _base_cte(channels: List[str]) -> str:
    union = " UNION ALL ".join(_UNION_PARTS[c] for c in channels)
    return (
        "WITH m AS (" + union + "), "
        # Непрочитанное = входящие после того, как владелец открыл диалог ИЛИ сам ответил
        # (в инбоксе или с телефона) — ответил, значит, видел.
        "own AS (SELECT m.cid, max(m.created_at) AS t FROM m "
        "  WHERE m.direction = 'outbound' AND m.sent_by = 'owner' GROUP BY m.cid), "
        "agg AS ("
        "  SELECT m.cid, max(m.created_at) AS last_at, "
        "    max(m.created_at) FILTER (WHERE m.direction = 'inbound') AS last_in, "
        "    count(*) FILTER (WHERE m.direction = 'inbound' AND m.created_at > "
        "      GREATEST(COALESCE(r.last_read_at, :unread_since), COALESCE(own.t, :unread_since))) AS unread, "
        "    array_agg(DISTINCT m.ch) AS chans "
        "  FROM m LEFT JOIN inbox_read_state r ON r.agent_contact_id = m.cid AND r.user_id = :uid "
        "  LEFT JOIN own ON own.cid = m.cid "
        "  GROUP BY m.cid"
        "), "
        "last AS ("
        "  SELECT DISTINCT ON (m.cid) m.cid, m.ch, m.direction, m.sent_by, m.body "
        "  FROM m ORDER BY m.cid, m.created_at DESC"
        ") "
    )


def _filters(params: Dict[str, Any], *, agent_id=None, stage=None, channel=None, q=None) -> List[str]:
    where = ["c.user_id = :uid"]
    if agent_id:
        where.append("c.agent_config_id = :aid")
        params["aid"] = str(agent_id)
    if stage:
        where.append("c.status = :stage")
        params["stage"] = stage
    if channel:
        where.append(":ch = ANY(agg.chans)")
        params["ch"] = channel
    if q:
        params["q"] = f"%{q.strip()}%"
        params["qd"] = "%" + "".join(ch for ch in q if ch.isdigit()) + "%" if any(ch.isdigit() for ch in q) else None
        cond = ["c.name ILIKE :q", "c.company ILIKE :q", "c.tags ILIKE :q",
                "c.id IN (SELECT cid FROM m WHERE body ILIKE :q)"]
        if params["qd"]:
            cond.append("regexp_replace(c.phone, '\\D', '', 'g') LIKE :qd")
        where.append("(" + " OR ".join(cond) + ")")
    return where


def _folder_cond(folder: str) -> Optional[str]:
    return {
        "waiting": "last.direction = 'inbound'",
        "unread": "agg.unread > 0",
        "paused": "c.ai_paused_until IS NOT NULL AND c.ai_paused_until > :now",
    }.get(folder)


def _preview(body: Optional[str]) -> str:
    text = FILE_ID_RE.sub("", body or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:140]


def list_dialogs(
    db: Session, user, *, folder: str = "all", channel: Optional[str] = None,
    stage: Optional[str] = None, q: Optional[str] = None, agent_id=None,
    before: Optional[datetime] = None, limit: int = 40, settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Диалоги владельца (по клиентам), свежие сверху, + счётчики папок."""
    from backend.models.agent_contact import AgentContact
    from backend.models.task import Task, TaskStatus
    from backend.services import agent_pause

    settings = settings or get_settings(db, user.id)
    channels = [c for c in CHANNELS if (settings.get("channels") or {}).get(c, True)]
    if channel and channel not in channels:
        channels = []
    now = datetime.utcnow()

    counts = {"all": 0, "waiting": 0, "unread": 0, "paused": 0, "no_card": 0}
    counts["no_card"] = count_unlinked(db, user, agent_id=agent_id) if "whatsapp" in channels else 0
    if folder == "no_card":
        items = list_unlinked(db, user, agent_id=agent_id, limit=limit) if "whatsapp" in channels else []
        return {"items": items, "counts": _counts(db, user, channels, agent_id, stage, channel, q, now, counts),
                "has_more": False}
    if not channels:
        return {"items": [], "counts": counts, "has_more": False}

    params: Dict[str, Any] = {"uid": str(user.id), "unread_since": now - UNREAD_DEFAULT_WINDOW,
                              "now": now, "limit": int(limit) + 1}
    where = _filters(params, agent_id=agent_id, stage=stage, channel=channel, q=q)
    cond = _folder_cond(folder)
    if cond:
        where.append(cond)
    if before is not None:
        where.append("agg.last_at < :before")
        params["before"] = before

    sql = (
        _base_cte(channels)
        + "SELECT c.id, agg.last_at, agg.last_in, agg.unread, agg.chans, "
          "last.ch AS last_ch, last.direction AS last_dir, last.sent_by AS last_by, last.body AS last_body "
          "FROM agg JOIN last ON last.cid = agg.cid JOIN agent_contacts c ON c.id = agg.cid "
          "WHERE " + " AND ".join(where) + " "
          "ORDER BY agg.last_at DESC, c.id LIMIT :limit"
    )
    rows = db.execute(sql_text(sql), params).fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]

    ids = [r.id for r in rows]
    contacts = {c.id: c for c in db.query(AgentContact).filter(AgentContact.id.in_(ids)).all()} if ids else {}
    tasks_by: Dict[Any, List[Any]] = {}
    if ids:
        for t in (
            db.query(Task)
            .filter(Task.agent_contact_id.in_(ids), Task.status == TaskStatus.SCHEDULED)
            .order_by(Task.scheduled_time.asc())
            .all()
        ):
            tasks_by.setdefault(t.agent_contact_id, []).append(t)

    items = []
    for r in rows:
        c = contacts.get(r.id)
        if c is None:
            continue
        tasks = tasks_by.get(r.id, [])
        next_task = tasks[0] if tasks else None
        items.append({
            "key": f"contact:{c.id}",
            "contact_id": str(c.id),
            "agent_id": str(c.agent_config_id) if c.agent_config_id else None,
            "name": display_name(c),
            "phone": real_phone(c.phone),
            "company": c.company,
            "stage": c.status,
            "tags": [t.strip() for t in (c.tags or "").split(",") if t.strip()],
            "channels": sorted(r.chans or []),
            "last_at": iso_utc(r.last_at),
            "last_in_at": iso_utc(r.last_in),
            "last_channel": r.last_ch,
            "last_direction": r.last_dir,
            "last_by": _who(r.last_dir, r.last_by),
            "preview": _preview(r.last_body),
            "unread": int(r.unread or 0),
            "waiting": r.last_dir == "inbound",
            "paused": agent_pause.is_paused(c, now),
            "paused_until": iso_utc(c.ai_paused_until) if agent_pause.is_paused(c, now) else None,
            "paused_forever": bool(agent_pause.is_paused(c, now) and c.ai_paused_until.year >= 9999),
            "tasks_count": len(tasks),
            "next_task": _task_dict(next_task) if next_task is not None else None,
        })
    return {
        "items": items,
        "counts": _counts(db, user, channels, agent_id, stage, channel, q, now, counts),
        "has_more": has_more,
    }


def _counts(db, user, channels, agent_id, stage, channel, q, now, counts) -> Dict[str, int]:
    if not channels:
        return counts
    params: Dict[str, Any] = {"uid": str(user.id), "unread_since": now - UNREAD_DEFAULT_WINDOW, "now": now}
    where = _filters(params, agent_id=agent_id, stage=stage, channel=channel, q=q)
    sql = (
        _base_cte(channels)
        + "SELECT count(*) AS all_n, "
          "count(*) FILTER (WHERE last.direction = 'inbound') AS waiting_n, "
          "count(*) FILTER (WHERE agg.unread > 0) AS unread_n, "
          "COALESCE(sum(agg.unread), 0) AS unread_msgs, "
          "count(*) FILTER (WHERE c.ai_paused_until IS NOT NULL AND c.ai_paused_until > :now) AS paused_n "
          "FROM agg JOIN last ON last.cid = agg.cid JOIN agent_contacts c ON c.id = agg.cid "
          "WHERE " + " AND ".join(where)
    )
    row = db.execute(sql_text(sql), params).fetchone()
    out = dict(counts)
    if row is not None:
        out.update({"all": int(row.all_n or 0), "waiting": int(row.waiting_n or 0),
                    "unread": int(row.unread_n or 0), "unread_messages": int(row.unread_msgs or 0),
                    "paused": int(row.paused_n or 0)})
    return out


def latest_activity(db: Session, user) -> Optional[datetime]:
    """Время последнего сообщения во всех каналах — дешёвая проверка «что-то новое?»."""
    params = {"uid": str(user.id)}
    sql = (
        "SELECT max(x) FROM ("
        "SELECT max(w.created_at) AS x FROM agent_whatsapp_messages w JOIN agent_contacts c ON c.id = w.agent_contact_id WHERE c.user_id = :uid "
        "UNION ALL SELECT max(t.created_at) FROM agent_telegram_messages t JOIN agent_contacts c ON c.id = t.agent_contact_id WHERE c.user_id = :uid "
        "UNION ALL SELECT max(i.created_at) FROM agent_instagram_messages i JOIN agent_contacts c ON c.id = i.agent_contact_id WHERE c.user_id = :uid "
        "UNION ALL SELECT max(m.created_at) FROM agent_whatsapp_messages m JOIN agent_whatsapp_accounts a ON a.id = m.account_id "
        "  WHERE a.user_id = :uid AND m.agent_contact_id IS NULL"
        ") s"
    )
    return db.execute(sql_text(sql), params).scalar()


# ---------- Без карточки (WhatsApp: писали незнакомые, бот им не отвечает) ----------

def _unlinked_query(db: Session, user, agent_id=None):
    from backend.models.agent_whatsapp import AgentWhatsAppAccount, AgentWhatsAppChat
    q = (
        db.query(AgentWhatsAppChat, AgentWhatsAppAccount)
        .join(AgentWhatsAppAccount, AgentWhatsAppAccount.id == AgentWhatsAppChat.account_id)
        .filter(AgentWhatsAppAccount.user_id == user.id, AgentWhatsAppChat.agent_contact_id.is_(None))
    )
    if agent_id:
        q = q.filter(AgentWhatsAppAccount.agent_config_id == agent_id)
    return q


def count_unlinked(db: Session, user, agent_id=None) -> int:
    from backend.models.agent_whatsapp import AgentWhatsAppChat, AgentWhatsAppMessage
    try:
        return (
            _unlinked_query(db, user, agent_id)
            .filter(db.query(AgentWhatsAppMessage.id).filter(
                AgentWhatsAppMessage.chat_id == AgentWhatsAppChat.id).exists())
            .count()
        )
    except Exception as e:
        logger.warning(f"[INBOX] count_unlinked failed: {e}")
        db.rollback()
        return 0


def list_unlinked(db: Session, user, agent_id=None, limit: int = 40) -> List[Dict[str, Any]]:
    from backend.models.agent_whatsapp import AgentWhatsAppChat, AgentWhatsAppMessage
    out = []
    rows = _unlinked_query(db, user, agent_id).order_by(AgentWhatsAppChat.updated_at.desc()).limit(200).all()
    for chat, account in rows:
        last = (
            db.query(AgentWhatsAppMessage)
            .filter(AgentWhatsAppMessage.chat_id == chat.id)
            .order_by(AgentWhatsAppMessage.created_at.desc())
            .first()
        )
        if last is None:
            continue
        out.append({
            "key": f"wa_chat:{chat.id}",
            "contact_id": None,
            "chat_id": str(chat.id),
            "agent_id": str(account.agent_config_id),
            "name": chat.push_name or (("+" + chat.phone) if chat.phone else "Новый клиент"),
            "phone": ("+" + chat.phone) if chat.phone else None,
            "stage": None, "tags": [], "channels": ["whatsapp"],
            "last_at": iso_utc(last.created_at), "last_channel": "whatsapp",
            "last_direction": last.direction, "last_by": _who(last.direction, last.sent_by),
            "preview": _preview(last.body), "unread": 0,
            "waiting": last.direction == "inbound", "paused": False, "no_card": True,
            "tasks_count": 0, "next_task": None,
        })
    out.sort(key=lambda x: x["last_at"] or "", reverse=True)
    return out[:limit]


def unlinked_timeline(db: Session, user, chat_id) -> Dict[str, Any]:
    from backend.models.agent_whatsapp import AgentWhatsAppMessage
    found = None
    for chat, account in _unlinked_query(db, user).all():
        if str(chat.id) == str(chat_id):
            found = (chat, account)
            break
    if found is None:
        return {}
    chat, account = found
    msgs = (
        db.query(AgentWhatsAppMessage)
        .filter(AgentWhatsAppMessage.chat_id == chat.id)
        .order_by(AgentWhatsAppMessage.created_at.desc())
        .limit(100)
        .all()
    )
    files = _files_for(db, [m.attachment_id for m in msgs])
    items = [_msg_item("whatsapp", m, files, {}) for m in reversed(msgs)]
    return {
        "chat": {"id": str(chat.id), "name": chat.push_name, "phone": ("+" + chat.phone) if chat.phone else None,
                 "agent_id": str(account.agent_config_id)},
        "items": items,
    }


def link_unlinked(db: Session, user, chat_id, contact_id=None, name: Optional[str] = None):
    """Чат WhatsApp без карточки → новая карточка (или существующая). Сообщения чата переходят к ней."""
    from backend.models.agent_contact import AgentContact
    from backend.models.agent_whatsapp import AgentWhatsAppMessage
    found = None
    for chat, account in _unlinked_query(db, user).all():
        if str(chat.id) == str(chat_id):
            found = (chat, account)
            break
    if found is None:
        raise LookupError("not_found")
    chat, account = found
    if contact_id:
        contact = db.query(AgentContact).filter(
            AgentContact.id == contact_id, AgentContact.user_id == user.id,
        ).first()
        if contact is None:
            raise LookupError("contact_not_found")
    else:
        contact = AgentContact(
            agent_config_id=account.agent_config_id,
            user_id=user.id,
            phone=("+" + chat.phone) if chat.phone else f"wa:{chat.remote_jid.split('@')[0]}",
            name=(name or "").strip() or chat.push_name,
            status="new",
            memory={},
        )
        db.add(contact)
        db.flush()
    chat.agent_contact_id = contact.id
    db.query(AgentWhatsAppMessage).filter(
        AgentWhatsAppMessage.chat_id == chat.id,
        AgentWhatsAppMessage.agent_contact_id.is_(None),
    ).update({"agent_contact_id": contact.id}, synchronize_session=False)
    db.commit()
    logger.info(f"[INBOX] WhatsApp chat {chat.id} linked to contact {contact.id}")
    return contact


# ============================================================================
# ЛЕНТА ДИАЛОГА
# ============================================================================

def _who(direction: Optional[str], sent_by: Optional[str]) -> str:
    if (direction or "inbound") != "outbound":
        return "client"
    return "owner" if sent_by == "owner" else "bot"


def real_phone(phone: Optional[str]) -> Optional[str]:
    """Номер карточки без служебных tg:/ig:/wa: (у клиента из мессенджера номера может не быть)."""
    if not phone or phone.startswith(("tg:", "ig:", "wa:")):
        return None
    return phone


def display_name(contact) -> str:
    name = (contact.name or "").strip()
    if name:
        return name
    return real_phone(contact.phone) or "Клиент без имени"


def _files_for(db: Session, ids) -> Dict[str, Any]:
    from backend.models.agent_file import AgentFile
    ids = [i for i in {str(x) for x in ids if x}]
    if not ids:
        return {}
    try:
        return {str(f.id): f for f in db.query(AgentFile).filter(AgentFile.id.in_(ids)).all()}
    except Exception:
        db.rollback()
        return {}


def _file_dict(f) -> Optional[Dict[str, Any]]:
    from backend.services import agent_media_service
    if f is None:
        return None
    return {
        "id": str(f.id),
        "kind": f.kind,
        "filename": f.filename,
        "mime": f.mime_type,
        "size": f.size_bytes,
        "url": agent_media_service.file_link(f, expires_seconds=6 * 3600),
        "text": (f.extracted_text or "")[:600] if getattr(f, "extracted_text", None) else None,
    }


def _clean_body(body: str, has_file: bool) -> str:
    text = FILE_ID_RE.sub("", body or "")
    if has_file:
        # «📎 Отправлен файл «x»» и подписи-заглушки вложений дублируют карточку файла
        text = re.sub(r"^📎 Отправлен файл «[^»]*»( \(ссылкой\))?\n?", "", text)
        text = re.sub(r"\n?(🎤 Голосовое сообщение|🎵 Аудио|🎥 Видео|🖼 Изображение|📄 Документ|📎 Файл)( \(обрабатывается…\))?$", "", text)
    return text.strip()


def _msg_item(channel: str, m, files: Dict[str, Any], tr: Dict[Tuple[str, str], Any]) -> Dict[str, Any]:
    f = files.get(str(m.attachment_id)) if m.attachment_id else None
    body = m.body or ""
    is_voice = body.startswith("🎤 Голосовое: ")
    text = body[len("🎤 Голосовое: "):].strip() if is_voice else _clean_body(body, f is not None)
    key = str(m.id)
    translation = tr.get((key, "translation"))
    original = tr.get((key, "original"))
    return {
        "id": key,
        "type": "message",
        "channel": channel,
        "direction": m.direction or "inbound",
        "who": _who(m.direction, getattr(m, "sent_by", None)),
        "text": text,
        "voice": is_voice,
        "ts": iso_utc(getattr(m, "sent_at", None) or m.created_at),
        "attachment": _file_dict(f),
        "translation": ({"text": translation.text, "language": translation.language} if translation else None),
        "original": ({"text": original.text, "language": original.language} if original else None),
    }


def _translations_for(db: Session, ids: List[str]) -> Dict[Tuple[str, str], Any]:
    from backend.models.inbox import InboxTranslation
    if not ids:
        return {}
    try:
        rows = db.query(InboxTranslation).filter(InboxTranslation.message_id.in_(ids)).all()
    except Exception:
        db.rollback()
        return {}
    return {(str(r.message_id), r.kind): r for r in rows}


def timeline(db: Session, contact, *, before: Optional[datetime] = None, limit: int = 60,
             settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Сообщения всех каналов + звонки, старые → новые. before — для «показать раньше»."""
    from backend.models.agent_call import AgentCall
    from backend.models.agent_instagram import AgentInstagramMessage
    from backend.models.agent_telegram_account import AgentTelegramMessage
    from backend.models.agent_whatsapp import AgentWhatsAppMessage
    from backend.services.agent_call_finalizer import call_outcome, sip_calls_for

    settings = settings or {}
    enabled = settings.get("channels") or {}
    models = [("whatsapp", AgentWhatsAppMessage), ("telegram", AgentTelegramMessage),
              ("instagram", AgentInstagramMessage)]
    fetched: List[Tuple[str, Any]] = []
    for ch, model in models:
        if enabled and not enabled.get(ch, True):
            continue
        q = db.query(model).filter(model.agent_contact_id == contact.id)
        if before is not None:
            q = q.filter(model.created_at < before)
        for m in q.order_by(model.created_at.desc()).limit(limit + 1).all():
            fetched.append((ch, m))
    fetched.sort(key=lambda x: x[1].created_at, reverse=True)
    has_more = len(fetched) > limit
    fetched = fetched[:limit]
    oldest = fetched[-1][1].created_at if (fetched and has_more) else None

    files = _files_for(db, [m.attachment_id for _, m in fetched])
    tr = _translations_for(db, [str(m.id) for _, m in fetched])
    items = [_msg_item(ch, m, files, tr) for ch, m in fetched]

    # Звонки в том же окне времени
    cq = db.query(AgentCall).filter(AgentCall.agent_contact_id == contact.id)
    if before is not None:
        cq = cq.filter(AgentCall.created_at < before)
    if oldest is not None:
        cq = cq.filter(AgentCall.created_at >= oldest)
    calls = [c for c in cq.order_by(AgentCall.created_at.desc()).limit(30).all()
             if (c._resolve_channel() if hasattr(c, "_resolve_channel") else "call") == "call"]
    sip = sip_calls_for(db, calls)
    for c in calls:
        s = sip.get(str(c.call_session_id))
        transcript = c.transcript if c.transcript and c.transcript != "(Транскрипт недоступен)" else None
        items.append({
            "id": str(c.id),
            "type": "call",
            "channel": "call",
            "direction": c.direction or "outbound",
            "ts": iso_utc(c.started_at or c.created_at),
            "duration": (s.duration_sec if s is not None and s.duration_sec else c.duration_seconds) or 0,
            "outcome": call_outcome(c, s),
            "decision": c.post_call_decision,
            "record_url": s.recording_url if s is not None else None,
            "transcript": (transcript or "")[:6000] or None,
            "status": c.status,
        })
    items.sort(key=lambda x: x.get("ts") or "")
    return {"items": items, "has_more": has_more,
            "before": iso_utc(fetched[-1][1].created_at) if fetched else None}


def _task_dict(t) -> Dict[str, Any]:
    return {
        "id": str(t.id),
        "title": t.title,
        "description": t.description,
        "channel": t.channel or "call",
        "created_by": getattr(t, "created_by", None) or "agent",
        "scheduled_time": iso_utc(t.scheduled_time),
        "status": t.status.value if hasattr(t.status, "value") else t.status,
    }


def contact_card(db: Session, user, contact) -> Dict[str, Any]:
    """Правая колонка: карточка, что узнал бот, задачи, каналы клиента."""
    from backend.models.agent_config import AgentConfig
    from backend.models.agent_instagram import AgentInstagramConversation
    from backend.models.agent_telegram_account import AgentTelegramDialog
    from backend.models.agent_whatsapp import AgentWhatsAppChat
    from backend.models.agent_file import AgentFile
    from backend.models.task import Task, TaskStatus

    data = contact.to_dict()
    data["display_name"] = display_name(contact)
    data["real_phone"] = real_phone(contact.phone)
    tasks = (
        db.query(Task)
        .filter(Task.agent_contact_id == contact.id, Task.status == TaskStatus.SCHEDULED)
        .order_by(Task.scheduled_time.asc())
        .all()
    )
    data["tasks"] = [_task_dict(t) for t in tasks]

    handles: Dict[str, Any] = {}
    wa = db.query(AgentWhatsAppChat).filter(AgentWhatsAppChat.agent_contact_id == contact.id).first()
    if wa is not None:
        handles["whatsapp"] = {"phone": ("+" + wa.phone) if wa.phone else None, "name": wa.push_name}
    tg = db.query(AgentTelegramDialog).filter(AgentTelegramDialog.agent_contact_id == contact.id).first()
    if tg is not None:
        handles["telegram"] = {"username": tg.tg_username, "name": tg.tg_name}
    ig = db.query(AgentInstagramConversation).filter(AgentInstagramConversation.agent_contact_id == contact.id).first()
    if ig is not None:
        handles["instagram"] = {"username": ig.ig_username}
    data["handles"] = handles

    # Документы, которые бот собрал для клиента (КП, счета)
    docs = (
        db.query(AgentFile)
        .filter(AgentFile.agent_contact_id == contact.id, AgentFile.source == "generated")
        .order_by(AgentFile.created_at.desc())
        .limit(5)
        .all()
    )
    data["documents"] = [_file_dict(f) for f in docs]

    agent = db.query(AgentConfig).filter(AgentConfig.id == contact.agent_config_id).first() if contact.agent_config_id else None
    data["agent"] = {"id": str(agent.id), "name": agent.name, "is_active": bool(agent.is_active)} if agent else None
    data["send_channels"] = send_channels(db, contact, agent)
    return data


# Почему бот молчит с клиентом. Раньше все эти причины были молчаливыми: сообщение
# лежало в инбоксе, бот не отвечал, а владелец гадал — кредиты, тариф, выключатель
# или сбой. Порядок = порядок проверок в handle_inbound_* и PostCall.
_INBOUND_DIRECTIONS = ("telegram_inbound", "whatsapp_inbound", "instagram_inbound", "sms_inbound")
_BOT_PAGE = "/static/start-bot.html"


def bot_issues(db: Session, user, contact, agent) -> List[Dict[str, Any]]:
    """Список причин, по которым бот не ответит (или не ответил) этому клиенту."""
    from backend.models.agent_call import AgentCall
    from backend.services.credit_service import CreditService

    full = False
    try:
        full = user.agent_mode() == "full"
    except Exception:
        pass
    bot_page = "/static/agent.html" if full else _BOT_PAGE
    issues: List[Dict[str, Any]] = []

    def add(code, text, action=None, url=None):
        issues.append({"code": code, "text": text, "action": action, "url": url})

    if agent is None:
        add("no_agent", "У клиента нет бота: он не привязан к чат-боту.", "Открыть чат-бот", bot_page)
        return issues
    if not agent.is_active:
        add("agent_off", "Бот выключен и никому не отвечает.", "Включить бота", bot_page)
    try:
        mode = user.agent_mode()
    except Exception:
        mode = "unknown"
    if mode is None:
        add("no_access", "Пробный период или тариф закончился — бот не отвечает клиентам.",
            "Выбрать тариф", "/static/dashboard.html#plans")
    balance = int(getattr(user, "credits_balance", 0) or 0)
    if balance < CreditService.MIN_PRECHECK_BALANCE:
        add("no_credits",
            f"Кредиты бота закончились: {balance} (нужно не меньше {CreditService.MIN_PRECHECK_BALANCE}). "
            "Бот не отвечает, пока их нет.",
            "Кредиты", bot_page + ("" if full else "#s-credits"))

    # Каналы, в которых клиент пишет: отключены или с выключенным автоответом.
    try:
        from backend.models.agent_instagram import AgentInstagramConversation
        from backend.models.agent_telegram_account import AgentTelegramDialog
        from backend.models.agent_whatsapp import AgentWhatsAppChat
        used = []
        if db.query(AgentWhatsAppChat.id).filter(AgentWhatsAppChat.agent_contact_id == contact.id).first():
            used.append(("whatsapp", "WhatsApp"))
        if db.query(AgentTelegramDialog.id).filter(AgentTelegramDialog.agent_contact_id == contact.id).first():
            used.append(("telegram", "Telegram"))
        if db.query(AgentInstagramConversation.id).filter(AgentInstagramConversation.agent_contact_id == contact.id).first():
            used.append(("instagram", "Instagram"))
        if used:
            chs = channel_status(db, agent)
            for key, title in used:
                ch = chs.get(key) or {}
                if ch.get("exists") and not ch.get("connected"):
                    add("channel_off", f"{title} бота отключён — бот не видит новые сообщения в нём. Подключите заново.",
                        "Каналы", bot_page + ("" if full else "#s-channels"))
                elif ch.get("connected") and ch.get("auto_reply_enabled") is False:
                    add("auto_reply_off", f"В {title} выключен автоответ — бот не отвечает в этом канале.",
                        "Каналы", bot_page + ("" if full else "#s-channels"))
    except Exception as e:
        logger.warning(f"[INBOX] каналы для проверки бота не прочитаны: {e}")

    # Последний разбор входящего этого клиента: упал или ответ не ушёл.
    try:
        rows = (
            db.query(AgentCall)
            .filter(AgentCall.agent_contact_id == contact.id)
            .order_by(AgentCall.created_at.desc())
            .limit(5)
            .all()
        )
    except Exception:
        rows = []
    for row in rows:
        log = row.postcall_log if isinstance(row.postcall_log, dict) else {}
        if log.get("call_direction") not in _INBOUND_DIRECTIONS:
            continue
        err = str(log.get("error") or "")
        when = row.created_at
        if err == "blocked_credits":
            if not any(i["code"] == "no_credits" for i in issues):
                add("no_credits", "Бот не ответил на последнее сообщение: не хватило кредитов.", "Кредиты",
                    bot_page + ("" if full else "#s-credits"))
        elif err == "blocked_access":
            if not any(i["code"] == "no_access" for i in issues):
                add("no_access", "Бот не ответил на последнее сообщение: нет доступа (тариф или пробный период).",
                    "Выбрать тариф", "/static/dashboard.html#plans")
        elif err:
            add("postcall_error", "Бот не смог ответить на последнее сообщение: сбой модели бота ("
                + err[:160] + "). Попробуйте сменить модель бота или написать ещё раз.", "Модель бота", bot_page)
        elif log.get("warning") == "no_reply_to_client":
            failed = [t for t in (log.get("tool_calls") or [])
                      if isinstance(t, dict) and str(t.get("tool", "")).endswith(("send_message", "send_file"))
                      and isinstance(t.get("result"), dict) and t["result"].get("ok") is False]
            detail = ""
            if failed:
                detail = " Ошибка отправки: " + str(failed[-1]["result"].get("error") or "")[:200]
            add("no_reply", "Бот разобрал сообщение, но ответ клиенту не ушёл." + detail, None, None)
        if when and issues and issues[-1]["code"] in ("postcall_error", "no_reply"):
            issues[-1]["at"] = when.isoformat() + "Z"
        break  # только самый свежий разбор входящего
    return issues


def send_channels(db: Session, contact, agent) -> Dict[str, Dict[str, Any]]:
    """В какие каналы можно написать этому клиенту и почему нельзя в остальные."""
    from backend.models.agent_instagram import AgentInstagramMessage
    from backend.models.agent_telegram_account import AgentTelegramDialog
    from backend.models.agent_whatsapp import AgentWhatsAppChat
    from backend.services import instagram_service

    out: Dict[str, Dict[str, Any]] = {}
    if agent is None:
        return {c: {"can_send": False, "reason": "Клиент не привязан к боту"} for c in CHANNELS}
    status = channel_status(db, agent)
    phone = real_phone(contact.phone)

    wa = status["whatsapp"]
    if not wa["connected"]:
        out["whatsapp"] = {"can_send": False, "reason": "WhatsApp не подключён"}
    else:
        chat = db.query(AgentWhatsAppChat.id).filter(AgentWhatsAppChat.agent_contact_id == contact.id).first()
        out["whatsapp"] = ({"can_send": True} if chat is not None or phone
                           else {"can_send": False, "reason": "Нет номера WhatsApp"})
        if chat is None and phone:
            out["whatsapp"]["note"] = "Новый чат: клиент ещё не писал в WhatsApp"

    tg = status["telegram"]
    if not tg["connected"]:
        out["telegram"] = {"can_send": False, "reason": "Telegram не подключён"}
    else:
        dialog = db.query(AgentTelegramDialog.id).filter(AgentTelegramDialog.agent_contact_id == contact.id).first()
        out["telegram"] = ({"can_send": True} if dialog is not None or phone
                           else {"can_send": False, "reason": "Нет диалога в Telegram"})

    ig = status["instagram"]
    if not ig["connected"]:
        out["instagram"] = {"can_send": False, "reason": "Instagram не подключён"}
    else:
        conv = instagram_service.conversation_for_contact(db, agent.id, contact.id)
        if conv is None or not conv.igsid:
            out["instagram"] = {"can_send": False, "reason": "Клиент не писал в Instagram — первым там написать нельзя"}
        else:
            last_in = (
                db.query(AgentInstagramMessage.created_at)
                .filter(AgentInstagramMessage.agent_contact_id == contact.id,
                        AgentInstagramMessage.direction == "inbound")
                .order_by(AgentInstagramMessage.created_at.desc())
                .first()
            )
            out["instagram"] = {"can_send": True, "media": "images_only"}
            if last_in is None or datetime.utcnow() - last_in[0] > IG_REPLY_WINDOW:
                out["instagram"]["note"] = ("Клиент писал больше суток назад — Instagram может не "
                                            "пропустить ответ. Надёжнее написать в WhatsApp.")
    return out


def default_channel(db: Session, contact, available: Dict[str, Dict[str, Any]]) -> Optional[str]:
    """Канал ответа по умолчанию: где клиент писал последним (если туда можно написать)."""
    from backend.models.agent_instagram import AgentInstagramMessage
    from backend.models.agent_telegram_account import AgentTelegramMessage
    from backend.models.agent_whatsapp import AgentWhatsAppMessage
    best = None
    for ch, model in (("whatsapp", AgentWhatsAppMessage), ("telegram", AgentTelegramMessage),
                      ("instagram", AgentInstagramMessage)):
        if not (available.get(ch) or {}).get("can_send"):
            continue
        row = (
            db.query(model.created_at)
            .filter(model.agent_contact_id == contact.id, model.direction == "inbound")
            .order_by(model.created_at.desc())
            .first()
        )
        ts = row[0] if row else None
        if best is None or (ts is not None and (best[1] is None or ts > best[1])):
            best = (ch, ts)
    return best[0] if best else None


def mark_read(db: Session, user_id, contact_id) -> None:
    from backend.models.inbox import InboxReadState
    row = db.query(InboxReadState).filter(
        InboxReadState.user_id == user_id, InboxReadState.agent_contact_id == contact_id,
    ).first()
    now = datetime.utcnow()
    if row is None:
        db.add(InboxReadState(user_id=user_id, agent_contact_id=contact_id, last_read_at=now))
    else:
        row.last_read_at = now
    try:
        db.commit()
    except Exception:
        db.rollback()  # гонка двух вкладок: строка уже есть — не страшно


# ============================================================================
# ОТПРАВКА ВЛАДЕЛЬЦА
# ============================================================================

class SendError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


async def _voice_payload(data: bytes, mime: Optional[str]) -> Dict[str, Any]:
    """Запись с микрофона браузера (webm/ogg/mp4) → OGG/Opus для «настоящего» голосового."""
    from backend.services import agent_voice_service
    ogg = data if data[:4] == b"OggS" else await agent_voice_service._to_ogg_opus(data)
    if ogg:
        return {"data": ogg, "filename": "voice.ogg", "is_ogg": True,
                "duration": agent_voice_service.ogg_duration_seconds(ogg), "mime": "audio/ogg"}
    ext = {"audio/webm": "webm", "audio/mp4": "m4a", "audio/mpeg": "mp3"}.get((mime or "").split(";")[0], "webm")
    return {"data": data, "filename": f"voice.{ext}", "is_ogg": False, "duration": 0,
            "mime": (mime or "audio/webm").split(";")[0]}


async def send_message(
    db: Session, user, contact, *, channel: str, text: str = "",
    upload: Optional[Dict[str, Any]] = None, voice: Optional[Dict[str, Any]] = None,
    library_file_id: Optional[str] = None, pause_minutes=None,
    original: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Отправить клиенту от имени владельца. upload = {data, filename, mime},
    voice = {data, mime}. pause_minutes: None/"channel" — настройка канала,
    минуты, -1 — до ручного включения, 0 — не ставить паузу.
    original = {text, language} — что владелец написал до перевода.
    """
    from backend.core.config import settings as app_settings
    from backend.models.agent_config import AgentConfig
    from backend.models.agent_file import AgentFile
    from backend.services import agent_media_service, agent_pause, agent_tools

    if channel not in CHANNELS:
        raise SendError("invalid_channel", "Неизвестный канал")
    agent = db.query(AgentConfig).filter(
        AgentConfig.id == contact.agent_config_id, AgentConfig.user_id == user.id,
    ).first() if contact.agent_config_id else None
    if agent is None:
        raise SendError("no_agent", "Клиент не привязан к боту")
    avail = send_channels(db, contact, agent).get(channel) or {}
    if not avail.get("can_send"):
        raise SendError("channel_unavailable", avail.get("reason") or "В этот канал написать нельзя")

    text = (text or "").strip()
    file_row = None
    voice_payload = None
    max_bytes = int(getattr(app_settings, "AGENT_MEDIA_MAX_MB", 20) or 20) * 1024 * 1024

    if library_file_id:
        file_row = db.query(AgentFile).filter(
            AgentFile.id == library_file_id, AgentFile.agent_config_id == agent.id,
        ).first()
        if file_row is None or not file_row.storage_key:
            raise SendError("file_not_found", "Файл не найден")
    elif upload is not None:
        data = upload.get("data") or b""
        if not data:
            raise SendError("empty_file", "Пустой файл")
        if len(data) > max_bytes:
            raise SendError("file_too_large", f"Файл больше {max_bytes // (1024 * 1024)} МБ")
        file_row = await agent_media_service.store_file(
            db, user_id=user.id, agent_config_id=agent.id, data=data,
            filename=upload.get("filename") or "file", mime=upload.get("mime"),
            source="outbound", agent_contact_id=contact.id, channel=channel, status="ready",
        )
        if not file_row.storage_key:
            raise SendError("storage_unavailable", "Хранилище файлов недоступно — попробуйте позже")
    elif voice is not None:
        data = voice.get("data") or b""
        if len(data) < 200:
            raise SendError("empty_voice", "Запись слишком короткая")
        if len(data) > max_bytes:
            raise SendError("file_too_large", "Запись слишком длинная")
        voice_payload = await _voice_payload(data, voice.get("mime"))
        # Запись хранится у нас: владелец переслушает её в ленте, Instagram получит ссылку
        file_row = await agent_media_service.store_file(
            db, user_id=user.id, agent_config_id=agent.id, data=voice_payload["data"],
            filename=voice_payload["filename"], mime=voice_payload["mime"], kind="voice",
            source="outbound", agent_contact_id=contact.id, channel=channel, status="ready",
        )
    elif not text:
        raise SendError("empty_text", "Пустое сообщение")

    args = {"agent_contact_id": str(contact.id), "text": text}
    common = dict(skip_rate_limit=True, sent_by="owner")
    if channel == "whatsapp":
        if voice_payload is not None:
            result = await agent_tools.fn_whatsapp_send_message(
                args, user.id, agent, db, voice=voice_payload, attachment_id=file_row.id if file_row else None,
                typing_ms=600, **common)
        else:
            result = await agent_tools.fn_whatsapp_send_message(
                args, user.id, agent, db, file_row=file_row, typing_ms=600, **common)
    elif channel == "telegram":
        if voice_payload is not None:
            result = await agent_tools.fn_telegram_send_message(
                args, user.id, agent, db, voice=voice_payload, attachment_id=file_row.id if file_row else None,
                typing_seconds=0, **common)
        elif file_row is not None:
            data = await agent_media_service.load_bytes(file_row)
            if not data:
                raise SendError("storage_unavailable", "Файл недоступен в хранилище")
            result = await agent_tools.fn_telegram_send_message(
                args, user.id, agent, db, file_row=file_row, file_bytes=data, typing_seconds=0, **common)
        else:
            result = await agent_tools.fn_telegram_send_message(args, user.id, agent, db, typing_seconds=0, **common)
    else:
        result = await agent_tools.fn_instagram_send_message(args, user.id, agent, db, file_row=file_row, **common)

    if not result.get("ok"):
        raise SendError("send_failed", result.get("hint") or result.get("error") or "Не удалось отправить")

    # Пауза бота с этим клиентом (во всех каналах)
    paused_until = None
    if pause_minutes not in (0, "0", "none"):
        if pause_minutes in (None, "", "channel"):
            minutes = agent_pause.owner_pause_minutes(pause_row_for(db, agent.id, channel))
            agent_pause.on_owner_message(db, contact.id, minutes)
        else:
            minutes = int(pause_minutes)
            current = contact.ai_paused_until if agent_pause.is_paused(contact) else None
            target = agent_pause.pause_until_for(minutes)
            if current is None or target > current:
                agent_pause.set_pause(contact, minutes, "owner_message")
        db.commit()
        db.refresh(contact)
        paused_until = contact.ai_paused_until if agent_pause.is_paused(contact) else None

    row_id = result.get("message_row_id")
    if original and row_id and (original.get("text") or "").strip():
        _save_translation(db, channel, row_id, "original", original.get("text"), original.get("language"))

    if voice_payload is not None and file_row is not None and row_id:
        _spawn(_transcribe_owner_voice(str(file_row.id), channel, row_id))

    logger.info(f"[INBOX] owner → {channel} contact {contact.id} ({'voice' if voice_payload else 'file' if file_row else 'text'})")
    return {
        "ok": True,
        "message_id": row_id,
        "paused_until": iso_utc(paused_until),
        "paused_forever": bool(paused_until is not None and paused_until.year >= 9999),
        "sent_as": result.get("sent_as"),
    }


_BG_TASKS: set = set()


def _spawn(coro) -> None:
    try:
        task = asyncio.get_running_loop().create_task(coro)
        _BG_TASKS.add(task)
        task.add_done_callback(_BG_TASKS.discard)
    except RuntimeError:
        pass


async def _transcribe_owner_voice(file_id: str, channel: str, message_row_id: str) -> None:
    """Голосовое владельца → текст в ленту: бот, вернувшись к клиенту, знает, что сказал владелец."""
    from backend.db.session import SessionLocal
    from backend.models.agent_file import AgentFile
    from backend.services import agent_media_service
    model = _message_model(channel)
    db = SessionLocal()
    try:
        row = db.query(AgentFile).filter(AgentFile.id == file_id).first()
        if row is None:
            return
        data = await agent_media_service.load_bytes(row)
        if not data:
            return
        await agent_media_service.analyze_file(db, row, data)
        text = (row.extracted_text or "").strip()
        if not text:
            return
        msg = db.query(model).filter(model.id == message_row_id).first()
        if msg is not None:
            msg.body = "🎤 Голосовое: " + text[:2000]
            db.commit()
    except Exception as e:
        logger.warning(f"[INBOX] owner voice transcription failed: {e}")
        db.rollback()
    finally:
        db.close()


def _message_model(channel: str):
    from backend.models.agent_instagram import AgentInstagramMessage
    from backend.models.agent_telegram_account import AgentTelegramMessage
    from backend.models.agent_whatsapp import AgentWhatsAppMessage
    return {"whatsapp": AgentWhatsAppMessage, "telegram": AgentTelegramMessage,
            "instagram": AgentInstagramMessage}[channel]


def _save_translation(db: Session, channel: str, message_id, kind: str, text: str, language: Optional[str]):
    from backend.models.inbox import InboxTranslation
    try:
        row = db.query(InboxTranslation).filter(
            InboxTranslation.channel == channel, InboxTranslation.message_id == message_id,
            InboxTranslation.kind == kind,
        ).first()
        if row is None:
            db.add(InboxTranslation(channel=channel, message_id=message_id, kind=kind,
                                    text=text[:8000], language=language))
        else:
            row.text, row.language = text[:8000], language
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f"[INBOX] save translation failed: {e}")


def find_message(db: Session, user, channel: str, message_id):
    """Сообщение канала, принадлежащее владельцу (через карточку клиента)."""
    from backend.models.agent_contact import AgentContact
    if channel not in CHANNELS:
        return None
    model = _message_model(channel)
    try:
        msg = db.query(model).filter(model.id == message_id).first()
    except Exception:
        db.rollback()
        return None
    if msg is None or msg.agent_contact_id is None:
        return None
    owner = db.query(AgentContact.user_id).filter(AgentContact.id == msg.agent_contact_id).first()
    return msg if owner is not None and str(owner[0]) == str(user.id) else None


# ============================================================================
# ЗВОНОК И НАПОМИНАНИЕ ПО КОМАНДЕ ВЛАДЕЛЬЦА
# ============================================================================

def queue_owner_call(db: Session, user, contact) -> Dict[str, Any]:
    """
    «Позвонить» в инбоксе: бот звонит клиенту сейчас (задача created_by="owner",
    планировщик подхватит за ~30 с). Работает и на тарифе Старт — там агент сам
    не звонит, а прямая команда владельца разрешена.
    """
    from backend.models.agent_config import AgentConfig
    from backend.models.sip_gateway import is_o_number, normalize_sip_number
    from backend.models.task import Task, TaskStatus
    from backend.services.agent_contact_phones import dial_number
    from backend.services.agent_tools import assistant_task_kwargs
    from backend.services.sip_gateway_service import SipGatewayService

    if not user.has_agent_chat_access():
        raise SendError("no_access", "Нужен тариф Старт или Profi")
    agent = db.query(AgentConfig).filter(
        AgentConfig.id == contact.agent_config_id, AgentConfig.user_id == user.id,
    ).first() if contact.agent_config_id else None
    if agent is None:
        raise SendError("no_agent", "Клиент не привязан к боту")
    if not agent.is_active:
        raise SendError("agent_inactive", "Бот выключен — включите его, чтобы он позвонил")
    kwargs = assistant_task_kwargs(agent)
    if not any(kwargs.values()):
        raise SendError("no_voice", "У бота не настроен голос для звонков")
    if SipGatewayService.outbound_number_for_user(db, user.id) is None:
        raise SendError("no_number", "Нет номера с исходящими звонками (раздел «Телефония»)")
    phone = dial_number(contact)
    if not phone:
        raise SendError("no_phone", "У клиента нет номера телефона")
    if not is_o_number(normalize_sip_number(phone)):
        raise SendError("other_operator", "Звонки идут только на номера O! (050, 070). Попросите у клиента номер O!")

    recent = db.query(Task).filter(
        Task.agent_contact_id == contact.id,
        Task.status.in_([TaskStatus.SCHEDULED, TaskStatus.PENDING]),
        Task.created_by == "owner",
        Task.channel == "call",
        Task.created_at >= datetime.utcnow() - timedelta(minutes=3),
    ).first()
    if recent is not None:
        return {"ok": True, "task_id": str(recent.id), "already": True}

    task = Task(
        is_agent_task=True,
        agent_contact_id=contact.id,
        user_id=user.id,
        contact_id=None,
        status=TaskStatus.SCHEDULED,
        scheduled_time=datetime.utcnow(),
        title="Звонок по вашей команде",
        description="Владелец нажал «Позвонить» в инбоксе",
        channel="call",
        created_by="owner",
        **kwargs,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    logger.info(f"[INBOX] owner call queued: task {task.id} contact {contact.id}")
    return {"ok": True, "task_id": str(task.id), "phone": phone}


def create_reminder(db: Session, user, contact, title: str, when: datetime, note: str = "") -> Dict[str, Any]:
    """Напоминание владельцу (channel="owner"): планировщик его не исполняет, оно висит в задачах."""
    from backend.models.task import Task, TaskStatus
    title = (title or "").strip()[:255]
    if not title:
        raise SendError("title_required", "Напишите, о чём напомнить")
    task = Task(
        is_agent_task=True,
        channel="owner",
        agent_contact_id=contact.id,
        user_id=user.id,
        contact_id=None,
        status=TaskStatus.SCHEDULED,
        scheduled_time=when,
        title=title,
        description=(note or "").strip(),
        created_by="owner",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return _task_dict(task)


# ============================================================================
# ПОМОЩНИК (улучшить / перевести / черновик)
# ============================================================================

TONE_HINT = {
    "neutral": "Тон — вежливый и деловой, как в переписке менеджера с клиентом.",
    "friendly": "Тон — дружелюбный и тёплый, без фамильярности.",
    "formal": "Тон — официальный, на «Вы».",
}


def _lang_name(code: Optional[str]) -> str:
    return LANGUAGE_NAMES.get(code or "", code or "")


def _assist_messages(action: str, text: str, *, language: Optional[str], tone: str,
                     context: str = "") -> List[Dict[str, str]]:
    base = ("Ты помощник менеджера в мессенджере. Отвечай ТОЛЬКО готовым текстом сообщения — "
            "без кавычек, пояснений, вариантов и markdown. " + TONE_HINT.get(tone, TONE_HINT["neutral"]))
    if action == "fix":
        task = ("Исправь орфографию, пунктуацию и грамматику, сделай текст понятным и вежливым. "
                "Смысл, факты, цены и язык сообщения не меняй, ничего не добавляй.")
        return [{"role": "system", "content": base + " " + task}, {"role": "user", "content": text}]
    if action == "translate":
        task = (f"Переведи сообщение на язык: {_lang_name(language)}. Естественно, как написал бы носитель "
                "в мессенджере, без канцелярита. Числа, цены, имена и ссылки сохрани. "
                "Кыргызский — разговорный бишкекский, как пишут в мессенджере: кыргызская грамматика, деловые "
                "слова (заказ, доставка, скидка, тариф, ссылка) можно по-русски, без книжных слов.")
        return [{"role": "system", "content": base + " " + task}, {"role": "user", "content": text}]
    if action == "translate_incoming":
        task = (f"Переведи сообщение клиента на язык: {_lang_name(language)}. Точно по смыслу, "
                "сохрани числа, имена и ссылки. Если текст уже на этом языке — верни его без изменений.")
        return [{"role": "system", "content": "Ты переводчик. Отвечай только переводом. " + task},
                {"role": "user", "content": text}]
    # draft
    task = ("Напиши ответ клиенту от лица компании: коротко (1–4 предложения), по делу, на языке клиента. "
            "Опирайся только на факты из знаний компании и переписки; чего не знаешь — не выдумывай, "
            "предложи уточнить. Если менеджер дал заготовку — разверни её.")
    user_part = (context + ("\n\nЗаготовка менеджера: " + text if text else "")).strip()
    return [{"role": "system", "content": base + " " + task}, {"role": "user", "content": user_part}]


def _draft_context(db: Session, contact) -> str:
    """Знания бота + память о клиенте + последние сообщения — для черновика ответа."""
    from backend.services import agent_knowledge_service
    parts = []
    try:
        kb = agent_knowledge_service.build_prompt_block(db, contact.agent_config_id, budget=4000) if contact.agent_config_id else ""
    except Exception:
        db.rollback()
        kb = ""
    if kb:
        parts.append("Знания компании:\n" + kb)
    mem = contact.memory or {}
    facts = "; ".join(str(f) for f in (mem.get("key_facts") or [])[:8])
    about = "; ".join(x for x in [mem.get("summary"), mem.get("direction_need"), facts] if x)
    if about:
        parts.append("Что известно о клиенте: " + str(about)[:1200])
    tl = timeline(db, contact, limit=16)
    lines = []
    for it in tl["items"]:
        if it.get("type") != "message":
            continue
        who = {"client": "Клиент", "bot": "Компания (бот)", "owner": "Компания (менеджер)"}[it["who"]]
        txt = it.get("text") or (("[" + it["attachment"]["kind"] + "]") if it.get("attachment") else "")
        if txt:
            lines.append(f"{who}: {txt[:500]}")
    if lines:
        parts.append("Переписка (старые → новые):\n" + "\n".join(lines[-16:]))
    return "\n\n".join(parts)


async def assist(db: Session, user, *, action: str, text: str, language: Optional[str] = None,
                 contact=None, settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Помощник: возвращает {text} — предложение, которое владелец сам отправит или поправит."""
    from backend.models.user import User
    from backend.services.credit_service import CreditService
    from backend.services.openrouter_client import get_openrouter_client

    if action not in ("fix", "translate", "translate_incoming", "draft"):
        raise SendError("invalid_action", "Неизвестное действие")
    text = (text or "").strip()[:ASSIST_MAX_CHARS]
    if action != "draft" and not text:
        raise SendError("empty_text", "Сначала напишите текст")
    if action in ("translate", "translate_incoming") and language not in LANGUAGE_NAMES:
        raise SendError("invalid_language", "Выберите язык")
    fresh = db.query(User).filter(User.id == user.id).first()
    if fresh is None or (fresh.credits_balance or 0) <= 0:
        raise SendError("insufficient_credits", "Не хватает кредитов бота для помощника")
    settings = settings or get_settings(db, user.id)
    context = _draft_context(db, contact) if action == "draft" and contact is not None else ""
    messages = _assist_messages(action, text, language=language, tone=settings.get("tone") or "neutral",
                                context=context)
    try:
        resp = await get_openrouter_client().chat_completion(
            ASSIST_MODEL, messages, temperature=0.3 if action != "draft" else 0.5,
            max_tokens=1200, timeout=40.0,
        )
    except Exception as e:
        logger.warning(f"[INBOX] assist {action} failed: {e}")
        raise SendError("assist_failed", "Помощник сейчас недоступен — попробуйте ещё раз")
    out = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    out = out.strip().strip('"').strip("«»").strip()
    usage = resp.get("usage") or {}
    try:
        CreditService.charge(db, user.id, ASSIST_MODEL, int(usage.get("prompt_tokens") or 0),
                             int(usage.get("completion_tokens") or 0), ref_type="inbox_assist",
                             notes=action)
    except Exception as e:
        db.rollback()
        logger.warning(f"[INBOX] assist charge failed: {e}")
    if not out:
        raise SendError("assist_empty", "Помощник не смог предложить текст")
    return {"text": out, "language": language}
