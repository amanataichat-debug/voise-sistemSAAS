"""
Реестр своих голосов GPT-Live (prompt-to-voice) — см. backend/models/live_voice.py.

Встроенные голоса (`live_client.LIVE_VOICES`) — строки («marin»), свои — объект {"id": "voice_…"}.
Карточка и агент хранят в `live_voice` короткий `key` своего голоса так же, как имя встроенного; здесь он
превращается в значение для session.start и описание голоса для instructions.

Список читается из БД не чаще раза в CACHE_SECONDS на воркер; ошибка БД = пустой список (звонок идёт на
встроенном голосе). Новый голос из скрипта виден всем воркерам в течение минуты.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any, Dict, List, Optional

from backend.core.logging import get_logger

logger = get_logger(__name__)

CACHE_SECONDS = 60
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")

_lock = threading.Lock()
_cache: Dict[str, Any] = {"at": 0.0, "voices": []}


def _load() -> List[Dict[str, Any]]:
    from backend.db.session import SessionLocal
    from backend.models.live_voice import LiveCustomVoice
    db = SessionLocal()
    try:
        rows = (db.query(LiveCustomVoice).filter(LiveCustomVoice.active.is_(True))
                .order_by(LiveCustomVoice.id).all())
        return [{"key": r.key, "voice_id": r.voice_id, "title": r.title, "gender": (r.gender or "").lower()[:1],
                 "description": r.description or ""} for r in rows]
    finally:
        db.close()


def custom_voices(force: bool = False) -> List[Dict[str, Any]]:
    now = time.time()
    if not force and now - _cache["at"] < CACHE_SECONDS:
        return _cache["voices"]
    with _lock:
        if not force and time.time() - _cache["at"] < CACHE_SECONDS:
            return _cache["voices"]
        try:
            voices = _load()
        except Exception as e:  # нет таблицы / БД недоступна — только встроенные голоса
            logger.debug(f"[LIVE-VOICES] load failed: {e}")
            voices = _cache["voices"] if _cache["at"] else []
        _cache.update(at=time.time(), voices=voices)
        return voices


def set_cache(voices: List[Dict[str, Any]]) -> None:
    """Для тестов и скрипта: подставить список без БД."""
    _cache.update(at=time.time() + 10 ** 9, voices=list(voices))


def get(key: Optional[str]) -> Optional[Dict[str, Any]]:
    k = (key or "").strip().lower()
    if not k:
        return None
    for v in custom_voices():
        if v["key"] == k:
            return v
    return None


def keys() -> List[str]:
    return [v["key"] for v in custom_voices()]


def all_live_voices() -> List[str]:
    """Встроенные + свои: список для кабинета и проверки выбора."""
    from backend.websockets.live_client import LIVE_VOICES
    return list(LIVE_VOICES) + [k for k in keys() if k not in LIVE_VOICES]


def is_live_voice(key: Optional[str]) -> bool:
    from backend.websockets.live_client import LIVE_VOICE_SET
    k = (key or "").strip().lower()
    return k in LIVE_VOICE_SET or get(k) is not None


def labels() -> Dict[str, str]:
    """Подписи своих голосов для кабинета: key → «Алия — Бишкек (рус + кырг)»."""
    return {v["key"]: v["title"] for v in custom_voices()}


def voice_note(voice: Dict[str, Any]) -> str:
    """Описание голоса в instructions (совет OpenAI для голосов prompt-to-voice)."""
    desc = (voice.get("description") or "").strip()
    if not desc:
        return ""
    return "# Твой голос\n" + desc + "\n\n"
