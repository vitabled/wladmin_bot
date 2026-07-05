"""Async SQLAlchemy engine / sessionmaker helpers."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from bot.config import get_settings


def create_engine(echo: bool = False) -> AsyncEngine:
    """Create the async engine from settings."""
    settings = get_settings()
    return create_async_engine(
        settings.DATABASE_URL,
        echo=echo,
        pool_pre_ping=True,
    )


def get_async_session_maker(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """Build a session factory bound to ``engine``."""
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db(echo: bool = False) -> AsyncEngine:
    """Create the engine and (dev fallback) ensure tables exist.

    Production schema is managed by Alembic (``make migrate``); ``create_all``
    is a safety net for local/dev runs and is a no-op once migrations ran.
    """
    engine = create_engine(echo=echo)
    from bot.db.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine
