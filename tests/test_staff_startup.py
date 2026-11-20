"""Startup seeds staff before registering their private command scopes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from bot import __main__ as entry


async def test_staff_setup_commits_seeds_and_registers_active_members(monkeypatch):
    session = AsyncMock()
    session_maker = MagicMock()
    session_maker.return_value.__aenter__.return_value = session
    settings = SimpleNamespace(
        OWNER_ID=11, default_staff_ids=(22,), allowed_dm_ids=(44,)
    )
    seed = AsyncMock()
    listing = AsyncMock(return_value=[SimpleNamespace(user_id=33)])
    commands = AsyncMock()
    monkeypatch.setattr(entry, "seed_defaults", seed, raising=False)
    monkeypatch.setattr(entry, "list_staff", listing, raising=False)
    monkeypatch.setattr(entry, "setup_bot_commands", commands)
    bot = MagicMock()
    redis = AsyncMock()

    await entry._setup_staff(bot, settings, session_maker, redis)

    seed.assert_awaited_once_with(session, 11, (22,), redis=redis)
    session.commit.assert_awaited_once()
    listing.assert_awaited_once_with(session)
    commands.assert_awaited_once_with(
        bot, allowed_dm_ids=(44,), staff_ids=(11, 22, 33)
    )
