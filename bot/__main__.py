"""Main entry point for the bot."""

import asyncio
import logging
import signal

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.types import Update
from aiogram.webhook.aiohttp_server import (
    SimpleRequestHandler,
    setup_application,
)

from bot.config import get_settings, configure_logging
from bot.db.session import init_db, get_async_session_maker
from bot.cache.redis import RedisClient
from bot.handlers import common, moderation, antispam

logger = logging.getLogger(__name__)


async def on_startup(app: web.Application) -> None:
    """Initialize bot and connect to services."""
    settings = get_settings()
    configure_logging(settings)

    # Initialize database
    engine = await init_db()
    session_maker = get_async_session_maker(engine)
    app["session_maker"] = session_maker

    # Initialize Redis
    redis_client = RedisClient(settings.REDIS_URL)
    await redis_client.connect()
    app["redis"] = redis_client

    # Initialize bot
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    app["bot"] = bot
    app["settings"] = settings

    logger.info("Bot started")


async def on_shutdown(app: web.Application) -> None:
    """Cleanup on shutdown."""
    if "redis" in app:
        await app["redis"].disconnect()
    if "bot" in app:
        await app["bot"].session.close()

    logger.info("Bot shutdown")


async def main():
    """Main bot entry point."""
    settings = get_settings()
    configure_logging(settings)

    # Create bot and dispatcher
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()

    # Setup routers
    dp.include_router(common.router)
    dp.include_router(moderation.router)
    dp.include_router(antispam.router)

    # Create aiohttp app
    app = web.Application()
    app["bot"] = bot
    app["settings"] = settings

    # Setup webhook
    SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=settings.WEBHOOK_SECRET,
    ).register(app, path="/webhook")

    setup_application(app, dp, secret_token=settings.WEBHOOK_SECRET)

    # Setup startup/shutdown
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    # Run server
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, settings.WEBHOOK_HOST, settings.WEBHOOK_PORT)
    await site.start()

    logger.info(
        f"Bot running on {settings.WEBHOOK_HOST}:{settings.WEBHOOK_PORT}"
    )

    # Setup graceful shutdown
    loop = asyncio.get_event_loop()

    def handle_signal(sig):
        logger.info(f"Received signal {sig}")
        asyncio.create_task(runner.cleanup())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_signal, sig)

    # Keep running
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
