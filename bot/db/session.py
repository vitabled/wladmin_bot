from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from bot.config import get_settings


async def init_db():
    """Initialize database connection."""
    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL, echo=False)

    async with engine.begin() as conn:
        from bot.db.models import Base

        await conn.run_sync(Base.metadata.create_all)

    return engine


def get_async_session_maker(engine):
    """Get async session maker."""
    return async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
