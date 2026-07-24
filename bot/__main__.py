"""Webhook entry point: wiring, middlewares, routers, graceful lifecycle."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from bot.cache.redis import RedisClient
from bot.commands import setup_bot_commands
from bot.config import configure_logging, get_settings
from bot.db.session import create_engine, get_async_session_maker
from bot.handlers import (
    antispam,
    captcha,
    common,
    moderation,
    settings_cmd,
    welcome,
)
from bot.i18n.loader import get_i18n
from bot.middlewares.admin import AdminMiddleware
from bot.middlewares.database import DatabaseMiddleware
from bot.middlewares.i18n import I18nMiddleware
from bot.middlewares.settings import SettingsMiddleware
from bot.utils.tasks import cancel_all

logger = logging.getLogger(__name__)


def _run_migrations() -> None:
    """Apply Alembic migrations to head (runs in a worker thread)."""
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")


def build_dispatcher(
    session_maker, redis: RedisClient, owner_id: int, cache_ttl: int
) -> Dispatcher:
    """Create the Dispatcher, register middlewares (outer) and routers."""
    storage = RedisStorage.from_url(get_settings().REDIS_URL)
    dp = Dispatcher(storage=storage)

    i18n = get_i18n()
    observers = (dp.message, dp.edited_message, dp.callback_query)
    for observer in observers:
        observer.outer_middleware(DatabaseMiddleware(session_maker))
        observer.outer_middleware(SettingsMiddleware(redis, cache_ttl))
        observer.outer_middleware(I18nMiddleware(i18n))
        observer.outer_middleware(AdminMiddleware(redis, owner_id))

    # Order matters: specific command/event routers first, catch-all antispam last.
    dp.include_router(common.router)
    dp.include_router(settings_cmd.router)
    dp.include_router(moderation.router)
    dp.include_router(captcha.router)
    dp.include_router(welcome.router)
    dp.include_router(antispam.router)

    dp["redis"] = redis
    dp["session_maker"] = session_maker
    return dp


async def on_startup(app: web.Application) -> None:
    settings = get_settings()
    bot: Bot = app["bot"]
    redis: RedisClient = app["redis"]

    await redis.connect()
    try:
        await asyncio.to_thread(_run_migrations)
        logger.info("migrations.applied")
    except Exception:
        logger.exception("migrations.failed")
        raise

    # Webhook registration is best-effort: a transient Telegram outage (or an
    # invalid token during local bring-up) must not crash the pod. We log and
    # keep serving /health; ops can re-set the webhook. Schema integrity
    # (migrations) above stays fatal.
    try:
        await bot.set_webhook(
            url=settings.WEBHOOK_URL,
            secret_token=settings.WEBHOOK_SECRET,
            allowed_updates=app["dp"].resolve_used_update_types(),
            drop_pending_updates=True,
        )
        logger.info("bot.started")
    except Exception:
        logger.exception("set_webhook.failed")

    # Populate the ☰ command menu (best-effort, non-fatal).
    await setup_bot_commands(bot)


async def on_shutdown(app: web.Application) -> None:
    await cancel_all()
    bot: Bot = app["bot"]
    try:
        await bot.delete_webhook()
    except Exception:
        logger.warning("delete_webhook.failed", exc_info=True)
    await app["dp"].storage.close()
    await app["redis"].disconnect()
    await app["engine"].dispose()
    await bot.session.close()
    logger.info("bot.shutdown")


def main() -> None:
    settings = get_settings()
    configure_logging(settings)

    bot = Bot(
        token=settings.TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=None),
    )
    engine = create_engine()
    session_maker = get_async_session_maker(engine)
    redis = RedisClient(settings.REDIS_URL)
    dp = build_dispatcher(
        session_maker, redis, settings.OWNER_ID, settings.SETTINGS_CACHE_TTL
    )

    app = web.Application()
    app["bot"] = bot
    app["dp"] = dp
    app["redis"] = redis
    app["engine"] = engine
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    async def health(_: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    app.router.add_get("/health", health)

    SimpleRequestHandler(
        dispatcher=dp, bot=bot, secret_token=settings.WEBHOOK_SECRET
    ).register(app, path=settings.WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    web.run_app(app, host=settings.WEBHOOK_HOST, port=settings.WEBHOOK_PORT)


if __name__ == "__main__":
    main()
