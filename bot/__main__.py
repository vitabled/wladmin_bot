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
from bot.config import Settings, configure_logging, get_settings
from bot.db.session import create_engine, get_async_session_maker
from bot.handlers import (
    antispam,
    captcha,
    common,
    federation,
    menu,
    moderation,
    scam,
    schedule,
    settings_cmd,
    stats,
    welcome,
)
from bot.i18n.loader import get_i18n
from bot.middlewares.admin import AdminMiddleware
from bot.middlewares.database import DatabaseMiddleware
from bot.middlewares.i18n import I18nMiddleware
from bot.middlewares.settings import SettingsMiddleware
from bot.scheduler import run_scheduler
from bot.utils.tasks import cancel_all, spawn

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
    dp.include_router(menu.router)
    dp.include_router(settings_cmd.router)
    dp.include_router(moderation.router)
    dp.include_router(stats.router)
    dp.include_router(schedule.router)
    dp.include_router(federation.router)
    dp.include_router(scam.router)
    dp.include_router(captcha.router)
    dp.include_router(welcome.router)
    dp.include_router(antispam.router)

    dp["redis"] = redis
    dp["session_maker"] = session_maker
    return dp


async def _run_polling(
    dp: Dispatcher,
    bot: Bot,
    settings: Settings,
    session_maker,
    redis: RedisClient,
    engine,
) -> None:
    """Long-polling delivery (getUpdates) instead of a webhook.

    Used when no public HTTPS webhook URL is available (local/dev hosts).
    Migrations, the /start command menu and the scheduler behave exactly as
    in webhook mode; a minimal /health endpoint keeps the compose healthcheck
    and port probes working.
    """
    await redis.connect()
    try:
        await asyncio.to_thread(_run_migrations)
        logger.info("migrations.applied")
    except Exception:
        logger.exception("migrations.failed")
        raise

    # Telegram routes updates to the webhook until it is cleared; drop any
    # stale webhook so getUpdates starts delivering.
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("webhook.cleared_for_polling")
    except Exception:
        logger.exception("delete_webhook.failed")

    await setup_bot_commands(bot)
    spawn(run_scheduler(bot, session_maker))
    logger.info("scheduler.spawned")
    logger.info("bot.started (polling mode)")

    # Minimal liveness endpoint on the webhook port for healthchecks.
    health_app = web.Application()

    async def health(_: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "mode": "polling"})

    health_app.router.add_get("/health", health)
    runner = web.AppRunner(health_app)
    await runner.setup()
    await web.TCPSite(runner, settings.WEBHOOK_HOST, settings.WEBHOOK_PORT).start()

    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
        )
    finally:
        await cancel_all()
        await runner.cleanup()
        await dp.storage.close()
        await redis.disconnect()
        await engine.dispose()
        await bot.session.close()
        logger.info("bot.shutdown")


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

    # Start the scheduled-posting worker (cancelled on shutdown by cancel_all).
    spawn(run_scheduler(bot, app["session_maker"]))
    logger.info("scheduler.spawned")


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
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    engine = create_engine()
    session_maker = get_async_session_maker(engine)
    redis = RedisClient(settings.REDIS_URL)
    dp = build_dispatcher(
        session_maker, redis, settings.OWNER_ID, settings.SETTINGS_CACHE_TTL
    )

    if settings.BOT_MODE.lower() == "polling":
        asyncio.run(_run_polling(dp, bot, settings, session_maker, redis, engine))
        return

    app = web.Application()
    app["bot"] = bot
    app["dp"] = dp
    app["redis"] = redis
    app["engine"] = engine
    app["session_maker"] = session_maker
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
