"""Tests for the ☰ command-menu registration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from bot.commands import _ADMIN, _PRIVATE, setup_bot_commands


def test_every_command_has_en_description():
    # English is the fallback for unmatched locales — it must always exist.
    for name, desc in [*_PRIVATE, *_ADMIN]:
        assert "en" in desc, name
        assert 1 <= len(name) <= 32
        assert 1 <= len(desc["en"]) <= 256


async def test_setup_registers_scopes():
    bot = MagicMock()
    bot.set_my_commands = AsyncMock()
    await setup_bot_commands(bot)
    # 2 langs × 2 scopes + 2 fallback calls = 6 registrations.
    assert bot.set_my_commands.await_count == 6


async def test_setup_swallows_errors():
    bot = MagicMock()
    bot.set_my_commands = AsyncMock(side_effect=RuntimeError("telegram down"))
    # Must not raise — startup continues even if the menu can't be set.
    await setup_bot_commands(bot)
