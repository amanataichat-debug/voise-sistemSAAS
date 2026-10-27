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

Переписка v2: поллер видит и исходящие с аккаунта. Те, чьих tg_message_id нет
среди отправок агента, написал владелец сам — они сохраняются в тред
(sent_by='owner') и ставят паузу агента с клиентом (agent_pause). Пока
последнее сообщение клиента моложе account.reply_delay_seconds, диалог
откладывается до следующего тика (реальная задержка = настройка + до 60 с).

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
from backend.services import agent_pause
from backend.services import telegram_user_service as tg_user

logger = get_logger(__name__)

# Claim должен «протухать» чуть раньше интервала, чтобы не пропускать тики.
CLAIM_CUTOFF_SECONDS = 55

# Исходящее, которого нет среди отправок агента, моложе этого — ещё может
# оказаться отправкой агента, не успевшей записаться в БД; ждём тик.
OWNER_ECHO_GRACE_SECONDS = 20

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
    """AgentContact агента по видимому номеру Telegram-пользователя (или None).
    Учитывает и второй номер карточки (ветка 2110-client-memory)."""
    from backend.services.agent_contact_phones import find_contact_by_phone
    return find_contact_by_phone(db, agent_config_id, phone)


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

    snap = await tg_user.poll_dialogs(session_str, known, agent_pause.reply_delay_seconds(account))
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

    now = datetime.utcnow()
    runs = 0
    for d in snap.get("dialogs", []):
        peer_id = d["peer_id"]
        row = by_peer.get(peer_id)
        msgs = d.get("new_messages") or []
        top_id = int(d.get("top_id") or 0)

        if row is None:
            # Диалог появился после подключения (или baseline его не покрыл).
            # Маркер ставим перед первым новым сообщением: при отложенной
            # обработке (deferred, лимит за тик) они останутся «новыми».
            contact = _match_contact_by_phone(db, account.agent_config_id, d.get("phone"))
            first_id = min((int(m["id"]) for m in msgs), default=top_id + 1)
            row = AgentTelegramDialog(
                account_id=account.id,
                agent_contact_id=(contact.id if contact else None),
                tg_peer_id=peer_id,
                tg_username=d.get("username"),
                tg_name=d.get("name"),
                last_processed_msg_id=max(first_id - 1, 0),
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

        # Нет новых сообщений — просто двигаем маркер к top_id, чтобы не
        # пере-сканировать этот диалог.
        if not msgs:
            if top_id > int(row.last_processed_msg_id or 0):
                row.last_processed_msg_id = top_id
            continue

        # «Отвечать через N секунд»: клиент ещё пишет — маркер не трогаем,
        # вернёмся в следующий тик.
        if d.get("deferred"):
            continue

        inbound = [m for m in msgs if not m.get("out")]
        outgoing = [m for m in msgs if m.get("out")]
        agent_ids = _agent_sent_ids(db, account.id, [m["id"] for m in outgoing])
        owner_msgs = [m for m in outgoing if int(m["id"]) not in agent_ids]
        # Отправка агента могла уйти в Telegram, но ещё не записаться в БД —
        # свежие «чужие» исходящие разбираем в следующий тик, чтобы не принять
        # агента за владельца.
        if any(m.get("date") and (now - m["date"]).total_seconds() < OWNER_ECHO_GRACE_SECONDS
               for m in owner_msgs):
            continue

        # Превышен лимит запусков за тик — маркер НЕ трогаем, добьём в следующий
        # тик (сообщения останутся «новыми»).
        if inbound and runs >= MAX_DIALOG_RUNS_PER_TICK:
            continue

        # Охват автоответа: contacts — только привязанные диалоги.
        contact_id = row.agent_contact_id
        if contact_id is None and (inbound or owner_msgs):
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
            logger.info(f"[TG-POLLER] 🆕 Создан AgentContact {contact_id} для TG {peer_id}")

        # Сообщения, которые владелец написал сам: в тред (агент увидит их
        # после паузы) и пауза агента с этим клиентом во всех каналах.
        for m in owner_msgs:
            msg_row = tg_user.store_message(
                db, account, "outbound", m["text"],
                agent_contact_id=contact_id, tg_peer_id=peer_id, tg_message_id=m["id"],
            )
            if msg_row is not None:
                msg_row.sent_by = "owner"
            agent_pause.on_owner_message(
                db, contact_id, agent_pause.owner_pause_minutes(account), m.get("date"),
            )

        # Сохраняем входящие в тред и продвигаем маркер ДО запуска оркестратора
        # (идемпотентность: падение LLM не приведёт к повторной обработке).
        # Вложения пока пишутся заглушкой — текст подставит _process_media_and_dispatch.
        stored = []
        for m in inbound:
            msg_row = tg_user.store_message(
                db, account, "inbound", _placeholder(m),
                agent_contact_id=contact_id,
                tg_peer_id=peer_id,
                tg_message_id=m["id"],
            )
            stored.append((m, msg_row))
        max_msg_id = max([int(row.last_processed_msg_id or 0)] + [int(m["id"] or 0) for m in msgs])
        # Двигаем к top_id (он >= max нового сообщения).
        row.last_processed_msg_id = max(max_msg_id, top_id)
        db.commit()

        if not inbound:
            continue

        logger.info(
            f"[TG-POLLER] {len(inbound)} new message(s) from peer {peer_id} "
            f"(account {account.id}) → orchestrator"
        )
        if any(m.get("media") for m in inbound):
            items = [(m, (r.id if r is not None else None)) for m, r in stored]
            asyncio.create_task(_process_media_and_dispatch(str(account.id), str(contact_id), items))
        else:
            text_joined = "\n".join(m["text"] for m in inbound)
            asyncio.create_task(handle_inbound_telegram(str(account.id), str(contact_id), text_joined))
        runs += 1

    db.commit()


def _agent_sent_ids(db, account_id, ids) -> set:
    """Какие из исходящих tg_message_id отправил агент (записаны тулзой)."""
    from backend.models.agent_telegram_account import AgentTelegramMessage
    ids = [int(i) for i in ids if i]
    if not ids:
        return set()
    rows = db.query(AgentTelegramMessage.tg_message_id).filter(
        AgentTelegramMessage.account_id == account_id,
        AgentTelegramMessage.tg_message_id.in_(ids),
    ).all()
    return {int(r[0]) for r in rows if r[0] is not None}


def _placeholder(m) -> str:
    """Тело сообщения до обработки вложения (подпись + вид вложения)."""
    from backend.services.agent_media_service import KIND_LABELS
    media = m.get("media")
    if not media:
        return m["text"]
    label = KIND_LABELS.get(media.get("kind") or "other", KIND_LABELS["other"])
    return ((m.get("text") or "") + f"\n{label} (обрабатывается…)").strip()


async def _process_media_and_dispatch(account_id: str, contact_id: str, items) -> None:
    """
    Фоном: распознать вложения входящих (голосовые → Whisper, фото и сканы →
    OCR, документы → текст), подставить результат в сохранённые сообщения
    треда и отдать оркестратору итоговый текст. Медиа обрабатывается только
    если агент активен и у владельца есть доступ — иначе кредиты не тратим, а
    handle_inbound_telegram всё равно пропустит сообщение.
    """
    from backend.models.agent_config import AgentConfig
    from backend.models.agent_telegram_account import AgentTelegramAccount, AgentTelegramMessage
    from backend.models.user import User
    from backend.services import agent_media_service as media_svc
    from backend.services.agent_orchestrator import handle_inbound_telegram

    db = SessionLocal()
    parts = []
    try:
        account = db.query(AgentTelegramAccount).filter(AgentTelegramAccount.id == account_id).first()
        agent = db.query(AgentConfig).filter(AgentConfig.id == account.agent_config_id).first() if account else None
        user = db.query(User).filter(User.id == account.user_id).first() if account else None
        allowed = bool(agent and agent.is_active and user)
        if allowed:
            try:
                allowed = user.has_agent_chat_access()
            except Exception:
                pass

        for m, msg_row_id in items:
            media = m.get("media")
            if not media:
                parts.append(m["text"])
                continue
            if not allowed:
                plain = _placeholder(m).replace(" (обрабатывается…)", "")
                parts.append(plain)
                if msg_row_id:
                    db.query(AgentTelegramMessage).filter(
                        AgentTelegramMessage.id == msg_row_id
                    ).update({"body": plain}, synchronize_session=False)
                    db.commit()
                continue
            try:
                file_row = await media_svc.process_inbound(
                    db,
                    user_id=account.user_id,
                    agent_config_id=account.agent_config_id,
                    agent_contact_id=contact_id,
                    channel="telegram",
                    external_message_id=m["id"],
                    data=media.get("data"),
                    filename=media.get("filename") or "file",
                    mime=media.get("mime"),
                    kind_hint=media.get("kind"),
                    duration_hint=media.get("duration"),
                    error=media.get("error"),
                )
                parts.append(media_svc.describe_for_prompt(file_row, caption=m.get("text")))
                if msg_row_id:
                    msg_row = db.query(AgentTelegramMessage).filter(AgentTelegramMessage.id == msg_row_id).first()
                    if msg_row is not None:
                        msg_row.body = media_svc.describe_for_prompt(file_row, caption=m.get("text"), preview_chars=500)
                        msg_row.attachment_id = file_row.id
                        db.commit()
            except Exception as e:
                logger.error(f"[TG-POLLER] media processing failed (msg {m.get('id')}): {e}", exc_info=True)
                try:
                    db.rollback()
                except Exception:
                    pass
                parts.append(_placeholder(m).replace(" (обрабатывается…)", " — не удалось обработать"))
    except Exception as e:
        logger.error(f"[TG-POLLER] media dispatch failed for account {account_id}: {e}", exc_info=True)
        if not parts:
            parts = [_placeholder(m).replace(" (обрабатывается…)", "") for m, _ in items]
    finally:
        db.close()

    text_joined = "\n".join(p for p in parts if p)
    if text_joined:
        await handle_inbound_telegram(account_id, contact_id, text_joined)
