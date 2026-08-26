"""Unit tests for the shared ``build_scam_body`` verdict core (tasks 5+6).

The core is used by ``/scam``, the DM panel AND the JSON API; it must return
the same verdicts as the old inline logic (scam list → verified → risk) and
must NOT collect risk factors when no chat/bot is provided (API path).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.db import crud
from bot.handlers import scam
from bot.i18n.loader import get_i18n
from tests.conftest import make_bot


def _ru(key: str, **kw) -> str:
    """Real Russian translation, decorated like the i18n middleware does."""
    from bot.emoji import decorate

    text = get_i18n().get(key, "ru", **kw)
    return decorate(text) or text


@pytest.fixture(autouse=True)
def patch_crud(monkeypatch):
    monkeypatch.setattr(crud, "get_scam_entry", AsyncMock(return_value=None))
    from bot.utils import join_date

    monkeypatch.setattr(join_date, "get_joined_date", AsyncMock(return_value=None))


def _group_chat():
    return SimpleNamespace(id=-100, type="supergroup", title="G")


# --------------------------------------------------------------------------- #
# List lookups (no risk factors needed)
# --------------------------------------------------------------------------- #
async def test_build_scam_body_scam_found_escaped(session):
    crud.get_scam_entry.return_value = SimpleNamespace(
        source="scam", reason="злой <script>"
    )
    body = await scam.build_scam_body(session, _ru, 424242, "Seller")
    assert "в списке скама" in body
    assert "<script>" not in body
    assert "&lt;script&gt;" in body


async def test_build_scam_body_scam_found_no_reason(session):
    crud.get_scam_entry.return_value = SimpleNamespace(source="scam", reason=None)
    body = await scam.build_scam_body(session, _ru, 424242, "Seller")
    assert "в списке скама" in body
    assert "без причины" in body


async def test_build_scam_body_verified(session):
    crud.get_scam_entry.return_value = SimpleNamespace(source="verified", reason=None)
    body = await scam.build_scam_body(session, _ru, 424242, "Seller")
    assert "проверенный продавец" in body


async def test_build_scam_body_ok_without_chat_or_bot(session):
    """API path without chat: no risk factors are collected at all."""
    body = await scam.build_scam_body(session, _ru, 424242, "Seller")
    assert "риск скама минимален" in body
    from bot.utils import join_date

    join_date.get_joined_date.assert_not_awaited()


# --------------------------------------------------------------------------- #
# Risk factors (chat + bot provided)
# --------------------------------------------------------------------------- #
async def test_build_scam_body_recent_join_high_risk(session):
    from bot.utils import join_date

    join_date.get_joined_date.return_value = datetime.now(UTC) - timedelta(days=3)
    body = await scam.build_scam_body(
        session, _ru, 424242, "Seller", chat=_group_chat(), bot=make_bot()
    )
    assert "Высокий риск скама" in body
    assert "Присоединился к каналу меньше недели назад." in body


async def test_build_scam_body_old_join_no_risk(session):
    from bot.utils import join_date

    join_date.get_joined_date.return_value = datetime.now(UTC) - timedelta(days=90)
    body = await scam.build_scam_body(
        session, _ru, 424242, "Seller", chat=_group_chat(), bot=make_bot()
    )
    assert "риск скама минимален" in body
    assert "Высокий риск" not in body


async def test_build_scam_body_private_chat_no_factors(session):
    """A private chat is not a group → no join-date factors → scam_ok."""
    chat = SimpleNamespace(id=111, type="private", title="PM")
    body = await scam.build_scam_body(session, _ru, 424242, "Seller", chat=chat, bot=make_bot())
    assert "риск скама минимален" in body


async def test_build_scam_body_bot_without_chat_no_factors(session):
    """bot alone (no chat) must not trigger join-date lookups."""
    body = await scam.build_scam_body(
        session, _ru, 424242, "Seller", bot=make_bot()
    )
    assert "риск скама минимален" in body
    from bot.utils import join_date

    join_date.get_joined_date.assert_not_awaited()


async def test_build_scam_body_includes_mention(session):
    crud.get_scam_entry.return_value = SimpleNamespace(source="verified", reason=None)
    body = await scam.build_scam_body(session, _ru, 424242, "Seller")
    assert 'href="tg://user?id=424242"' in body
    assert "Seller" in body
