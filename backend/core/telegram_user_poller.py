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
откладывается до следующего тика.

Слушатель (15.11.2026, «Telegram отвечает раз в 2 минуты, а WhatsApp сразу»):
опрос раз в 30 с плюс откладывание «клиент ещё пишет» ещё на тик давали
задержку 30–90 с поверх настройки «Отвечать через N секунд». Теперь каждый
воркер держит ПОСТОЯННОЕ соединение Telethon для аккаунтов, которые ему
достались (аренда через last_poll_at в будущем, см. _lease_*), и ловит новые
сообщения событием: через reply_delay_seconds после последнего сообщения
клиента (5/10/15 с из настроек) диалог разбирается тем же _poll_account.
Опрос раз в POLL_SECONDS остаётся страховкой: слушатель сам делает его на
своём соединении, а аккаунт без слушателя (не подключился) — по-старому.

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
from backend.core.spawn import spawn
from backend.db.session import SessionLocal
from backend.services import agent_pause
from backend.services import telegram_user_service as tg_user

logger = get_logger(__name__)

# Claim должен «протухать» чуть раньше интервала, чтобы не пропускать тики.
from backend.core.config import settings as _settings  # noqa: E402
POLL_SECONDS = max(15, int(getattr(_settings, "TG_POLL_SECONDS", 30) or 30))
CLAIM_CUTOFF_SECONDS = POLL_SECONDS - 5

# Исходящее, которого нет среди отправок агента, моложе этого — ещё может
# оказаться отправкой агента, не успевшей записаться в БД; ждём тик.
OWNER_ECHO_GRACE_SECONDS = 20

# Максимум запусков оркестратора с одного аккаунта за тик (защита от лавины
# после долгого простоя/переподключения).
MAX_DIALOG_RUNS_PER_TICK = 5


# Слушатель: аренда аккаунта воркером и её продление.
LISTENER_ENABLED = str(getattr(_settings, "TG_LISTENER_ENABLED", "1") or "1").strip().lower() not in ("0", "false", "no")
LEASE_SECONDS = 45
MANAGE_SECONDS = 10
# После неудачного подключения слушателя этот воркер не пробует аккаунт столько секунд.
LISTENER_BACKOFF_SECONDS = 60
# Запас к «Отвечать через N секунд»: дата сообщения в Telegram — секунды сервера.
DUE_MARGIN_SECONDS = 1.0
# Диалог ещё «пишет» (deferred) — перепроверить через столько.
DEFERRED_RECHECK_SECONDS = 3.0


async def start_telegram_user_poller(check_interval: int = POLL_SECONDS):
    """Запуск поллера. No-op, если TELEGRAM_API_ID/HASH/SESSION_KEY не заданы."""
    if not tg_user.is_configured():
        logger.info("[TG-POLLER] Telegram user connector not configured, poller disabled")
        return

    logger.info(f"[TG-POLLER] Started (poll every {check_interval}s, live listener "
                f"{'on' if LISTENER_ENABLED else 'off'})")
    last_poll = 0.0
    loop = asyncio.get_running_loop()
    while True:
        try:
            if LISTENER_ENABLED:
                await _manage_listeners()
            if loop.time() - last_poll >= check_interval:
                last_poll = loop.time()
                await _tick()
        except Exception as e:
            logger.error(f"[TG-POLLER] tick error: {e}", exc_info=True)
        await asyncio.sleep(MANAGE_SECONDS if LISTENER_ENABLED else check_interval)


# ============================================================================
# СЛУШАТЕЛЬ (постоянное соединение, ответ через reply_delay_seconds)
# ============================================================================

_LISTENERS: dict = {}       # account_id(str) → _Listener
_BACKOFF: dict = {}         # account_id(str) → monotonic time, до которого не пробуем


def _lease_acquire(db, account_id):
    """Арендовать аккаунт: last_poll_at = now + LEASE. Токен аренды или None."""
    from backend.models.agent_telegram_account import AgentTelegramAccount
    now = datetime.utcnow()
    token = now + timedelta(seconds=LEASE_SECONDS)
    got = db.query(AgentTelegramAccount).filter(
        AgentTelegramAccount.id == account_id,
        or_(AgentTelegramAccount.last_poll_at.is_(None),
            AgentTelegramAccount.last_poll_at < now - timedelta(seconds=CLAIM_CUTOFF_SECONDS)),
    ).update({"last_poll_at": token}, synchronize_session=False)
    db.commit()
    return token if got else None


def _lease_renew(db, account_id, token):
    from backend.models.agent_telegram_account import AgentTelegramAccount
    new = datetime.utcnow() + timedelta(seconds=LEASE_SECONDS)
    got = db.query(AgentTelegramAccount).filter(
        AgentTelegramAccount.id == account_id,
        AgentTelegramAccount.last_poll_at == token,
    ).update({"last_poll_at": new}, synchronize_session=False)
    db.commit()
    return new if got else None


def _lease_release(db, account_id, token) -> None:
    """Отпустить аренду так, чтобы другой воркер мог взять аккаунт сразу."""
    from backend.models.agent_telegram_account import AgentTelegramAccount
    db.query(AgentTelegramAccount).filter(
        AgentTelegramAccount.id == account_id,
        AgentTelegramAccount.last_poll_at == token,
    ).update({"last_poll_at": datetime.utcnow() - timedelta(seconds=POLL_SECONDS)},
             synchronize_session=False)
    db.commit()


async def _manage_listeners() -> None:
    """Раз в MANAGE_SECONDS: продлить свои аренды, взять свободные аккаунты, убрать лишние."""
    from backend.models.agent_telegram_account import AgentTelegramAccount

    db = SessionLocal()
    try:
        wanted = {
            str(r.id) for r in db.query(AgentTelegramAccount.id).filter(
                AgentTelegramAccount.status == "connected",
                AgentTelegramAccount.auto_reply_enabled == True,  # noqa: E712
            ).all()
        }
        loop = asyncio.get_running_loop()
        for acc_id, lst in list(_LISTENERS.items()):
            if acc_id not in wanted or lst.stopped:
                await lst.stop(db)
                _LISTENERS.pop(acc_id, None)
                continue
            token = _lease_renew(db, acc_id, lst.token)
            if token is None:
                logger.warning(f"[TG-LISTEN] account {acc_id}: аренду забрал другой воркер — останавливаю слушатель")
                lst.token = None
                await lst.stop(db)
                _LISTENERS.pop(acc_id, None)
            else:
                lst.token = token
        for acc_id in wanted - set(_LISTENERS):
            if _BACKOFF.get(acc_id, 0) > loop.time():
                continue
            token = _lease_acquire(db, acc_id)
            if token is None:
                continue
            lst = _Listener(acc_id, token)
            _LISTENERS[acc_id] = lst
            lst.task = spawn(lst.run(), name=f"tg-listen-{acc_id[:8]}")
    except Exception as e:
        logger.error(f"[TG-LISTEN] manage failed: {e}", exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()


class _Listener:
    """Постоянное соединение одного аккаунта: событие → разбор через reply_delay_seconds."""

    def __init__(self, account_id: str, token):
        self.account_id = account_id
        self.token = token
        self.task = None
        self.client = None
        self.session_str = None
        self.stopped = False
        self._wake = asyncio.Event()
        self._due = None            # loop.time(), когда разобрать диалоги
        self._lock = asyncio.Lock()

    def note_message(self, delay_seconds: float) -> None:
        loop = asyncio.get_running_loop()
        self._due = loop.time() + max(0.0, float(delay_seconds)) + DUE_MARGIN_SECONDS
        self._wake.set()

    async def run(self) -> None:
        from telethon import events
        from backend.models.agent_telegram_account import AgentTelegramAccount

        loop = asyncio.get_running_loop()
        db = SessionLocal()
        try:
            account = db.query(AgentTelegramAccount).filter(AgentTelegramAccount.id == self.account_id).first()
            if not account or not account.session_encrypted:
                self.stopped = True
                return
            self.session_str = tg_user.decrypt_session(account.session_encrypted)
            delay = agent_pause.reply_delay_seconds(account)
        except Exception as e:
            logger.error(f"[TG-LISTEN] account {self.account_id}: не удалось прочитать сессию: {e}")
            self.stopped = True
            db.close()
            return
        finally:
            try:
                db.close()
            except Exception:
                pass

        try:
            self.client = tg_user._new_client(self.session_str)
            await self.client.connect()
            if not await self.client.is_user_authorized():
                logger.warning(f"[TG-LISTEN] account {self.account_id}: сессия не авторизована — слушатель не запущен")
                _BACKOFF[self.account_id] = loop.time() + LISTENER_BACKOFF_SECONDS
                self.stopped = True
                return

            listener = self

            async def on_new(event):
                try:
                    if not event.is_private:
                        return
                    # Свежая настройка задержки: владелец мог поменять её в кабинете.
                    listener.note_message(listener._delay_cache)
                except Exception as e:
                    logger.warning(f"[TG-LISTEN] event handler: {e}")

            self._delay_cache = delay
            self.client.add_event_handler(on_new, events.NewMessage())
            await self.client.get_me()   # Telegram начинает слать обновления этому соединению
            tg_user.register_live_client(self.session_str, self.client)
            logger.info(f"[TG-LISTEN] account {self.account_id}: слушаю (ответ через {delay} с после сообщения)")

            self._due = loop.time()      # сразу один разбор — догнать пропущенное
            last_full = loop.time()
            while not self.stopped:
                if not self.client.is_connected():
                    logger.warning(f"[TG-LISTEN] account {self.account_id}: соединение потеряно")
                    break
                now = loop.time()
                wait = POLL_SECONDS - (now - last_full)
                if self._due is not None:
                    wait = min(wait, self._due - now)
                if wait > 0:
                    self._wake.clear()
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=wait)
                        continue          # пришло новое сообщение — пересчитать срок
                    except asyncio.TimeoutError:
                        pass
                self._due = None
                last_full = loop.time()
                deferred = await self.poll_once()
                if deferred:
                    self._due = loop.time() + DEFERRED_RECHECK_SECONDS
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[TG-LISTEN] account {self.account_id}: слушатель упал: {type(e).__name__}: {e}", exc_info=True)
            _BACKOFF[self.account_id] = loop.time() + LISTENER_BACKOFF_SECONDS
        finally:
            self.stopped = True
            await self._disconnect()

    async def poll_once(self) -> int:
        """Разобрать новые сообщения на своём соединении. Возвращает число отложенных диалогов."""
        from backend.models.agent_telegram_account import AgentTelegramAccount
        async with self._lock:
            db = SessionLocal()
            try:
                account = db.query(AgentTelegramAccount).filter(AgentTelegramAccount.id == self.account_id).first()
                if not account or account.status != "connected" or not account.auto_reply_enabled:
                    self.stopped = True
                    return 0
                self._delay_cache = agent_pause.reply_delay_seconds(account)
                return await _poll_account(db, account)
            except Exception as e:
                logger.error(f"[TG-LISTEN] account {self.account_id}: разбор упал: {e}", exc_info=True)
                try:
                    db.rollback()
                except Exception:
                    pass
                return 0
            finally:
                db.close()

    async def _disconnect(self) -> None:
        if self.session_str:
            tg_user.unregister_live_client(self.session_str, self.client)
        if self.client is not None:
            try:
                await self.client.disconnect()
            except Exception:
                pass
            self.client = None

    async def stop(self, db) -> None:
        self.stopped = True
        self._wake.set()
        if self.task is not None and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except BaseException:
                pass
        await self._disconnect()
        if self.token is not None:
            try:
                _lease_release(db, self.account_id, self.token)
            except Exception:
                db.rollback()
            self.token = None


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
            if str(account_id) in _LISTENERS:
                continue  # слушатель этого воркера разбирает сам
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
        return 0

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
        return 0

    if account.last_error:
        account.last_error = None

    now = datetime.utcnow()
    runs = 0
    deferred = 0
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
            deferred += 1
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
            deferred += 1
            continue

        # Превышен лимит запусков за тик — маркер НЕ трогаем, добьём в следующий
        # тик (сообщения останутся «новыми»).
        if inbound and runs >= MAX_DIALOG_RUNS_PER_TICK:
            deferred += 1
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
            spawn(_process_media_and_dispatch(str(account.id), str(contact_id), items))
        else:
            text_joined = "\n".join(m["text"] for m in inbound)
            spawn(handle_inbound_telegram(str(account.id), str(contact_id), text_joined))
        runs += 1

    db.commit()
    return deferred


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
