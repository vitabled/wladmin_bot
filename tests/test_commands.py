"""Tests for the ☰ command-menu registration (role-based scopes)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from aiogram.types import (
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
)

from bot.commands import _ADMIN, _ALL_USERS, setup_bot_commands


def test_every_command_has_en_description():
    # English is the fallback for unmatched locales — it must always exist.
    for name, desc in [*_ALL_USERS, *_ADMIN]:
        assert "en" in desc, name
        assert 1 <= len(name) <= 32
        assert 1 <= len(desc["en"]) <= 256


def test_all_users_commands():
    names = [name for name, _ in _ALL_USERS]
    assert names == ["start", "help", "info", "scam"]


def test_admin_commands_include_all_users_plus_moderation():
    admin_names = [name for name, _ in _ADMIN]
    all_names = [name for name, _ in _ALL_USERS]
    # Админ видит и общие команды (скоупы могут перекрывать друг друга).
    for name in all_names:
        assert name in admin_names
    # Админские команды поверх общего набора.
    for name in ("addtowl", "ban", "mute", "warn", "settings", "menu", "finfo"):
        assert name in admin_names


async def test_setup_registers_scopes():
    bot = MagicMock()
    bot.set_my_commands = AsyncMock()
    await setup_bot_commands(bot)
    # 2 langs × 3 scopes + 3 fallback calls = 9 registrations.
    assert bot.set_my_commands.await_count == 9

    scopes = {call.kwargs["scope"].type for call in bot.set_my_commands.await_args_list}
    assert scopes == {
        BotCommandScopeAllPrivateChats().type,
        BotCommandScopeAllGroupChats().type,
        BotCommandScopeAllChatAdministrators().type,
    }


async def test_setup_registers_all_users_for_groups():
    bot = MagicMock()
    bot.set_my_commands = AsyncMock()
    await setup_bot_commands(bot)

    group_calls = [
        call
        for call in bot.set_my_commands.await_args_list
        if call.kwargs["scope"].type == BotCommandScopeAllGroupChats().type
    ]
    assert group_calls, "no AllGroupChats registration"
    # Каждый вызов для групп несёт только общий набор (без модерации).
    for call in group_calls:
        names = [c.command for c in call.args[0]]
        assert names == ["start", "help", "info", "scam"]

    admin_calls = [
        call
        for call in bot.set_my_commands.await_args_list
        if call.kwargs["scope"].type == BotCommandScopeAllChatAdministrators().type
    ]
    assert admin_calls, "no AllChatAdministrators registration"
    admin_names = [c.command for call in admin_calls for c in call.args[0]]
    assert "addtowl" in admin_names
    assert "ban" in admin_names


async def test_setup_swallows_errors():
    bot = MagicMock()
    bot.set_my_commands = AsyncMock(side_effect=RuntimeError("telegram down"))
    # Must not raise — startup continues even if the menu can't be set.
    await setup_bot_commands(bot)
