"""
Instagram Poller — периодический опрос входящих DM подключённых
Instagram-коннекторов агентов (Composio, toolkit INSTAGRAM).

У тулкита INSTAGRAM нет триггеров/вебхуков через Composio, поэтому — поллинг
(как telegram_user_poller у личного Telegram): раз в check_interval секунд для
каждого коннектора со status='connected' снимается срез DM-тредов
(LIST_ALL_CONVERSATIONS → LIST_ALL_MESSAGES), новые входящие сохраняются в
agent_instagram_messages и уходят в PostCall-оркестратор
(handle_inbound_instagram), который отвечает клиенту тулзой
instagram_send_message.

Мультиворкер: startup запускает поллер в КАЖДОМ воркере, коннектор достаётся
ровно одному через атомарный claim по БД (agent_connectors.last_poll_at) — та
же схема, что у telegram_user_poller.

Идемпотентность: входящие сохраняются в тред и watermark
(conversation.last_processed_at) продвигается ДО запуска оркестратора; дедуп —
по ig_message_id (mid Graph API).

Первый опрос нового треда: чтобы при подключении агент не бросился отвечать
на всю старую переписку, для тредов без watermark обрабатываются только
сообщения свежее NEW_CONVERSATION_LOOKBACK_MINUTES; остальное молча
становится baseline'ом.
"""

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import or_

from backend.core.logging import get_logger
from backend.db.session import SessionLocal
from backend.services import instagram_service as ig

logger = get_logger(__name__)

# Интервал опроса и «протухание» claim'а (чуть меньше интервала).
CHECK_INTERVAL_SECONDS = 90
CLAIM_CUTOFF_SECONDS = 80

# Сколько тредов смотрим за тик и сколько сообщений берём из треда.
POLL_CONVERSATIONS_LIMIT = 15
POLL_MESSAGES_PER_CONVERSATION = 15

# Максимум запусков оркестратора с одного коннектора за тик.
MAX_CONVERSATION_RUNS_PER_TICK = 5

# Тред без watermark (первый раз видим): обрабатываем только сообщения свежее
# этого окна, остальные — baseline. Поллер ходит каждые ~90с, так что живые
# новые сообщения в окно попадают всегда.
NEW_CONVERSATION_LOOKBACK_MINUTES = 15


async def start_instagram_poller(check_interval: int = CHECK_INTERVAL_SECONDS):
    """Запуск поллера. No-op, если Composio/Instagram не настроены на сервере."""
    if not ig.is_configured():
        logger.info("[IG-POLLER] Instagram connector not configured, poller disabled")
        return

    logger.info(f"[IG-POLLER] Started (check every {check_interval}s)")
    while True:
        try:
            await _tick()
        except Exception as e:
            logger.error(f"[IG-POLLER] tick error: {e}", exc_info=True)
        await asyncio.sleep(check_interval)


async def _tick():
    from backend.models.agent_connector import AgentConnector

    db = SessionLocal()
    try:
        connector_ids = [
            row.id for row in db.query(AgentConnector.id).filter(
                AgentConnector.toolkit == "instagram",
                AgentConnector.status == "connected",
            ).all()
        ]
        for connector_id in connector_ids:
            if not _claim(db, connector_id):
                continue
            connector = db.query(AgentConnector).filter(
                AgentConnector.id == connector_id
            ).first()
            if not connector:
                continue
            try:
                await _poll_connector(db, connector)
            except Exception as e:
                logger.error(f"[IG-POLLER] connector {connector_id} poll error: {e}", exc_info=True)
                try:
                    db.rollback()
                except Exception:
                    pass
    finally:
        db.close()


def _claim(db, connector_id) -> bool:
    """Атомарно занять коннектор на этот тик (ровно один воркер из всех)."""
    from backend.models.agent_connector import AgentConnector

    now = datetime.utcnow()
    cutoff = now - timedelta(seconds=CLAIM_CUTOFF_SECONDS)
    claimed = db.query(AgentConnector).filter(
        AgentConnector.id == connector_id,
        or_(
            AgentConnector.last_poll_at.is_(None),
            AgentConnector.last_poll_at < cutoff,
        ),
    ).update({"last_poll_at": now}, synchronize_session=False)
    db.commit()
    return bool(claimed)


async def _ensure_own_account_id(db, connector) -> str:
    """
    Свой IG User ID (для отличения своих сообщений от входящих). Лениво
    резолвится через INSTAGRAM_GET_USER_INFO('me') и кэшируется в строке
    коннектора (external_account_id). Пустая строка — резолв не удался.
    """
    if connector.external_account_id:
        return connector.external_account_id
    composio_user_id = connector.composio_user_id or f"agent_{connector.agent_config_id}"
    me = await ig.get_me(composio_user_id)
    if me.get("ok") and me.get("id"):
        connector.external_account_id = me["id"]
        if me.get("username") and not connector.connected_email:
            connector.connected_email = f"@{me['username']}"
        db.commit()
        logger.info(f"[IG-POLLER] resolved own IG id {me['id']} for connector {connector.id}")
        return me["id"]
    logger.warning(f"[IG-POLLER] failed to resolve own IG id for connector {connector.id}: {me.get('error')}")
    return ""


async def _poll_connector(db, connector):
    from backend.models.agent_instagram import AgentInstagramConversation
    from backend.services.agent_orchestrator import handle_inbound_instagram

    own_id = await _ensure_own_account_id(db, connector)
    if not own_id:
        return  # без своего id не отличить направление — пропускаем тик

    composio_user_id = connector.composio_user_id or f"agent_{connector.agent_config_id}"
    convs = await ig.list_conversations(composio_user_id, limit=POLL_CONVERSATIONS_LIMIT)
    if not convs.get("ok"):
        logger.warning(f"[IG-POLLER] list_conversations failed for connector {connector.id}: {convs.get('error')}")
        return

    rows = db.query(AgentInstagramConversation).filter(
        AgentInstagramConversation.agent_config_id == connector.agent_config_id
    ).all()
    by_conv_id = {r.ig_conversation_id: r for r in rows}

    runs = 0
    for conv in convs.get("conversations", []):
        conv_id = str(conv.get("id") or "")
        if not conv_id:
            continue
        row = by_conv_id.get(conv_id)
        updated_at = ig.parse_ig_time(conv.get("updated_time"))

        # Тред не менялся с последней обработки — сообщений не дёргаем.
        if row is not None and row.last_processed_at and updated_at \
                and updated_at <= row.last_processed_at:
            continue
        if runs >= MAX_CONVERSATION_RUNS_PER_TICK:
            continue

        msgs = await ig.list_messages(
            composio_user_id, conv_id, limit=POLL_MESSAGES_PER_CONVERSATION
        )
        if not msgs.get("ok"):
            logger.warning(f"[IG-POLLER] list_messages failed conv={conv_id}: {msgs.get('error')}")
            continue

        processed = await _process_conversation(
            db, connector, row, conv_id, msgs.get("messages", []), own_id,
            handle_inbound_instagram,
        )
        if processed:
            runs += 1

    db.commit()


async def _process_conversation(db, connector, row, conv_id, messages, own_id,
                                handle_inbound_instagram) -> bool:
    """
    Обработать один тред: сохранить новые сообщения, привязать/создать контакт,
    продвинуть watermark и (для входящих) запустить оркестратор.
    Возвращает True, если оркестратор был запущен.
    """
    from backend.models.agent_contact import AgentContact
    from backend.models.agent_instagram import AgentInstagramConversation

    # Нормализуем сообщения: (mid, время, from_id, from_username, текст),
    # старые → новые (Graph отдаёт новые первыми).
    parsed = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        frm = m.get("from") or {}
        parsed.append({
            "mid": str(m.get("id") or ""),
            "ts": ig.parse_ig_time(m.get("created_time")),
            "from_id": str(frm.get("id") or ""),
            "from_username": frm.get("username"),
            "text": (m.get("message") or "").strip(),
            "attachments": ig.parse_attachments(m),
        })
    parsed = [m for m in parsed if m["mid"] and m["ts"]]
    parsed.sort(key=lambda m: m["ts"])
    if not parsed:
        return False

    newest_ts = parsed[-1]["ts"]

    # Собеседник (не мы) — igsid/username из любого его сообщения.
    peer = next((m for m in reversed(parsed) if m["from_id"] and m["from_id"] != own_id), None)

    if row is None:
        row = AgentInstagramConversation(
            agent_config_id=connector.agent_config_id,
            ig_conversation_id=conv_id,
            igsid=(peer["from_id"] if peer else None),
            ig_username=(peer["from_username"] if peer else None),
        )
        db.add(row)
        db.flush()
    else:
        if peer:
            if peer["from_id"] and not row.igsid:
                row.igsid = peer["from_id"]
            if peer["from_username"]:
                row.ig_username = peer["from_username"]

    # Порог «новизны»: watermark; для незнакомого треда — короткое окно,
    # чтобы не отвечать на старую переписку после подключения.
    threshold = row.last_processed_at
    if threshold is None:
        threshold = datetime.utcnow() - timedelta(minutes=NEW_CONVERSATION_LOOKBACK_MINUTES)

    fresh = [
        m for m in parsed
        if m["ts"] > threshold and not_seen(db, connector.agent_config_id, m["mid"])
    ]
    inbound = [m for m in fresh if m["from_id"] != own_id and (m["text"] or m["attachments"])]
    outbound = [m for m in fresh if m["from_id"] == own_id and (m["text"] or m["attachments"])]

    # Watermark двигаем в любом случае — тред просмотрен.
    row.last_processed_at = newest_ts

    if not inbound and not outbound:
        db.commit()
        return False

    # Контакт: существующая привязка → по igsid среди контактов агента → создать.
    contact = None
    if row.agent_contact_id:
        contact = db.query(AgentContact).filter(
            AgentContact.id == row.agent_contact_id
        ).first()
    if contact is None and row.igsid:
        contact = db.query(AgentContact).filter(
            AgentContact.agent_config_id == connector.agent_config_id,
            AgentContact.phone == f"ig:{row.igsid}",
        ).first()
    if contact is None and inbound:
        contact = AgentContact(
            agent_config_id=connector.agent_config_id,
            user_id=connector.user_id,
            phone=f"ig:{row.igsid or conv_id[:40]}",
            name=(("@" + row.ig_username) if row.ig_username else "Instagram"),
            status="new",
        )
        db.add(contact)
        db.flush()
        logger.info(f"[IG-POLLER] 🆕 Создан AgentContact {contact.id} для входящего IG {row.igsid}")
    if contact is not None and row.agent_contact_id is None:
        row.agent_contact_id = contact.id

    # Сохраняем сообщения (исходящие из приложения владельца — тоже, чтобы
    # тред был полным) ДО запуска оркестратора.
    for m in outbound:
        ig.store_message(
            db, connector.agent_config_id, "outbound", _placeholder(m, processing=False),
            agent_contact_id=(contact.id if contact else None),
            ig_conversation_id=conv_id, ig_message_id=m["mid"], sent_at=m["ts"],
        )
    stored = []
    for m in inbound:
        msg_row = ig.store_message(
            db, connector.agent_config_id, "inbound", _placeholder(m),
            agent_contact_id=(contact.id if contact else None),
            ig_conversation_id=conv_id, ig_message_id=m["mid"], sent_at=m["ts"],
        )
        stored.append((m, msg_row))
    db.commit()

    if not inbound or contact is None:
        return False

    logger.info(
        f"[IG-POLLER] {len(inbound)} new message(s) in conv {conv_id} "
        f"(connector {connector.id}) → orchestrator"
    )
    if any(m["attachments"] for m in inbound):
        items = [(m, r.id) for m, r in stored]
        asyncio.create_task(_process_media_and_dispatch(
            str(connector.agent_config_id), str(contact.id), items
        ))
    else:
        text_joined = "\n".join(m["text"] for m in inbound)
        asyncio.create_task(handle_inbound_instagram(
            str(connector.agent_config_id), str(contact.id), text_joined
        ))
    return True


def _placeholder(m, processing: bool = True) -> str:
    """Тело сообщения до обработки вложений (текст + виды вложений)."""
    from backend.services.agent_media_service import KIND_LABELS
    parts = [m["text"]] if m["text"] else []
    for a in m.get("attachments") or []:
        label = KIND_LABELS.get(a.get("kind_hint") or "other", KIND_LABELS["other"])
        parts.append(label + (" (обрабатывается…)" if processing else ""))
    return "\n".join(parts)


async def _process_media_and_dispatch(agent_config_id: str, contact_id: str, items) -> None:
    """
    Фоном: скачать вложения входящих DM (CDN-ссылки Instagram), распознать
    (голос → Whisper, фото → OCR, документы → текст), подставить результат в
    сохранённые сообщения и отдать оркестратору. Как у Telegram-поллера: при
    неактивном агенте или без доступа кредиты не тратим.
    """
    from backend.core.config import settings
    from backend.models.agent_config import AgentConfig
    from backend.models.agent_instagram import AgentInstagramMessage
    from backend.models.user import User
    from backend.services import agent_media_service as media_svc
    from backend.services.agent_orchestrator import handle_inbound_instagram

    db = SessionLocal()
    parts = []
    try:
        agent = db.query(AgentConfig).filter(AgentConfig.id == agent_config_id).first()
        user = db.query(User).filter(User.id == agent.user_id).first() if agent else None
        allowed = bool(agent and agent.is_active and user)
        if allowed:
            try:
                allowed = user.has_active_agent_subscription()
            except Exception:
                pass
        max_bytes = settings.AGENT_MEDIA_MAX_MB * 1024 * 1024

        for m, msg_row_id in items:
            if not m["attachments"]:
                parts.append(m["text"])
                continue
            if not allowed:
                plain = _placeholder(m, processing=False)
                parts.append(plain)
                db.query(AgentInstagramMessage).filter(
                    AgentInstagramMessage.id == msg_row_id
                ).update({"body": plain}, synchronize_session=False)
                db.commit()
                continue
            descr_prompt, descr_thread, first_file = [], [], None
            for a in m["attachments"]:
                try:
                    dl = await ig.download_attachment(a["url"], max_bytes)
                    file_row = await media_svc.process_inbound(
                        db,
                        user_id=user.id,
                        agent_config_id=agent.id,
                        agent_contact_id=contact_id,
                        channel="instagram",
                        external_message_id=m["mid"],
                        data=dl.get("data"),
                        filename=a.get("filename") or f"{a.get('kind_hint') or 'file'}",
                        mime=a.get("mime") or dl.get("mime"),
                        kind_hint=a.get("kind_hint"),
                        error=(None if dl.get("ok") else dl.get("error")),
                    )
                    first_file = first_file or file_row
                    descr_prompt.append(media_svc.describe_for_prompt(file_row))
                    descr_thread.append(media_svc.describe_for_prompt(file_row, preview_chars=500))
                except Exception as e:
                    logger.error(f"[IG-POLLER] attachment processing failed (mid {m['mid']}): {e}", exc_info=True)
                    try:
                        db.rollback()
                    except Exception:
                        pass
                    descr_prompt.append("📎 Вложение — не удалось обработать")
                    descr_thread.append("📎 Вложение — не удалось обработать")
            caption = f"\nПодпись клиента: {m['text']}" if m["text"] else ""
            parts.append("\n".join(descr_prompt) + caption)
            msg_row = db.query(AgentInstagramMessage).filter(AgentInstagramMessage.id == msg_row_id).first()
            if msg_row is not None:
                msg_row.body = "\n".join(descr_thread) + caption
                if first_file is not None:
                    msg_row.attachment_id = first_file.id
                db.commit()
    except Exception as e:
        logger.error(f"[IG-POLLER] media dispatch failed for agent {agent_config_id}: {e}", exc_info=True)
        if not parts:
            parts = [_placeholder(m, processing=False) for m, _ in items]
    finally:
        db.close()

    text_joined = "\n".join(p for p in parts if p)
    if text_joined:
        await handle_inbound_instagram(agent_config_id, contact_id, text_joined)


def not_seen(db, agent_config_id, mid) -> bool:
    from backend.services import instagram_service as _ig
    return not _ig.message_exists(db, agent_config_id, mid)
