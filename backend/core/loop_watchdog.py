"""
Сторож цикла событий воркера: ловит «замирания» и записывает, какой код их вызвал.

Зачем (15.11.2026, звонок 4e842046): в «Логи звонка» строка «Путь звука» показала, что сервер замирал до
3 с, а звук клиента ждал в очереди перед моделью до 2,8 с — агент «думал» 5–14 с и не понимал кыргызскую
речь, потому что слышал её поздно и пачкой. Звонок живёт в том же цикле событий, что и все запросы воркера:
любой синхронный код в нём (запрос к БД в async-обработчике, тяжёлый разбор, большой JSON) останавливает
звук. Чтобы чинить конкретное место, а не гадать, отдельный поток раз в PING_SEC стучится в цикл; если
ответа нет дольше STALL_SEC, он раз в PING_SEC снимает стек главного потока цикла. По окончании замирания
запоминаются длительность и самый частый стек (что держало цикл дольше всего).

Использование: start(loop) — один раз на воркер (повторный вызов ничего не делает);
stalls_between(t0, t1) — замирания за звонок (адаптер звонка пишет их в журнал).
"""
from __future__ import annotations

import asyncio
import collections
import logging
import os
import sys
import threading
import time
import traceback
from typing import Any, Deque, Dict, List, Optional

logger = logging.getLogger("loop-watchdog")

PING_SEC = 0.1
STALL_SEC = 0.4
MAX_STALLS = 200
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_lock = threading.Lock()
_started_for: Optional[int] = None          # id(loop), для которого запущен сторож
_stalls: Deque[Dict[str, Any]] = collections.deque(maxlen=MAX_STALLS)


def _short_stack(frame) -> str:
    """Стек главного потока: свои файлы проекта (последние 4) + самая верхняя чужая строка."""
    entries = traceback.extract_stack(frame)
    own = []
    for fs in entries:
        path = os.path.abspath(fs.filename)
        if path.startswith(_ROOT) and "/site-packages/" not in path:
            own.append(f"{os.path.relpath(path, _ROOT)}:{fs.lineno} {fs.name}")
    top = entries[-1] if entries else None
    parts = own[-4:]
    if top is not None:
        tpath = os.path.abspath(top.filename)
        if not (tpath.startswith(_ROOT) and "/site-packages/" not in tpath):
            lib = tpath.split("site-packages/")[-1] if "site-packages/" in tpath else os.path.basename(tpath)
            parts.append(f"{lib}:{top.lineno} {top.name}")
    return " → ".join(parts) or "(стек недоступен)"


def _run(loop: asyncio.AbstractEventLoop, loop_thread: int) -> None:
    last_pong = [time.monotonic()]

    def pong() -> None:
        last_pong[0] = time.monotonic()

    stall_start: Optional[float] = None
    stall_wall: float = 0.0
    samples: collections.Counter = collections.Counter()
    while not loop.is_closed():
        try:
            loop.call_soon_threadsafe(pong)
        except RuntimeError:
            return
        time.sleep(PING_SEC)
        now = time.monotonic()
        lag = now - last_pong[0]
        if lag > STALL_SEC:
            if stall_start is None:
                stall_start = last_pong[0]
                stall_wall = time.time() - lag
                samples = collections.Counter()
            frame = sys._current_frames().get(loop_thread)
            if frame is not None:
                try:
                    samples[_short_stack(frame)] += 1
                except Exception:
                    pass
        elif stall_start is not None:
            duration = last_pong[0] - stall_start
            stack = samples.most_common(1)[0][0] if samples else "(стек не снят)"
            item = {"at": stall_wall, "duration": duration, "stack": stack}
            with _lock:
                _stalls.append(item)
            logger.warning(f"[LOOP-WATCHDOG] цикл событий замер на {duration:.2f} с: {stack}")
            stall_start = None


def start(loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
    """Запустить сторожа для цикла (вызывать из потока этого цикла). Повторный вызов — ничего не делает."""
    global _started_for
    try:
        loop = loop or asyncio.get_running_loop()
    except RuntimeError:
        return
    with _lock:
        if _started_for == id(loop):
            return
        _started_for = id(loop)
    t = threading.Thread(target=_run, args=(loop, threading.get_ident()), name="loop-watchdog", daemon=True)
    t.start()


def stalls_between(t0: float, t1: float) -> List[Dict[str, Any]]:
    """Замирания, начавшиеся в [t0, t1] (время — time.time())."""
    with _lock:
        return [dict(s) for s in _stalls if t0 - 0.5 <= s["at"] <= t1]
