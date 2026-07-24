"""Handler tests for federation commands (Phase 8)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.db import crud
from bot.handlers import federation
from bot.utils.targets import Target
from tests.conftest import Cmd, make_message, make_user


@pytest.fixture(autouse=True)
def patch_common(monkeypatch):
    monkeypatch.setattr(crud, "add_mod_log", AsyncMock())
    monkeypatch.setattr(federation, "safe_ban_member", AsyncMock(return_value=True))
    monkeypatch.setattr(federation, "safe_unban_member", AsyncMock(return_value=True))


def _fed(**kw):
    base = {"id": 5, "name": "F", "owner_id": 1}
    base.update(kw)
    return SimpleNamespace(**base)


# --------------------------------------------------------------------------- #
# /fcreate
# --------------------------------------------------------------------------- #
async def test_fcreate_ok(base_data, monkeypatch):
    monkeypatch.setattr(
        crud, "create_federation", AsyncMock(return_value=_fed(id=5, name="MyFed"))
    )
    msg = make_message(from_user=make_user(9, "A"))
    await federation.cmd_fcreate(msg, Cmd("MyFed"), **base_data)
    crud.create_federation.assert_awaited_once()


async def test_fcreate_invalid_name(base_data, monkeypatch):
    monkeypatch.setattr(crud, "create_federation", AsyncMock())
    msg = make_message()
    await federation.cmd_fcreate(msg, Cmd("ab"), **base_data)  # too short
    crud.create_federation.assert_not_awaited()


async def test_fcreate_name_taken(base_data, monkeypatch):
    monkeypatch.setattr(crud, "create_federation", AsyncMock(return_value=None))
    msg = make_message()
    await federation.cmd_fcreate(msg, Cmd("MyFed"), **base_data)
    msg.reply.assert_awaited()


# --------------------------------------------------------------------------- #
# /fjoin, /fleave, /finfo
# --------------------------------------------------------------------------- #
async def test_fjoin_owner_ok(base_data, monkeypatch):
    monkeypatch.setattr(
        crud, "get_federation", AsyncMock(return_value=_fed(owner_id=9))
    )
    monkeypatch.setattr(crud, "add_chat_to_federation", AsyncMock(return_value=True))
    msg = make_message(from_user=make_user(9, "A"))
    await federation.cmd_fjoin(msg, Cmd("5"), **base_data)
    crud.add_chat_to_federation.assert_awaited_once()


async def test_fjoin_forbidden_for_non_owner(base_data, monkeypatch):
    monkeypatch.setattr(
        crud, "get_federation", AsyncMock(return_value=_fed(owner_id=1))
    )
    monkeypatch.setattr(crud, "add_chat_to_federation", AsyncMock())
    base_data["is_owner"] = False
    msg = make_message(from_user=make_user(9, "A"))
    await federation.cmd_fjoin(msg, Cmd("5"), **base_data)
    crud.add_chat_to_federation.assert_not_awaited()


async def test_fjoin_bad_id(base_data, monkeypatch):
    monkeypatch.setattr(crud, "get_federation", AsyncMock())
    msg = make_message()
    await federation.cmd_fjoin(msg, Cmd("abc"), **base_data)
    crud.get_federation.assert_not_awaited()


async def test_fleave(base_data, monkeypatch):
    monkeypatch.setattr(
        crud, "remove_chat_from_federation", AsyncMock(return_value=True)
    )
    msg = make_message()
    await federation.cmd_fleave(msg, Cmd(None), **base_data)
    crud.remove_chat_from_federation.assert_awaited_once()


async def test_finfo(base_data, monkeypatch):
    monkeypatch.setattr(crud, "get_chat_federation", AsyncMock(return_value=_fed()))
    monkeypatch.setattr(crud, "count_federation_chats", AsyncMock(return_value=3))
    monkeypatch.setattr(crud, "count_fedbans", AsyncMock(return_value=2))
    msg = make_message()
    await federation.cmd_finfo(msg, Cmd(None), **base_data)
    msg.reply.assert_awaited_once()


async def test_finfo_not_in_federation(base_data, monkeypatch):
    monkeypatch.setattr(crud, "get_chat_federation", AsyncMock(return_value=None))
    msg = make_message()
    await federation.cmd_finfo(msg, Cmd(None), **base_data)
    msg.reply.assert_awaited_once()


# --------------------------------------------------------------------------- #
# /fban, /funban
# --------------------------------------------------------------------------- #
async def test_fban_applies_across_chats(base_data, monkeypatch):
    monkeypatch.setattr(
        crud, "get_chat_federation", AsyncMock(return_value=_fed(owner_id=1))
    )
    monkeypatch.setattr(
        federation,
        "resolve_target",
        AsyncMock(return_value=(Target(500, "Bad", "bad"), None, 1)),
    )
    monkeypatch.setattr(crud, "add_fedban", AsyncMock(return_value=True))
    monkeypatch.setattr(
        crud, "list_federation_chats", AsyncMock(return_value=[-100, -200])
    )
    msg = make_message(from_user=make_user(9, "A"))
    await federation.cmd_fban(msg, Cmd("@bad spam"), **base_data)
    crud.add_fedban.assert_awaited_once()
    assert federation.safe_ban_member.await_count == 2


async def test_fban_protects_federation_owner(base_data, monkeypatch):
    monkeypatch.setattr(
        crud, "get_chat_federation", AsyncMock(return_value=_fed(owner_id=500))
    )
    monkeypatch.setattr(
        federation,
        "resolve_target",
        AsyncMock(return_value=(Target(500, "Owner", None), None, 1)),
    )
    monkeypatch.setattr(crud, "add_fedban", AsyncMock())
    msg = make_message(from_user=make_user(9, "A"))
    await federation.cmd_fban(msg, Cmd("@owner"), **base_data)
    crud.add_fedban.assert_not_awaited()


async def test_fban_requires_federation(base_data, monkeypatch):
    monkeypatch.setattr(crud, "get_chat_federation", AsyncMock(return_value=None))
    monkeypatch.setattr(federation, "resolve_target", AsyncMock())
    msg = make_message()
    await federation.cmd_fban(msg, Cmd("@x"), **base_data)
    federation.resolve_target.assert_not_awaited()


async def test_funban_removes_across_chats(base_data, monkeypatch):
    monkeypatch.setattr(
        crud, "get_chat_federation", AsyncMock(return_value=_fed(owner_id=1))
    )
    monkeypatch.setattr(
        federation,
        "resolve_target",
        AsyncMock(return_value=(Target(500, "Bad", None), None, 1)),
    )
    monkeypatch.setattr(crud, "remove_fedban", AsyncMock(return_value=True))
    monkeypatch.setattr(crud, "list_federation_chats", AsyncMock(return_value=[-100]))
    msg = make_message(from_user=make_user(9, "A"))
    await federation.cmd_funban(msg, Cmd("@bad"), **base_data)
    crud.remove_fedban.assert_awaited_once()
    federation.safe_unban_member.assert_awaited_once()
