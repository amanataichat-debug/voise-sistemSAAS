"""
Telegram User Poller — опрос личных Telegram-аккаунтов агентов.

У MTProto нет вебхуков, а постоянный listener на каждый аккаунт в 4 gunicorn-
воркерах — тяжело и рискованно. Поэтому — поллинг: раз в check_interval секунд
для каждого подключённого аккаунта с включённым автоответом снимается срез
диалогов (Telethon, клиент на один опрос). Входящие сообщения НЕ уходят в
оркестратор напрямую — складываются в очередь pending_messages на диалоге,
а отдельный flush-воркер (`chat_batching_worker`) отдаёт пачку по истечении
окна тишины (chat_batching_window_seconds, по умолчанию 15с) или по
достижению жёсткого потолка (chat_batching_max_window_seconds, 60с). Так
агент не отвечает на каждое короткое сообщение отдельно, если клиент
доводит мысль несколькими репликами подряд.

Если chat_batching_enabled = FALSE для агента — поведение прежнее: сразу
asyncio.create_task(handle_inbound_telegram(...)).

Мультиворкер: startup-событие запускает поллер в КАЖДОМ воркере, поэтому
аккаунт достаётся ровно одному через атомарный claim по БД (UPDATE ... WHERE
last_poll_at < cutoff) — та же идея, что claim финализации в PostCall.

Порядок обработки диалога устроен так, что сбой LLM НЕ приводит к повторной
обработке: сообщения сохраняются в тред и last_processed_msg_id продвигается
ДО постановки в очередь.

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
from sqlalchemy.orm.attributes import flag_modified

from backend.core.logging import get_logger
from backend.db.session import SessionLocal
from backend.services import telegram_user_service as tg_user

logger = get_logger(__name__)

# Интервал опроса. Понижен с 60 до 10 — батчинг теперь делает flush-воркер,
# а поллер должен быстрее собирать входящие в pending_messages, чтобы окно
# тишины отсчитывалось от реального последнего сообщения, а не от момента,
# когда поллер его увидел.
DEFAULT_CHECK_INTERVAL = 10
# Claim должен «протухать» чуть раньше интервала, чтобы не пропускать тики.
CLAIM_CUTOFF_SECONDS = 8

# Максимум запусков оркестратора с одного аккаунта за тик (защита от лавины
# после долгого простоя/переподключения). При включённом batching это
# ограничивает только постановки в очередь; реальные вызовы LLM контролирует
# flush-воркер.
MAX_DIALOG_RUNS_PER_TICK = 5


async def start_telegram_user_poller(check_interval: int = DEFAULT_CHECK_INTERVAL):
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

        # Сохраняем входящие в тред и продвигаем маркер ДО постановки в очередь /
        # вызова оркестратора (идемпотентность: падение LLM не приведёт к повторной
        # обработке — сообщения уже помечены как прочитанные поллером).
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

        # Решаем, батчить или вызывать сразу. Настройки — на уровне агента
        # (chat_batching_*), читаем через ленивый импорт, чтобы не тащить
        # ORM-круговую зависимость при импорте модуля.
        agent = _load_agent(db, account.agent_config_id)
        batching_on = bool(getattr(agent, "chat_batching_enabled", True)) if agent else True

        if batching_on:
            _enqueue_pending(db, row, agent, msgs)
            db.commit()
            logger.info(
                f"[TG-POLLER] {len(msgs)} new message(s) from peer {peer_id} "
                f"(account {account.id}) → pending (flush_at={row.pending_flush_at.isoformat() if row.pending_flush_at else '?'})"
            )
        else:
            db.commit()
            text_joined = "\n".join(m["text"] for m in msgs)
            logger.info(
                f"[TG-POLLER] {len(msgs)} new message(s) from peer {peer_id} "
                f"(account {account.id}) → orchestrator (batching off)"
            )
            asyncio.create_task(handle_inbound_telegram(str(account.id), str(contact_id), text_joined))
        runs += 1

    db.commit()


def _load_agent(db, agent_config_id):
    """Безопасный лениво-импортируемый ресолв AgentConfig для настроек batching."""
    try:
        from backend.models.agent_config import AgentConfig
        return db.query(AgentConfig).filter(AgentConfig.id == agent_config_id).first()
    except Exception as e:
        logger.warning(f"[TG-POLLER] load agent {agent_config_id} failed: {e}")
        return None


def _enqueue_pending(db, dialog_row, agent, msgs):
    """
    Добавить входящие в pending_messages и подвинуть pending_flush_at по
    sliding window с жёстким потолком.

    Элемент pending_messages: {"text": str, "msg_id": int, "ts": iso_str}.
    pending_flush_at = min(now + window, pending_first_at + max_window).
    """
    now = datetime.utcnow()
    window = int(getattr(agent, "chat_batching_window_seconds", 15) or 15)
    max_window = int(getattr(agent, "chat_batching_max_window_seconds", 60) or 60)
    if max_window < window:
        max_window = window

    pending = list(dialog_row.pending_messages or [])
    first_at = dialog_row.pending_first_at or now
    if not pending:
        first_at = now

    for m in msgs:
        pending.append({
            "text": m.get("text") or "",
            "msg_id": int(m.get("id") or 0),
            "ts": now.isoformat(),
        })

    sliding = now + timedelta(seconds=window)
    hard_cap = first_at + timedelta(seconds=max_window)
    flush_at = min(sliding, hard_cap)

    dialog_row.pending_messages = pending
    dialog_row.pending_first_at = first_at
    dialog_row.pending_flush_at = flush_at
    flag_modified(dialog_row, "pending_messages")
