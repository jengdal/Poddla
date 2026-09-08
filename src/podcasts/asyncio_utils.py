import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)

_background_tasks: set[asyncio.Task[Any]] = set()


def start_task[T](coro: Coroutine[Any, Any, T], *, on_error: str) -> asyncio.Task[T]:
    """Run `coro` in a asyncio task.

    Keep a reference to the task around until the task completes. The point of
    this is to prevent it from being garbage collected, which theoretically
    could happen in some cases, in particular if the task is wrapped in
    `asyncio.shield` and end up completing with nobody `await`ing it.
    """

    def _done(t: asyncio.Task[T]) -> None:
        _background_tasks.discard(t)

        if t.cancelled():
            return
        error = t.exception()
        if error:
            logger.error(on_error, exc_info=error)

    task: asyncio.Task[T] = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_done)
    return task
