"""
Сторож цикла событий (backend/core/loop_watchdog.py) и строки «Сервер замер…» в журнале звонка.

15.11.2026, звонок 4e842046: сервер замирал до 3 с, звук клиента копился перед моделью — агент «думал»
5–14 с. Сторож должен засечь замирание, снять стек виновника, а адаптер звонка — показать его в журнале.
Запуск: python scripts/voice_tests/test_loop_watchdog.py (без сети и БД).
"""
import asyncio
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.update(HOST_URL="http://x", DATABASE_URL="sqlite://", PRODUCTION="false")
import backend.core.config  # noqa: E402,F401
from backend.core import loop_watchdog as wd  # noqa: E402

failures = 0


def check(name, ok, extra=""):
    global failures
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if not ok:
        failures += 1


def blocking_db_call_imitation():
    t = time.time()
    while time.time() - t < 0.9:
        pass


async def main():
    wd.start()
    wd.start()  # повторный запуск — без второго потока
    check("сторож: один поток на воркер",
          sum(1 for t in __import__("threading").enumerate() if t.name == "loop-watchdog") == 1)
    t0 = time.time()
    await asyncio.sleep(0.3)
    await asyncio.sleep(0.3)
    check("сторож: обычная работа цикла — замираний нет", wd.stalls_between(t0, time.time()) == [])
    blocking_db_call_imitation()
    await asyncio.sleep(0.5)
    st = wd.stalls_between(t0, time.time())
    check("сторож: замирание засечено с длительностью", len(st) == 1 and 0.6 < st[0]["duration"] < 1.4, st)
    check("сторож: в стеке — функция-виновник", bool(st) and "blocking_db_call_imitation" in st[0]["stack"], st)

    from backend.websockets import sip_media_adapter as sma
    sock = sma.HandlerSocket.__new__(sma.HandlerSocket)
    sock.started_at = t0
    lines = sock.stall_lines()
    check("журнал звонка: строка «Сервер замер на … с … Код: …»",
          len(lines) == 1 and lines[0].startswith("Сервер замер на") and "blocking_db_call_imitation" in lines[0], lines)
    sock.started_at = time.time() + 10
    check("журнал звонка: замирания до начала звонка не попадают", sock.stall_lines() == [])


asyncio.run(main())
print("\nПРОВАЛОВ:", failures)
sys.exit(1 if failures else 0)
