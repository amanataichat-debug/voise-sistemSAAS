"""
Telegram User Poller — минутный опрос личных Telegram-аккаунтов агентов.

У MTProto нет вебхуков, а постоянный listener на каждый аккаунт в 4 gunicorn-
воркерах — тяжело и рискованно. Поэтому — поллинг: раз в check_interval секунд
для каждого подключённого аккаунта с включённым автоответом снимается срез
диалогов (Telethon, клиент на один опрос), новые входящие сообщения уходят в
PostCall-оркестратор (handle_inbound_telegram), который отвечает клиенту
тулзой telegram_send_message.

Мультиворкер: startup-событие запускает поллер в КАЖДОМ воркере, поэтому
аккаунт достаётся ровно одному через атомарный claim по БД (UPDATE ... WHERE
last_poll_at < cutoff) — та же идея, что claim финализации в PostCall.

Порядок обработки диалога устроен так, что сбой LLM НЕ приводит к повторной
обработке: сообщения сохраняются в тред и last_processed_msg_id продвигается
ДО запуска оркестратора.

Охват автоответа (account.reply_scope):
  contacts — только диалоги, привязанные к контактам агента (по baseline-снимку,
             по номеру телефона или после исходящего telegram_send_message);
  all      — любые новые личные диалоги (контакт создаётся автоматически).
Группы, каналы, боты и Saved Messages не обрабатываются никогда (фильтр в
telegram_user_service.poll_dialogs).
"""

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import or_

from backend.core.logging import get_logger
from backend.db.session import SessionLocal
from backend.services import telegram_user_service as tg_user

logger = get_logger(__name__)

# Claim должен «протухать» чуть раньше интервала, чтобы не пропускать тики.
CLAIM_CUTOFF_SECONDS = 55

# Максимум запусков оркестратора с одного аккаунта за тик (защита от лавины
# после долгого простоя/переподключения).
MAX_DIALOG_RUNS_PER_TICK = 5


async def start_telegram_user_poller(check_interval: int = 60):
    """Запуск поллера. No-op, если TELEGRAM_API_ID/HASH/SESSION_KEY не заданы."""
    if not tg_user.is_configured():
        logger.info("[TG-POLLER] Telegram user connector not configured, poller disabled")
        return

    logger.info(f"[TG-POLLER] Started (check every {check_interval}s)")
    while True:
        try:
            await _tick()
        except Exception as e:
            logger.error(f"[TG-POLLER] tick error: {e}", exc_info=True)
        await asyncio.sleep(check_interval)


async def _tick():
    from backend.models.agent_telegram_account import AgentTelegramAccount

    db = SessionLocal()
    try:
        account_ids = [
            row.id for row in db.query(AgentTelegramAccount.id).filter(
                AgentTelegramAccount.status == "connected",
                AgentTelegramAccount.auto_reply_enabled == True,  # noqa: E712
            ).all()
        ]
        for account_id in account_ids:
            if not _claim(db, account_id):
                continue
            account = db.query(AgentTelegramAccount).filter(
                AgentTelegramAccount.id == account_id
            ).first()
            if not account:
                continue
            try:
                await _poll_account(db, account)
            except Exception as e:
                logger.error(f"[TG-POLLER] account {account_id} poll error: {e}", exc_info=True)
                try:
                    db.rollback()
                except Exception:
                    pass
    finally:
        db.close()


def _claim(db, account_id) -> bool:
    """Атомарно занять аккаунт на этот тик (ровно один воркер из всех)."""
    from backend.models.agent_telegram_account import AgentTelegramAccount

    now = datetime.utcnow()
    cutoff = now - timedelta(seconds=CLAIM_CUTOFF_SECONDS)
    claimed = db.query(AgentTelegramAccount).filter(
        AgentTelegramAccount.id == account_id,
        or_(
            AgentTelegramAccount.last_poll_at.is_(None),
            AgentTelegramAccount.last_poll_at < cutoff,
        ),
    ).update({"last_poll_at": now}, synchronize_session=False)
    db.commit()
    return bool(claimed)


def _match_contact_by_phone(db, agent_config_id, phone):
    """AgentContact агента по видимому номеру Telegram-пользователя (или None)."""
    from backend.models.agent_contact import AgentContact
    from backend.services.sms_history import phone_suffix

    suf = phone_suffix(phone or "")
    if not suf:
        return None
    return db.query(AgentContact).filter(
        AgentContact.agent_config_id == agent_config_id,
        AgentContact.phone.like(f"%{suf}"),
    ).order_by(AgentContact.created_at.desc()).first()


async def _poll_account(db, account):
    from backend.models.agent_contact import AgentContact
    from backend.models.agent_telegram_account import AgentTelegramDialog
    from backend.services.agent_orchestrator import handle_inbound_telegram

    try:
        session_str = tg_user.decrypt_session(account.session_encrypted)
    except Exception as e:
        logger.error(f"[TG-POLLER] session decrypt failed for account {account.id}: {e}")
        account.status = "error"
        account.last_error = "session_decrypt_failed"
        db.commit()
        return

    dialog_rows = db.query(AgentTelegramDialog).filter(
        AgentTelegramDialog.account_id == account.id
    ).all()
    known = {int(r.tg_peer_id): int(r.last_processed_msg_id or 0) for r in dialog_rows}
    by_peer = {int(r.tg_peer_id): r for r in dialog_rows}

    snap = await tg_user.poll_dialogs(session_str, known)
    if not snap.get("ok"):
        err = snap.get("error") or "telegram_error"
        if err == "session_revoked":
            # Сессию отозвали из настроек Telegram — нужно переподключение.
            account.status = "error"
        account.last_error = err
        db.commit()
        logger.warning(f"[TG-POLLER] poll failed for account {account.id}: {err}")
        return

    if account.last_error:
        account.last_error = None

    runs = 0
    for d in snap.get("dialogs", []):
        peer_id = d["peer_id"]
        row = by_peer.get(peer_id)
        msgs = d.get("new_messages") or []
        top_id = int(d.get("top_id") or 0)

        if row is None:
            # Диалог появился после подключения (или baseline его не покрыл).
            # Маркер пока НЕ ставим в top_id — иначе при отложенной обработке
            # (см. deferred ниже) сообщения потеряются. Ставим 0, а продвинем
            # ниже по факту обработки/отсутствия входящих.
            contact = _match_contact_by_phone(db, account.agent_config_id, d.get("phone"))
            row = AgentTelegramDialog(
                account_id=account.id,
                agent_contact_id=(contact.id if contact else None),
                tg_peer_id=peer_id,
                tg_username=d.get("username"),
                tg_name=d.get("name"),
                last_processed_msg_id=0,
                created_via="inbound",
            )
            db.add(row)
            by_peer[peer_id] = row
        else:
            if d.get("username"):
                row.tg_username = d["username"]
            if d.get("name"):
                row.tg_name = d["name"]
            if row.agent_contact_id is None and d.get("phone"):
                contact = _match_contact_by_phone(db, account.agent_config_id, d.get("phone"))
                if contact:
                    row.agent_contact_id = contact.id

        # Нет новых входящих (или только исходящие владельца) — просто двигаем
        # маркер к top_id, чтобы не пере-сканировать этот диалог.
        if not msgs:
            if top_id > int(row.last_processed_msg_id or 0):
                row.last_processed_msg_id = top_id
            continue

        # Превышен лимит запусков за тик — маркер НЕ трогаем, добьём в следующий
        # тик (сообщения останутся «новыми»).
        if runs >= MAX_DIALOG_RUNS_PER_TICK:
            continue

        # Охват автоответа: contacts — только привязанные диалоги.
        contact_id = row.agent_contact_id
        if contact_id is None:
            if (account.reply_scope or "contacts") != "all":
                # Не отвечаем этому диалогу — но помечаем сообщения прочитанными
                # маркером, чтобы не перебирать их каждый тик.
                if top_id > int(row.last_processed_msg_id or 0):
                    row.last_processed_msg_id = top_id
                continue
            contact = AgentContact(
                agent_config_id=account.agent_config_id,
                user_id=account.user_id,
                phone=d.get("phone") or f"tg:{peer_id}",
                name=d.get("name"),
                status="new",
            )
            db.add(contact)
            db.flush()
            row.agent_contact_id = contact.id
            contact_id = contact.id
            logger.info(f"[TG-POLLER] 🆕 Создан AgentContact {contact_id} для входящего TG {peer_id}")

        # Сохраняем входящие в тред и продвигаем маркер ДО запуска оркестратора
        # (идемпотентность: падение LLM не приведёт к повторной обработке).
        max_msg_id = int(row.last_processed_msg_id or 0)
        for m in msgs:
            tg_user.store_message(
                db, account, "inbound", m["text"],
                agent_contact_id=contact_id,
                tg_peer_id=peer_id,
                tg_message_id=m["id"],
            )
            max_msg_id = max(max_msg_id, int(m["id"] or 0))
        # Двигаем к top_id (он >= max входящего; включает возможные исходящие).
        row.last_processed_msg_id = max(max_msg_id, top_id)
        db.commit()

        text_joined = "\n".join(m["text"] for m in msgs)
        logger.info(
            f"[TG-POLLER] {len(msgs)} new message(s) from peer {peer_id} "
            f"(account {account.id}) → orchestrator"
        )
        asyncio.create_task(handle_inbound_telegram(str(account.id), str(contact_id), text_joined))
        runs += 1

    db.commit()
