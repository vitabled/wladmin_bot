"""Regression tests for the staff-only DM administration gate."""

from __future__ import annotations

from unittest.mock import AsyncMock

from aiogram import Dispatcher
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import MessageEntity

from bot.filters.staff import IsStaff
from bot.handlers import dm_menu, dm_shop
from bot.middlewares.admin import AdminMiddleware
from bot.services.staff import ROLE_ADMIN, ROLE_MODERATOR
from tests.conftest import make_callback, make_chat, make_message, make_user


async def test_admin_middleware_loads_staff_role_from_session(monkeypatch):
    session = AsyncMock()
    redis = AsyncMock()
    role_lookup = AsyncMock(return_value=ROLE_MODERATOR)
    monkeypatch.setattr("bot.middlewares.admin.get_role_cached", role_lookup)
    middleware = AdminMiddleware(redis, owner_id=1, default_staff_ids=(2,))
    handler = AsyncMock(return_value="handled")
    event = make_message(
        chat=make_chat(10, "private"),
        from_user=make_user(3),
    )
    data = {"bot": event.bot, "session": session}

    result = await middleware(handler, event, data)

    assert result == "handled"
    role_lookup.assert_awaited_once_with(session, redis, 3)
    assert data["staff_role"] == ROLE_MODERATOR
    assert data["is_staff"] is True


async def test_admin_middleware_uses_defaults_only_without_session(monkeypatch):
    redis = AsyncMock()
    role_lookup = AsyncMock()
    monkeypatch.setattr("bot.middlewares.admin.get_role_cached", role_lookup)
    middleware = AdminMiddleware(redis, owner_id=1, default_staff_ids=(2,))
    handler = AsyncMock(return_value="handled")
    event = make_message(
        chat=make_chat(10, "private"),
        from_user=make_user(2),
    )
    data = {"bot": event.bot}

    await middleware(handler, event, data)

    role_lookup.assert_not_awaited()
    assert data["staff_role"] == ROLE_ADMIN
    assert data["is_staff"] is True


async def test_is_staff_honors_minimum_injected_role():
    event = make_message(from_user=make_user(3))

    assert await IsStaff()(event, staff_role=ROLE_MODERATOR) is True
    assert await IsStaff(min_role=ROLE_ADMIN)(
        event, staff_role=ROLE_MODERATOR
    ) is False


async def test_is_staff_loads_role_when_middleware_data_absent(monkeypatch):
    session = AsyncMock()
    role_lookup = AsyncMock(return_value=ROLE_ADMIN)
    monkeypatch.setattr("bot.filters.staff.get_role", role_lookup)
    event = make_message(from_user=make_user(3))

    allowed = await IsStaff(min_role=ROLE_ADMIN)(event, session=session)

    assert allowed is True
    role_lookup.assert_awaited_once_with(session, 3)


def test_shop_keyboard_has_four_public_actions():
    keyboard = dm_shop.build_shop_menu(lambda key, **kwargs: key)

    callbacks = [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
    ]
    assert callbacks == [
        "shop:pin",
        "shop:early",
        "shop:orders",
        "shop:help",
    ]


async def test_nonstaff_admin_command_is_stopped_but_scam_falls_through(base_data):
    dispatcher = Dispatcher()
    dispatcher.sub_routers.extend([dm_shop.router])
    command_data = {
        **base_data,
        "staff_role": None,
        "event_chat": make_chat(111, "private"),
        "event_user": make_user(1000),
    }
    settings = make_message(
        text="/settings",
        chat=command_data["event_chat"],
        from_user=command_data["event_user"],
        entities=[MessageEntity(type="bot_command", offset=0, length=9)],
    )
    scam = make_message(
        text="/scam",
        chat=command_data["event_chat"],
        from_user=command_data["event_user"],
        entities=[MessageEntity(type="bot_command", offset=0, length=5)],
    )

    await dispatcher.propagate_event("message", settings, **command_data)
    await dispatcher.propagate_event("message", scam, **command_data)

    settings.answer.assert_awaited_once_with("dm_staff_command_denied")
    scam.answer.assert_not_awaited()


async def test_outsider_dm_callback_is_denied_without_consuming_fsm(base_data):
    storage = MemoryStorage()
    state = FSMContext(
        storage=storage,
        key=StorageKey(bot_id=42, chat_id=111, user_id=1000),
    )
    await state.set_state(dm_menu.DmAdmin.awaiting_target)
    callback = make_callback(
        data="dm:unknown",
        from_user=make_user(1000),
        chat=make_chat(111, "private"),
    )
    dispatcher = Dispatcher(storage=storage)
    dispatcher.sub_routers.extend([dm_menu.router])

    await dispatcher.propagate_event(
        "callback_query",
        callback,
        **{
            **base_data,
            "state": state,
            "staff_role": None,
            "event_chat": callback.message.chat,
            "event_user": callback.from_user,
        },
    )

    callback.answer.assert_awaited_once_with(
        "dm_staff_denied",
        show_alert=True,
    )
    assert await state.get_state() == dm_menu.DmAdmin.awaiting_target
    await storage.close()


async def test_staff_dm_callback_reaches_admin_menu(base_data):
    storage = MemoryStorage()
    state = FSMContext(
        storage=storage,
        key=StorageKey(bot_id=42, chat_id=111, user_id=1000),
    )
    callback = make_callback(
        data="dm:unknown",
        from_user=make_user(1000),
        chat=make_chat(111, "private"),
    )
    dispatcher = Dispatcher(storage=storage)
    dispatcher.sub_routers.extend([dm_menu.router])

    await dispatcher.propagate_event(
        "callback_query",
        callback,
        **{
            **base_data,
            "state": state,
            "staff_role": ROLE_MODERATOR,
            "event_chat": callback.message.chat,
            "event_user": callback.from_user,
        },
    )

    callback.answer.assert_awaited_once_with()
    await storage.close()
