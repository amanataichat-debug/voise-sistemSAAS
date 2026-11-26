"""
Онлайн-запись: оповещения (ветка 1811-niche-admin-speed).

Владельцу (Telegram-бот его агента, как остальные оповещения) — новая запись по ссылке или от бота.
Клиенту — в тот мессенджер, где он писал последним (WhatsApp / Telegram / Instagram, через те же
функции отправки, что у бота):
  • за сутки — если записался заранее (больше чем за 26 ч);
  • за 2 часа — если записался больше чем за 3 ч;
  • «спасибо, ждём снова» — через час после визита (настройка thanks).
Включается настройкой reminders / thanks страницы записи. Каждое сообщение — один раз: метка
reminder_24h_at / reminder_2h_at / followup_at ставится атомарным UPDATE (несколько воркеров).
Клиенту, которого бот заблокировал как спам (пауза навсегда), ничего не шлём. Нет мессенджера —
пропускаем молча (SMS нет).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.services import booking_service as bs

logger = logging.getLogger(__name__)
TICK_SECONDS = 60


def _first_name(b) -> str:
    n = (b.client_name or "").strip().split()
    return n[0] if n else ""


def _when_words(b, now) -> str:
    st = bs.to_local(b.start_at)
    today = bs.to_local(now).date()
    hm = st.strftime("%H:%M")
    if st.date() == today:
        return f"сегодня в {hm}"
    if st.date() == today + timedelta(days=1):
        return f"завтра в {hm}"
    return f"{st.day:02d}.{st.month:02d} в {hm}"


def reminder_text(b, kind: str, resource_name: Optional[str], address: str, now) -> str:
    name = _first_name(b)
    hello = f"Здравствуйте{', ' + name if name else ''}!"
    what = f" на «{b.service_title}»" if b.service_title else ""
    who = f" к мастеру {resource_name}" if resource_name else ""
    addr = f" Адрес: {address}." if address else ""
    if kind == "24h":
        return (f"{hello} Напоминаем: {_when_words(b, now)} вы записаны{what}{who}.{addr} "
                "Если планы изменились — напишите, перенесём на удобное время.")
    if kind == "2h":
        return f"{hello} Ждём вас {_when_words(b, now)}{what}.{addr}"
    return (f"{name + ', с' if name else 'С'}пасибо, что пришли к нам! Будем рады видеть вас снова. "
            "Если что-то понравилось или нет — напишите, нам важно.")


async def send_to_client(db: Session, user, contact, message: str) -> bool:
    """Текст клиенту в его последний мессенджер (как бот, без паузы бота)."""
    from backend.models.agent_config import AgentConfig
    from backend.services import agent_tools, inbox_service, agent_pause
    if contact is None or not message:
        return False
    if agent_pause.is_paused(contact) and contact.ai_paused_until and contact.ai_paused_until.year >= 9999:
        return False   # заблокирован как спам / бот выключен навсегда
    agent = db.query(AgentConfig).filter(AgentConfig.id == contact.agent_config_id).first()
    if agent is None:
        return False
    avail = inbox_service.send_channels(db, contact, agent)
    ch = inbox_service.default_channel(db, contact, avail)
    if not ch:
        return False
    args = {"agent_contact_id": str(contact.id), "text": message}
    try:
        if ch == "whatsapp":
            res = await agent_tools.fn_whatsapp_send_message(args, user.id, agent, db, skip_rate_limit=True, typing_ms=0)
        elif ch == "telegram":
            res = await agent_tools.fn_telegram_send_message(args, user.id, agent, db, skip_rate_limit=True, typing_seconds=0)
        else:
            res = await agent_tools.fn_instagram_send_message(args, user.id, agent, db, skip_rate_limit=True)
    except Exception as e:
        logger.warning(f"[BOOKING] reminder send failed ({ch}): {e}")
        return False
    return bool(res and res.get("ok"))


def _claim(db: Session, booking_id, column: str) -> bool:
    row = db.execute(text(f"UPDATE bookings SET {column} = now() WHERE id = :id AND {column} IS NULL RETURNING id"),
                     {"id": booking_id}).first()
    db.commit()
    return row is not None


def due(db: Session, now) -> list:
    """[(booking_id, kind)] — кому пора напомнить или сказать спасибо."""
    out = []
    rows = db.execute(text("""
        SELECT b.id, b.start_at, b.end_at, b.created_at, b.status, b.reminder_24h_at, b.reminder_2h_at, b.followup_at,
               p.settings, a.manager_settings
        FROM bookings b JOIN booking_pages p ON p.user_id = b.user_id
        LEFT JOIN agent_contacts c ON c.id = b.contact_id
        LEFT JOIN agent_configs a ON a.id = c.agent_config_id
        WHERE b.contact_id IS NOT NULL AND b.status IN ('booked', 'confirmed', 'arrived', 'completed')
          AND b.start_at < :max_start AND b.end_at > :min_end
    """), {"max_start": now + timedelta(hours=24, minutes=5), "min_end": now - timedelta(hours=26)}).mappings().all()
    for r in rows:
        cfg = bs.settings_of(type("P", (), {"settings": r["settings"] or {}})())
        st, en, cr = r["start_at"], r["end_at"], r["created_at"]
        if cfg["reminders"] and r["status"] in ("booked", "confirmed"):
            if r["reminder_24h_at"] is None and st - now <= timedelta(hours=24) and st - now > timedelta(hours=3) \
                    and st - cr >= timedelta(hours=26):
                out.append((r["id"], "24h"))
            elif r["reminder_2h_at"] is None and st - now <= timedelta(hours=2) and st - now > timedelta(minutes=20) \
                    and st - cr >= timedelta(hours=3):
                out.append((r["id"], "2h"))
        # «Спасибо» после визита; со сценарием «Повтор услуги и отзыв» (шаг 1.6б) — с просьбой оценить 1–5,
        # и только за визит, на котором клиент был (не за неявку).
        review = bool(((r["manager_settings"] or {}).get("repeat_review") or {}).get("on"))
        if (cfg["thanks"] or review) and r["followup_at"] is None and timedelta(hours=1) <= now - en <= timedelta(hours=24):
            if not review or r["status"] in ("arrived", "completed"):
                out.append((r["id"], "review" if review else "thanks"))
    return out


async def run_once(now=None) -> int:
    from backend.db.session import SessionLocal
    from backend.models.agent_contact import AgentContact
    from backend.models.booking import Booking, BookingResource
    from backend.models.user import User
    now = now or bs.now_utc()
    db = SessionLocal()
    sent = 0
    try:
        for booking_id, kind in due(db, now):
            column = {"24h": "reminder_24h_at", "2h": "reminder_2h_at", "thanks": "followup_at",
                      "review": "followup_at"}[kind]
            if not _claim(db, booking_id, column):
                continue
            b = db.query(Booking).filter(Booking.id == booking_id).first()
            user = db.query(User).filter(User.id == b.user_id).first() if b else None
            contact = db.query(AgentContact).filter(AgentContact.id == b.contact_id).first() if b else None
            if not (b and user and contact) or not user.has_agent_chat_access():
                continue
            res = db.query(BookingResource).filter(BookingResource.id == b.resource_id).first() if b.resource_id else None
            cfg = bs.settings_of(bs.get_page(db, user, create=False))
            if kind == "review":
                from backend.services.salon_manager import review_text
                msg = review_text(b, res.name if res else None)
            else:
                msg = reminder_text(b, kind, res.name if res else None, cfg.get("address") or "", now)
            if await send_to_client(db, user, contact, msg):
                sent += 1
                if kind == "review":
                    b.review_asked_at = bs.now_utc()
                    db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"[BOOKING] reminders tick failed: {e}", exc_info=True)
    finally:
        db.close()
    return sent


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
        head = {"online": "📅 Новая онлайн-запись", "bot": "📅 Бот записал клиента", "call": "📅 Запись со звонка"}.get(by, "📅 Новая запись")
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
