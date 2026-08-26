"""Handler tests for the DM main-menu button interface (bot/handlers/dm_menu.py).

Follows the established style: handlers are called directly with mocked
messages/callbacks from tests/conftest.py, and the FSM state is exercised
with a real FSMContext backed by aiogram's MemoryStorage.

Covers the tabbed main menu (Администрирование / Рейтинги / Рассылки +
info/help, no separate Статистика tab) and the unscoped seller-check flow
started from the Рейтинги tab (``DmScam.awaiting_target`` with no ``chat_id``
in state).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from bot.db import crud
from bot.handlers import dm_menu
from tests.conftest import make_callback, make_chat, make_message, make_user


def _translate(key, **kwargs):
    return key


def _dm_chat():
    return make_chat(111, "private", "PM")


@pytest.fixture(autouse=True)
def patch_crud(monkeypatch):
    """No DB in unit tests: scam list lookups return 'unknown seller'."""
    monkeypatch.setattr(crud, "get_scam_entry", AsyncMock(return_value=None))
    monkeypatch.setattr(crud, "get_user_by_username", AsyncMock(return_value=None))


@pytest.fixture
async def fsm():
    """A real FSMContext on MemoryStorage (chat 111 / user 1000)."""
    storage = MemoryStorage()
    ctx = FSMContext(
        storage=storage,
        key=StorageKey(bot_id=42, chat_id=111, user_id=1000),
    )
    yield ctx
    await storage.close()


# --------------------------------------------------------------------------- #
# Keyboard builders (pure)
# --------------------------------------------------------------------------- #
def test_main_menu_has_panel_button_plus_three_tabs_plus_info_help():
    kb = dm_menu.build_main_menu(_translate)
    rows = kb.inline_keyboard
    assert len(rows) == 5  # webapp panel + 3 tab rows + info/help row
    assert [len(row) for row in rows] == [1, 1, 1, 1, 2]
    # First row is the WebApp button — it carries web_app, no callback_data.
    assert rows[0][0].web_app is not None
    assert rows[0][0].web_app.url == "https://admin.whitelistmarket.lol"
    assert rows[0][0].callback_data is None
    callbacks = [row[0].callback_data for row in rows[1:4]]
    callbacks += [btn.callback_data for btn in rows[4]]
    assert callbacks == [
        "dm:tab:admin",
        "dm:tab:ratings",
        "dm:tab:broadcast",
        "dm:info",
        "dm:help",
    ]
    # No separate Статистика tab — stats live inside Администрирование.
    assert "dm:tab:stats" not in callbacks


def test_main_menu_has_exactly_three_tabs():
    kb = dm_menu.build_main_menu(_translate)
    callbacks = [
        btn.callback_data for row in kb.inline_keyboard for btn in row
    ]
    tabs = [c for c in callbacks if c and c.startswith("dm:tab:")]
    assert tabs == ["dm:tab:admin", "dm:tab:ratings", "dm:tab:broadcast"]


def test_main_menu_has_no_scam_or_groups_buttons():
    kb = dm_menu.build_main_menu(_translate)
    callbacks = [
        btn.callback_data for row in kb.inline_keyboard for btn in row
    ]
    assert "dm:scam" not in callbacks
    assert "dm:groups" not in callbacks


def test_main_menu_labels_stay_plain_no_tg_emoji():
    # Кнопки не парсят HTML: подписи обязаны приходить без <tg-emoji> тегов.
    kb = dm_menu.build_main_menu(_translate)
    for row in kb.inline_keyboard:
        for btn in row:
            assert "<tg-emoji" not in btn.text


def test_back_kb_single_menu_button():
    kb = dm_menu._back_kb(_translate)
    rows = kb.inline_keyboard
    assert len(rows) == 1
    assert rows[0][0].callback_data == "dm:menu"


# --------------------------------------------------------------------------- #
# /start in private chat
# --------------------------------------------------------------------------- #
async def test_dm_start_shows_welcome_and_menu(base_data):
    msg = make_message(chat=_dm_chat())
    await dm_menu.dm_start(msg, **base_data)
    msg.answer.assert_awaited_once()
    args, kwargs = msg.answer.await_args
    assert args[0] == "dm_menu_welcome"
    kb = kwargs["reply_markup"]
    # Row 0 is the WebApp «Панель» button; rows 1-3 are the three tabs.
    assert [row[0].callback_data for row in kb.inline_keyboard[1:4]] == [
        "dm:tab:admin",
        "dm:tab:ratings",
        "dm:tab:broadcast",
    ]


# --------------------------------------------------------------------------- #
# Plain text in private chat re-shows the menu
# --------------------------------------------------------------------------- #
async def test_dm_plain_text_shows_menu(base_data):
    msg = make_message(text="привет", chat=_dm_chat())
    await dm_menu.dm_any_text(msg, **base_data)
    msg.answer.assert_awaited_once()
    args, kwargs = msg.answer.await_args
    assert args[0] == "dm_menu_title"
    assert "reply_markup" in kwargs


async def test_dm_any_text_filter_skips_commands_and_groups():
    # The router-level filter must accept plain DM text, reject commands
    # (so /scam, /addtowl etc. keep working in PM) and reject groups.
    handler = next(
        h for h in dm_menu.router.message.handlers if h.callback is dm_menu.dm_any_text
    )

    ok, _ = await handler.check(make_message(text="hello", chat=_dm_chat()))
    assert ok is True

    bad, _ = await handler.check(
        make_message(text="/scam @someuser", chat=_dm_chat())
    )
    assert bad is False  # command — leave it for its own router

    bad, _ = await handler.check(make_message(text="hello"))  # supergroup
    assert bad is False

    bad, _ = await handler.check(make_message(text=None, chat=_dm_chat()))
    assert bad is False  # no text (photo/sticker) — not "text"


# --------------------------------------------------------------------------- #
# Callbacks
# --------------------------------------------------------------------------- #
def _cb(data: str):
    cb = make_callback(
        data=data, from_user=make_user(1000, "Actor", "actor"), chat=_dm_chat()
    )
    cb.message.edit_text = AsyncMock()
    cb.message.answer = AsyncMock()
    cb.message.delete = AsyncMock()
    return cb


async def test_dm_rt_check_callback_sets_dmscam_state(base_data, fsm):
    # The Рейтинги «Проверить продавца» reuses the DmScam flow unscoped.
    cb = _cb("dm:rt:check")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    assert await fsm.get_state() == dm_menu.DmScam.awaiting_target
    state_data = await fsm.get_data()
    assert state_data.get("chat_id") is None  # no risk_chat scoping
    text = cb.message.edit_text.await_args.args[0]
    assert text == "dm_rt_check_prompt"
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    assert [row[0].callback_data for row in kb.inline_keyboard] == ["dm:menu"]
    cb.answer.assert_awaited_once()


async def test_dm_menu_callback_clears_state_and_shows_menu(base_data, fsm):
    await fsm.set_state(dm_menu.DmScam.awaiting_target)
    cb = _cb("dm:menu")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    assert await fsm.get_state() is None  # back button bails out of the flow
    assert await fsm.get_data() == {}  # state data wiped too
    assert cb.message.edit_text.await_args.args[0] == "dm_menu_title"
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    # WebApp panel row + 3 tab rows + info/help row.
    assert len(kb.inline_keyboard) == 5
    cb.answer.assert_awaited_once()


async def test_dm_info_callback(base_data, fsm):
    cb = _cb("dm:info")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    assert cb.message.edit_text.await_args.args[0] == "cmd_info"
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    assert len(kb.inline_keyboard) == 5
    cb.answer.assert_awaited_once()


async def test_dm_help_callback(base_data, fsm):
    cb = _cb("dm:help")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    assert cb.message.edit_text.await_args.args[0] == "cmd_help_private"
    cb.answer.assert_awaited_once()


async def test_dm_close_callback_is_removed(base_data, fsm):
    # No close buttons anywhere: dm:close must fall through to the unknown
    # branch (acknowledge + ignore), never delete the message.
    cb = _cb("dm:close")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    cb.answer.assert_awaited_once()
    cb.message.delete.assert_not_awaited()
    cb.message.edit_text.assert_not_awaited()


async def test_dm_unknown_callback_just_answers(base_data, fsm):
    cb = _cb("dm:whatever")
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    cb.answer.assert_awaited_once()
    cb.message.edit_text.assert_not_awaited()
    cb.message.delete.assert_not_awaited()


async def test_dm_callback_falls_back_to_answer_when_edit_fails(base_data, fsm):
    cb = _cb("dm:menu")
    cb.message.edit_text = AsyncMock(side_effect=Exception("message is too old"))
    await dm_menu.on_dm_callback(cb, state=fsm, **base_data)
    assert cb.message.answer.await_args.args[0] == "dm_menu_title"
    cb.answer.assert_awaited_once()


# --------------------------------------------------------------------------- #
# Seller-check flow (DmScam.awaiting_target, unscoped — no chat_id)
# --------------------------------------------------------------------------- #
async def test_dm_scam_target_resolves_and_answers_verdict(base_data, fsm):
    await fsm.set_state(dm_menu.DmScam.awaiting_target)
    msg = make_message(text="@someuser", chat=_dm_chat())
    msg.bot.get_chat = AsyncMock(
        return_value=SimpleNamespace(
            id=777, type="private", full_name="Seller", username="someuser"
        )
    )
    await dm_menu.dm_scam_target(msg, state=fsm, **base_data)
    crud.get_scam_entry.assert_awaited_once()
    args, _ = crud.get_scam_entry.await_args
    assert args[1] == 777  # resolved via bot.get_chat
    text = msg.answer.await_args.args[0]
    assert "scam_ok" in text
    assert "scam_footer" in text
    assert msg.answer.await_args.kwargs["parse_mode"] == "HTML"
    kb = msg.answer.await_args.kwargs["reply_markup"]
    # Unscoped flow ends on «🏠 В меню» (not the full main menu).
    assert [row[0].callback_data for row in kb.inline_keyboard] == ["dm:menu"]
    assert await fsm.get_state() is None  # state cleared after the verdict


async def test_dm_scam_target_reply_to_seller(base_data, fsm):
    await fsm.set_state(dm_menu.DmScam.awaiting_target)
    seller = make_user(424242, "Seller", "seller1")
    reply = make_message(text="продаю аккаунт", from_user=seller)
    msg = make_message(text="", chat=_dm_chat(), reply_to_message=reply)
    msg.text = None  # reply without own text — still resolvable via the reply
    await dm_menu.dm_scam_target(msg, state=fsm, **base_data)
    args, _ = crud.get_scam_entry.await_args
    assert args[1] == 424242  # resolved via the reply
    assert "scam_ok" in msg.answer.await_args.args[0]
    assert await fsm.get_state() is None


async def test_dm_scam_target_invalid_keeps_state(base_data, fsm):
    await fsm.set_state(dm_menu.DmScam.awaiting_target)
    msg = make_message(text="not-a-target", chat=_dm_chat())
    await dm_menu.dm_scam_target(msg, state=fsm, **base_data)
    text = msg.answer.await_args.args[0]
    assert "scam_no_target" in text
    kb = msg.answer.await_args.kwargs["reply_markup"]
    assert [row[0].callback_data for row in kb.inline_keyboard] == ["dm:menu"]
    assert await fsm.get_state() == dm_menu.DmScam.awaiting_target  # not cleared


async def test_dm_scam_target_bot_username_is_no_target(base_data, fsm):
    await fsm.set_state(dm_menu.DmScam.awaiting_target)
    msg = make_message(text="@lotesadminbot", chat=_dm_chat())
    msg.bot.get_chat = AsyncMock(
        return_value=SimpleNamespace(
            id=msg.bot.id, type="private", full_name="WL Market Admin", username="lotesadminbot"
        )
    )
    await dm_menu.dm_scam_target(msg, state=fsm, **base_data)
    crud.get_scam_entry.assert_not_awaited()
    text = msg.answer.await_args.args[0]
    assert "scam_no_target" in text
    assert await fsm.get_state() == dm_menu.DmScam.awaiting_target


async def test_dm_scam_target_not_found_error_keeps_state(base_data, fsm):
    # "@missing" that Telegram cannot resolve → error_target_not_found,
    # mapped through the same error keys as /scam.
    await fsm.set_state(dm_menu.DmScam.awaiting_target)
    msg = make_message(text="@missing", chat=_dm_chat())
    msg.bot.get_chat = AsyncMock(side_effect=Exception("user not found"))
    await dm_menu.dm_scam_target(msg, state=fsm, **base_data)
    text = msg.answer.await_args.args[0]
    assert "error_target_not_found" in text
    assert await fsm.get_state() == dm_menu.DmScam.awaiting_target
