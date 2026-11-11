"""Фоновые задачи не теряются (spawn) и опрос Telegram/Instagram стал чаще (ветка 1111-clean-system)."""
import asyncio
import gc
import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.update(HOST_URL="http://x", DATABASE_URL="sqlite://", PRODUCTION="false")
logging.disable(logging.CRITICAL)
import backend.core.config  # noqa: F401
from backend.core import spawn as sp

failures = 0


def check(name, cond, extra=""):
    global failures
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    failures += 0 if cond else 1


async def main():
    done = []

    async def work():
        await asyncio.sleep(0.05)
        done.append(1)

    async def boom():
        raise RuntimeError("x")

    sp.spawn(work())
    gc.collect()
    check("spawn держит ссылку на задачу, пока она работает", sp.live_count() == 1)
    await asyncio.sleep(0.1)
    check("задача доработала, ссылка отпущена", done == [1] and sp.live_count() == 0)
    sp.spawn(boom())
    await asyncio.sleep(0.01)
    check("упавшая задача не роняет цикл и отпускается", sp.live_count() == 0)

asyncio.run(main())

from backend.core import telegram_user_poller as tgp, instagram_poller as igp
check("Telegram опрашивается не реже раза в 30 с, claim чуть короче интервала",
      tgp.POLL_SECONDS <= 30 and tgp.CLAIM_CUTOFF_SECONDS < tgp.POLL_SECONDS)
check("Instagram опрашивается не реже раза в 45 с, claim чуть короче интервала",
      igp.CHECK_INTERVAL_SECONDS <= 45 and igp.CLAIM_CUTOFF_SECONDS < igp.CHECK_INTERVAL_SECONDS)
src = open(os.path.join(ROOT, "backend/core/telegram_user_poller.py"), encoding="utf-8").read()
src += open(os.path.join(ROOT, "backend/core/instagram_poller.py"), encoding="utf-8").read()
check("поллеры запускают разборы через spawn (ссылка не теряется)", "asyncio.create_task(handle_inbound" not in src
      and "spawn(handle_inbound" in src)
print("ПРОВАЛОВ:", failures)
sys.exit(1 if failures else 0)
