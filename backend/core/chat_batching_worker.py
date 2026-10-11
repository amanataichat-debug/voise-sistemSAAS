"""
Chat Batching Worker — отдаёт накопленные входящие сообщения в оркестратор.

Пара к telegram_user_poller: поллер складывает входящие MTProto-сообщения в
agent_telegram_dialogs.pending_messages и ставит pending_flush_at на now()
+ окно тишины (chat_batching_window_seconds, 15с), не превышая hard cap
(chat_batching_max_window_seconds, 60с от первого непрочитанного). Этот
воркер раз в check_interval секунд выбирает диалоги с истёкшим
pending_flush_at, атомарно их занимает и зовёт handle_inbound_telegram
со склеенным текстом — ровно одно сообщение в оркестратор на всю пачку.

Мультиворкер:
  Атомарный claim через UPDATE ... SET pending_flush_at = now() + 1 hour
  WHERE id = :id AND pending_flush_at <= :now. Если rowcount > 0 — текущий
  gunicorn-воркер захватил диалог. При падении LLM следующий flush
  произойдёт через час (fail-safe), и то только если за это время не
  пришли новые сообщения, которые переустановят pending_flush_at.

Идемпотентность:
  Пачка pending_messages очищается ДО вызова handle_inbound_telegram — так
  падение оркестратора не вызовет повторной обработки тех же сообщений.
  Сообщения уже сохранены поллером в agent_telegram_messages (тред контакта),
  так что контекст для следующего прогона всё равно есть.
"""

import asyncio
from datetime import datetime, timedelta

from sqlalchemy.orm.attributes import flag_modified

from backend.core.logging import get_logger
from backend.db.session import SessionLocal

logger = get_logger(__name__)

# Как часто сканируем pending-очереди. Поллер MTProto работает с 10с
# интервалом, окно тишины — 15с, так что 2с здесь хватает чтобы не
# «пропустить» flush больше чем на пару секунд.
DEFAULT_CHECK_INTERVAL = 2

# Максимум диалогов, обрабатываемых за один тик (защита от лавины).
MAX_FLUSHES_PER_TICK = 25

# Fail-safe: если handle_inbound_telegram упал, через столько сможет
# подхватить следующий воркер. Больше окна — чтобы не ретраить сразу.
CLAIM_RETRY_HOURS = 1


async def start_chat_batching_worker(check_interval: int = DEFAULT_CHECK_INTERVAL):
    """
    Запуск воркера. Запускается в каждом gunicorn-воркере на startup;
    конкуренция решается атомарным claim по agent_telegram_dialogs.
    """
    logger.info(f"[CHAT-BATCH] Started (check every {check_interval}s)")
    while True:
        try:
            await _tick()
        except Exception as e:
            logger.error(f"[CHAT-BATCH] tick error: {e}", exc_info=True)
        await asyncio.sleep(check_interval)


async def _tick():
    from backend.models.agent_telegram_account import AgentTelegramDialog

    db = SessionLocal()
    try:
        now = datetime.utcnow()
        candidate_ids = [
            row.id for row in db.query(AgentTelegramDialog.id).filter(
                AgentTelegramDialog.pending_flush_at.isnot(None),
                AgentTelegramDialog.pending_flush_at <= now,
            ).order_by(AgentTelegramDialog.pending_flush_at.asc())
             .limit(MAX_FLUSHES_PER_TICK).all()
        ]
        for dialog_id in candidate_ids:
            if not _claim(db, dialog_id):
                continue
            try:
                await _flush_dialog(db, dialog_id)
            except Exception as e:
                logger.error(
                    f"[CHAT-BATCH] flush dialog {dialog_id} error: {e}",
                    exc_info=True,
                )
                try:
                    db.rollback()
                except Exception:
                    pass
    finally:
        db.close()


def _claim(db, dialog_id) -> bool:
    """
    Атомарно занять диалог: сдвигаем pending_flush_at на час вперёд. Если
    в это время мы падаем до очистки pending_messages — следующий тик
    через час (CLAIM_RETRY_HOURS) попробует снова. Нормальный путь всё
    равно очищает очередь внутри _flush_dialog.
    """
    from backend.models.agent_telegram_account import AgentTelegramDialog

    now = datetime.utcnow()
    retry_at = now + timedelta(hours=CLAIM_RETRY_HOURS)
    claimed = db.query(AgentTelegramDialog).filter(
        AgentTelegramDialog.id == dialog_id,
        AgentTelegramDialog.pending_flush_at.isnot(None),
        AgentTelegramDialog.pending_flush_at <= now,
    ).update({"pending_flush_at": retry_at}, synchronize_session=False)
    db.commit()
    return bool(claimed)


async def _flush_dialog(db, dialog_id):
    """
    Собрать pending_messages, очистить состояние, запустить оркестратор.

    Без agent_contact_id (диалог ещё не привязан к контакту) — просто
    чистим очередь, оркестратор не вызываем: поллер привяжет контакт
    при следующей встрече по номеру/username, тогда и ответим.
    """
    from backend.models.agent_telegram_account import AgentTelegramDialog
    from backend.services.agent_orchestrator import handle_inbound_telegram

    row = db.query(AgentTelegramDialog).filter(
        AgentTelegramDialog.id == dialog_id
    ).first()
    if row is None:
        return

    pending = list(row.pending_messages or [])
    account_id = row.account_id
    contact_id = row.agent_contact_id

    # Чистим состояние пачки ДО вызова оркестратора — ещё одно падение не
    # вызовет повторной обработки.
    row.pending_messages = []
    row.pending_first_at = None
    row.pending_flush_at = None
    row.last_flushed_at = datetime.utcnow()
    flag_modified(row, "pending_messages")
    db.commit()

    if not pending:
        return

    if not contact_id:
        logger.info(
            f"[CHAT-BATCH] dialog {dialog_id}: {len(pending)} msg(s) без контакта — пропускаем"
        )
        return

    text_joined = "\n".join((m.get("text") or "").strip() for m in pending).strip()
    if not text_joined:
        return

    logger.info(
        f"[CHAT-BATCH] dialog {dialog_id}: flushing {len(pending)} msg(s) → orchestrator"
    )
    asyncio.create_task(
        handle_inbound_telegram(str(account_id), str(contact_id), text_joined)
    )
