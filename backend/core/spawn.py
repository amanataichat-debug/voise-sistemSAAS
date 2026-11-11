"""
Фоновая задача, которая не исчезнет посреди работы (ветка 1111-clean-system).

asyncio держит задачи слабыми ссылками: `asyncio.create_task(...)` без сохранения результата
сборщик мусора может уничтожить на середине (документация Python, «Save a reference to the result
of this function»). Так терялись разборы сообщений Telegram/Instagram (клиент без ответа), записи
журналов функций и ротация распознавания речи посреди звонка. spawn() хранит ссылку до завершения
и пишет необработанную ошибку задачи в лог, а не в пустоту.
"""
import asyncio
from typing import Any, Coroutine, Optional, Set

from backend.core.logging import get_logger

logger = get_logger(__name__)

_LIVE: Set[asyncio.Task] = set()


def _done(task: asyncio.Task) -> None:
    _LIVE.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(f"[SPAWN] фоновая задача {task.get_name()} упала: {exc!r}", exc_info=exc)


def spawn(coro: Coroutine[Any, Any, Any], name: Optional[str] = None) -> asyncio.Task:
    task = asyncio.create_task(coro, name=name)
    _LIVE.add(task)
    task.add_done_callback(_done)
    return task


def live_count() -> int:
    return len(_LIVE)
