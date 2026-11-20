"""Handler tests for /start, /help, /info (common router)."""

from __future__ import annotations

from bot.handlers import common
from tests.conftest import make_message


def _data(translator):
    return {"_": translator, "_raw": translator}


async def test_start_answers():
    msg = make_message()
    await common.cmd_start(msg, **_data(lambda key, **kw: key))
    msg.answer.assert_awaited_once_with("cmd_start")


async def test_start_private_staff_shows_admin_menu():
    msg = make_message()
    msg.chat.type = "private"
    await common.cmd_start(
        msg,
        staff_role="moderator",
        **_data(lambda key, **kw: key),
    )
    assert msg.answer.await_args.args[0] == "dm_menu_welcome"


async def test_start_private_nonstaff_shows_shop():
    msg = make_message()
    msg.chat.type = "private"
    await common.cmd_start(
        msg,
        staff_role=None,
        **_data(lambda key, **kw: key),
    )
    assert msg.answer.await_args.args[0] == "shop_title"


async def test_help_group_variant():
    msg = make_message()
    msg.chat.type = "supergroup"
    await common.cmd_help(msg, **_data(lambda key, **kw: key))
    msg.answer.assert_awaited_once_with("cmd_help_group")


async def test_help_private_staff_variant():
    msg = make_message()
    msg.chat.type = "private"
    await common.cmd_help(
        msg,
        staff_role="moderator",
        **_data(lambda key, **kw: key),
    )
    msg.answer.assert_awaited_once_with("cmd_help_private")


async def test_help_private_nonstaff_variant():
    msg = make_message()
    msg.chat.type = "private"
    await common.cmd_help(
        msg,
        staff_role=None,
        **_data(lambda key, **kw: key),
    )
    assert msg.answer.await_args.args[0] == "shop_help"


async def test_info_available_for_everyone():
    # /info — для всех, без админ-прав: handler не читает is_admin.
    msg = make_message()
    await common.cmd_info(msg, **_data(lambda key, **kw: key))
    msg.answer.assert_awaited_once_with("cmd_info")
    msg.answer.assert_awaited_once()  # no extra calls / no reply_markup
