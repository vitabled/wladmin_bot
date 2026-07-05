"""Handler tests for moderation commands (mocked aiogram)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.handlers import actions, moderation
from tests.conftest import (
    BOT_ID,
    OWNER_ID,
    Cmd,
    make_message,
    make_user,
)


@pytest.fixture(autouse=True)
def patch_mod(monkeypatch):
    """Default: bot can restrict, actor (id 1000) is admin, target (500) is not.

    is_user_admin is keyed by user id so the actor fresh-recheck passes while
    the target admin-protection check sees a non-admin.
    """
    monkeypatch.setattr(moderation, "bot_can_restrict", AsyncMock(return_value=True))
    monkeypatch.setattr(
        moderation,
        "is_user_admin",
        AsyncMock(side_effect=lambda bot, chat_id, uid: uid == 1000),
    )
    for name in ("do_ban", "do_unban", "do_kick", "do_mute", "do_unmute"):
        monkeypatch.setattr(actions, name, AsyncMock(return_value=True))


def _reply_to(user):
    return SimpleNamespace(sender_chat=None, from_user=user)


async def test_ban_denied_for_non_admin(base_data):
    base_data["is_admin"] = False
    msg = make_message(reply_to_message=_reply_to(make_user(500, "V")))
    await moderation.cmd_ban(msg, Cmd(), **base_data)
    msg.reply.assert_awaited_once_with("error_not_admin")


async def test_ban_no_target(base_data):
    msg = make_message()
    await moderation.cmd_ban(msg, Cmd(), **base_data)
    msg.reply.assert_awaited_once_with("error_no_target")


async def test_ban_bot_not_allowed_to_restrict(base_data, monkeypatch):
    monkeypatch.setattr(moderation, "bot_can_restrict", AsyncMock(return_value=False))
    msg = make_message(reply_to_message=_reply_to(make_user(500, "V")))
    await moderation.cmd_ban(msg, Cmd(), **base_data)
    msg.reply.assert_awaited_once_with("error_bot_cant_restrict")


async def test_ban_cannot_target_self(base_data):
    actor = make_user(1000, "Actor", "actor")
    msg = make_message(from_user=actor, reply_to_message=_reply_to(actor))
    await moderation.cmd_ban(msg, Cmd(), **base_data)
    msg.reply.assert_awaited_once_with("error_cannot_act_on_self")


async def test_ban_cannot_target_bot(base_data):
    msg = make_message(
        reply_to_message=_reply_to(make_user(BOT_ID, "Bot", is_bot=True))
    )
    await moderation.cmd_ban(msg, Cmd(), **base_data)
    msg.reply.assert_awaited_once_with("error_cannot_act_on_bot")


async def test_ban_cannot_target_owner(base_data):
    msg = make_message(reply_to_message=_reply_to(make_user(OWNER_ID, "Owner")))
    await moderation.cmd_ban(msg, Cmd(), **base_data)
    msg.reply.assert_awaited_once_with("error_cannot_act_on_owner")


async def test_ban_cannot_target_admin(base_data, monkeypatch):
    monkeypatch.setattr(moderation, "is_user_admin", AsyncMock(return_value=True))
    msg = make_message(reply_to_message=_reply_to(make_user(500, "AdminV")))
    await moderation.cmd_ban(msg, Cmd(), **base_data)
    msg.reply.assert_awaited_once_with("error_cannot_act_on_admin")


async def test_ban_happy_path(base_data):
    msg = make_message(reply_to_message=_reply_to(make_user(500, "V")))
    await moderation.cmd_ban(msg, Cmd(), **base_data)
    actions.do_ban.assert_awaited_once()
    args, _ = msg.answer.call_args
    assert args[0] == "mod_ban"


async def test_ban_temporary_with_reason(base_data):
    msg = make_message(reply_to_message=_reply_to(make_user(500, "V")))
    await moderation.cmd_ban(msg, Cmd(args="30m being rude"), **base_data)
    # duration parsed -> temp ban message; reason passed through to do_ban
    args, _ = msg.answer.call_args
    assert args[0] == "mod_ban_temp"
    _, call_kwargs = actions.do_ban.call_args
    ban_args = actions.do_ban.call_args.args
    assert ban_args[5] == 1800  # 30m in seconds
    assert ban_args[6] == "being rude"


async def test_unwarn_none_active(base_data, monkeypatch):
    from bot.db import crud

    monkeypatch.setattr(crud, "deactivate_last_warn", AsyncMock(return_value=False))
    msg = make_message(reply_to_message=_reply_to(make_user(500, "V")))
    await moderation.cmd_unwarn(msg, Cmd(), **base_data)
    args, _ = msg.answer.call_args
    assert args[0] == "mod_unwarn_none"


async def test_unwarn_success(base_data, monkeypatch):
    from bot.db import crud

    monkeypatch.setattr(crud, "deactivate_last_warn", AsyncMock(return_value=True))
    monkeypatch.setattr(crud, "count_active_warns", AsyncMock(return_value=1))
    monkeypatch.setattr(crud, "add_mod_log", AsyncMock())
    msg = make_message(reply_to_message=_reply_to(make_user(500, "V")))
    await moderation.cmd_unwarn(msg, Cmd(), **base_data)
    args, _ = msg.answer.call_args
    assert args[0] == "mod_unwarn"


async def test_warns_empty(base_data, monkeypatch):
    from bot.db import crud

    monkeypatch.setattr(crud, "list_active_warns", AsyncMock(return_value=[]))
    msg = make_message(reply_to_message=_reply_to(make_user(500, "V")))
    await moderation.cmd_warns(msg, Cmd(), **base_data)
    args, _ = msg.answer.call_args
    assert args[0] == "mod_warns_none"


async def test_ban_actor_demoted_fresh_recheck(base_data, monkeypatch):
    # Cached is_admin is stale-True, but a fresh re-check says the actor is no
    # longer an admin -> command is refused.
    monkeypatch.setattr(moderation, "is_user_admin", AsyncMock(return_value=False))
    msg = make_message(reply_to_message=_reply_to(make_user(500, "V")))
    await moderation.cmd_ban(msg, Cmd(), **base_data)
    msg.reply.assert_awaited_once_with("error_not_admin")
    actions.do_ban.assert_not_awaited()


async def test_ban_reports_failure_when_telegram_rejects(base_data, monkeypatch):
    monkeypatch.setattr(actions, "do_ban", AsyncMock(return_value=False))
    msg = make_message(reply_to_message=_reply_to(make_user(500, "V")))
    await moderation.cmd_ban(msg, Cmd(), **base_data)
    msg.reply.assert_awaited_once_with("error_bot_not_admin")
    # no success confirmation was sent
    for call in msg.answer.await_args_list:
        assert call.args[0] != "mod_ban"
