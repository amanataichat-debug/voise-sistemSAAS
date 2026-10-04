"""
Отдельный event loop в отдельном потоке для фоновых циклов воркера.

Планировщик задач (каждые 30 с), поллеры личного Telegram (60 с) и Instagram
(90 с), проверки подписок делают синхронные запросы к удалённой БД прямо в
корутинах. В основном event loop воркера, где идут звонки, каждый такой тик
останавливал звук на 0.4–2 с («event loop blocked» в логах звонка): рваный
поток в распознавание, задержки ответа.

Здесь они крутятся в своём loop'е в daemon-потоке: синхронный запрос
блокирует только этот поток (драйвер БД отпускает GIL на сетевом ожидании),
звонки основного loop'а не замечают. Всё, что фоновые циклы порождают через
asyncio.create_task (обработка входящих сообщений оркестратором и т.п.), тоже
живёт в этом loop'е.

Ограничение: фоновый код не должен трогать asyncio-объекты основного loop'а
(вебсокеты звонков, их очереди и события). Сейчас он общается с остальным
приложением только через БД (очередь sip_calls и т.п.).
"""

import asyncio
import threading
from typing import Awaitable, Callable, List, Optional

from backend.core.logging import get_logger

logger = get_logger(__name__)

_loop: Optional[asyncio.AbstractEventLoop] = None
_lock = threading.Lock()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    global _loop
    with _lock:
        if _loop is not None:
            return _loop
        loop = asyncio.new_event_loop()
        ready = threading.Event()

        def run() -> None:
            asyncio.set_event_loop(loop)
            ready.set()
            loop.run_forever()

        threading.Thread(target=run, name="background-jobs", daemon=True).start()
        ready.wait()
        _loop = loop
        return loop


def start_background_jobs(jobs: List[Callable[[], Awaitable]]) -> None:
    """Запустить корутины (фабрики без аргументов) в фоновом loop'е; падение одной не трогает другие."""
    loop = _ensure_loop()

    async def guarded(factory: Callable[[], Awaitable]) -> None:
        name = getattr(factory, "__name__", repr(factory))
        try:
            await factory()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[BG-LOOP] job {name} crashed: {e}", exc_info=True)

    for factory in jobs:
        asyncio.run_coroutine_threadsafe(guarded(factory), loop)
    logger.info(f"[BG-LOOP] {len(jobs)} background job(s) started in a separate thread")


def submit(coro) -> None:
    """
    Запустить корутину в фоновом loop'е из любого места — из основного loop'а,
    из asyncio.to_thread или из обычного потока (там своего loop'а нет).
    """
    asyncio.run_coroutine_threadsafe(coro, _ensure_loop())
