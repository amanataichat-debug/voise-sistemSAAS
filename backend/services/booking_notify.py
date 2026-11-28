"""
Онлайн-запись: оповещения (ветка 1811-niche-admin-speed; с шага 2.2 Voksy Beauty — через очередь отправок).

Владельцу (Telegram-бот его агента, как остальные оповещения) — новая запись по ссылке или от бота.
Клиенту — автоматические сообщения по правилам владельца (services/automations.py): подтверждение записи,
напоминание накануне и в день визита, «спасибо» после визита (или просьба оценить 1–5), «не пришёл»,
поздравление с ДР, «пора повторить», «давно не были». Цикл раз в минуту: automations.plan() ставит сообщения
в очередь (notification_queue, один раз — idem_key + метки reminder_24h_at / reminder_2h_at / followup_at),
notify_queue.process() отправляет — в мессенджер, где клиент писал последним, с запасным каналом и повторами.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.orm import Session

from backend.services import booking_service as bs

logger = logging.getLogger(__name__)
TICK_SECONDS = 60


async def send_to_client(db: Session, user, contact, message: str) -> bool:
    """Текст клиенту в его последний мессенджер (как бот, без паузы бота). Заказы и другие разовые сообщения."""
    from backend.models.agent_config import AgentConfig
    from backend.services import agent_pause, inbox_service, notify_queue
    if contact is None or not message:
        return False
    if agent_pause.is_paused(contact) and contact.ai_paused_until and contact.ai_paused_until.year >= 9999:
        return False   # заблокирован как спам / бот выключен навсегда
    agent = db.query(AgentConfig).filter(AgentConfig.id == contact.agent_config_id).first()
    if agent is None:
        return False
    ch = inbox_service.default_channel(db, contact, inbox_service.send_channels(db, contact, agent))
    if not ch:
        return False
    return await notify_queue.send_via(db, user, contact, message, ch, agent=agent)


_OLD_KINDS = {"reminder_24h": "24h", "reminder_2h": "2h", "thanks": "thanks", "review": "review"}


def due(db: Session, now) -> list:
    """[(booking_id, kind)] — кому пора напомнить или сказать спасибо (kind: 24h | 2h | thanks | review).
    Подтверждение записи и «не пришёл» — в automations.due_bookings."""
    from backend.services import automations
    return [(bid, _OLD_KINDS[rule]) for bid, rule in automations.due_bookings(db, now) if rule in _OLD_KINDS]


async def run_once(now=None) -> int:
    """Поставить в очередь, что пора, и отправить очередь. Возвращает, сколько сообщений ушло."""
    from backend.db.session import SessionLocal
    from backend.services import automations, notify_queue
    now = now or bs.now_utc()
    db = SessionLocal()
    try:
        automations.plan(db, now)
        from backend.services import waitlist_service
        try:
            waitlist_service.tick(db, now)        # лист ожидания (шаг 2.4): просрочки и новые предложения
        except Exception as e:
            db.rollback()
            logger.warning(f"[BOOKING] waitlist tick failed: {e}")
        return await notify_queue.process(db, now)
    except Exception as e:
        db.rollback()
        logger.error(f"[BOOKING] reminders tick failed: {e}", exc_info=True)
        return 0
    finally:
        db.close()


async def start_booking_reminders_loop() -> None:
    logger.info(f"[BOOKING] reminders loop started (every {TICK_SECONDS}s)")
    while True:
        try:
            await run_once()
        except Exception as e:
            logger.error(f"[BOOKING] reminders loop error: {e}")
        await asyncio.sleep(TICK_SECONDS)


async def notify_owner_new_booking(owner_id, booking_id, by: str = "online") -> bool:
    """Владельцу в Telegram: новая запись (по ссылке или от бота)."""
    from backend.db.session import SessionLocal
    from backend.models.agent_config import AgentConfig
    from backend.models.booking import Booking, BookingResource
    from backend.services.owner_notifications import _send
    db = SessionLocal()
    try:
        b = db.query(Booking).filter(Booking.id == booking_id).first()
        agent = db.query(AgentConfig).filter(AgentConfig.user_id == owner_id).order_by(AgentConfig.created_at).first()
        if b is None or agent is None:
            return False
        res = db.query(BookingResource).filter(BookingResource.id == b.resource_id).first() if b.resource_id else None
        head = {"online": "📅 Новая онлайн-запись", "bot": "📅 Бот записал клиента", "call": "📅 Запись со звонка",
                "waitlist": "📅 Запись из листа ожидания"}.get(by, "📅 Новая запись")
        lines = [f"**{head}**", f"{b.client_name or 'Клиент'}" + (f" · {b.client_phone}" if b.client_phone else ""),
                 f"{bs.fmt_local(b.start_at)}" + (f" — {b.service_title}" if b.service_title else "")
                 + (f" · {res.name}" if res else "")]
        if b.note:
            lines.append(f"Комментарий: {b.note[:200]}")
        return await _send(agent, db, "\n".join(lines))
    except Exception as e:
        logger.warning(f"[BOOKING] owner notify failed: {e}")
        return False
    finally:
        db.close()
