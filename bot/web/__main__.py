"""Run the dashboard: ``python -m bot.web`` (or via the compose ``dashboard``).

Shares the bot's Postgres/Redis. The bot process must be running for settings
changes to take effect live (this app only writes DB + invalidates the cache).
"""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI

from bot.cache.redis import RedisClient
from bot.config import configure_logging, get_settings
from bot.db.session import create_engine, get_async_session_maker
from bot.web.app import create_app


def build() -> FastAPI:
    """Wire the dashboard app with real DB/Redis and lifecycle hooks."""
    settings = get_settings()
    configure_logging(settings)
    engine = create_engine()
    session_maker = get_async_session_maker(engine)
    redis = RedisClient(settings.REDIS_URL)

    app = create_app(settings, session_maker, redis)
    app.add_event_handler("startup", redis.connect)
    app.add_event_handler("shutdown", redis.disconnect)
    app.add_event_handler("shutdown", engine.dispose)
    return app


def main() -> None:
    settings = get_settings()
    uvicorn.run(build(), host=settings.WEB_HOST, port=settings.WEB_PORT)


if __name__ == "__main__":
    main()
