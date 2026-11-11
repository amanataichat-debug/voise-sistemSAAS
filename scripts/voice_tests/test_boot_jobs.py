"""
Тест запуска: все фоновые циклы сервера (app.background_jobs) реально стартуют и делают хотя бы один тик.

11.11.2026 удаление Voximplant стёрло строку `_task_scheduler = None` — планировщик задач падал NameError
при старте каждого воркера, и ни один запланированный звонок/сообщение не выполнялся. Модульные тесты
этого не видели: они проверяли отдельные функции, а не запуск. Здесь — ровно тот список, что запускает app.py.

Ошибки БД (тестовой базы может не быть) допустимы. Провал — ошибка КОДА: NameError, ImportError,
AttributeError, TypeError, UnboundLocalError, SyntaxError — или цикл, который упал целиком.

С BOOT_TEST_DATABASE_URL (PostgreSQL) циклы работают на настоящей схеме — так ловятся и ошибки запросов.
"""
import asyncio
import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
DB = os.environ.get("BOOT_TEST_DATABASE_URL")
os.environ.update(HOST_URL="https://boot.voksyai.online", DATABASE_URL=DB or "sqlite://", PRODUCTION="true",
                  JWT_SECRET_KEY="boot-secret-0123456789abcdef0123456789")
import backend.core.config  # noqa: E402,F401

CODE_ERRORS = (NameError, ImportError, AttributeError, TypeError, UnboundLocalError, SyntaxError)
records = []


class Grab(logging.Handler):
    def emit(self, record):
        if record.levelno >= logging.ERROR:
            records.append(record)


logging.getLogger().addHandler(Grab())
logging.getLogger().setLevel(logging.INFO)
for h in list(logging.getLogger().handlers):
    if not isinstance(h, Grab):
        h.setLevel(logging.CRITICAL + 1)

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


import app as app_module  # noqa: E402

if DB:
    from sqlalchemy import create_engine
    from backend.models.base import Base
    Base.metadata.create_all(create_engine(DB))

# Поллеры без ключей сразу выходят — включаем их, чтобы проверить и их цикл.
from backend.services import telegram_user_service as _tg  # noqa: E402
from backend.services import composio_service as _cs  # noqa: E402
for mod in (_tg, _cs):
    if hasattr(mod, "is_configured"):
        mod.is_configured = lambda *a, **k: True
try:
    from backend.core import instagram_poller as _igp, telegram_user_poller as _tgp
    for poller in (_igp, _tgp):
        for name in ("tg_user", "ig"):
            obj = getattr(poller, name, None)
            if obj is not None and hasattr(obj, "is_configured"):
                obj.is_configured = lambda *a, **k: True
except Exception as e:  # noqa: BLE001
    check("импорт поллеров", False, repr(e))


async def main():
    # Миграции (alembic env.py) не должны отключать логгеры приложения (fileConfig по умолчанию отключал все).
    try:
        app_module.run_migrations()
    except Exception as e:  # noqa: BLE001  — окружение без alembic/mako: проверяем только логгеры
        print(f"(миграции не запустились: {type(e).__name__})")
    off = [n for n in ("app", "backend.services.agent_orchestrator", "backend.websockets.handler_live",
                       "backend.core.task_scheduler") if logging.getLogger(n).disabled]
    check("миграции не отключают логи приложения", not off, off)
    jobs = app_module.background_jobs()
    check("список фоновых циклов не пуст", len(jobs) >= 7, len(jobs))
    finished_early = []

    async def run(factory):
        name = getattr(factory, "__name__", repr(factory))
        try:
            await asyncio.wait_for(factory(), timeout=6)
            finished_early.append(name)
        except asyncio.TimeoutError:
            pass  # цикл крутится — так и должно быть
        except CODE_ERRORS as e:
            check(f"цикл {name} стартует", False, f"{type(e).__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            check(f"цикл {name} стартует", False, f"{type(e).__name__}: {e}")

    await asyncio.gather(*(run(f) for f in jobs))

    from backend.core import task_scheduler as ts
    check("планировщик задач запущен (_task_scheduler)", getattr(ts, "_task_scheduler", None) is not None)
    ts.stop_task_scheduler()
    check("планировщик останавливается", ts._task_scheduler is None)

    bad = []
    for r in records:
        exc = r.exc_info[1] if r.exc_info else None
        msg = r.getMessage()
        if isinstance(exc, CODE_ERRORS) or "is not defined" in msg or "has no attribute" in msg \
                or "crashed" in msg or "unexpected keyword" in msg or "positional argument" in msg:
            bad.append(f"{r.name}: {msg[:200]}")
    check("в логах фоновых циклов нет ошибок кода", not bad, "; ".join(bad[:5]))


asyncio.run(main())
print(f"ПРОВАЛОВ: {failures}")
sys.exit(1 if failures else 0)
