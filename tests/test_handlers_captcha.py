"""Handler tests for the captcha flow."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot, Dispatcher, types

from bot.db import crud
from bot.handlers import captcha, welcome
from tests.conftest import make_bot, make_callback, make_chat, make_message, make_user


def make_member_event(user, *, old_status="left", new_status="member"):
    event = MagicMock(spec=types.ChatMemberUpdated)
    event.bot = make_bot()
    event.chat = make_chat()
    event.from_user = make_user(1000, "Inviter")
    event.old_chat_member = SimpleNamespace(status=old_status, user=user)
    event.new_chat_member = SimpleNamespace(status=new_status, user=user)
    return event


@pytest.fixture(autouse=True)
def patch_captcha(monkeypatch):
    monkeypatch.setattr(captcha, "safe_unmute_member", AsyncMock(return_value=True))
    monkeypatch.setattr(captcha, "safe_delete_message", AsyncMock(return_value=True))
    monkeypatch.setattr(captcha, "safe_mute_member", AsyncMock(return_value=True))
    monkeypatch.setattr(captcha, "send_welcome", AsyncMock())
    monkeypatch.setattr(captcha, "_start_captcha", AsyncMock())
    monkeypatch.setattr(crud, "upsert_user", AsyncMock())
    # Phase 8: no federation by default, so the on-join fed-ban check is skipped.
    monkeypatch.setattr(crud, "get_chat_federation", AsyncMock(return_value=None))


async def test_wrong_user_cannot_solve(base_data):
    # callback targets user 500, but stranger 999 presses it.
    cb = make_callback(data="captcha:-100:500:ok", from_user=make_user(999, "Stranger"))
    await captcha.on_captcha_answer(cb, **base_data)
    cb.answer.assert_awaited_once_with("captcha_not_for_you", show_alert=True)


async def test_button_correct_solves(base_data):
    base_data["redis"].get_captcha = AsyncMock(
        return_value={"type": "button", "answer": "", "message_id": 10}
    )
    base_data["redis"].delete_captcha = AsyncMock()
    cb = make_callback(data="captcha:-100:500:ok", from_user=make_user(500, "Newbie"))
    await captcha.on_captcha_answer(cb, **base_data)
    captcha.safe_unmute_member.assert_awaited_once()
    captcha.send_welcome.assert_awaited_once()


async def test_no_pending_double_press(base_data):
    base_data["redis"].get_captcha = AsyncMock(return_value=None)
    cb = make_callback(data="captcha:-100:500:ok", from_user=make_user(500, "Newbie"))
    await captcha.on_captcha_answer(cb, **base_data)
    cb.answer.assert_awaited_once_with()  # silent ack, no alert
    captcha.safe_unmute_member.assert_not_awaited()


async def test_concurrent_press_loses_atomic_claim(base_data):
    # Both presses read pending, but Redis DEL removed 0 keys for this caller
    # (the other concurrent task already claimed it) -> no double unmute/welcome.
    base_data["redis"].get_captcha = AsyncMock(
        return_value={"type": "button", "answer": "", "message_id": 10}
    )
    base_data["redis"].delete_captcha = AsyncMock(return_value=False)
    cb = make_callback(data="captcha:-100:500:ok", from_user=make_user(500, "Newbie"))
    await captcha.on_captcha_answer(cb, **base_data)
    captcha.safe_unmute_member.assert_not_awaited()
    captcha.send_welcome.assert_not_awaited()


async def test_math_wrong_answer(base_data):
    base_data["redis"].get_captcha = AsyncMock(
        return_value={"type": "math", "answer": "8", "message_id": 10}
    )
    cb = make_callback(data="captcha:-100:500:9", from_user=make_user(500, "Newbie"))
    await captcha.on_captcha_answer(cb, **base_data)
    cb.answer.assert_awaited_once_with("captcha_wrong", show_alert=True)
    captcha.safe_unmute_member.assert_not_awaited()


async def test_new_member_captcha_enabled(base_data):
    base_data["settings"]["captcha_enabled"] = True
    newbie = make_user(500, "Newbie", "newbie")
    event = make_member_event(newbie)
    await captcha.on_chat_member(event, **base_data)
    captcha._start_captcha.assert_awaited_once_with(
        event.bot,
        base_data["redis"],
        base_data["session_maker"],
        event.chat,
        newbie,
        base_data["settings"],
        base_data["_"],
        base_data["lang"],
        base_data["_raw"],
    )
    captcha.send_welcome.assert_not_awaited()


async def test_new_member_welcome_when_captcha_disabled(base_data):
    base_data["settings"]["captcha_enabled"] = False
    newbie = make_user(500, "Newbie", "newbie")
    event = make_member_event(newbie)
    await captcha.on_chat_member(event, **base_data)
    captcha.send_welcome.assert_awaited_once()
    captcha._start_captcha.assert_not_awaited()


async def test_fedbanned_member_banned_on_join(base_data, monkeypatch):
    base_data["settings"]["captcha_enabled"] = True
    base_data["settings"]["newbie_media_enabled"] = True
    monkeypatch.setattr(
        crud, "get_chat_federation", AsyncMock(return_value=SimpleNamespace(id=5))
    )
    monkeypatch.setattr(crud, "is_fedbanned", AsyncMock(return_value=True))
    monkeypatch.setattr(captcha, "safe_ban_member", AsyncMock(return_value=True))
    newbie = make_user(500, "Banned", "banned")
    event = make_member_event(newbie)
    await captcha.on_chat_member(event, **base_data)
    captcha.safe_ban_member.assert_awaited_once()
    base_data["redis"].mark_newbie.assert_not_awaited()
    captcha._start_captcha.assert_not_awaited()
    captcha.send_welcome.assert_not_awaited()


async def test_new_member_bot_ignored(base_data):
    base_data["settings"]["captcha_enabled"] = True
    bot_user = make_user(700, "SomeBot", "somebot", is_bot=True)
    event = make_member_event(bot_user)
    await captcha.on_chat_member(event, **base_data)
    captcha._start_captcha.assert_not_awaited()
    captcha.send_welcome.assert_not_awaited()
    crud.upsert_user.assert_not_awaited()
    crud.get_chat_federation.assert_not_awaited()
    base_data["redis"].mark_newbie.assert_not_awaited()


async def test_admin_join_welcome_without_captcha(base_data):
    base_data["settings"]["captcha_enabled"] = True
    event = make_member_event(make_user(500), new_status="administrator")
    await captcha.on_chat_member(event, **base_data)
    captcha.send_welcome.assert_awaited_once_with(
        event.bot,
        event.chat,
        event.new_chat_member.user,
        base_data["settings"],
        base_data["_"],
    )
    captcha._start_captcha.assert_not_awaited()
    captcha.safe_mute_member.assert_not_awaited()


async def test_newbie_marked_before_captcha(base_data):
    base_data["settings"].update(
        captcha_enabled=True, newbie_media_enabled=True, newbie_period=120
    )
    event = make_member_event(make_user(500))

    async def start(*args):
        base_data["redis"].mark_newbie.assert_awaited_once_with(event.chat.id, 500, 120)
        return True

    captcha._start_captcha.side_effect = start
    await captcha.on_chat_member(event, **base_data)
    captcha._start_captcha.assert_awaited_once()


async def test_captcha_failure_falls_back_to_welcome(base_data):
    base_data["settings"]["captcha_enabled"] = True
    captcha._start_captcha.return_value = False
    await captcha.on_chat_member(make_member_event(make_user(500)), **base_data)
    captcha.send_welcome.assert_awaited_once()


@pytest.mark.parametrize(
    ("old_status", "new_status", "joined"),
    [
        ("left", "member", True),
        ("kicked", "member", True),
        ("left", "restricted", True),
        ("left", "administrator", True),
        ("member", "administrator", False),
        ("restricted", "member", False),
        ("member", "left", False),
        ("member", "kicked", False),
        ("member", "member", False),
    ],
)
async def test_router_filters_transitions(base_data, old_status, new_status, joined):
    event = make_member_event(
        make_user(500), old_status=old_status, new_status=new_status
    )
    await captcha.router.propagate_event("chat_member", event, **base_data)
    assert captcha.send_welcome.await_count == int(joined)
    assert crud.upsert_user.await_count == int(joined)


async def test_join_without_service_message_through_dispatcher(base_data, monkeypatch):
    from bot import __main__ as entry
    from bot.middlewares.admin import AdminMiddleware
    from bot.middlewares.database import DatabaseMiddleware
    from bot.middlewares.i18n import I18nMiddleware
    from bot.middlewares.settings import SettingsMiddleware

    dp = Dispatcher()
    monkeypatch.setattr(entry, "Dispatcher", lambda **kwargs: dp)
    monkeypatch.setattr(entry.RedisStorage, "from_url", lambda url: None)
    monkeypatch.setattr(
        entry, "get_settings", lambda: SimpleNamespace(REDIS_URL="redis://localhost")
    )

    # Include real routers without retaining parent links between tests.
    def include_router(router):
        dp.sub_routers.append(router)

    monkeypatch.setattr(dp, "include_router", include_router)
    entry.build_dispatcher(
        base_data["session_maker"], base_data["redis"], 999, 3600, ()
    )
    middleware_types = [type(m) for m in dp.chat_member.outer_middleware]
    for middleware in (
        DatabaseMiddleware,
        SettingsMiddleware,
        I18nMiddleware,
        AdminMiddleware,
    ):
        assert middleware in middleware_types
    assert "chat_member" in dp.resolve_used_update_types()
    # DB/Redis boundaries are mocked; dispatch, filters and Telegram models are real.
    dp.chat_member.outer_middleware._middlewares.clear()
    base_data["settings"]["captcha_enabled"] = True
    user = types.User(id=500, is_bot=False, first_name="Newbie")
    chat = types.Chat(id=-1001234, type="supergroup", title="Chat")
    update = types.Update(
        update_id=1,
        chat_member=types.ChatMemberUpdated(
            chat=chat,
            from_user=types.User(id=1000, is_bot=False, first_name="Inviter"),
            date=datetime(2026, 8, 10, tzinfo=UTC),
            old_chat_member=types.ChatMemberLeft(user=user),
            new_chat_member=types.ChatMemberMember(user=user),
        ),
    )
    bot = Bot("42:TEST")
    try:
        await dp.feed_update(bot, update, **base_data)
    finally:
        await bot.session.close()
    captcha._start_captcha.assert_awaited_once()
    assert captcha._start_captcha.call_args.args[4].id == user.id
    captcha.safe_delete_message.assert_not_awaited()


@pytest.mark.parametrize("delete_service_messages", [True, False])
async def test_service_join_only_cleans_message(
    base_data, monkeypatch, delete_service_messages
):
    monkeypatch.setattr(welcome, "safe_delete_message", AsyncMock())
    monkeypatch.setattr(welcome, "send_welcome", AsyncMock())
    base_data["settings"]["delete_service_messages"] = delete_service_messages
    msg = make_message(new_chat_members=[make_user(500)])
    dp = Dispatcher()
    dp.sub_routers.extend([captcha.router, welcome.router])
    await dp.propagate_event("message", msg, **base_data)
    assert welcome.safe_delete_message.await_count == int(delete_service_messages)
    if delete_service_messages:
        welcome.safe_delete_message.assert_awaited_once_with(
            msg.bot, msg.chat.id, msg.message_id
        )
    welcome.send_welcome.assert_not_awaited()
    captcha.send_welcome.assert_not_awaited()
    captcha._start_captcha.assert_not_awaited()
