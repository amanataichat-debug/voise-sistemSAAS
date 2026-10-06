"""
Обработка webhook'ов WhatsApp-шлюза (Evolution API) — фоновый loop.

POST /api/whatsapp/webhook/{account_id} проверяет секрет и отдаёт payload
сюда через background_loop.submit (синхронные запросы к БД не должны стопорить
звонки в основном loop'е воркера).

События:
  connection.update — номер подключился (state=open) / вышел (close + 401/403);
  messages.upsert   — сообщение в личном чате:
    • fromMe (написано с телефона владельца или нашей отправкой) — в тред как
      outbound, оркестратор не зовём; наши отправки отсекаются по wa_message_id;
    • входящее — в тред, вложение распознаётся (agent_media_service), затем
      через DEBOUNCE_SECONDS чат отдаётся оркестратору (handle_inbound_whatsapp)
      одним прогоном на серию сообщений: прогон запускает только задача
      последнего входящего, а атомарный claim по last_dispatched_at исключает
      двойной запуск при нескольких gunicorn-воркерах.
"""

import asyncio
import base64
from datetime import datetime, timedelta
from typing import Any, Dict

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.db.session import SessionLocal
from backend.services import whatsapp_service as wa

logger = get_logger(__name__)

# Пауза перед ответом: клиент часто пишет серией коротких сообщений.
DEBOUNCE_SECONDS = 8

# Причины закрытия, после которых нужен новый QR (вышли с телефона/бан).
LOGGED_OUT_CODES = {401, 403, 402, 406}

# Старше этого входящие только сохраняются, без ответа (досинхронизация
# истории после подключения/перезапуска шлюза приходит тем же messages.upsert).
STALE_AFTER = timedelta(hours=12)

# Ссылки на фоновые задачи: asyncio держит задачи слабыми ссылками.
_TASKS: set = set()


async def handle_webhook(account_id: str, payload: Dict[str, Any]) -> None:
    event = (payload.get("event") or "").lower().replace("_", ".")
    data = payload.get("data")
    try:
        if event == "connection.update":
            _on_connection(account_id, data if isinstance(data, dict) else {})
        elif event == "messages.upsert":
            items = data if isinstance(data, list) else [data]
            for item in items:
                await _on_message(account_id, item)
    except Exception as e:
        logger.error(f"[WA-IN] webhook {event} for account {account_id} failed: {e}", exc_info=True)


def _on_connection(account_id: str, data: dict) -> None:
    from backend.models.agent_whatsapp import AgentWhatsAppAccount

    state = data.get("state")
    db = SessionLocal()
    try:
        account = db.query(AgentWhatsAppAccount).filter(AgentWhatsAppAccount.id == account_id).first()
        if not account:
            return
        if state == "open":
            jid = data.get("wuid") or account.wa_jid
            account.status = "connected"
            account.wa_jid = jid
            account.wa_phone = wa.jid_phone(jid or "") or account.wa_phone
            account.wa_name = data.get("profileName") or account.wa_name
            account.last_error = None
            if account.connected_at is None:
                account.connected_at = datetime.utcnow()
            logger.info(f"[WA-IN] account {account_id} connected as {account.wa_phone}")
        elif state == "close":
            try:
                reason = int(data.get("statusReason") or 0)
            except (TypeError, ValueError):
                reason = 0
            if reason in LOGGED_OUT_CODES and account.status == "connected":
                account.status = "disconnected"
                account.last_error = f"logged_out:{reason}"
                logger.warning(f"[WA-IN] account {account_id} logged out (reason {reason})")
        db.commit()
    finally:
        db.close()


def _placeholder(msg: dict, processing: bool = True) -> str:
    from backend.services.agent_media_service import KIND_LABELS
    media = msg.get("media")
    if not media:
        return msg["text"]
    label = KIND_LABELS.get(media.get("kind") or "other", KIND_LABELS["other"])
    tail = " (обрабатывается…)" if processing else ""
    return ((msg.get("text") or "") + f"\n{label}{tail}").strip()


async def _on_message(account_id: str, item: dict) -> None:
    from backend.models.agent_contact import AgentContact
    from backend.models.agent_whatsapp import AgentWhatsAppAccount, AgentWhatsAppChat

    msg = wa.parse_incoming(item)
    if msg is None or not msg.get("id"):
        return
    if msg["from_me"]:
        # Эхо нашей же отправки может обогнать коммит outbound-строки в
        # fn_whatsapp_send_message — даём ему записаться, чтобы не задвоить.
        await asyncio.sleep(5)

    db = SessionLocal()
    try:
        account = db.query(AgentWhatsAppAccount).filter(AgentWhatsAppAccount.id == account_id).first()
        if not account:
            return
        if wa.message_exists(db, account.agent_config_id, msg["id"]):
            return  # дубль webhook'а или наша же отправка

        chat = db.query(AgentWhatsAppChat).filter(
            AgentWhatsAppChat.account_id == account.id,
            AgentWhatsAppChat.remote_jid == msg["remote_jid"],
        ).first()
        if chat is None:
            chat = AgentWhatsAppChat(
                account_id=account.id,
                remote_jid=msg["remote_jid"],
                phone=msg.get("phone"),
                created_via="send" if msg["from_me"] else "inbound",
            )
            db.add(chat)
            db.flush()
        if msg.get("phone") and not chat.phone:
            chat.phone = msg["phone"]
        if msg.get("push_name") and not msg["from_me"]:
            chat.push_name = msg["push_name"]
        if chat.agent_contact_id is None and chat.phone:
            contact = wa.match_contact_by_phone(db, account.agent_config_id, chat.phone)
            if contact:
                chat.agent_contact_id = contact.id

        # Сообщение владельца с телефона — только в историю.
        if msg["from_me"]:
            wa.store_message(
                db, account, "outbound", _placeholder(msg, processing=False),
                chat=chat, agent_contact_id=chat.agent_contact_id,
                wa_message_id=msg["id"], sent_at=msg["ts"],
            )
            db.commit()
            return

        contact_id = chat.agent_contact_id
        if contact_id is None and (account.reply_scope or "contacts") == "all":
            contact = AgentContact(
                agent_config_id=account.agent_config_id,
                user_id=account.user_id,
                phone=("+" + chat.phone) if chat.phone else f"wa:{chat.remote_jid.split('@')[0]}",
                name=chat.push_name,
                status="new",
            )
            db.add(contact)
            db.flush()
            chat.agent_contact_id = contact.id
            contact_id = contact.id
            logger.info(f"[WA-IN] 🆕 AgentContact {contact_id} for WhatsApp {chat.phone or chat.remote_jid}")

        row = wa.store_message(
            db, account, "inbound", _placeholder(msg),
            chat=chat, agent_contact_id=contact_id,
            wa_message_id=msg["id"], sent_at=msg["ts"],
        )
        db.commit()

        now = datetime.utcnow()
        # До первого подключения номера (история из телефона) — не отвечаем.
        baseline = account.connected_at or account.created_at
        stale = msg["ts"] < now - STALE_AFTER or (
            baseline is not None and msg["ts"] < baseline - timedelta(minutes=2)
        )
        if contact_id is None or stale or not account.auto_reply_enabled or not account.is_connected():
            if msg.get("media"):
                row.body = _placeholder(msg, processing=False)
                db.commit()
            return

        task_args = (str(account.id), str(chat.id), str(contact_id), str(row.id), msg)
    finally:
        db.close()

    task = asyncio.create_task(_process_and_dispatch(*task_args))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def _process_and_dispatch(account_id: str, chat_id: str, contact_id: str,
                                msg_row_id: str, msg: dict) -> None:
    if msg.get("media"):
        await _process_media(account_id, contact_id, msg_row_id, msg)
    await asyncio.sleep(DEBOUNCE_SECONDS)
    await _dispatch_if_latest(account_id, chat_id, contact_id, msg_row_id)


async def _process_media(account_id: str, contact_id: str, msg_row_id: str, msg: dict) -> None:
    """Вложение → AgentFile с распознанным текстом; тело сообщения в треде обновляется."""
    from backend.models.agent_config import AgentConfig
    from backend.models.agent_whatsapp import AgentWhatsAppAccount, AgentWhatsAppMessage
    from backend.models.user import User
    from backend.services import agent_media_service as media_svc

    media = msg["media"]
    db = SessionLocal()
    try:
        account = db.query(AgentWhatsAppAccount).filter(AgentWhatsAppAccount.id == account_id).first()
        row = db.query(AgentWhatsAppMessage).filter(AgentWhatsAppMessage.id == msg_row_id).first()
        if not account or not row:
            return
        agent = db.query(AgentConfig).filter(AgentConfig.id == account.agent_config_id).first()
        user = db.query(User).filter(User.id == account.user_id).first()
        allowed = bool(agent and agent.is_active and user)
        if allowed:
            try:
                allowed = user.has_active_agent_subscription()
            except Exception:
                pass
        if not allowed:
            row.body = _placeholder(msg, processing=False)
            db.commit()
            return

        data, error = None, None
        b64 = media.get("base64")
        mime = media.get("mime")
        try:
            size = int(media.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if size and size > settings.AGENT_MEDIA_MAX_MB * 1024 * 1024:
            error = "file_too_large"
        elif not b64:
            got = await wa.get_media_base64(account.instance_name, msg["id"])
            if got:
                b64 = got.get("base64")
                mime = mime or got.get("mimetype")
        if b64 and not error:
            try:
                data = base64.b64decode(b64)
            except Exception:
                error = "download_failed"
        elif not error:
            error = "download_failed"

        try:
            file_row = await media_svc.process_inbound(
                db,
                user_id=account.user_id,
                agent_config_id=account.agent_config_id,
                agent_contact_id=contact_id,
                channel="whatsapp",
                external_message_id=msg["id"],
                data=data,
                filename=media.get("filename") or "",
                mime=mime,
                kind_hint=media.get("kind"),
                duration_hint=media.get("duration"),
                error=error,
            )
            row.body = media_svc.describe_for_prompt(file_row, caption=msg.get("text"), preview_chars=500)
            row.attachment_id = file_row.id
            db.commit()
        except Exception as e:
            logger.error(f"[WA-IN] media processing failed (msg {msg.get('id')}): {e}", exc_info=True)
            db.rollback()
            row = db.query(AgentWhatsAppMessage).filter(AgentWhatsAppMessage.id == msg_row_id).first()
            if row is not None:
                row.body = _placeholder(msg, processing=False) + " — не удалось обработать"
                db.commit()
    finally:
        db.close()


async def _dispatch_if_latest(account_id: str, chat_id: str, contact_id: str, msg_row_id: str) -> None:
    """Отдать серию входящих оркестратору — только из задачи последнего сообщения."""
    from sqlalchemy import or_
    from backend.models.agent_whatsapp import AgentWhatsAppChat, AgentWhatsAppMessage
    from backend.services.agent_orchestrator import handle_inbound_whatsapp

    db = SessionLocal()
    try:
        latest = db.query(AgentWhatsAppMessage).filter(
            AgentWhatsAppMessage.chat_id == chat_id,
            AgentWhatsAppMessage.direction == "inbound",
        ).order_by(AgentWhatsAppMessage.created_at.desc()).first()
        if latest is None or str(latest.id) != msg_row_id:
            return  # пришло новое сообщение — ответит его задача

        chat = db.query(AgentWhatsAppChat).filter(AgentWhatsAppChat.id == chat_id).first()
        if chat is None:
            return
        since = chat.last_dispatched_at
        claimed = db.query(AgentWhatsAppChat).filter(
            AgentWhatsAppChat.id == chat_id,
            or_(AgentWhatsAppChat.last_dispatched_at.is_(None),
                AgentWhatsAppChat.last_dispatched_at < latest.created_at),
        ).update({"last_dispatched_at": latest.created_at}, synchronize_session=False)
        db.commit()
        if not claimed:
            return

        q = db.query(AgentWhatsAppMessage).filter(
            AgentWhatsAppMessage.chat_id == chat_id,
            AgentWhatsAppMessage.direction == "inbound",
            AgentWhatsAppMessage.created_at <= latest.created_at,
        )
        if since is not None:
            q = q.filter(AgentWhatsAppMessage.created_at > since)
        parts = [m.body for m in q.order_by(AgentWhatsAppMessage.created_at.asc()).limit(20).all() if m.body]
    finally:
        db.close()

    text = "\n".join(parts).strip()
    if not text:
        return
    logger.info(f"[WA-IN] {len(parts)} message(s) in chat {chat_id} → orchestrator")
    await handle_inbound_whatsapp(account_id, contact_id, text)
