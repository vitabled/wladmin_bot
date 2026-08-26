"""JSON API for the admin webapp (Phase 7, tasks 5+6).

Read-only dashboard data + mutation endpoints (toggles, slow mode, scam
whitelist) + a topic-aware broadcast action. Auth mirrors the HTML panel:
only users in ``Settings.allowed_dm_ids`` (owner-only allowlist) may call
any endpoint — 401 without a session, 403 with a non-allowlisted session.

Router functions reach shared services via ``request.app.state``
(settings / session_maker / redis), mirroring ``create_app``.
"""

from __future__ import annotations

from typing import Any

from aiogram import Bot, types
from aiogram.client.default import DefaultBotProperties
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from bot.constants import SCAM_SOURCE_VERIFIED, TOGGLE_FIELDS, TOP_DEFAULT
from bot.db import crud
from bot.handlers.scam import build_scam_body
from bot.i18n.loader import get_i18n
from bot.services.broadcast import send_broadcast

SLOW_MODE_MIN = 60
SLOW_MODE_MAX = 30 * 86400  # 30 days
_SLOW_MODE_DEFAULTS = {
    "enabled": False,
    "regular_seconds": 21600,
    "wl_seconds": 10800,
    "topic_ids": None,
}


def _translate(key: str, **kwargs: Any) -> str:
    """Translate a key to Russian via the shared i18n loader (JSON files)."""
    return get_i18n().get(key, lang="ru", **kwargs)


def _make_bot(token: str) -> Bot:
    """Ad-hoc bot for Bot API calls (raw text: parse_mode=None)."""
    return Bot(token=token, default=DefaultBotProperties(parse_mode=None))


async def _resolve_username(settings: Any, target: str) -> tuple[int, str] | None:
    """Resolve an @username via the Bot API to ``(user_id, display name)``.

    ``get_chat`` accepts a username for both users and channels; returns
    None when the username does not exist or the bot cannot reach it.
    """
    bot = _make_bot(settings.TELEGRAM_BOT_TOKEN)
    try:
        chat = await bot.get_chat(target)
    except Exception:
        return None
    finally:
        await bot.session.close()
    name = chat.title or getattr(chat, "full_name", None) or str(chat.id)
    return chat.id, name


async def require_user(request: Request) -> int:
    """Auth dependency: allowlist members only (401 no session, 403 denied)."""
    user_id = request.session.get("user_id")
    if user_id is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if user_id not in request.app.state.settings.allowed_dm_ids:
        raise HTTPException(status_code=403, detail="Access denied")
    return user_id


router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])

# Public (auth-free) endpoints — callable before login, e.g. the SPA login
# page needs the bot username to render the Telegram Login Widget.
public_router = APIRouter(prefix="/api")


class ToggleIn(BaseModel):
    field: str


class SlowModeIn(BaseModel):
    enabled: bool
    regular_seconds: int
    wl_seconds: int
    topic_ids: list[int] | None = None


class WlIn(BaseModel):
    user_id: int | None = None
    target: str | None = None


class BroadcastIn(BaseModel):
    chat_id: int
    thread_ids: list[int]
    text: str


@router.get("/me")
async def api_me(request: Request, user_id: int = Depends(require_user)) -> dict:
    """Current allowlisted user (for the frontend header)."""
    return {"user_id": user_id, "name": request.session.get("name", "")}


@router.get("/chats")
async def api_chats(request: Request) -> list[dict[str, Any]]:
    """List active chats with message totals and ban counts (by title)."""
    async with request.app.state.session_maker() as session:
        chats = await crud.list_active_chats(session)
        out: list[dict[str, Any]] = []
        for chat in sorted(chats, key=lambda c: c.title or ""):
            total, _users = await crud.chat_activity_totals(session, chat.chat_id)
            banned = await crud.count_bans(session, chat.chat_id)
            out.append(
                {
                    "chat_id": chat.chat_id,
                    "title": chat.title,
                    "total_messages": total,
                    "banned": banned,
                }
            )
    return out


@router.get("/chats/{chat_id}")
async def api_chat_detail(request: Request, chat_id: int) -> dict[str, Any]:
    """Full per-chat snapshot: settings, slow mode, activity, topics."""
    async with request.app.state.session_maker() as session:
        chat = await crud.get_chat(session, chat_id)
        if chat is None:
            raise HTTPException(status_code=404, detail="not_found")
        settings_obj = await crud.get_or_create_settings(session, chat_id)
        await session.commit()
        slow = await crud.get_slow_mode(session, chat_id)
        total, users = await crud.chat_activity_totals(session, chat_id)
        warns = await crud.count_warns_chat(session, chat_id)
        banned = await crud.count_bans(session, chat_id)
        topics = await crud.list_topics(session, chat_id)
    return {
        "chat_id": chat.chat_id,
        "title": chat.title,
        "settings": crud.settings_to_dict(settings_obj),
        "slow_mode": (
            {
                "enabled": slow.enabled,
                "regular_seconds": slow.regular_seconds,
                "wl_seconds": slow.wl_seconds,
                "topic_ids": slow.topic_ids,
            }
            if slow is not None
            else dict(_SLOW_MODE_DEFAULTS)
        ),
        "activity": {"total": total, "users": users},
        "warns": warns,
        "banned": banned,
        "topics": [
            {
                "thread_id": topic.thread_id,
                "message_count": topic.message_count,
                "last_seen": topic.last_seen.isoformat() if topic.last_seen else None,
            }
            for topic in topics
        ],
    }


@router.post("/chats/{chat_id}/toggle")
async def api_chat_toggle(
    request: Request, chat_id: int, body: ToggleIn
) -> dict[str, Any]:
    """Flip a boolean chat setting and invalidate the Redis cache."""
    if body.field not in TOGGLE_FIELDS:
        raise HTTPException(status_code=400, detail=f"Unknown field: {body.field}")
    async with request.app.state.session_maker() as session:
        settings_obj = await crud.get_or_create_settings(session, chat_id)
        new_value = not bool(getattr(settings_obj, body.field))
        await crud.update_settings(session, chat_id, **{body.field: new_value})
        await session.commit()
    await request.app.state.redis.invalidate_settings(chat_id)
    return {"ok": True, "field": body.field, "value": new_value}


@router.post("/chats/{chat_id}/slow-mode")
async def api_chat_slow_mode(
    request: Request, chat_id: int, body: SlowModeIn
) -> dict[str, Any]:
    """Persist slow-mode intervals (clamped to [60s, 30 days]).

    ``topic_ids`` optionally scopes the rule to forum topics: ``None`` leaves
    the stored scope unchanged, ``[]`` = whole chat, non-empty = those
    threads only.
    """
    if body.regular_seconds < 0 or body.wl_seconds < 0:
        raise HTTPException(status_code=422, detail="seconds must be >= 0")
    regular = max(SLOW_MODE_MIN, min(body.regular_seconds, SLOW_MODE_MAX))
    wl = max(SLOW_MODE_MIN, min(body.wl_seconds, SLOW_MODE_MAX))
    async with request.app.state.session_maker() as session:
        saved = await crud.set_slow_mode(
            session,
            chat_id,
            enabled=body.enabled,
            regular_seconds=regular,
            wl_seconds=wl,
            topic_ids=body.topic_ids,
        )
        await session.commit()
    return {
        "ok": True,
        "enabled": body.enabled,
        "regular_seconds": regular,
        "wl_seconds": wl,
        "topic_ids": saved.topic_ids,
    }


@router.get("/chats/{chat_id}/stats")
async def api_chat_stats(request: Request, chat_id: int) -> dict[str, Any]:
    """Activity totals, moderation counts and the top-active users."""
    async with request.app.state.session_maker() as session:
        total, users = await crud.chat_activity_totals(session, chat_id)
        banned = await crud.count_bans(session, chat_id)
        warns = await crud.count_warns_chat(session, chat_id)
        top = await crud.top_active(session, chat_id, TOP_DEFAULT)
        names = await crud.get_users_by_ids(session, [uid for uid, _ in top])
    return {
        "total": total,
        "users": users,
        "banned": banned,
        "warns": warns,
        "top": [
            {"user_id": uid, "name": names.get(uid, str(uid)), "count": count}
            for uid, count in top
        ],
    }


@router.get("/rating")
async def api_rating(
    request: Request,
    target: str = "",
    chat_id: int | None = None,
):
    """Scam verdict for a numeric id or @username (body without footer).

    ``chat_id`` scopes the join-date risk factors to a group; without it the
    verdict is list-based only.
    """
    settings = request.app.state.settings
    target = (target or "").strip()
    if not target:
        raise HTTPException(status_code=422, detail="target is required")
    async with request.app.state.session_maker() as session:
        if target.isdigit():
            target_id = int(target)
            names = await crud.get_users_by_ids(session, [target_id])
            target_name = names.get(target_id) or str(target_id)
        elif target.startswith("@"):
            resolved = await _resolve_username(settings, target)
            if resolved is None:
                return JSONResponse(status_code=404, content={"error": "not_found"})
            target_id, target_name = resolved
        else:
            raise HTTPException(status_code=422, detail="invalid target")

        if chat_id is not None:
            chat = await crud.get_chat(session, chat_id)
            if chat is None:
                return JSONResponse(status_code=404, content={"error": "not_found"})
            risk_chat = types.Chat(id=chat_id, type="supergroup", title=chat.title)
            bot = _make_bot(settings.TELEGRAM_BOT_TOKEN)
            try:
                body = await build_scam_body(
                    session,
                    _translate,
                    target_id,
                    target_name,
                    chat=risk_chat,
                    bot=bot,
                )
            finally:
                await bot.session.close()
        else:
            body = await build_scam_body(session, _translate, target_id, target_name)
    return {"body": body, "target_id": target_id, "target_name": target_name}


@router.get("/rating/list")
async def api_rating_list(request: Request) -> list[dict[str, Any]]:
    """All scam-list entries (both flagged and verified sellers)."""
    async with request.app.state.session_maker() as session:
        entries = await crud.list_scam_entries(session)
        names = await crud.get_users_by_ids(session, [e.user_id for e in entries])
    return [
        {
            "user_id": entry.user_id,
            "name": names.get(entry.user_id, str(entry.user_id)),
            "source": entry.source,
            "reason": entry.reason,
        }
        for entry in entries
    ]


@router.post("/rating/wl")
async def api_rating_wl_add(request: Request, body: WlIn):
    """Whitelist a seller as verified (numeric id or @username)."""
    settings = request.app.state.settings
    user_id = body.user_id
    if user_id is None:
        if not body.target:
            raise HTTPException(status_code=422, detail="user_id or target required")
        resolved = await _resolve_username(settings, body.target)
        if resolved is None:
            return JSONResponse(status_code=404, content={"error": "not_found"})
        user_id, _name = resolved
    async with request.app.state.session_maker() as session:
        await crud.upsert_scam_entry(session, user_id, SCAM_SOURCE_VERIFIED, None)
        await session.commit()
    return {"ok": True}


@router.delete("/rating/wl")
async def api_rating_wl_remove(request: Request, body: WlIn):
    """Remove a user from the scam list entirely."""
    if body.user_id is None:
        raise HTTPException(status_code=422, detail="user_id required")
    async with request.app.state.session_maker() as session:
        removed = await crud.remove_scam_entry(session, body.user_id)
        await session.commit()
    return {"ok": removed}


@router.post("/broadcast")
async def api_broadcast(request: Request, body: BroadcastIn) -> dict[str, Any]:
    """Send raw text to forum topics of a chat (one result per thread)."""
    if not body.text.strip():
        raise HTTPException(status_code=422, detail="text must not be empty")
    settings = request.app.state.settings
    bot = _make_bot(settings.TELEGRAM_BOT_TOKEN)
    try:
        results = await send_broadcast(bot, body.chat_id, body.thread_ids, body.text)
    finally:
        await bot.session.close()
    return {"results": results}


@public_router.get("/login-config")
async def api_login_config(request: Request) -> dict[str, str]:
    """Public login-page config: bot username for the Telegram Login Widget.

    No auth on purpose — the SPA login screen must be able to fetch it before
    the visitor has a session.
    """
    username = getattr(request.app.state.settings, "WEB_BOT_USERNAME", "")
    return {"bot_username": username}
