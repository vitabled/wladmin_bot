"""Backend refactor tests: explicit chat_id plumbing for the DM per-group panel.

Covers:
* ``moderation._prepare`` / ``prepare_action`` running guards against an
  explicit ``chat_id`` (the SELECTED group) instead of ``message.chat.id``;
* menu toggle callbacks carrying an explicit group chat id
  (``menu:t:<field>:<chat_id>``) while legacy ``menu:t:<field>`` keeps
  acting on the event chat;
* ``scam.build_scam_verdict`` scoping join-date risk factors to an explicit
  ``risk_chat`` (the SELECTED group) instead of the message's chat.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.db import crud
from bot.handlers import menu, moderation, scam
from bot.utils.targets import Target
from tests.conftest import (
    BOT_ID,
    Cmd,
    make_callback,
    make_chat,
    make_message,
    make_user,
)

PRIVATE_CHAT_ID = 111
GROUP_CHAT_ID = 12345


@pytest.fixture(autouse=True)
def patch_mod(monkeypatch):
    """Bot can restrict; actor (id 1000) is admin; target (500) is not."""
    monkeypatch.setattr(moderation, "bot_can_restrict", AsyncMock(return_value=True))
    monkeypatch.setattr(
        moderation,
        "is_user_admin",
        AsyncMock(side_effect=lambda bot, chat_id, uid: uid == 1000),
    )


def _private_message() -> SimpleNamespace:
    """A DM message (private chat) — the future DM panel's context."""
    return make_message(
        chat=make_chat(chat_id=PRIVATE_CHAT_ID, chat_type="private", title="PM")
    )


# --------------------------------------------------------------------------- #
# moderation._prepare with an explicit chat_id
# --------------------------------------------------------------------------- #
async def test_prepare_explicit_chat_id_used_for_guards(base_data, monkeypatch):
    """Guards must run against chat_id=12345, not the DM chat id."""
    monkeypatch.setattr(
        moderation,
        "resolve_target",
        AsyncMock(return_value=(Target(500, "Victim", "victim"), None, 0)),
    )
    msg = _private_message()
    prep = await moderation._prepare(
        msg,
        Cmd(),
        base_data,
        allow_duration=True,
        protect_target=True,
        need_restrict=True,
        chat_id=GROUP_CHAT_ID,
    )
    assert prep is not None
    assert prep.target.user_id == 500
    # fresh actor re-check + bot rights probe both against the explicit chat
    moderation.bot_can_restrict.assert_awaited_with(msg.bot, GROUP_CHAT_ID, BOT_ID)
    calls = moderation.is_user_admin.await_args_list
    assert any(call.args == (msg.bot, GROUP_CHAT_ID, 1000) for call in calls)
    assert all(call.args[1] == GROUP_CHAT_ID for call in calls)
    # no guard consulted the DM chat id
    assert calls[0].args[1] != PRIVATE_CHAT_ID


async def test_prepare_explicit_chat_id_non_admin(base_data):
    base_data["is_admin"] = False
    msg = _private_message()
    prep = await moderation._prepare(
        msg,
        Cmd(),
        base_data,
        allow_duration=True,
        protect_target=True,
        need_restrict=True,
        chat_id=GROUP_CHAT_ID,
    )
    assert prep is None
    msg.reply.assert_awaited_once_with("error_not_admin")


async def test_prepare_protect_target_admin_in_explicit_chat(base_data, monkeypatch):
    # actor stays admin in the explicit chat; target IS admin there → refused.
    monkeypatch.setattr(
        moderation,
        "is_user_admin",
        AsyncMock(
            side_effect=lambda bot, chat_id, uid: uid == 1000
            or (chat_id == GROUP_CHAT_ID and uid == 500)
        ),
    )
    monkeypatch.setattr(
        moderation,
        "resolve_target",
        AsyncMock(return_value=(Target(500, "Victim", "victim"), None, 0)),
    )
    msg = _private_message()
    prep = await moderation._prepare(
        msg,
        Cmd(),
        base_data,
        allow_duration=True,
        protect_target=True,
        need_restrict=True,
        chat_id=GROUP_CHAT_ID,
    )
    assert prep is None
    msg.reply.assert_awaited_once_with("error_cannot_act_on_admin")


# --------------------------------------------------------------------------- #
# moderation.prepare_action — the DM-panel reusable core
# --------------------------------------------------------------------------- #
async def test_prepare_action_plain_args(base_data, monkeypatch):
    monkeypatch.setattr(
        moderation,
        "resolve_target",
        AsyncMock(return_value=(Target(777, "Someone", "someone"), None, 1)),
    )
    msg = _private_message()
    prep = await moderation.prepare_action(
        msg,
        ["@someone"],
        base_data,
        chat_id=GROUP_CHAT_ID,
        allow_duration=True,
        protect_target=True,
        need_restrict=True,
    )
    assert prep is not None
    assert prep.target.user_id == 777
    assert prep.duration is None
    assert prep.reason is None


async def test_prepare_action_duration_and_reason(base_data, monkeypatch):
    # ModerationService.parse_duration("2h") == 7200 (see services/moderation.py).
    # consumed=0 → the target came from a reply, so BOTH tokens remain for
    # duration/reason parsing.
    monkeypatch.setattr(
        moderation,
        "resolve_target",
        AsyncMock(return_value=(Target(777, "Someone", "someone"), None, 0)),
    )
    msg = _private_message()
    prep = await moderation.prepare_action(
        msg,
        ["2h", "спам"],
        base_data,
        chat_id=GROUP_CHAT_ID,
        allow_duration=True,
        protect_target=True,
        need_restrict=True,
    )
    assert prep is not None
    assert prep.duration == 7200
    assert prep.reason == "спам"


async def test_prepare_action_guards_against_explicit_chat(base_data, monkeypatch):
    # Direct call without resolve_target mocking: no target → error reply,
    # and the failing path never touched the DM chat.
    msg = _private_message()
    prep = await moderation.prepare_action(
        msg,
        [],
        base_data,
        chat_id=GROUP_CHAT_ID,
        allow_duration=True,
        protect_target=True,
        need_restrict=True,
    )
    assert prep is None
    msg.reply.assert_awaited_once_with("error_no_target")


# --------------------------------------------------------------------------- #
# menu toggle callbacks with an explicit chat id
# --------------------------------------------------------------------------- #
def _menu_callback(data_str: str):
    cb = make_callback(data=data_str, from_user=make_user(9, "Adm"))
    cb.message.edit_reply_markup = AsyncMock()
    cb.message.delete = AsyncMock()
    return cb


async def test_menu_toggle_explicit_chat_id(base_data, monkeypatch):
    monkeypatch.setattr(crud, "update_settings", AsyncMock())
    group = SimpleNamespace(id=GROUP_CHAT_ID, type="supergroup", title="Group")
    monkeypatch.setattr(crud, "get_chat", AsyncMock(return_value=group))
    base_data["settings"]["filter_links"] = False
    cb = _menu_callback(f"menu:t:filter_links:{GROUP_CHAT_ID}")
    await menu.on_menu_callback(cb, **base_data)
    args, kwargs = crud.update_settings.await_args
    assert args[1] == GROUP_CHAT_ID
    assert kwargs == {"filter_links": True}
    base_data["redis"].invalidate_settings.assert_awaited_once_with(GROUP_CHAT_ID)


async def test_menu_toggle_legacy_uses_event_chat(base_data, monkeypatch):
    monkeypatch.setattr(crud, "update_settings", AsyncMock())
    base_data["settings"]["filter_links"] = True
    cb = _menu_callback("menu:t:filter_links")
    await menu.on_menu_callback(cb, **base_data)
    args, kwargs = crud.update_settings.await_args
    assert args[1] == base_data["event_chat"].id
    assert kwargs == {"filter_links": False}


async def test_menu_toggle_bad_chat_id_falls_back_to_event_chat(base_data, monkeypatch):
    monkeypatch.setattr(crud, "update_settings", AsyncMock())
    base_data["settings"]["filter_links"] = False
    cb = _menu_callback("menu:t:filter_links:not_a_number")
    await menu.on_menu_callback(cb, **base_data)
    args, _ = crud.update_settings.await_args
    assert args[1] == base_data["event_chat"].id


# --------------------------------------------------------------------------- #
# scam.build_scam_verdict with an explicit risk_chat
# --------------------------------------------------------------------------- #
async def test_build_scam_verdict_uses_risk_chat(base_data, monkeypatch):
    """Join-date factors must be computed against the provided GROUP chat."""
    from bot.utils import join_date

    monkeypatch.setattr(crud, "get_scam_entry", AsyncMock(return_value=None))
    monkeypatch.setattr(
        join_date,
        "get_joined_date",
        AsyncMock(return_value=datetime.now(UTC) - timedelta(days=3)),
    )
    group_chat = SimpleNamespace(id=-100999, type="supergroup", title="Group")
    msg = _private_message()
    body = await scam.build_scam_verdict(
        msg, Target(500, "Victim", "victim"), base_data, risk_chat=group_chat
    )
    # factor lookup went to the GROUP chat id, not the DM chat id
    join_date.get_joined_date.assert_awaited_once_with(-100999, 500)
    # join-date factor present → risk verdict (DM chat alone would be empty)
    assert body == "scam_risk"


async def test_build_scam_verdict_default_uses_message_chat(base_data, monkeypatch):
    """Without risk_chat the message's own chat drives the factors."""
    from bot.utils import join_date

    monkeypatch.setattr(crud, "get_scam_entry", AsyncMock(return_value=None))
    monkeypatch.setattr(
        join_date,
        "get_joined_date",
        AsyncMock(return_value=datetime.now(UTC) - timedelta(days=3)),
    )
    group_msg = make_message(
        chat=make_chat(chat_id=-100999, chat_type="supergroup", title="Group")
    )
    body = await scam.build_scam_verdict(
        group_msg, Target(500, "Victim", "victim"), base_data
    )
    join_date.get_joined_date.assert_awaited_once_with(-100999, 500)
    assert body == "scam_risk"
