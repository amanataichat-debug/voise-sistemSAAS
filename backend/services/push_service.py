"""
Push-уведомления на телефон и в браузер (Web Push + VAPID), Voksy Beauty шаг 1.6б.

Утренний отчёт ИИ-управляющего, «есть предложения на одобрение» и похожие события приходят владельцу
уведомлением на экран телефона — из установленного приложения Voksy AI (PWA). На iPhone push работает
только у приложения, добавленного на экран «Домой» (iOS 16.4+); на Android и компьютере — и в браузере.

Ключи VAPID: переменные VAPID_PRIVATE_KEY (PEM) + VAPID_PUBLIC_KEY (base64url), а если их нет — пара
создаётся один раз и хранится в таблице web_push_keys (под advisory-замком: 4 воркера не создадут две).
Отправка синхронная (requests внутри pywebpush) — вызывать из потока (send_to_user_sync) или через
asyncio.to_thread (send_to_user). Подписка, которую браузер отозвал (404/410), удаляется сама.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_KEYS: Optional[Dict[str, str]] = None     # кэш воркера: {"pem", "public"}
MAX_PAYLOAD = 3000


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _generate() -> Dict[str, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {"pem": pem, "public": _b64url(pub)}


def keys(db: Session) -> Dict[str, str]:
    """Ключи VAPID: из окружения, иначе из базы (создаются при первом обращении)."""
    global _KEYS
    if _KEYS:
        return _KEYS
    env_priv, env_pub = os.getenv("VAPID_PRIVATE_KEY", "").strip(), os.getenv("VAPID_PUBLIC_KEY", "").strip()
    if env_priv and env_pub:
        _KEYS = {"pem": env_priv.replace("\\n", "\n"), "public": env_pub}
        return _KEYS
    row = db.execute(text("SELECT private_pem, public_b64 FROM web_push_keys WHERE id = 'main'")).first()
    if row is None:
        gen = _generate()
        db.execute(text("SELECT pg_advisory_xact_lock(26112026)"))
        db.execute(text("INSERT INTO web_push_keys (id, private_pem, public_b64, created_at) "
                        "VALUES ('main', :pem, :pub, now()) ON CONFLICT (id) DO NOTHING"),
                   {"pem": gen["pem"], "pub": gen["public"]})
        db.commit()
        row = db.execute(text("SELECT private_pem, public_b64 FROM web_push_keys WHERE id = 'main'")).first()
    _KEYS = {"pem": row[0], "public": row[1]}
    return _KEYS


def public_key(db: Session) -> str:
    return keys(db)["public"]


def subscribe(db: Session, user_id, sub: Dict[str, Any], user_agent: Optional[str] = None) -> bool:
    """Сохранить подписку браузера (endpoint уникален: тот же телефон другим входом — переезжает)."""
    endpoint = str((sub or {}).get("endpoint") or "").strip()
    k = (sub or {}).get("keys") or {}
    p256dh, auth = str(k.get("p256dh") or "").strip(), str(k.get("auth") or "").strip()
    if not endpoint.startswith("https://") or not p256dh or not auth or len(endpoint) > 2000:
        return False
    db.execute(text("""
        INSERT INTO push_subscriptions (id, user_id, endpoint, p256dh, auth, user_agent, created_at)
        VALUES (gen_random_uuid(), :uid, :ep, :p, :a, :ua, now())
        ON CONFLICT (endpoint) DO UPDATE SET user_id = :uid, p256dh = :p, auth = :a, user_agent = :ua
    """), {"uid": str(user_id), "ep": endpoint, "p": p256dh[:200], "a": auth[:100], "ua": (user_agent or "")[:300]})
    db.commit()
    return True


def unsubscribe(db: Session, user_id, endpoint: str) -> None:
    db.execute(text("DELETE FROM push_subscriptions WHERE user_id = :uid AND endpoint = :ep"),
               {"uid": str(user_id), "ep": endpoint})
    db.commit()


def count(db: Session, user_id) -> int:
    return int(db.execute(text("SELECT count(*) FROM push_subscriptions WHERE user_id = :uid"),
                          {"uid": str(user_id)}).scalar() or 0)


def _claims() -> Dict[str, str]:
    host = (os.getenv("HOST_URL") or "https://voksyai.online").rstrip("/")
    return {"sub": host if host.startswith("https://") else "https://voksyai.online"}


def send_to_user_sync(db: Session, user_id, title: str, body: str, url: str = "/static/dashboard.html",
                      tag: Optional[str] = None) -> int:
    """Отправить уведомление на все устройства владельца. Возвращает, сколько ушло."""
    try:
        from py_vapid import Vapid
        from pywebpush import WebPushException, webpush
    except Exception as e:      # библиотека не установлена — уведомления просто не уходят
        logger.warning(f"[PUSH] pywebpush unavailable: {e}")
        return 0
    rows = db.execute(text("SELECT id, endpoint, p256dh, auth FROM push_subscriptions WHERE user_id = :uid"),
                      {"uid": str(user_id)}).fetchall()
    if not rows:
        return 0
    k = keys(db)
    vapid = Vapid.from_pem(k["pem"].encode())
    payload = json.dumps({"title": title[:120], "body": body[:600], "url": url, "tag": tag or "voksy"},
                         ensure_ascii=False)[:MAX_PAYLOAD]
    sent = 0
    for sid, endpoint, p256dh, auth in rows:
        try:
            webpush({"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}}, payload,
                    vapid_private_key=vapid, vapid_claims=dict(_claims()), ttl=12 * 3600, timeout=10)
            db.execute(text("UPDATE push_subscriptions SET last_ok_at = :t WHERE id = :id"),
                       {"t": datetime.now(timezone.utc), "id": sid})
            sent += 1
        except WebPushException as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code in (404, 410):
                db.execute(text("DELETE FROM push_subscriptions WHERE id = :id"), {"id": sid})
            else:
                logger.warning(f"[PUSH] send failed ({code}): {str(e)[:200]}")
        except Exception as e:
            logger.warning(f"[PUSH] send error: {e}")
    db.commit()
    return sent


async def send_to_user(user_id, title: str, body: str, url: str = "/static/dashboard.html",
                       tag: Optional[str] = None) -> int:
    """То же из асинхронного кода: своя сессия БД в потоке (сеть и запросы не держат цикл)."""
    import asyncio

    def run() -> int:
        from backend.db.session import SessionLocal
        db = SessionLocal()
        try:
            return send_to_user_sync(db, user_id, title, body, url, tag)
        finally:
            db.close()
    try:
        return await asyncio.to_thread(run)
    except Exception as e:
        logger.warning(f"[PUSH] async send failed: {e}")
        return 0
