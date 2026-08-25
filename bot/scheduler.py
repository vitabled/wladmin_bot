"""Background scheduler: post due scheduled messages (Phase 5).

Лёгкий воркер внутри того же процесса: раз в ``SCHEDULER_TICK_SECONDS`` берёт
due-посты (``FOR UPDATE SKIP LOCKED``), отправляет их и перепланирует
(recurring) или отключает (one-off). Своя сессия на тик; коммит в конце.
Запускается в ``on_startup`` через ``spawn`` и снимается ``cancel_all``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.constants import SCHEDULER_TICK_SECONDS
from bot.db import crud
from bot.services.scheduler import SchedulerService
from bot.utils.telegram import safe_send_message
from bot.utils.text import escape_html

logger = logging.getLogger(__name__)


async def _tick(bot: Bot, session_maker: async_sessionmaker[AsyncSession]) -> None:
    """Process all posts due as of now in a single committed transaction."""
    now = datetime.now(UTC)
    async with session_maker() as session:
        due = await crud.due_scheduled_posts(session, now)
        for post in due:
            # post.text — пользовательский текст админа: при глобальном
            # parse_mode="HTML" неэкранированные теги сломали бы отправку,
            # поэтому экранируем при отправке (форматирование <b>/<i> теряется,
            # но пост никогда не упадёт из-за инъекции).
            await safe_send_message(
                bot, post.chat_id, escape_html(post.text), parse_mode="HTML"
            )
            next_run = SchedulerService.next_run(
                post.run_at, post.interval_seconds, now
            )
            await crud.mark_post_ran(session, post, now, next_run)
        await session.commit()
        if due:
            logger.info("scheduler.posted", extra={"count": len(due)})


async def run_scheduler(
    bot: Bot,
    session_maker: async_sessionmaker[AsyncSession],
    tick_seconds: int = SCHEDULER_TICK_SECONDS,
) -> None:
    """Loop forever, posting due messages each tick. Cancelled on shutdown."""
    logger.info("scheduler.started")
    while True:
        try:
            await _tick(bot, session_maker)
        except asyncio.CancelledError:
            logger.info("scheduler.stopped")
            raise
        except Exception:
            # Never let one bad tick kill the loop.
            logger.exception("scheduler.tick_failed")
        await asyncio.sleep(tick_seconds)
