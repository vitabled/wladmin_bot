"""Unit tests for the topic-aware broadcast service (tasks 5+6)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from bot.services.broadcast import send_broadcast


def _bot_with_results(*results):
    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=list(results))
    return bot


async def test_send_broadcast_mixed_report():
    bot = _bot_with_results(None, RuntimeError("boom"))
    results = await send_broadcast(bot, -100, [1, 2], "hello")
    assert results == [
        {"thread_id": 1, "ok": True, "error": None},
        {"thread_id": 2, "ok": False, "error": "boom"},
    ]
    assert bot.send_message.await_count == 2
    first = bot.send_message.await_args_list[0]
    assert first.args == (-100, "hello")
    assert first.kwargs == {"message_thread_id": 1}


async def test_send_broadcast_passes_no_parse_mode():
    """parse_mode stays None (raw text) — no parse_mode kwarg is sent."""
    bot = _bot_with_results(None)
    await send_broadcast(bot, -100, [7], "hi")
    call = bot.send_message.await_args_list[0]
    assert "parse_mode" not in call.kwargs
    assert call.kwargs == {"message_thread_id": 7}


async def test_send_broadcast_continues_after_failure():
    bot = _bot_with_results(RuntimeError("first"), None, RuntimeError("last"))
    results = await send_broadcast(bot, -100, [1, 2, 3], "x")
    assert [r["ok"] for r in results] == [False, True, False]
    assert bot.send_message.await_count == 3


async def test_send_broadcast_error_truncated_to_200():
    bot = _bot_with_results(RuntimeError("e" * 500))
    results = await send_broadcast(bot, -100, [1], "x")
    assert results[0]["ok"] is False
    assert len(results[0]["error"]) == 200


async def test_send_broadcast_empty_threads():
    bot = _bot_with_results()
    results = await send_broadcast(bot, -100, [], "x")
    assert results == []
    bot.send_message.assert_not_awaited()
