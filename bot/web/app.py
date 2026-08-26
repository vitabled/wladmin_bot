"""FastAPI dashboard (Phase 7): Telegram-login auth + per-chat settings.

Отдельное приложение, разделяющее БД с ботом. Доступ только для владельцев
из ``ALLOWED_DM_IDS`` (см. ``Settings.allowed_dm_ids``): Telegram Login
принимается, только если id пользователя есть в списке, иначе — 403. Все
допущенные id управляют всеми чатами. Прежняя модель с Redis-кэшем админов
(``admin:{chat}:{user}`` = "1") убрана — дашборд owner-only.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from bot.constants import TOGGLE_FIELDS, TOGGLES
from bot.db import crud
from bot.web.auth import verify_telegram_login, verify_telegram_webapp

# Production SPA build (React + Vite). When it exists, "/" serves the SPA and
# /assets is mounted; otherwise the legacy server-rendered login page below is
# the fallback (dev without a built frontend). Monkeypatched in tests.
DIST = Path(__file__).resolve().parent.parent.parent / "web" / "dist"

# Boolean settings the dashboard can toggle (label shown in the UI).
# Canonical list lives in bot/constants.py (shared with the JSON API).
_TOGGLES: list[tuple[str, str]] = list(TOGGLES)
_TOGGLE_FIELDS = TOGGLE_FIELDS


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<style>body{font-family:system-ui,sans-serif;max-width:760px;margin:2rem auto;"
        "padding:0 1rem;line-height:1.5}a{color:#2481cc}"
        ".row{display:flex;justify-content:space-between;align-items:center;"
        "border-bottom:1px solid #eee;padding:.5rem 0}"
        "button{cursor:pointer;padding:.3rem .8rem;border-radius:6px;border:1px solid #ccc}"
        ".on{background:#e7f7e7}.off{background:#f7e7e7}</style></head>"
        f"<body>{body}</body></html>"
    )


def _uid(request: Request) -> int | None:
    return request.session.get("user_id")


def create_app(settings: Any, session_maker: Any, redis: Any) -> FastAPI:
    """Build the dashboard app around shared settings / DB / Redis."""
    app = FastAPI(title="Bot Dashboard")
    secret = getattr(settings, "WEB_SESSION_SECRET", "") or settings.WEBHOOK_SECRET
    app.add_middleware(SessionMiddleware, secret_key=secret, https_only=False)
    app.state.settings = settings
    app.state.session_maker = session_maker
    app.state.redis = redis

    # JSON API for the webapp frontend (auth: same owner allowlist) +
    # public pre-login endpoints (login config for the SPA login screen).
    from bot.web.api import public_router, router

    app.include_router(router)
    app.include_router(public_router)

    async def _can_manage(user_id: int, chat_id: int) -> bool:
        # Owner-only panel: allowlist members may manage every chat.
        return user_id in settings.allowed_dm_ids

    async def _managed_chats(session: Any, user_id: int) -> list[Any]:
        if user_id not in settings.allowed_dm_ids:
            return []
        return await crud.list_active_chats(session)

    if DIST.exists():
        # Production SPA build present → serve the React app. Auth state is
        # decided client-side via GET /api/me (401 → login screen), so "/"
        # always returns index.html, even with a session.
        app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

        @app.get("/", response_class=FileResponse)
        async def index_spa() -> FileResponse:
            return FileResponse(DIST / "index.html")
    else:
        # Dev fallback (no built frontend): legacy server-rendered login page.
        @app.get("/", response_class=HTMLResponse)
        async def index(request: Request) -> HTMLResponse:
            if _uid(request) is not None:
                return RedirectResponse("/chats", status_code=303)
            username = getattr(settings, "WEB_BOT_USERNAME", "")
            if username:
                widget = (
                    "<script async src='https://telegram.org/js/telegram-widget.js?22' "
                    f"data-telegram-login='{html.escape(username)}' data-size='large' "
                    "data-auth-url='/auth/telegram'></script>"
                )
            else:
                widget = (
                    "<p><em>Set WEB_BOT_USERNAME to enable the Telegram login "
                    "button.</em></p>"
                )
            if getattr(settings, "WEB_DEV_LOGIN", False):
                widget += (
                    "<p style='margin-top:1rem'><a href='/dev-login'>"
                    "🔓 Dev login (local only)</a></p>"
                )
            return HTMLResponse(
                _page("Login", f"<h1>Bot Dashboard</h1><p>Sign in:</p>{widget}")
            )

    if getattr(settings, "WEB_DEV_LOGIN", False):

        @app.get("/dev-login")
        async def dev_login(request: Request) -> Any:
            """LOCAL DEV ONLY: sign in as OWNER_ID without Telegram."""
            request.session["user_id"] = settings.OWNER_ID
            request.session["name"] = "dev"
            return RedirectResponse("/", status_code=303)

    @app.get("/auth/telegram")
    async def auth(request: Request) -> Any:
        # The widget posts the signed fields to this URL (GET for older
        # versions / direct links); accept both.
        data = dict(request.query_params)
        if not data:
            data = {k: str(v) for k, v in (await request.form()).items()}
        if not verify_telegram_login(data, settings.TELEGRAM_BOT_TOKEN):
            return HTMLResponse(
                _page("Auth failed", "<h1>Authentication failed</h1>"),
                status_code=401,
            )
        if int(data["id"]) not in settings.allowed_dm_ids:
            # Owner-only panel: valid Telegram login, but the user is not in
            # the allowlist — refuse WITHOUT creating a session.
            return HTMLResponse(
                _page(
                    "Access denied",
                    "<h1>403 — Доступ только для владельцев</h1>"
                    "<p>Access denied. Only the owners may use this panel.</p>",
                ),
                status_code=403,
            )
        request.session["user_id"] = int(data["id"])
        request.session["name"] = data.get("first_name", "")
        # Back to "/" so the SPA boots and sees the new session via /api/me.
        return RedirectResponse("/", status_code=303)

    @app.post("/auth/webapp")
    async def auth_webapp(request: Request) -> Any:
        """Telegram Mini App login: verify initData and create a session.

        The SPA (opened via the bot's WebApp button) POSTs the raw ``initData``
        from Telegram; no login form needed. Returns JSON so the frontend can
        reload and boot with the session.
        """
        form = await request.form()
        init_data = str(form.get("initData") or form.get("init_data") or "")
        fields = verify_telegram_webapp(init_data, settings.TELEGRAM_BOT_TOKEN)
        if fields is None:
            return JSONResponse({"ok": False, "error": "bad_init_data"}, status_code=401)
        try:
            user = json.loads(fields.get("user", "{}"))
        except (TypeError, ValueError):
            user = {}
        user_id = int(user.get("id", 0) or 0)
        if not user_id or user_id not in settings.allowed_dm_ids:
            return JSONResponse(
                {"ok": False, "error": "not_allowed"}, status_code=403
            )
        request.session["user_id"] = user_id
        request.session["name"] = str(user.get("first_name", ""))
        return JSONResponse({"ok": True, "user_id": user_id})

    @app.get("/logout")
    async def logout(request: Request) -> Any:
        request.session.clear()
        return RedirectResponse("/", status_code=303)

    @app.get("/chats", response_class=HTMLResponse)
    async def chats(request: Request) -> Any:
        user_id = _uid(request)
        if user_id is None:
            return RedirectResponse("/", status_code=303)
        if user_id not in settings.allowed_dm_ids:
            return HTMLResponse(
                _page(
                    "Forbidden",
                    "<h1>403 — Доступ только для владельцев</h1>"
                    "<p>Access denied. Only the owners may use this panel.</p>",
                ),
                status_code=403,
            )
        async with session_maker() as session:
            managed = await _managed_chats(session, user_id)
        if not managed:
            body = "<h1>Your chats</h1><p>No chats you can manage yet.</p>"
        else:
            rows = "".join(
                f"<div class='row'><span>{html.escape(c.title or str(c.chat_id))}</span>"
                f"<a href='/chats/{c.chat_id}'>Manage →</a></div>"
                for c in managed
            )
            body = f"<h1>Your chats</h1>{rows}"
        body += "<p><a href='/logout'>Log out</a></p>"
        return HTMLResponse(_page("Chats", body))

    @app.get("/chats/{chat_id}", response_class=HTMLResponse)
    async def chat_view(request: Request, chat_id: int) -> Any:
        user_id = _uid(request)
        if user_id is None:
            return RedirectResponse("/", status_code=303)
        if not await _can_manage(user_id, chat_id):
            return HTMLResponse(
                _page("Forbidden", "<h1>403 — not your chat</h1>"),
                status_code=403,
            )
        async with session_maker() as session:
            chat = await crud.get_chat(session, chat_id)
            settings_obj = await crud.get_or_create_settings(session, chat_id)
            data = crud.settings_to_dict(settings_obj)
            total, users = await crud.chat_activity_totals(session, chat_id)
            await session.commit()

        title = html.escape(chat.title if chat else str(chat_id))
        toggles = "".join(
            "<div class='row'>"
            f"<span>{html.escape(label)}</span>"
            f"<form method='post' action='/chats/{chat_id}/toggle'>"
            f"<input type='hidden' name='field' value='{field}'>"
            f"<button class='{'on' if data.get(field) else 'off'}'>"
            f"{'ON' if data.get(field) else 'OFF'}</button></form></div>"
            for field, label in _TOGGLES
        )
        body = (
            f"<p><a href='/chats'>← chats</a></p><h1>{title}</h1>"
            f"<p>Activity: {total} messages from {users} users.</p>"
            f"<h2>Settings</h2>{toggles}"
        )
        return HTMLResponse(_page(title, body))

    @app.post("/chats/{chat_id}/toggle")
    async def toggle(request: Request, chat_id: int, field: str = Form(...)) -> Any:
        user_id = _uid(request)
        if user_id is None:
            return RedirectResponse("/", status_code=303)
        if not await _can_manage(user_id, chat_id) or field not in _TOGGLE_FIELDS:
            return HTMLResponse(_page("Forbidden", "<h1>403</h1>"), status_code=403)
        async with session_maker() as session:
            settings_obj = await crud.get_or_create_settings(session, chat_id)
            current = bool(getattr(settings_obj, field))
            await crud.update_settings(session, chat_id, **{field: not current})
            await session.commit()
        await redis.invalidate_settings(chat_id)
        return RedirectResponse(f"/chats/{chat_id}", status_code=303)

    return app
