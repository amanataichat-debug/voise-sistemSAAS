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

# 15.11.2026, звонок bd6e2de4: сторож поймал замирания 1–1,8 с от GET-опросов кабинета (инбокс раз в 6 с, список
# звонков, дашборд) — async-обработчики с синхронными запросами к БД останавливали звонки на том же воркере.
# GET-обработчик без await должен быть обычной функцией (FastAPI выполнит его в потоке).
import ast as _ast
_files = ["inbox.py", "agent.py", "dashboard.py", "conversations.py", "wallet.py", "contacts.py", "sip_gateway.py",
          "eleven_assistants.py"]
_bad = []
for _f in _files:
    _src = open(os.path.join(ROOT, "backend", "api", _f), encoding="utf-8").read()
    for _n in _ast.parse(_src).body:
        if isinstance(_n, _ast.AsyncFunctionDef) and any(
                isinstance(d, _ast.Call) and isinstance(d.func, _ast.Attribute) and d.func.attr == "get"
                for d in _n.decorator_list):
            _seg = _ast.get_source_segment(_src, _n)
            if not any(k in _seg for k in ("await ", "async for", "async with", "yield")):
                _bad.append(f"{_f}:{_n.name}")
check("API кабинета: GET без await — обычные функции (не держат цикл звонков)", not _bad, _bad[:10])
_sg = open(os.path.join(ROOT, "backend", "api", "sip_gateway.py"), encoding="utf-8").read()
check("окончание звонка (стенограмма, разметка, PostCall) — в потоке, не в цикле",
      "await asyncio.to_thread(_finish_call_sync" in _sg)

# 22.11.2026, звонок a72651fc: замирания 1,4–2,5 с в get_current_user → psycopg2 connect. Авторизация кабинета
# делала запрос к базе в цикле звонков, а без пула каждый запрос открывал новое SSL-соединение.
import inspect as _inspect
from backend.core import dependencies as _deps
from backend.services.auth_service import AuthService as _AS
check("авторизация кабинета — обычные функции (FastAPI выполняет их в потоке)",
      not any(_inspect.iscoroutinefunction(f) for f in (
          _deps.get_current_user, _deps.get_current_user_flexible, _deps.get_assistant_by_id, _AS.get_current_user)))
_sess = open(os.path.join(ROOT, "backend", "db", "session.py"), encoding="utf-8").read()
check("база: пул соединений для Postgres (DB_POOL=0 — прежний NullPool), безопасен после fork",
      "pool_size=DB_POOL_SIZE" in _sess and "register_at_fork" in _sess and 'startswith(("postgres"' in _sess)

print("\nПРОВАЛОВ:", failures)
sys.exit(1 if failures else 0)
