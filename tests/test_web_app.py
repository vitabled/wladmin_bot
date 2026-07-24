"""Integration tests for the FastAPI dashboard (Phase 7)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from bot.db import crud
from bot.web.app import create_app

_SETTINGS = SimpleNamespace(
    TELEGRAM_BOT_TOKEN="123:abc",
    OWNER_ID=999,
    WEB_BOT_USERNAME="mybot",
    WEB_SESSION_SECRET="test-secret",
    WEBHOOK_SECRET="wh",
)


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
def client(redis):
    session = AsyncMock()
    session_maker = lambda: _SessionCM(session)  # noqa: E731
    app = create_app(_SETTINGS, session_maker, redis)
    return TestClient(app)


def _login(client, monkeypatch, user_id: int):
    monkeypatch.setattr("bot.web.app.verify_telegram_login", lambda *a, **k: True)
    r = client.get(
        f"/auth/telegram?id={user_id}&first_name=T&auth_date=1&hash=x",
        follow_redirects=False,
    )
    assert r.status_code == 303


# --------------------------------------------------------------------------- #
# Auth / access
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


def test_owner_sees_all_chats(client, monkeypatch):
    monkeypatch.setattr(
        crud,
        "list_active_chats",
        AsyncMock(return_value=[SimpleNamespace(chat_id=-100, title="Chat A")]),
    )
    _login(client, monkeypatch, 999)  # owner
    r = client.get("/chats")
    assert r.status_code == 200
    assert "Chat A" in r.text


def test_non_owner_without_flag_sees_none(client, monkeypatch, redis):
    monkeypatch.setattr(
        crud,
        "list_active_chats",
        AsyncMock(return_value=[SimpleNamespace(chat_id=-100, title="Chat A")]),
    )
    redis.get = AsyncMock(return_value=None)  # not a cached admin
    _login(client, monkeypatch, 7)
    r = client.get("/chats")
    assert "No chats" in r.text


def test_forbidden_chat_for_non_admin(client, monkeypatch, redis):
    redis.get = AsyncMock(return_value=None)
    _login(client, monkeypatch, 7)
    r = client.get("/chats/-100", follow_redirects=False)
    assert r.status_code == 403


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
