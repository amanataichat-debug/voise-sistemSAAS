"""
Сообщение владельцу платформы в Telegram о новой регистрации (ветка 1811-niche-admin-speed).

Владелец хочет звонить новым клиентам сразу, пока они в кабинете: имя, телефон, почта, компания,
ниша и кнопки «Открыть в админке» / «Написать в WhatsApp».

Куда: в Telegram-бот агента админа (тот же бот, куда приходят оповещения агента: токен бота и чаты
владельца из agent_configs) — ничего настраивать не нужно. Можно задать явно:
ADMIN_TELEGRAM_BOT_TOKEN + ADMIN_TELEGRAM_CHAT_IDS (через запятую). SIGNUP_TELEGRAM_NOTIFY=0 — выключить.

Когда: сразу после выбора ниши на экране «Чем занимается ваш бизнес?» (notify_signup) или, если
клиент её не выбрал, через SIGNUP_NOTIFY_DELAY_SECONDS (3 мин) после регистрации — фоновый цикл
sweep. Одно сообщение на клиента: флаг users.signup_notified_at ставится атомарным UPDATE, поэтому
четыре воркера gunicorn не пришлют четыре одинаковых сообщения. Сбой отправки ничего не ломает.
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

DELAY_SECONDS = int(os.getenv("SIGNUP_NOTIFY_DELAY_SECONDS", "180") or 180)
SWEEP_SECONDS = 60
LOOKBACK = timedelta(hours=6)      # старых клиентов при первом запуске не трогаем
BISHKEK = timezone(timedelta(hours=6))


def enabled() -> bool:
    return os.getenv("SIGNUP_TELEGRAM_NOTIFY", "1").strip().lower() not in ("0", "false", "no", "off")


def _targets(db: Session) -> List[Tuple[str, List[str]]]:
    """[(токен бота, [chat_id…])]: явные из env или боты агентов админов с чатами владельца."""
    token = (os.getenv("ADMIN_TELEGRAM_BOT_TOKEN") or "").strip()
    chats = [c.strip() for c in (os.getenv("ADMIN_TELEGRAM_CHAT_IDS") or "").split(",") if c.strip()]
    if token and chats:
        return [(token, chats)]
    out: Dict[str, set] = {}
    try:
        rows = db.execute(text("""
            SELECT a.telegram_bot_token AS token, a.telegram_chat_ids AS chats
            FROM agent_configs a JOIN users u ON u.id = a.user_id
            WHERE coalesce(u.is_admin, false) AND a.telegram_enabled
              AND a.telegram_bot_token IS NOT NULL AND a.telegram_bot_token <> ''
            ORDER BY a.created_at
        """)).mappings().all()
    except Exception as e:
        db.rollback()
        logger.warning(f"[SIGNUP-NOTIFY] targets lookup failed: {e}")
        return []
    for r in rows:
        ids = []
        for item in (r["chats"] or []):
            cid = item.get("chat_id") if isinstance(item, dict) else item
            if cid:
                ids.append(str(cid))
        if ids:
            out.setdefault(r["token"], set()).update(ids)
    return [(tok, sorted(ids)) for tok, ids in out.items()]


def _pretty_phone(p: Optional[str]) -> str:
    d = "".join(ch for ch in (p or "") if ch.isdigit())
    if len(d) == 12 and d.startswith("996"):
        return f"+996 {d[3:6]} {d[6:9]} {d[9:]}"
    return p or ""


def format_signup(row) -> Tuple[str, list]:
    """Текст (HTML) и кнопки сообщения о регистрации."""
    from backend.services.niche_catalog import niche_title
    from backend.core.config import settings
    name = " ".join(x for x in [row.get("first_name"), row.get("last_name")] if x) or "Без имени"
    lines = ["🆕 <b>Новая регистрация</b>", f"<b>{html.escape(name)}</b>"
             + (f" · {html.escape(row['company_name'])}" if row.get("company_name") else "")]
    if row.get("phone"):
        lines.append(f"📞 {html.escape(_pretty_phone(row['phone']))}")
    else:
        lines.append("📞 телефон не указан")
    lines.append(f"✉️ {html.escape(row.get('email') or '')}")
    niche = niche_title(row.get("business_niche"), row.get("business_niche_other"))
    lines.append(f"🏷 {html.escape(niche) if niche else 'ниша пока не выбрана'}")
    created = row.get("created_at")
    if created:
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        lines.append(f"🕒 {created.astimezone(BISHKEK).strftime('%d.%m %H:%M')} (Бишкек)")
    host = (getattr(settings, "HOST_URL", "") or "").rstrip("/")
    buttons = []
    if host.startswith("https://"):
        buttons.append({"text": "Открыть в админке", "url": f"{host}/static/admin.html#client={row['id']}"})
    digits = "".join(ch for ch in (row.get("phone") or "") if ch.isdigit())
    if digits:
        hello = (f"Здравствуйте, {row.get('first_name') or ''}! Это Voksy AI. "
                 "Вы зарегистрировались у нас — помогу настроить кабинет под ваш бизнес. Удобно сейчас?")
        from urllib.parse import quote
        buttons.append({"text": "WhatsApp", "url": f"https://wa.me/{digits}?text={quote(hello)}"})
    return "\n".join(lines), ([buttons] if buttons else [])


async def _send(token: str, chat_id: str, text_html: str, keyboard: list) -> bool:
    import httpx
    payload = {"chat_id": chat_id, "text": text_html, "parse_mode": "HTML", "disable_web_page_preview": True}
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload)
        if r.status_code != 200:
            logger.warning(f"[SIGNUP-NOTIFY] Telegram {r.status_code}: {r.text[:200]}")
        return r.status_code == 200
    except Exception as e:
        logger.warning(f"[SIGNUP-NOTIFY] send failed: {e}")
        return False


def _claim(db: Session, user_id) -> Optional[dict]:
    """Атомарно забрать клиента под отправку: вернёт строку, только если сообщение ещё не уходило."""
    row = db.execute(text("""
        UPDATE users SET signup_notified_at = now()
        WHERE id = :id AND signup_notified_at IS NULL AND NOT coalesce(is_admin, false)
        RETURNING id, email, first_name, last_name, company_name, phone, business_niche,
                  business_niche_other, created_at
    """), {"id": user_id}).mappings().first()
    db.commit()
    return dict(row) if row else None


async def notify_signup(db: Session, user_id) -> bool:
    """Отправить сообщение о регистрации (один раз на клиента). True — ушло хотя бы в один чат."""
    if not enabled():
        return False
    targets = _targets(db)
    if not targets:
        return False
    row = _claim(db, user_id)
    if not row:
        return False
    text_html, keyboard = format_signup(row)
    results = await asyncio.gather(*[_send(tok, cid, text_html, keyboard) for tok, chats in targets for cid in chats])
    ok = any(results)
    if not ok:
        # не ушло никуда — пусть следующая проверка попробует ещё раз
        db.execute(text("UPDATE users SET signup_notified_at = NULL WHERE id = :id"), {"id": row["id"]})
        db.commit()
    return ok


async def notify_signup_new_session(user_id) -> bool:
    """
    После выбора ниши — сообщение сразу (уже с нишей), но только для свежей регистрации:
    старый клиент, впервые заполнивший нишу, «новой регистрацией» не считается.
    """
    from backend.db.session import SessionLocal
    if not enabled():
        return False
    db = SessionLocal()
    try:
        fresh = db.execute(text("SELECT created_at >= :since FROM users WHERE id = :id"),
                           {"since": datetime.now(timezone.utc) - LOOKBACK, "id": user_id}).scalar()
        if not fresh:
            return False
        return await notify_signup(db, user_id)
    except Exception as e:
        db.rollback()
        logger.warning(f"[SIGNUP-NOTIFY] after niche failed: {e}")
        return False
    finally:
        db.close()


def due_user_ids(db: Session, now: Optional[datetime] = None, limit: int = 20) -> List:
    """Кому пора отправить: зарегистрировались 3 мин – 6 ч назад, сообщения ещё не было."""
    now = now or datetime.now(timezone.utc)
    rows = db.execute(text("""
        SELECT id FROM users
        WHERE signup_notified_at IS NULL AND NOT coalesce(is_admin, false)
          AND created_at <= :ready AND created_at >= :since
        ORDER BY created_at LIMIT :limit
    """), {"ready": now - timedelta(seconds=DELAY_SECONDS), "since": now - LOOKBACK, "limit": limit}).all()
    return [r[0] for r in rows]


async def sweep_once() -> int:
    from backend.db.session import SessionLocal
    if not enabled():
        return 0
    db = SessionLocal()
    sent = 0
    try:
        if not _targets(db):
            return 0
        for uid in due_user_ids(db):
            if await notify_signup(db, uid):
                sent += 1
    except Exception as e:
        db.rollback()
        logger.warning(f"[SIGNUP-NOTIFY] sweep failed: {e}")
    finally:
        db.close()
    return sent


async def start_signup_notify_loop() -> None:
    """Фоновый цикл: раз в минуту отправляет сообщения о регистрациях, у которых не выбрана ниша."""
    logger.info(f"[SIGNUP-NOTIFY] started (every {SWEEP_SECONDS}s, delay {DELAY_SECONDS}s)")
    while True:
        try:
            await asyncio.to_thread(lambda: None)  # отдать цикл, если старт пришёлся на тяжёлый момент
            await sweep_once()
        except Exception as e:
            logger.error(f"[SIGNUP-NOTIFY] loop error: {e}")
        await asyncio.sleep(SWEEP_SECONDS)
