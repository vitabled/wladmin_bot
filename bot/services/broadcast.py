"""Broadcast service (Phase 7, task 6): topic-aware raw-text broadcasts.

Sends the same raw text (parse_mode=None — no HTML/emoji processing) to a
list of forum topics of one chat and reports per-thread results. No DB
writes: the caller (JSON API) owns any persistence.
"""

from __future__ import annotations

from typing import Any

from aiogram import Bot


async def send_broadcast(
    bot: Bot, chat_id: int, thread_ids: list[int], text: str
) -> list[dict[str, Any]]:
    """Send ``text`` to each forum topic; one report dict per thread.

    A failed thread does not abort the others — every failure is captured
    as ``{"thread_id": t, "ok": False, "error": str(e)[:200]}``.
    """
    results: list[dict[str, Any]] = []
    for thread_id in thread_ids:
        try:
            await bot.send_message(chat_id, text, message_thread_id=thread_id)
            results.append({"thread_id": thread_id, "ok": True, "error": None})
        except Exception as exc:  # noqa: BLE001 — per-thread failure report
            results.append(
                {"thread_id": thread_id, "ok": False, "error": str(exc)[:200]}
            )
    return results
