"""Tests for the ☰ command-menu registration (role-based scopes)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from aiogram.types import (
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
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
    # /start скрыт у обычных пользователей (остаётся рабочим по прямому вводу).
    assert names == ["help", "info", "scam"]
    assert "start" not in names


def test_admin_commands_include_all_users_plus_moderation():
    admin_names = [name for name, _ in _ADMIN]
    all_names = [name for name, _ in _ALL_USERS]
    # Админ видит и общие команды (скоупы могут перекрывать друг друга).
    for name in all_names:
        assert name in admin_names
    # /start остаётся видимым только админам.
    assert "start" in admin_names
    # Админские команды поверх общего набора.
    for name in ("addtowl", "ban", "mute", "warn", "settings", "menu", "finfo"):
        assert name in admin_names


async def test_setup_registers_scopes_with_empty_allowlist():
    # Empty allowlist → legacy behavior: every private chat sees _ALL_USERS.
    bot = MagicMock()
    bot.set_my_commands = AsyncMock()
    await setup_bot_commands(bot, allowed_dm_ids=())
    # 2 langs × 3 scopes + 3 fallback calls = 9 registrations.
    assert bot.set_my_commands.await_count == 9

    scopes = {call.kwargs["scope"].type for call in bot.set_my_commands.await_args_list}
    assert scopes == {
        BotCommandScopeAllPrivateChats().type,
        BotCommandScopeAllGroupChats().type,
        BotCommandScopeAllChatAdministrators().type,
    }

    # Legacy: the global private scope still carries the full _ALL_USERS list.
    private_calls = [
        call
        for call in bot.set_my_commands.await_args_list
        if call.kwargs["scope"].type == BotCommandScopeAllPrivateChats().type
    ]
    assert private_calls
    for call in private_calls:
        names = [c.command for c in call.args[0]]
        assert names == ["help", "info", "scam"]


async def test_setup_registers_per_chat_scopes_for_allowed_dm_ids():
    # DM lockdown: global private scope is emptied, each allowed user gets
    # _ALL_USERS via BotCommandScopeChat; groups/admins unchanged.
    bot = MagicMock()
    bot.set_my_commands = AsyncMock()
    await setup_bot_commands(bot, allowed_dm_ids=(111, 222))
    # 2 langs × (group + admin + private-empty + 2 per-chat) + same for the
    # language-agnostic fallback = 15 registrations.
    assert bot.set_my_commands.await_count == 15

    calls = bot.set_my_commands.await_args_list
    scopes = {call.kwargs["scope"].type for call in calls}
    assert scopes == {
        BotCommandScopeAllPrivateChats().type,
        BotCommandScopeAllGroupChats().type,
        BotCommandScopeAllChatAdministrators().type,
        BotCommandScopeChat(chat_id=111).type,
    }

    # Global private scope must be EMPTY so non-allowed users see no menu.
    private_calls = [
        call
        for call in calls
        if call.kwargs["scope"].type == BotCommandScopeAllPrivateChats().type
    ]
    assert private_calls
    for call in private_calls:
        assert call.args[0] == []

    # Each allowed user gets the full _ALL_USERS list per-chat (2 langs + fallback).
    chat_calls = [
        call for call in calls if call.kwargs["scope"].type == "chat"
    ]
    assert len(chat_calls) == 6  # 2 users × (2 langs + fallback)
    chat_ids = {call.kwargs["scope"].chat_id for call in chat_calls}
    assert chat_ids == {111, 222}
    for call in chat_calls:
        names = [c.command for c in call.args[0]]
        assert names == ["help", "info", "scam"]

    # Groups keep the same _ALL_USERS list as before.
    group_calls = [
        call
        for call in calls
        if call.kwargs["scope"].type == BotCommandScopeAllGroupChats().type
    ]
    assert group_calls
    for call in group_calls:
        names = [c.command for c in call.args[0]]
        assert names == ["help", "info", "scam"]


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
    # Каждый вызов для групп несёт только общий набор (без модерации и /start).
    for call in group_calls:
        names = [c.command for c in call.args[0]]
        assert names == ["help", "info", "scam"]

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
