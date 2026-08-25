"""Handler tests for /scam and /addtowl (Phase 9 seller reputation)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.constants import SCAM_SOURCE_VERIFIED
from bot.db import crud
from bot.handlers import scam
from bot.i18n.loader import get_i18n
from tests.conftest import Cmd, make_message, make_user


def _ru(key: str, **kw) -> str:
    """Real Russian translation, decorated like the i18n middleware does."""
    from bot.emoji import decorate

    text = get_i18n().get(key, "ru", **kw)
    return decorate(text) or text


@pytest.fixture(autouse=True)
def patch_crud(monkeypatch):
    monkeypatch.setattr(crud, "get_scam_entry", AsyncMock(return_value=None))
    monkeypatch.setattr(crud, "upsert_scam_entry", AsyncMock())
    monkeypatch.setattr(crud, "remove_scam_entry", AsyncMock(return_value=True))
    # joined_date в Bot API/aiogram отсутствует — провайдер честно даёт None,
    # если тест не переопределил.
    from bot.utils import join_date

    monkeypatch.setattr(join_date, "get_joined_date", AsyncMock(return_value=None))


def _scam_entry(source: str, reason: str | None = None):
    return SimpleNamespace(source=source, reason=reason)


# --------------------------------------------------------------------------- #
# /scam — list lookup
# --------------------------------------------------------------------------- #
async def test_scam_no_target(base_data):
    msg = make_message()  # no reply, no args
    await scam.cmd_scam(msg, Cmd(None), **base_data)
    msg.reply.assert_awaited_once()
    assert msg.reply.await_args[0][0].startswith("scam_no_target")


async def test_scam_mention_without_target_hints_both_forms(base_data):
    # "/scam@lotesadminbot" без аргументов и без reply — то же, что /scam без
    # цели: подсказка должна объяснить оба способа (ответ на сообщение ИЛИ
    # /scam @nickname / /scam <telegram-id>).
    base_data["_"] = _ru
    msg = make_message()
    await scam.cmd_scam(msg, Cmd(None), **base_data)
    text = msg.reply.await_args[0][0]
    assert "ответом" in text
    assert "@nickname" in text
    assert "telegram-id" in text


async def test_scam_bot_own_username_is_no_target(base_data, monkeypatch):
    # "/scam @lotesadminbot" (с пробелом — клиент часто подставляет username
    # бота как аргумент): resolve_target резолвит самого бота через get_chat,
    # и это должно трактоваться как отсутствие цели, а не scam_ok.
    bot = make_message().bot
    bot.get_chat = AsyncMock(
        return_value=SimpleNamespace(
            id=bot.id, type="private", full_name="WL Market Admin", username="lotesadminbot"
        )
    )
    monkeypatch.setattr(crud, "get_user_by_username", AsyncMock(return_value=None))
    msg = make_message(from_user=make_user(9, "Checker"), bot=bot)
    await scam.cmd_scam(msg, Cmd("@lotesadminbot"), **base_data)
    crud.get_scam_entry.assert_not_awaited()
    text = msg.reply.await_args[0][0]
    assert "scam_no_target" in text


async def test_scam_numeric_id_lookup(base_data):
    crud.get_scam_entry.return_value = _scam_entry("scam", "обман на 5к")
    base_data["_"] = _ru
    msg = make_message()  # numeric id in args
    await scam.cmd_scam(msg, Cmd("987654321"), **base_data)
    args, _ = crud.get_scam_entry.await_args
    assert args[1] == 987654321
    text = msg.reply.await_args[0][0]
    assert "в списке скама" in text
    assert "обман на 5к" in text
    assert "wem1r0" in text  # footer on every answer


async def test_scam_verified_seller(base_data):
    crud.get_scam_entry.return_value = _scam_entry("verified")
    base_data["_"] = _ru
    msg = make_message()
    await scam.cmd_scam(msg, Cmd("123456"), **base_data)
    text = msg.reply.await_args[0][0]
    assert "проверенный продавец" in text
    assert "wem1r0" in text


async def test_scam_unknown_ok(base_data):
    # No entry, joined long ago → no risk factors.
    msg = make_message(chat=SimpleNamespace(id=-100, type="supergroup", title="G"))
    from bot.utils import join_date

    join_date.get_joined_date.return_value = datetime.now(UTC) - timedelta(days=90)
    base_data["_"] = _ru
    await scam.cmd_scam(msg, Cmd("123456"), **base_data)
    text = msg.reply.await_args[0][0]
    assert "риск скама минимален" in text
    assert "wem1r0" in text


async def test_scam_join_date_unavailable_ok(base_data):
    # Провайдер даты вступления недоступен (None) → риск не выдумываем.
    msg = make_message(chat=SimpleNamespace(id=-100, type="supergroup", title="G"))
    base_data["_"] = _ru
    await scam.cmd_scam(msg, Cmd("123456"), **base_data)
    text = msg.reply.await_args[0][0]
    assert "риск скама минимален" in text


async def test_scam_recent_join_high_risk(base_data):
    msg = make_message(chat=SimpleNamespace(id=-100, type="supergroup", title="G"))
    from bot.utils import join_date

    join_date.get_joined_date.return_value = datetime.now(UTC) - timedelta(days=3)
    base_data["_"] = _ru
    await scam.cmd_scam(msg, Cmd("123456"), **base_data)
    text = msg.reply.await_args[0][0]
    assert "Высокий риск скама" in text
    assert "Присоединился к каналу 2 недели назад." in text
    assert "wem1r0" in text


async def test_scam_risk_includes_account_age_when_available(base_data, monkeypatch):
    # Провайдер возраста аккаунта вернул > 5 месяцев → строка добавляется.
    from bot.utils import account_age, join_date

    join_date.get_joined_date.return_value = datetime.now(UTC) - timedelta(days=3)
    monkeypatch.setattr(account_age, "get_account_age_days", AsyncMock(return_value=400))
    msg = make_message(chat=SimpleNamespace(id=-100, type="supergroup", title="G"))
    base_data["_"] = _ru
    await scam.cmd_scam(msg, Cmd("123456"), **base_data)
    text = msg.reply.await_args[0][0]
    assert "Аккаунт создан больше 5 месяцев назад." in text


async def test_scam_risk_omits_account_age_when_unavailable(base_data, monkeypatch):
    # Источник возраста недоступен (None) → строка про возраст не выводится.
    from bot.utils import account_age, join_date

    join_date.get_joined_date.return_value = datetime.now(UTC) - timedelta(days=3)
    monkeypatch.setattr(account_age, "get_account_age_days", AsyncMock(return_value=None))
    msg = make_message(chat=SimpleNamespace(id=-100, type="supergroup", title="G"))
    base_data["_"] = _ru
    await scam.cmd_scam(msg, Cmd("123456"), **base_data)
    text = msg.reply.await_args[0][0]
    assert "Аккаунт создан" not in text
    assert "Присоединился к каналу 2 недели назад." in text


async def test_scam_reply_target(base_data):
    seller = make_user(424242, "Seller", "seller1")
    reply = make_message(text="продаю аккаунт", from_user=seller)
    msg = make_message(reply_to_message=reply)
    await scam.cmd_scam(msg, Cmd(None), **base_data)
    args, _ = crud.get_scam_entry.await_args
    assert args[1] == 424242
    assert msg.reply.await_args[0][0].startswith("scam_ok")


async def test_scam_reason_escaped(base_data):
    crud.get_scam_entry.return_value = _scam_entry("scam", "злой <script>")
    base_data["_"] = _ru
    msg = make_message()
    await scam.cmd_scam(msg, Cmd("555"), **base_data)
    text = msg.reply.await_args[0][0]
    assert "<script>" not in text
    assert "&lt;script&gt;" in text


# --------------------------------------------------------------------------- #
# /addtowl — admin whitelist
# --------------------------------------------------------------------------- #
async def test_addtowl_non_admin_blocked(base_data):
    base_data["is_admin"] = False
    msg = make_message()
    await scam.cmd_addtowl(msg, Cmd("123456"), **base_data)
    crud.upsert_scam_entry.assert_not_awaited()
    msg.reply.assert_awaited_once_with("error_not_admin")


async def test_addtowl_upserts_verified(base_data):
    msg = make_message()
    await scam.cmd_addtowl(msg, Cmd("123456"), **base_data)
    crud.upsert_scam_entry.assert_awaited_once()
    args, _ = crud.upsert_scam_entry.await_args
    assert args[1] == 123456
    assert args[2] == SCAM_SOURCE_VERIFIED
    text = msg.reply.await_args[0][0]
    assert "addtowl_added" in text


async def test_addtowl_remove(base_data):
    msg = make_message()
    await scam.cmd_addtowl(msg, Cmd("remove 123456"), **base_data)
    crud.remove_scam_entry.assert_awaited_once()
    crud.upsert_scam_entry.assert_not_awaited()
    text = msg.reply.await_args[0][0]
    assert "addtowl_removed" in text


async def test_addtowl_remove_missing(base_data):
    crud.remove_scam_entry.return_value = False
    msg = make_message()
    await scam.cmd_addtowl(msg, Cmd("remove 123456"), **base_data)
    text = msg.reply.await_args[0][0]
    assert "addtowl_not_found" in text


async def test_addtowl_owner_allowed_in_pm(base_data):
    base_data["is_admin"] = True
    base_data["is_owner"] = True
    msg = make_message(chat=SimpleNamespace(id=111, type="private", title="PM"))
    await scam.cmd_addtowl(msg, Cmd("123456"), **base_data)
    crud.upsert_scam_entry.assert_awaited_once()


async def test_addtowl_bot_own_username_blocked(base_data, monkeypatch):
    # "/addtowl @lotesadminbot" — цель = сам бот: не заносим в белый список.
    bot = make_message().bot
    bot.get_chat = AsyncMock(
        return_value=SimpleNamespace(
            id=bot.id, type="private", full_name="WL Market Admin", username="lotesadminbot"
        )
    )
    monkeypatch.setattr(crud, "get_user_by_username", AsyncMock(return_value=None))
    msg = make_message(from_user=make_user(9, "Admin"), bot=bot)
    await scam.cmd_addtowl(msg, Cmd("@lotesadminbot"), **base_data)
    crud.upsert_scam_entry.assert_not_awaited()
    crud.remove_scam_entry.assert_not_awaited()
    assert "error_cannot_act_on_bot" in msg.reply.await_args[0][0]
