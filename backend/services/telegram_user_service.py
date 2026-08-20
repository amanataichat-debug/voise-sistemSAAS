"""
Telegram User Service — работа с ЛИЧНЫМ Telegram-аккаунтом агента (MTProto).

НЕ путать с agent_telegram_service.py (Bot API, бот-фронтенд чата владельца).
Здесь — Telethon: авторизация личного аккаунта (телефон → код → 2FA),
отправка сообщений клиентам от имени владельца и чтение новых входящих.

Ключевые решения:
- Сессия — StringSession, шифруется Fernet (TELEGRAM_SESSION_KEY) и живёт в БД
  (agent_telegram_accounts.session_encrypted). Файловых сессий нет — ФС Render
  эфемерная, а StringSession переносима между воркерами.
- Клиент создаётся НА КАЖДУЮ операцию (connect → действие → disconnect):
  постоянные MTProto-соединения в 4 gunicorn-воркерах не нужны и рискованны.
- StringSession НЕ хранит кэш entity, поэтому резолв получателя по числовому
  peer_id работает только через скан get_dialogs. Порядок резолва:
  username → скан диалогов по peer_id → импорт контакта по телефону
  (ImportContacts — последний и самый рискованный способ: за злоупотребление
  Telegram выдаёт PeerFlood/бан, поэтому он ограничен почасовым лимитом на
  уровне тулзы agent_tools.fn_telegram_send_message).
- Все ошибки Telethon конвертируются в {ok: False, error: <код>} — коды
  маппятся на человекочитаемые сообщения в UI и тулзах.
"""

from typing import Any, Dict, List, Optional

from backend.core.logging import get_logger
from backend.core.config import settings

logger = get_logger(__name__)

# Лимиты безопасности (анти-бан). Проверяются в agent_tools по данным БД.
TG_SEND_HOURLY_LIMIT = 30        # исходящих сообщений с аккаунта в час
TG_PHONE_RESOLVE_HOURLY_LIMIT = 5  # новых диалогов через импорт номера в час

# Сколько диалогов просматривает поллер за тик и сколько сообщений берёт
# из одного диалога за раз.
POLL_DIALOGS_LIMIT = 30
POLL_MAX_MESSAGES_PER_DIALOG = 10


def is_configured() -> bool:
    """True, если заданы креды MTProto-приложения и ключ шифрования сессий."""
    api_id = settings.TELEGRAM_API_ID
    try:
        int(str(api_id))
    except (TypeError, ValueError):
        return False
    return bool(settings.TELEGRAM_API_HASH) and bool(settings.TELEGRAM_SESSION_KEY)


# ============================================================================
# ШИФРОВАНИЕ СЕССИИ
# ============================================================================

def _fernet():
    from cryptography.fernet import Fernet
    key = (settings.TELEGRAM_SESSION_KEY or "").strip()
    return Fernet(key.encode())


def encrypt_session(session_str: str) -> str:
    return _fernet().encrypt((session_str or "").encode()).decode()


def decrypt_session(token: str) -> str:
    return _fernet().decrypt((token or "").encode()).decode()


# ============================================================================
# КЛИЕНТ
# ============================================================================

def _new_client(session_str: str = ""):
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    return TelegramClient(
        StringSession(session_str) if session_str else StringSession(),
        int(str(settings.TELEGRAM_API_ID)),
        settings.TELEGRAM_API_HASH,
    )


def _error_code(e: Exception) -> str:
    """Исключение Telethon → стабильный код ошибки для UI/тулз."""
    from telethon import errors
    if isinstance(e, errors.FloodWaitError):
        return f"flood_wait:{e.seconds}"
    if isinstance(e, errors.PhoneNumberInvalidError):
        return "phone_invalid"
    if isinstance(e, errors.PhoneNumberBannedError):
        return "phone_banned"
    if isinstance(e, errors.PhoneCodeInvalidError):
        return "code_invalid"
    if isinstance(e, errors.PhoneCodeExpiredError):
        return "code_expired"
    if isinstance(e, errors.SessionPasswordNeededError):
        return "password_needed"
    if isinstance(e, (errors.PasswordHashInvalidError,)):
        return "password_invalid"
    if isinstance(e, (errors.AuthKeyUnregisteredError, errors.UnauthorizedError)):
        return "session_revoked"
    if isinstance(e, errors.PeerFloodError):
        return "peer_flood"
    return f"telegram_error: {type(e).__name__}: {e}"


def _me_dict(me) -> Dict[str, Any]:
    return {
        "tg_user_id": getattr(me, "id", None),
        "tg_username": getattr(me, "username", None),
        "tg_first_name": getattr(me, "first_name", None),
    }


# ============================================================================
# АВТОРИЗАЦИЯ (телефон → код → пароль 2FA)
# ============================================================================

async def start_login(phone: str) -> Dict[str, Any]:
    """
    Шаг 1: отправить код на номер. Возвращает {ok, session, phone_code_hash}.
    `session` — частичная (неавторизованная) StringSession: её ОБЯЗАТЕЛЬНО
    сохранить и передать в confirm_code — код привязан к этой сессии/DC.
    """
    client = _new_client()
    try:
        await client.connect()
        sent = await client.send_code_request(phone)
        return {
            "ok": True,
            "session": client.session.save(),
            "phone_code_hash": sent.phone_code_hash,
        }
    except Exception as e:
        logger.warning(f"[TG-USER] start_login failed for •••{(phone or '')[-4:]}: {type(e).__name__}: {e}")
        return {"ok": False, "error": _error_code(e)}
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def confirm_code(session_str: str, phone: str, code: str, phone_code_hash: str) -> Dict[str, Any]:
    """
    Шаг 2: подтвердить код. Возвращает:
      {ok: True, needs_password: True, session}          — аккаунт с 2FA
      {ok: True, needs_password: False, session, me...}  — авторизован
    """
    from telethon import errors
    client = _new_client(session_str)
    try:
        await client.connect()
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        except errors.SessionPasswordNeededError:
            return {"ok": True, "needs_password": True, "session": client.session.save()}
        me = await client.get_me()
        return {"ok": True, "needs_password": False, "session": client.session.save(), **_me_dict(me)}
    except Exception as e:
        logger.warning(f"[TG-USER] confirm_code failed: {type(e).__name__}: {e}")
        return {"ok": False, "error": _error_code(e)}
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def confirm_password(session_str: str, password: str) -> Dict[str, Any]:
    """Шаг 3 (только 2FA): облачный пароль. Возвращает {ok, session, me...}."""
    client = _new_client(session_str)
    try:
        await client.connect()
        await client.sign_in(password=password)
        me = await client.get_me()
        return {"ok": True, "session": client.session.save(), **_me_dict(me)}
    except Exception as e:
        logger.warning(f"[TG-USER] confirm_password failed: {type(e).__name__}: {e}")
        return {"ok": False, "error": _error_code(e)}
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def logout(session_str: str) -> bool:
    """Разлогинить сессию на стороне Telegram (best-effort)."""
    client = _new_client(session_str)
    try:
        await client.connect()
        await client.log_out()
        return True
    except Exception as e:
        logger.warning(f"[TG-USER] logout failed: {type(e).__name__}: {e}")
        return False
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


# ============================================================================
# ОТПРАВКА
# ============================================================================

async def send_message(
    session_str: str,
    text: str,
    peer_id: Optional[int] = None,
    username: Optional[str] = None,
    phone: Optional[str] = None,
    contact_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Отправить сообщение. Резолв получателя (по мере предпочтительности):
      1) username — самый надёжный и безопасный;
      2) peer_id — скан get_dialogs (работает, если диалог уже есть);
      3) phone — ImportContacts (рискованно, вызывающий обязан лимитировать).

    Возвращает {ok, tg_message_id, peer_id, username, name, resolved_via}
    или {ok: False, error}.
    """
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "empty_text"}

    client = _new_client(session_str)
    try:
        await client.connect()
        entity = None
        resolved_via = None

        if username:
            uname = username.lstrip("@").strip()
            if uname:
                try:
                    entity = await client.get_entity(uname)
                    resolved_via = "username"
                except Exception as e:
                    logger.info(f"[TG-USER] resolve by username @{uname} failed: {type(e).__name__}")

        if entity is None and peer_id:
            # StringSession не хранит кэш entity — ищем peer в списке диалогов.
            async for d in client.iter_dialogs(limit=100):
                ent = d.entity
                if getattr(ent, "id", None) == int(peer_id):
                    entity = ent
                    resolved_via = "peer_id"
                    break

        imported_contact_id = None
        if entity is None and phone:
            from telethon.tl.functions.contacts import ImportContactsRequest, DeleteContactsRequest
            from telethon.tl.types import InputPhoneContact
            digits = "".join(ch for ch in phone if ch.isdigit() or ch == "+")
            res = await client(ImportContactsRequest([
                InputPhoneContact(client_id=0, phone=digits, first_name=contact_name or "Клиент", last_name="")
            ]))
            users = list(getattr(res, "users", []) or [])
            if not users:
                return {"ok": False, "error": "phone_not_on_telegram"}
            entity = users[0]
            imported_contact_id = entity.id
            resolved_via = "phone"

        if entity is None:
            return {"ok": False, "error": "recipient_not_resolved"}

        if getattr(entity, "bot", False) or getattr(entity, "is_self", False):
            return {"ok": False, "error": "recipient_not_allowed"}

        msg = await client.send_message(entity, text)

        # Импортированный контакт убираем из адресной книги владельца —
        # доступ к диалогу уже установлен, мусорить в контактах не нужно.
        if imported_contact_id is not None:
            try:
                from telethon.tl.functions.contacts import DeleteContactsRequest
                await client(DeleteContactsRequest(id=[entity]))
            except Exception:
                pass

        return {
            "ok": True,
            "tg_message_id": getattr(msg, "id", None),
            "peer_id": getattr(entity, "id", None),
            "username": getattr(entity, "username", None),
            "name": " ".join(filter(None, [getattr(entity, "first_name", None), getattr(entity, "last_name", None)])) or None,
            "resolved_via": resolved_via,
        }
    except Exception as e:
        logger.error(f"[TG-USER] send_message failed: {type(e).__name__}: {e}")
        return {"ok": False, "error": _error_code(e)}
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


# ============================================================================
# ПОЛЛИНГ ВХОДЯЩИХ
# ============================================================================

async def poll_dialogs(
    session_str: str,
    known_last_ids: Dict[int, int],
) -> Dict[str, Any]:
    """
    Снять срез личных диалогов и новые входящие сообщения.

    known_last_ids — {tg_peer_id: last_processed_msg_id} из БД. Для известных
    диалогов новые сообщения = входящие с id > last_processed; для НЕизвестных
    (появились после подключения) — последние unread_count входящих (но не
    больше POLL_MAX_MESSAGES_PER_DIALOG).

    Возвращает {ok, dialogs: [{peer_id, username, name, phone, top_id, is_known,
    new_messages: [{id, text}] (старые → новые)}]} или {ok: False, error}.
    Только личные диалоги: не боты, не группы/каналы, не Saved Messages.
    """
    client = _new_client(session_str)
    try:
        await client.connect()
        out: List[Dict[str, Any]] = []
        async for d in client.iter_dialogs(limit=POLL_DIALOGS_LIMIT):
            if not d.is_user:
                continue
            ent = d.entity
            if getattr(ent, "bot", False) or getattr(ent, "is_self", False):
                continue
            peer_id = int(getattr(ent, "id", 0) or 0)
            if not peer_id:
                continue
            top_id = int(getattr(d.message, "id", 0) or 0)
            is_known = peer_id in known_last_ids

            new_messages: List[Dict[str, Any]] = []
            if is_known:
                last_id = int(known_last_ids.get(peer_id) or 0)
                if top_id > last_id:
                    msgs = await client.get_messages(
                        ent, min_id=last_id, limit=POLL_MAX_MESSAGES_PER_DIALOG
                    )
                    for m in reversed(list(msgs or [])):
                        if getattr(m, "out", False):
                            continue
                        body = (getattr(m, "message", None) or "").strip()
                        if body:
                            new_messages.append({"id": m.id, "text": body})
            else:
                unread = int(getattr(d, "unread_count", 0) or 0)
                if unread > 0:
                    msgs = await client.get_messages(
                        ent, limit=min(unread, POLL_MAX_MESSAGES_PER_DIALOG)
                    )
                    for m in reversed(list(msgs or [])):
                        if getattr(m, "out", False):
                            continue
                        body = (getattr(m, "message", None) or "").strip()
                        if body:
                            new_messages.append({"id": m.id, "text": body})

            out.append({
                "peer_id": peer_id,
                "username": getattr(ent, "username", None),
                "name": " ".join(filter(None, [getattr(ent, "first_name", None), getattr(ent, "last_name", None)])) or None,
                "phone": getattr(ent, "phone", None),
                "top_id": top_id,
                "is_known": is_known,
                "new_messages": new_messages,
            })
        return {"ok": True, "dialogs": out}
    except Exception as e:
        logger.warning(f"[TG-USER] poll_dialogs failed: {type(e).__name__}: {e}")
        return {"ok": False, "error": _error_code(e)}
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


# ============================================================================
# ХЕЛПЕРЫ ДЛЯ БД-СЛОЯ (тулзы/поллер/оркестратор)
# ============================================================================

def get_account_for_agent(db, agent_config_id, require_connected: bool = True):
    """Строка AgentTelegramAccount агента (или None). Ленивая — без Telethon."""
    from backend.models.agent_telegram_account import AgentTelegramAccount
    row = db.query(AgentTelegramAccount).filter(
        AgentTelegramAccount.agent_config_id == agent_config_id
    ).first()
    if row is None:
        return None
    if require_connected and not row.is_connected():
        return None
    return row


def account_connected(db, agent_config_id) -> bool:
    """True, если сервис настроен и у агента подключён личный Telegram."""
    if not is_configured():
        return False
    try:
        return get_account_for_agent(db, agent_config_id) is not None
    except Exception as e:
        logger.warning(f"[TG-USER] account_connected check failed: {e}")
        return False


def store_message(
    db,
    account,
    direction: str,
    body: str,
    agent_contact_id=None,
    tg_peer_id=None,
    tg_message_id=None,
):
    """Сохранить сообщение переписки (best-effort, без commit)."""
    from backend.models.agent_telegram_account import AgentTelegramMessage
    try:
        row = AgentTelegramMessage(
            account_id=account.id,
            agent_contact_id=agent_contact_id,
            tg_peer_id=tg_peer_id,
            tg_message_id=tg_message_id,
            direction=direction,
            body=body or "",
        )
        db.add(row)
        return row
    except Exception as e:
        logger.error(f"[TG-USER] store_message failed: {e}")
        return None


def get_thread(db, agent_contact_id, limit: int = 30) -> list:
    """Переписка с контактом (старые → новые) для карточки/контекста."""
    from backend.models.agent_telegram_account import AgentTelegramMessage
    rows = (
        db.query(AgentTelegramMessage)
        .filter(AgentTelegramMessage.agent_contact_id == agent_contact_id)
        .order_by(AgentTelegramMessage.created_at.desc())
        .limit(limit)
        .all()
    )
    return list(reversed(rows))


def build_thread_text(db, agent_contact_id, limit: int = 20) -> str:
    """Текстовый блок Telegram-переписки для промпта оркестратора ('' если пусто)."""
    from backend.core.timezone_utils import utc_to_msk
    rows = get_thread(db, agent_contact_id, limit)
    if not rows:
        return ""
    lines = []
    for m in rows:
        ts_str = utc_to_msk(m.created_at).strftime("%d.%m %H:%M") if m.created_at else "?"
        who = "Агент → клиенту" if (m.direction or "inbound") == "outbound" else "Клиент → агенту"
        text = (m.body or "").strip().replace("\n", " ")
        lines.append(f"[{ts_str}] {who}: {text}")
    return "\n".join(lines)


def error_human(code: str) -> str:
    """Код ошибки → человекочитаемое сообщение (для тулз и UI)."""
    if not code:
        return "Неизвестная ошибка Telegram"
    if code.startswith("flood_wait:"):
        secs = code.split(":", 1)[1]
        return f"Telegram просит подождать {secs} сек. (flood limit) — попробуйте позже"
    mapping = {
        "phone_invalid": "Неверный номер телефона",
        "phone_banned": "Этот номер заблокирован Telegram",
        "code_invalid": "Неверный код подтверждения",
        "code_expired": "Код истёк — запросите новый",
        "password_invalid": "Неверный облачный пароль (2FA)",
        "session_revoked": "Сессия отозвана — переподключите Telegram",
        "peer_flood": "Telegram ограничил отправку (антиспам). Не пишите первым незнакомым контактам какое-то время",
        "phone_not_on_telegram": "У этого номера нет Telegram-аккаунта",
        "recipient_not_resolved": "Не удалось найти получателя (нет диалога, username и телефона)",
        "recipient_not_allowed": "Этому получателю писать нельзя (бот или собственный аккаунт)",
        "empty_text": "Пустой текст сообщения",
        "send_limit_reached": "Достигнут почасовой лимит исходящих сообщений (защита от бана)",
        "phone_resolve_limit_reached": "Достигнут почасовой лимит новых диалогов по номеру телефона (защита от бана)",
        "not_connected": "Личный Telegram не подключён к агенту",
        "not_configured": "Коннектор Telegram не настроен на сервере",
    }
    return mapping.get(code, code)
