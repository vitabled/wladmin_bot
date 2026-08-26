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
def test_build_menu_has_all_toggles_no_close():
    kb = menu.build_menu({}, _translate)
    rows = kb.inline_keyboard
    # one button per toggle, one per row (adjust(1)) — no close button.
    assert len(rows) == len(menu._TOGGLES)
    callbacks = [row[0].callback_data for row in rows]
    assert all(c.startswith("menu:t:") for c in callbacks)
    assert "menu:close" not in callbacks


def test_build_menu_reflects_state():
    kb = menu.build_menu(
        {"welcome_enabled": True, "captcha_enabled": False}, _translate
    )
    icons = [row[0].icon_custom_emoji_id for row in kb.inline_keyboard]
    assert "5776375003280838798" in icons  # ✅ on
    assert "5778527486270770928" in icons  # ❌ off


def test_build_menu_labels_stay_plain_no_tg_emoji():
    # Кнопки не парсят HTML: подписи обязаны приходить без <tg-emoji> тегов,
    # иначе пользователь увидит сырую разметку. Marks ✅/❌ ушли в premium-иконки.
    kb = menu.build_menu({}, _translate)
    for row in kb.inline_keyboard:
        text = row[0].text
        assert "<tg-emoji" not in text
        assert row[0].icon_custom_emoji_id in (
            "5776375003280838798",
            "5778527486270770928",
        )


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


async def test_close_callback_is_removed(base_data):
    # No close buttons: menu:close falls through to the unknown branch and
    # never deletes the message.
    cb = _menu_callback("menu:close")
    await menu.on_menu_callback(cb, **base_data)
    cb.answer.assert_awaited()
    cb.message.delete.assert_not_awaited()
    cb.message.edit_reply_markup.assert_not_awaited()
