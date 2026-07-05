"""Fire-and-forget background task registry.

asyncio keeps only weak references to tasks; without holding a strong ref a
scheduled task (captcha timeout, delayed delete) can be garbage-collected mid
flight. We keep refs here and drop them on completion.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine

logger = logging.getLogger(__name__)

_tasks: set[asyncio.Task] = set()


def spawn(coro: Coroutine) -> asyncio.Task:
    """Schedule ``coro`` and keep a strong reference until it finishes."""
    task = asyncio.create_task(coro)
    _tasks.add(task)

    def _done(t: asyncio.Task) -> None:
        _tasks.discard(t)
        if not t.cancelled():
            exc = t.exception()
            if exc is not None:
                logger.error("background task failed: %r", exc)

    task.add_done_callback(_done)
    return task


async def cancel_all() -> None:
    """Cancel outstanding background tasks (graceful shutdown)."""
    for task in list(_tasks):
        task.cancel()
    for task in list(_tasks):
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    _tasks.clear()
