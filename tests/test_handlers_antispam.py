"""Handler tests for antispam message processing."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from bot.db import crud
from bot.handlers import actions, antispam
from tests.conftest import make_message, make_user


@pytest.fixture(autouse=True)
def patch_antispam(monkeypatch):
    monkeypatch.setattr(antispam, "safe_delete_message", AsyncMock(return_value=True))
    monkeypatch.setattr(crud, "add_mod_log", AsyncMock())
    for name in ("do_warn", "do_mute", "do_ban"):
        monkeypatch.setattr(actions, name, AsyncMock())


async def test_admin_is_exempt(base_data):
    base_data["settings"]["filter_links"] = True
    base_data["is_admin"] = True
    msg = make_message(text="visit http://spam.example")
    await antispam._process(msg, base_data)
    antispam.safe_delete_message.assert_not_awaited()


async def test_sender_chat_skipped(base_data):
    base_data["settings"]["filter_links"] = True
    base_data["is_admin"] = False
    msg = make_message(text="http://spam.example", sender_chat=object())
    await antispam._process(msg, base_data)
    antispam.safe_delete_message.assert_not_awaited()


async def test_auto_forward_skipped(base_data):
    base_data["settings"]["filter_forwards"] = True
    base_data["is_admin"] = False
    msg = make_message(text="post", is_automatic_forward=True, forward_origin=object())
    await antispam._process(msg, base_data)
    antispam.safe_delete_message.assert_not_awaited()


async def test_link_deleted(base_data):
    base_data["settings"]["filter_links"] = True
    base_data["is_admin"] = False
    msg = make_message(text="visit http://spam.example", from_user=make_user(500, "V"))
    await antispam._process(msg, base_data)
    antispam.safe_delete_message.assert_awaited_once()
    crud.add_mod_log.assert_awaited()


async def test_stopword_from_cache_triggers_warn(base_data):
    base_data["settings"]["filter_stopwords"] = True
    base_data["settings"]["antispam_action"] = "warn"
    base_data["is_admin"] = False
    base_data["redis"].get_cached_stopwords = AsyncMock(return_value=["spam"])
    msg = make_message(text="this is spam", from_user=make_user(500, "V"))
    await antispam._process(msg, base_data)
    antispam.safe_delete_message.assert_awaited_once()
    actions.do_warn.assert_awaited_once()


async def test_clean_message_no_action(base_data):
    base_data["settings"]["filter_links"] = True
    base_data["is_admin"] = False
    msg = make_message(text="just a normal message", from_user=make_user(500, "V"))
    await antispam._process(msg, base_data)
    antispam.safe_delete_message.assert_not_awaited()
