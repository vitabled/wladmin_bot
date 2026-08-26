"""Integration tests for the FastAPI dashboard (Phase 7).

Webapp is owner-only: access is granted to users in ``allowed_dm_ids`` only
(Holv + Alex); everyone else gets 403 and no session. The old Redis
admin-cache model (``admin:{chat}:{user}``) no longer exists.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from bot.db import crud
from bot.web.app import create_app
from tests.conftest import DEFAULT_SETTINGS

# Allowlisted ids for tests: 999 (Holv) and 700 (Alex).
ALLOWED = (999, 700)


def _settings(**overrides):
    base = dict(
        TELEGRAM_BOT_TOKEN="123:abc",
        OWNER_ID=999,
        WEB_BOT_USERNAME="mybot",
        WEB_SESSION_SECRET="test-secret",
        WEBHOOK_SECRET="wh",
        WEB_DEV_LOGIN=False,
        ALLOWED_DM_IDS="999,700",
        allowed_dm_ids=ALLOWED,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


_SETTINGS = _settings()


class _SessionCM:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def redis():
    r = SimpleNamespace()
    r.get = AsyncMock(return_value=None)
    r.invalidate_settings = AsyncMock()
    return r


@pytest.fixture
def client(redis, monkeypatch):
    # Force the legacy (no-SPA) code path regardless of a local web/dist
    # build — the SPA-serving behavior is tested explicitly below.
    monkeypatch.setattr("bot.web.app.DIST", Path("/nonexistent/web/dist"))
    session = AsyncMock()
    session_maker = lambda: _SessionCM(session)  # noqa: E731
    app = create_app(_SETTINGS, session_maker, redis)
    return TestClient(app)


def _login(client, monkeypatch, user_id: int):
    """Sign in via /auth/telegram (signature mocked); asserts the redirect."""
    monkeypatch.setattr("bot.web.app.verify_telegram_login", lambda *a, **k: True)
    r = client.get(
        f"/auth/telegram?id={user_id}&first_name=T&auth_date=1&hash=x",
        follow_redirects=False,
    )
    assert r.status_code == 303
    # After login the SPA must load at "/" (no more /chats redirect).
    assert r.headers["location"] == "/"
    return r


def _settings_obj(**overrides):
    """Full ChatSettings-shaped namespace (settings_to_dict reads all fields)."""
    data = dict(DEFAULT_SETTINGS)
    data.update(overrides)
    return SimpleNamespace(**data)


# --------------------------------------------------------------------------- #
# Auth / allowlist
# --------------------------------------------------------------------------- #
def test_login_page_shows_widget(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "telegram-widget.js" in r.text


def test_chats_requires_login(client):
    r = client.get("/chats", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/"


def test_auth_rejects_bad_signature(client, monkeypatch):
    monkeypatch.setattr("bot.web.app.verify_telegram_login", lambda *a, **k: False)
    r = client.get("/auth/telegram?id=1&hash=bad", follow_redirects=False)
    assert r.status_code == 401


def test_allowed_login_sets_session_and_redirects(client, monkeypatch):
    monkeypatch.setattr(crud, "list_active_chats", AsyncMock(return_value=[]))
    _login(client, monkeypatch, 999)  # Holv — allowlisted
    # Session persists → /chats renders instead of bouncing to /.
    r = client.get("/chats", follow_redirects=False)
    assert r.status_code == 200


def test_denied_login_returns_403_and_no_session(client, monkeypatch):
    monkeypatch.setattr("bot.web.app.verify_telegram_login", lambda *a, **k: True)
    r = client.get(
        "/auth/telegram?id=7&first_name=T&auth_date=1&hash=x",
        follow_redirects=False,
    )
    assert r.status_code == 403
    assert "Доступ только для владельцев" in r.text
    # No session was created → /chats still requires login.
    r2 = client.get("/chats", follow_redirects=False)
    assert r2.status_code == 303
    assert r2.headers["location"] == "/"


def test_allowed_user_sees_all_chats(client, monkeypatch):
    monkeypatch.setattr(
        crud,
        "list_active_chats",
        AsyncMock(return_value=[SimpleNamespace(chat_id=-100, title="Chat A")]),
    )
    _login(client, monkeypatch, 999)  # Holv
    r = client.get("/chats")
    assert r.status_code == 200
    assert "Chat A" in r.text


def test_second_allowed_user_gets_full_access(client, monkeypatch):
    """Both Holv and Alex (both allowlisted) manage every chat."""
    monkeypatch.setattr(
        crud,
        "list_active_chats",
        AsyncMock(return_value=[SimpleNamespace(chat_id=-100, title="Chat A")]),
    )
    monkeypatch.setattr(crud, "get_chat", AsyncMock(return_value=SimpleNamespace(title="Chat A")))
    monkeypatch.setattr(
        crud, "get_or_create_settings", AsyncMock(return_value=_settings_obj())
    )
    monkeypatch.setattr(crud, "chat_activity_totals", AsyncMock(return_value=(10, 3)))
    _login(client, monkeypatch, 700)  # Alex
    assert client.get("/chats").status_code == 200
    r = client.get("/chats/-100", follow_redirects=False)
    assert r.status_code == 200
    assert "Chat A" in r.text


def test_non_allowed_session_gets_403(redis, monkeypatch):
    """A session with a non-allowlisted id (e.g. old cookie) gets 403 everywhere."""
    monkeypatch.setattr(crud, "list_active_chats", AsyncMock(return_value=[]))
    monkeypatch.setattr(crud, "get_chat", AsyncMock(return_value=SimpleNamespace(title="X")))
    monkeypatch.setattr(crud, "get_or_create_settings", AsyncMock(return_value=_settings_obj()))
    monkeypatch.setattr(crud, "chat_activity_totals", AsyncMock(return_value=(0, 0)))
    # OWNER_ID=999 is NOT allowlisted here (only 700 is); dev-login creates a
    # session for 999, which must then be denied.
    app = create_app(
        _settings(WEB_DEV_LOGIN=True, OWNER_ID=999, allowed_dm_ids=(700,)),
        lambda: _SessionCM(AsyncMock()),  # noqa: E731
        redis,
    )
    dev_client = TestClient(app)
    assert dev_client.get("/dev-login", follow_redirects=False).status_code == 303
    assert dev_client.get("/chats", follow_redirects=False).status_code == 403
    assert dev_client.get("/chats/-100", follow_redirects=False).status_code == 403
    assert (
        dev_client.post(
            "/chats/-100/toggle", data={"field": "welcome_enabled"},
            follow_redirects=False,
        ).status_code
        == 403
    )


# --------------------------------------------------------------------------- #
# Settings view / toggle
# --------------------------------------------------------------------------- #
def test_owner_toggle_updates_and_invalidates(client, monkeypatch, redis):
    monkeypatch.setattr(
        crud,
        "get_or_create_settings",
        AsyncMock(return_value=SimpleNamespace(welcome_enabled=True)),
    )
    update = AsyncMock()
    monkeypatch.setattr(crud, "update_settings", update)
    _login(client, monkeypatch, 999)

    r = client.post(
        "/chats/-100/toggle",
        data={"field": "welcome_enabled"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    _, kwargs = update.await_args
    assert kwargs == {"welcome_enabled": False}
    redis.invalidate_settings.assert_awaited_once()


def test_toggle_rejects_unknown_field(client, monkeypatch):
    monkeypatch.setattr(crud, "update_settings", AsyncMock())
    _login(client, monkeypatch, 999)
    r = client.post(
        "/chats/-100/toggle", data={"field": "not_real"}, follow_redirects=False
    )
    assert r.status_code == 403
    crud.update_settings.assert_not_awaited()


# --------------------------------------------------------------------------- #
# Dev login (local only)
# --------------------------------------------------------------------------- #
def test_dev_login_disabled_by_default(client):
    r = client.get("/dev-login", follow_redirects=False)
    assert r.status_code == 404


def test_dev_login_signs_in_owner_when_enabled(redis, monkeypatch):
    monkeypatch.setattr(crud, "list_active_chats", AsyncMock(return_value=[]))
    session_maker = lambda: _SessionCM(AsyncMock())  # noqa: E731
    app = create_app(_settings(WEB_DEV_LOGIN=True), session_maker, redis)
    dev_client = TestClient(app)

    r = dev_client.get("/dev-login", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    # Session is now set → /chats is reachable (not redirected to /).
    r2 = dev_client.get("/chats", follow_redirects=False)
    assert r2.status_code == 200


# --------------------------------------------------------------------------- #
# SPA serving + public login config (frontend integration)
# --------------------------------------------------------------------------- #
def test_index_serves_spa_when_dist_exists(tmp_path, redis, monkeypatch):
    """With a built web/dist, / returns the SPA index.html and /assets works."""
    dist = tmp_path / "web" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!doctype html><html lang='ru'><head><title>Панель бота</title></head>"
        "<body><div id='root'></div></body></html>",
        encoding="utf-8",
    )
    (dist / "assets" / "index-x.js").write_text("console.log('hi');", encoding="utf-8")
    monkeypatch.setattr("bot.web.app.DIST", dist)

    app = create_app(_SETTINGS, lambda: _SessionCM(AsyncMock()), redis)  # noqa: E731
    spa_client = TestClient(app)

    r = spa_client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Панель бота" in r.text
    assert "<div id='root'>" in r.text

    r2 = spa_client.get("/assets/index-x.js")
    assert r2.status_code == 200
    assert r2.text == "console.log('hi');"


def test_index_falls_back_to_login_page_without_dist(client):
    """No web/dist at runtime → legacy server-rendered login page (widget)."""
    r = client.get("/")
    assert r.status_code == 200
    assert "telegram-widget.js" in r.text
    assert "data-telegram-login='mybot'" in r.text


def test_login_config_is_public_without_session(client):
    """The SPA login screen needs the bot username BEFORE any session exists."""
    r = client.get("/api/login-config")
    assert r.status_code == 200
    assert r.json() == {"bot_username": "mybot"}


def test_auth_redirects_to_root_after_login(client, monkeypatch):
    """Successful login lands on / so the SPA boots and calls /api/me."""
    monkeypatch.setattr("bot.web.app.verify_telegram_login", lambda *a, **k: True)
    r = client.get(
        "/auth/telegram?id=999&first_name=T&auth_date=1&hash=x",
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    # Session persisted → /api/me now answers 200 with the user.
    me = client.get("/api/me")
    assert me.status_code == 200
    assert me.json()["user_id"] == 999


# --------------------------------------------------------------------------- #
# Telegram Mini App login (/auth/webapp) — no login form flow
# --------------------------------------------------------------------------- #

def _webapp_init_data(token: str = "123:abc", user_id: int = 999, name: str = "Holv") -> str:
    """Build a valid (signed) WebApp initData string for the test token."""
    import hashlib
    import hmac
    import json
    import time
    import urllib.parse

    data = {
        "query_id": "AAH9x7TEST",
        "user": json.dumps({"id": user_id, "first_name": name}),
        "auth_date": str(int(time.time())),
    }
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return "&".join(
        f"{k}={urllib.parse.quote(str(v))}" for k, v in sorted(data.items())
    )


def test_auth_webapp_allowed_user_gets_session(client):
    """Mini App initData for an allowlisted user → session, no login form."""
    r = client.post("/auth/webapp", data={"initData": _webapp_init_data()})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    me = client.get("/api/me")
    assert me.status_code == 200
    assert me.json()["user_id"] == 999


def test_auth_webapp_bad_hash_rejected(client):
    """Tampered/unknown initData → 401 and NO session."""
    bad = _webapp_init_data().replace("Holv", "Eve")  # signed before the edit
    r = client.post("/auth/webapp", data={"initData": bad})
    assert r.status_code == 401
    assert client.get("/api/me").status_code == 401


def test_auth_webapp_not_allowed_user_rejected(client):
    """Valid initData but user not in allowlist → 403 and NO session."""
    r = client.post(
        "/auth/webapp", data={"initData": _webapp_init_data(user_id=500, name="Stranger")}
    )
    assert r.status_code == 403
    assert client.get("/api/me").status_code == 401


def test_auth_webapp_missing_init_data_rejected(client):
    r = client.post("/auth/webapp", data={"initData": ""})
    assert r.status_code == 401
