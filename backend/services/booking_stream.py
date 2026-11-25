"""
Мгновенное обновление журнала записи (Voksy Beauty, шаг 1.4).

Как это устроено (Redis нет, воркеров gunicorn 4):
  1. Любое изменение журнала (запись, перенос, статус, блок времени, мастер) делает в своей транзакции
     `pg_notify('voksy_booking', <id владельца>)` — booking_service.notify_changed. PostgreSQL доставляет
     уведомление только при commit (откат — ничего не уходит).
  2. В каждом воркере один поток держит отдельное соединение `LISTEN voksy_booking` (запускается при первом
     подписчике) и передаёт id владельца в цикл событий воркера.
  3. Открытый журнал держит поток GET /api/booking/stream (fetch с токеном, text/event-stream). Подписчики —
     очереди по id владельца; событие «changed» → страница перезапрашивает данные. Несколько изменений
     подряд склеиваются (DEBOUNCE). Раз в PING_SECONDS — пустая строка, чтобы прокси не закрыл поток.
Обрыв соединения с базой — поток переподключается сам; страница при обрыве потока переподключается, а опрос
раз в 60 с остаётся запасным.
"""

from __future__ import annotations

import asyncio
import logging
import select
import threading
import time
from typing import Dict, Optional, Set

logger = logging.getLogger(__name__)

CHANNEL = "voksy_booking"
PING_SECONDS = 25.0
DEBOUNCE = 0.35
QUEUE_MAX = 16
MAX_SECONDS = 900.0      # поток живёт не дольше 15 минут — страница переподключается сама (без «висящих» соединений)

_subs: Dict[str, Set[asyncio.Queue]] = {}
_loop: Optional[asyncio.AbstractEventLoop] = None
_thread: Optional[threading.Thread] = None
_lock = threading.Lock()
_stats = {"events": 0, "connects": 0}


def _dsn() -> Optional[str]:
    try:
        from backend.db.session import engine
        url = engine.url
        if not str(url.drivername).startswith("postgres"):
            return None
        return url.set(drivername="postgresql").render_as_string(hide_password=False)
    except Exception as e:
        logger.warning(f"[BOOKING-STREAM] no dsn: {e}")
        return None


def _fanout(user_id: str) -> None:
    for q in list(_subs.get(user_id, ())):
        try:
            q.put_nowait("changed")
        except asyncio.QueueFull:
            pass


def _dispatch(user_id: str) -> None:
    _stats["events"] += 1
    loop = _loop
    if loop is not None and not loop.is_closed():
        try:
            loop.call_soon_threadsafe(_fanout, user_id)
        except RuntimeError:
            pass


def _listen_forever(dsn: str) -> None:
    import psycopg2
    import psycopg2.extensions
    backoff = 2.0
    while True:
        conn = None
        try:
            try:
                conn = psycopg2.connect(dsn, sslmode="require", connect_timeout=10)
            except psycopg2.OperationalError:
                conn = psycopg2.connect(dsn, connect_timeout=10)      # локальная база без SSL
            conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
            with conn.cursor() as cur:
                cur.execute(f"LISTEN {CHANNEL}")
            _stats["connects"] += 1
            backoff = 2.0
            last_ping = time.monotonic()
            while True:
                if select.select([conn], [], [], 20.0) != ([], [], []):
                    conn.poll()
                    while conn.notifies:
                        n = conn.notifies.pop(0)
                        if n.payload:
                            _dispatch(n.payload)
                elif time.monotonic() - last_ping > 60:
                    with conn.cursor() as cur:          # соединение живо? (иначе — переподключение)
                        cur.execute("SELECT 1")
                    last_ping = time.monotonic()
        except Exception as e:
            logger.warning(f"[BOOKING-STREAM] listener reconnect in {backoff:.0f}s: {e}")
            time.sleep(backoff)
            backoff = min(backoff * 2, 60.0)
        finally:
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass


def _ensure_listener() -> bool:
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return True
        dsn = _dsn()
        if not dsn:
            return False
        _thread = threading.Thread(target=_listen_forever, args=(dsn,), name="booking-listen", daemon=True)
        _thread.start()
        return True


async def subscribe(user_id) -> asyncio.Queue:
    global _loop
    _loop = asyncio.get_running_loop()
    _ensure_listener()
    q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
    _subs.setdefault(str(user_id), set()).add(q)
    return q


def unsubscribe(user_id, q: asyncio.Queue) -> None:
    s = _subs.get(str(user_id))
    if s is not None:
        s.discard(q)
        if not s:
            _subs.pop(str(user_id), None)


async def events(user_id, is_disconnected):
    """Поток text/event-stream для страницы: «changed» после изменений, «: ping» раз в PING_SECONDS."""
    q = await subscribe(user_id)
    deadline = time.monotonic() + MAX_SECONDS
    try:
        yield "retry: 5000\n\n"
        yield "event: ready\ndata: {}\n\n"
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                yield "event: bye\ndata: {}\n\n"
                break
            try:
                await asyncio.wait_for(q.get(), timeout=min(PING_SECONDS, left))
            except asyncio.TimeoutError:
                if await is_disconnected():
                    break
                yield ": ping\n\n"
                continue
            await asyncio.sleep(DEBOUNCE)            # несколько изменений подряд — одно событие
            while not q.empty():
                q.get_nowait()
            yield "event: changed\ndata: {}\n\n"
    finally:
        unsubscribe(user_id, q)


def stats() -> Dict[str, int]:
    return {"subscribers": sum(len(v) for v in _subs.values()), "listener": bool(_thread and _thread.is_alive()), **_stats}
