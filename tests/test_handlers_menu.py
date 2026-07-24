"""Handler tests for the inline settings menu (Phase 6)."""

from __future__ import annotations

from unittest.mock import AsyncMock

from bot.db import crud
from bot.handlers import menu
from tests.conftest import make_callback, make_message, make_user


def _translate(key, **kwargs):
    return key


# --------------------------------------------------------------------------- #
# Keyboard builder (pure)
# --------------------------------------------------------------------------- #
def test_build_menu_has_all_toggles_plus_close():
    kb = menu.build_menu({}, _translate)
    rows = kb.inline_keyboard
    # one button per toggle + a close button, one per row (adjust(1)).
    assert len(rows) == len(menu._TOGGLES) + 1
    assert rows[-1][0].callback_data == "menu:close"


def test_build_menu_reflects_state():
    kb = menu.build_menu(
        {"welcome_enabled": True, "captcha_enabled": False}, _translate
    )
    texts = [row[0].text for row in kb.inline_keyboard]
    assert any(t.startswith("✅") for t in texts)
    assert any(t.startswith("❌") for t in texts)


# --------------------------------------------------------------------------- #
# /menu command
# --------------------------------------------------------------------------- #
async def test_cmd_menu_admin_opens(base_data):
    msg = make_message()
    await menu.cmd_menu(msg, **base_data)
    msg.reply.assert_awaited_once()
    _, kwargs = msg.reply.await_args
    assert "reply_markup" in kwargs


async def test_cmd_menu_non_admin_blocked(base_data):
    base_data["is_admin"] = False
    msg = make_message()
    await menu.cmd_menu(msg, **base_data)
    _, kwargs = msg.reply.await_args
    assert "reply_markup" not in kwargs


# --------------------------------------------------------------------------- #
# Callback handling
# --------------------------------------------------------------------------- #
def _menu_callback(data_str: str):
    cb = make_callback(data=data_str, from_user=make_user(9, "Adm"))
    cb.message.edit_reply_markup = AsyncMock()
    cb.message.delete = AsyncMock()
    return cb


async def test_toggle_flips_and_saves(base_data, monkeypatch):
    monkeypatch.setattr(crud, "update_settings", AsyncMock())
    base_data["settings"]["welcome_enabled"] = True
    cb = _menu_callback("menu:t:welcome_enabled")
    await menu.on_menu_callback(cb, **base_data)
    _, kwargs = crud.update_settings.await_args
    assert kwargs == {"welcome_enabled": False}
    cb.message.edit_reply_markup.assert_awaited_once()
    cb.answer.assert_awaited()


async def test_toggle_non_admin_alert(base_data, monkeypatch):
    monkeypatch.setattr(crud, "update_settings", AsyncMock())
    base_data["is_admin"] = False
    cb = _menu_callback("menu:t:welcome_enabled")
    await menu.on_menu_callback(cb, **base_data)
    crud.update_settings.assert_not_awaited()
    _, kwargs = cb.answer.await_args
    assert kwargs.get("show_alert") is True


async def test_unknown_field_ignored(base_data, monkeypatch):
    monkeypatch.setattr(crud, "update_settings", AsyncMock())
    cb = _menu_callback("menu:t:not_a_field")
    await menu.on_menu_callback(cb, **base_data)
    crud.update_settings.assert_not_awaited()
    cb.answer.assert_awaited()


async def test_close_deletes_message(base_data):
    cb = _menu_callback("menu:close")
    await menu.on_menu_callback(cb, **base_data)
    cb.message.delete.assert_awaited_once()
