"""Unit tests for bot/utils/join_date.py (MTProto joined-date provider)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.utils import join_date

_D = datetime(2026, 8, 25, 9, 28, 28, tzinfo=UTC)


async def test_returns_none_without_creds(monkeypatch):
    monkeypatch.setattr(join_date, "_env", lambda name: None)
    join_date._CACHE.clear()
    assert await join_date.get_joined_date(-100, 1) is None


async def test_get_joined_date_success_and_cache(monkeypatch):
    client = AsyncMock()
    client.return_value = SimpleNamespace(participant=SimpleNamespace(date=_D))
    monkeypatch.setattr(join_date, "_get_client", AsyncMock(return_value=client))
    join_date._CACHE.clear()

    got = await join_date.get_joined_date(-100, 1782827633)
    assert got == _D

    # Повторный вызов идёт из кэша — MTProto не дёргается.
    await join_date.get_joined_date(-100, 1782827633)
    assert client.await_count == 1


async def test_returns_none_on_error(monkeypatch):
    client = AsyncMock(side_effect=Exception("boom"))
    monkeypatch.setattr(join_date, "_get_client", AsyncMock(return_value=client))
    join_date._CACHE.clear()
    assert await join_date.get_joined_date(-100, 1) is None


async def test_none_is_not_cached_forever(monkeypatch):
    client = AsyncMock(side_effect=Exception("boom"))
    monkeypatch.setattr(join_date, "_get_client", AsyncMock(return_value=client))
    join_date._CACHE.clear()

    assert await join_date.get_joined_date(-100, 1) is None
    # Протухший None-кэш → повторный запрос к MTProto (а не вечный None).
    join_date._CACHE[(-100, 1)] = (
        None,
        asyncio_loop_time() - join_date._NONE_TTL - 1,
    )
    await join_date.get_joined_date(-100, 1)
    assert client.await_count == 2


def asyncio_loop_time() -> float:
    import asyncio

    return asyncio.get_event_loop().time()
