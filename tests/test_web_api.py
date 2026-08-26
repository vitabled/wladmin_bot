"""API tests for the FastAPI dashboard JSON endpoints (tasks 5+6).

Same httpx/TestClient harness as test_web_app.py: create_app with mocked
settings / session_maker / redis, sessions created via /auth/telegram
(signature mocked). All DB access is mocked at the crud layer.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from bot.constants import SCAM_SOURCE_VERIFIED
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
def client(redis):
    session = AsyncMock()
    session_maker = lambda: _SessionCM(session)  # noqa: E731
    app = create_app(_SETTINGS, session_maker, redis)
    return TestClient(app)


def _login(client, monkeypatch, user_id: int, name: str = "T"):
    """Sign in via /auth/telegram (signature mocked); asserts the redirect."""
    monkeypatch.setattr("bot.web.app.verify_telegram_login", lambda *a, **k: True)
    r = client.get(
        f"/auth/telegram?id={user_id}&first_name={name}&auth_date=1&hash=x",
        follow_redirects=False,
    )
    assert r.status_code == 303
    return r


def _settings_obj(**overrides):
    """Full ChatSettings-shaped namespace (settings_to_dict reads all fields)."""
    data = dict(DEFAULT_SETTINGS)
    data.update(overrides)
    return SimpleNamespace(**data)


# --------------------------------------------------------------------------- #
# Auth (owner allowlist)
# --------------------------------------------------------------------------- #
def test_api_me_without_session_401(client):
    r = client.get("/api/me")
    assert r.status_code == 401


def test_api_me_non_allowed_session_403(redis):
    """A session with a non-allowlisted id (e.g. stale cookie) gets 403."""
    app = create_app(
        _settings(WEB_DEV_LOGIN=True, OWNER_ID=999, allowed_dm_ids=(700,)),
        lambda: _SessionCM(AsyncMock()),  # noqa: E731
        redis,
    )
    dev_client = TestClient(app)
    assert dev_client.get("/dev-login", follow_redirects=False).status_code == 303
    assert dev_client.get("/api/me").status_code == 403


def test_api_me_allowed_200(client, monkeypatch):
    _login(client, monkeypatch, 999)
    r = client.get("/api/me")
    assert r.status_code == 200
    assert r.json() == {"user_id": 999, "name": "T"}


# --------------------------------------------------------------------------- #
# Chats
# --------------------------------------------------------------------------- #
def test_api_chats_lists_with_activity_and_bans(client, monkeypatch):
    _login(client, monkeypatch, 999)
    monkeypatch.setattr(
        crud,
        "list_active_chats",
        AsyncMock(
            return_value=[
                SimpleNamespace(chat_id=-100, title="Chat A"),
                SimpleNamespace(chat_id=-101, title="Chat B"),
            ]
        ),
    )
    monkeypatch.setattr(
        crud, "chat_activity_totals", AsyncMock(side_effect=[(10, 3), (5, 1)])
    )
    monkeypatch.setattr(crud, "count_bans", AsyncMock(side_effect=[2, 0]))
    r = client.get("/api/chats")
    assert r.status_code == 200
    data = r.json()
    assert data == [
        {"chat_id": -100, "title": "Chat A", "total_messages": 10, "banned": 2},
        {"chat_id": -101, "title": "Chat B", "total_messages": 5, "banned": 0},
    ]


def test_api_chat_detail_404_unknown(client, monkeypatch):
    _login(client, monkeypatch, 999)
    monkeypatch.setattr(crud, "get_chat", AsyncMock(return_value=None))
    assert client.get("/api/chats/-100").status_code == 404


def test_api_chat_detail_200(client, monkeypatch):
    _login(client, monkeypatch, 999)
    monkeypatch.setattr(
        crud,
        "get_chat",
        AsyncMock(return_value=SimpleNamespace(chat_id=-100, title="Chat A")),
    )
    monkeypatch.setattr(
        crud, "get_or_create_settings", AsyncMock(return_value=_settings_obj(welcome_enabled=False))
    )
    monkeypatch.setattr(crud, "chat_activity_totals", AsyncMock(return_value=(10, 3)))
    monkeypatch.setattr(crud, "count_warns_chat", AsyncMock(return_value=1))
    monkeypatch.setattr(crud, "count_bans", AsyncMock(return_value=2))
    monkeypatch.setattr(
        crud,
        "get_slow_mode",
        AsyncMock(
            return_value=SimpleNamespace(
                enabled=True, regular_seconds=120, wl_seconds=60, topic_ids=[3, 6]
            )
        ),
    )
    monkeypatch.setattr(
        crud,
        "list_topics",
        AsyncMock(
            return_value=[
                SimpleNamespace(
                    thread_id=5, message_count=3, last_seen=datetime(2026, 1, 2, tzinfo=UTC)
                )
            ]
        ),
    )
    r = client.get("/api/chats/-100")
    assert r.status_code == 200
    data = r.json()
    assert data["chat_id"] == -100
    assert data["title"] == "Chat A"
    assert data["settings"]["welcome_enabled"] is False
    assert data["slow_mode"] == {
        "enabled": True,
        "regular_seconds": 120,
        "wl_seconds": 60,
        "topic_ids": [3, 6],
    }
    assert data["activity"] == {"total": 10, "users": 3}
    assert data["warns"] == 1
    assert data["banned"] == 2
    assert data["topics"] == [
        {"thread_id": 5, "message_count": 3, "last_seen": "2026-01-02T00:00:00+00:00"}
    ]


def test_api_chat_detail_slow_mode_defaults(client, monkeypatch):
    _login(client, monkeypatch, 999)
    monkeypatch.setattr(
        crud,
        "get_chat",
        AsyncMock(return_value=SimpleNamespace(chat_id=-100, title="Chat A")),
    )
    monkeypatch.setattr(crud, "get_or_create_settings", AsyncMock(return_value=_settings_obj()))
    monkeypatch.setattr(crud, "chat_activity_totals", AsyncMock(return_value=(0, 0)))
    monkeypatch.setattr(crud, "count_warns_chat", AsyncMock(return_value=0))
    monkeypatch.setattr(crud, "count_bans", AsyncMock(return_value=0))
    monkeypatch.setattr(crud, "get_slow_mode", AsyncMock(return_value=None))
    monkeypatch.setattr(crud, "list_topics", AsyncMock(return_value=[]))
    data = client.get("/api/chats/-100").json()
    assert data["slow_mode"] == {
        "enabled": False,
        "regular_seconds": 21600,
        "wl_seconds": 10800,
        "topic_ids": None,
    }


# --------------------------------------------------------------------------- #
# Toggle / slow mode
# --------------------------------------------------------------------------- #
def test_api_toggle_flips_and_invalidates(client, monkeypatch, redis):
    _login(client, monkeypatch, 999)
    monkeypatch.setattr(
        crud,
        "get_or_create_settings",
        AsyncMock(return_value=SimpleNamespace(welcome_enabled=True)),
    )
    update = AsyncMock()
    monkeypatch.setattr(crud, "update_settings", update)
    r = client.post("/api/chats/-100/toggle", json={"field": "welcome_enabled"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "field": "welcome_enabled", "value": False}
    _, kwargs = update.await_args
    assert kwargs == {"welcome_enabled": False}
    redis.invalidate_settings.assert_awaited_once_with(-100)


def test_api_toggle_unknown_field_400(client, monkeypatch):
    _login(client, monkeypatch, 999)
    monkeypatch.setattr(crud, "update_settings", AsyncMock())
    r = client.post("/api/chats/-100/toggle", json={"field": "not_real"})
    assert r.status_code == 400
    crud.update_settings.assert_not_awaited()


def test_api_toggle_missing_field_422(client, monkeypatch):
    _login(client, monkeypatch, 999)
    r = client.post("/api/chats/-100/toggle", json={})
    assert r.status_code == 422


def test_api_slow_mode_persists_clamped(client, monkeypatch):
    _login(client, monkeypatch, 999)
    set_sm = AsyncMock(
        return_value=SimpleNamespace(
            enabled=True, regular_seconds=60, wl_seconds=2592000, topic_ids=None
        )
    )
    monkeypatch.setattr(crud, "set_slow_mode", set_sm)
    r = client.post(
        "/api/chats/-100/slow-mode",
        json={"enabled": True, "regular_seconds": 5, "wl_seconds": 999999999},
    )
    assert r.status_code == 200
    assert r.json() == {
        "ok": True,
        "enabled": True,
        "regular_seconds": 60,
        "wl_seconds": 2592000,
        "topic_ids": None,
    }
    _, kwargs = set_sm.await_args
    assert kwargs == {
        "enabled": True,
        "regular_seconds": 60,
        "wl_seconds": 2592000,
        "topic_ids": None,
    }


def test_api_slow_mode_with_topic_ids(client, monkeypatch):
    _login(client, monkeypatch, 999)
    set_sm = AsyncMock(
        return_value=SimpleNamespace(
            enabled=True, regular_seconds=21600, wl_seconds=10800, topic_ids=[3, 6]
        )
    )
    monkeypatch.setattr(crud, "set_slow_mode", set_sm)
    r = client.post(
        "/api/chats/-100/slow-mode",
        json={
            "enabled": True,
            "regular_seconds": 21600,
            "wl_seconds": 10800,
            "topic_ids": [3, 6],
        },
    )
    assert r.status_code == 200
    assert r.json()["topic_ids"] == [3, 6]
    _, kwargs = set_sm.await_args
    assert kwargs["topic_ids"] == [3, 6]


def test_api_slow_mode_topic_ids_omitted_leaves_scope_unchanged(client, monkeypatch):
    _login(client, monkeypatch, 999)
    # The row already carries a scope; a POST without topic_ids must not
    # clobber it — set_slow_mode is called with topic_ids=None and the
    # response echoes the actual stored value.
    set_sm = AsyncMock(
        return_value=SimpleNamespace(
            enabled=False, regular_seconds=60, wl_seconds=60, topic_ids=[3, 6]
        )
    )
    monkeypatch.setattr(crud, "set_slow_mode", set_sm)
    r = client.post(
        "/api/chats/-100/slow-mode",
        json={"enabled": False, "regular_seconds": 60, "wl_seconds": 60},
    )
    assert r.status_code == 200
    assert r.json()["topic_ids"] == [3, 6]
    _, kwargs = set_sm.await_args
    assert kwargs["topic_ids"] is None  # unchanged


def test_api_slow_mode_rejects_non_list_topic_ids(client, monkeypatch):
    _login(client, monkeypatch, 999)
    monkeypatch.setattr(crud, "set_slow_mode", AsyncMock())
    r = client.post(
        "/api/chats/-100/slow-mode",
        json={
            "enabled": True,
            "regular_seconds": 100,
            "wl_seconds": 100,
            "topic_ids": "3,6",
        },
    )
    assert r.status_code == 422
    crud.set_slow_mode.assert_not_awaited()


def test_api_slow_mode_rejects_negative(client, monkeypatch):
    _login(client, monkeypatch, 999)
    monkeypatch.setattr(crud, "set_slow_mode", AsyncMock())
    r = client.post(
        "/api/chats/-100/slow-mode",
        json={"enabled": True, "regular_seconds": -1, "wl_seconds": 100},
    )
    assert r.status_code == 422
    crud.set_slow_mode.assert_not_awaited()


# --------------------------------------------------------------------------- #
# Stats
# --------------------------------------------------------------------------- #
def test_api_stats_with_top_names(client, monkeypatch):
    _login(client, monkeypatch, 999)
    monkeypatch.setattr(crud, "chat_activity_totals", AsyncMock(return_value=(100, 20)))
    monkeypatch.setattr(crud, "count_bans", AsyncMock(return_value=3))
    monkeypatch.setattr(crud, "count_warns_chat", AsyncMock(return_value=4))
    monkeypatch.setattr(crud, "top_active", AsyncMock(return_value=[(1, 50), (2, 30)]))
    monkeypatch.setattr(crud, "get_users_by_ids", AsyncMock(return_value={1: "Alice"}))
    r = client.get("/api/chats/-100/stats")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 100
    assert data["users"] == 20
    assert data["banned"] == 3
    assert data["warns"] == 4
    assert data["top"] == [
        {"user_id": 1, "name": "Alice", "count": 50},
        {"user_id": 2, "name": "2", "count": 30},
    ]


# --------------------------------------------------------------------------- #
# Rating (scam verdict)
# --------------------------------------------------------------------------- #
def test_api_rating_numeric_target(client, monkeypatch):
    _login(client, monkeypatch, 999)
    build = AsyncMock(return_value="BODY")
    monkeypatch.setattr("bot.web.api.build_scam_body", build)
    monkeypatch.setattr(crud, "get_users_by_ids", AsyncMock(return_value={123: "Alice"}))
    r = client.get("/api/rating", params={"target": "123"})
    assert r.status_code == 200
    assert r.json() == {"body": "BODY", "target_id": 123, "target_name": "Alice"}
    args, kwargs = build.await_args
    assert args[2] == 123
    assert args[3] == "Alice"
    assert kwargs == {}


def test_api_rating_username_target(client, monkeypatch):
    _login(client, monkeypatch, 999)
    build = AsyncMock(return_value="BODY")
    monkeypatch.setattr("bot.web.api.build_scam_body", build)
    monkeypatch.setattr(
        "bot.web.api._resolve_username", AsyncMock(return_value=(555, "Alice Chan"))
    )
    r = client.get("/api/rating", params={"target": "@alice"})
    assert r.status_code == 200
    assert r.json() == {"body": "BODY", "target_id": 555, "target_name": "Alice Chan"}


def test_api_rating_username_not_found_404(client, monkeypatch):
    _login(client, monkeypatch, 999)
    monkeypatch.setattr("bot.web.api._resolve_username", AsyncMock(return_value=None))
    r = client.get("/api/rating", params={"target": "@nobody"})
    assert r.status_code == 404
    assert r.json() == {"error": "not_found"}


def test_api_rating_with_chat_scopes_factors(client, monkeypatch):
    _login(client, monkeypatch, 999)
    build = AsyncMock(return_value="BODY")
    monkeypatch.setattr("bot.web.api.build_scam_body", build)
    monkeypatch.setattr(crud, "get_users_by_ids", AsyncMock(return_value={123: "Alice"}))
    monkeypatch.setattr(crud, "get_chat", AsyncMock(return_value=SimpleNamespace(title="G")))
    r = client.get("/api/rating", params={"target": "123", "chat_id": "-100"})
    assert r.status_code == 200
    _, kwargs = build.await_args
    assert kwargs["chat"].id == -100
    assert kwargs["chat"].type == "supergroup"
    assert kwargs["bot"] is not None


def test_api_rating_unknown_chat_404(client, monkeypatch):
    _login(client, monkeypatch, 999)
    monkeypatch.setattr(crud, "get_users_by_ids", AsyncMock(return_value={123: "Alice"}))
    monkeypatch.setattr(crud, "get_chat", AsyncMock(return_value=None))
    r = client.get("/api/rating", params={"target": "123", "chat_id": "-100"})
    assert r.status_code == 404
    assert r.json() == {"error": "not_found"}


def test_api_rating_requires_target(client, monkeypatch):
    _login(client, monkeypatch, 999)
    r = client.get("/api/rating")
    assert r.status_code == 422


# --------------------------------------------------------------------------- #
# Rating list / whitelist
# --------------------------------------------------------------------------- #
def test_api_rating_list(client, monkeypatch):
    _login(client, monkeypatch, 999)
    monkeypatch.setattr(
        crud,
        "list_scam_entries",
        AsyncMock(
            return_value=[
                SimpleNamespace(user_id=1, source="scam", reason="bad"),
                SimpleNamespace(user_id=2, source="verified", reason=None),
            ]
        ),
    )
    monkeypatch.setattr(crud, "get_users_by_ids", AsyncMock(return_value={1: "Bob"}))
    r = client.get("/api/rating/list")
    assert r.status_code == 200
    assert r.json() == [
        {"user_id": 1, "name": "Bob", "source": "scam", "reason": "bad"},
        {"user_id": 2, "name": "2", "source": "verified", "reason": None},
    ]


def test_api_rating_wl_add(client, monkeypatch):
    _login(client, monkeypatch, 999)
    upsert = AsyncMock()
    monkeypatch.setattr(crud, "upsert_scam_entry", upsert)
    r = client.post("/api/rating/wl", json={"user_id": 123})
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    args, kwargs = upsert.await_args
    assert args[1] == 123
    assert args[2] == SCAM_SOURCE_VERIFIED
    assert args[3] is None
    assert kwargs == {}


def test_api_rating_wl_add_by_username(client, monkeypatch):
    _login(client, monkeypatch, 999)
    upsert = AsyncMock()
    monkeypatch.setattr(crud, "upsert_scam_entry", upsert)
    monkeypatch.setattr(
        "bot.web.api._resolve_username", AsyncMock(return_value=(555, "Alice"))
    )
    r = client.post("/api/rating/wl", json={"target": "@alice"})
    assert r.status_code == 200
    args, _ = upsert.await_args
    assert args[1] == 555


def test_api_rating_wl_add_username_not_found_404(client, monkeypatch):
    _login(client, monkeypatch, 999)
    monkeypatch.setattr(crud, "upsert_scam_entry", AsyncMock())
    monkeypatch.setattr("bot.web.api._resolve_username", AsyncMock(return_value=None))
    r = client.post("/api/rating/wl", json={"target": "@nobody"})
    assert r.status_code == 404
    crud.upsert_scam_entry.assert_not_awaited()


def test_api_rating_wl_remove(client, monkeypatch):
    _login(client, monkeypatch, 999)
    remove = AsyncMock(return_value=True)
    monkeypatch.setattr(crud, "remove_scam_entry", remove)
    r = client.request("DELETE", "/api/rating/wl", json={"user_id": 123})
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    args, _ = remove.await_args
    assert args[1] == 123


def test_api_rating_wl_remove_missing(client, monkeypatch):
    _login(client, monkeypatch, 999)
    monkeypatch.setattr(crud, "remove_scam_entry", AsyncMock(return_value=False))
    r = client.request("DELETE", "/api/rating/wl", json={"user_id": 123})
    assert r.status_code == 200
    assert r.json() == {"ok": False}


# --------------------------------------------------------------------------- #
# Broadcast
# --------------------------------------------------------------------------- #
def test_api_broadcast(client, monkeypatch):
    _login(client, monkeypatch, 999)
    send = AsyncMock(return_value=[{"thread_id": 1, "ok": True, "error": None}])
    monkeypatch.setattr("bot.web.api.send_broadcast", send)
    r = client.post(
        "/api/broadcast", json={"chat_id": -100, "thread_ids": [1], "text": "hello"}
    )
    assert r.status_code == 200
    assert r.json() == {"results": [{"thread_id": 1, "ok": True, "error": None}]}
    args, kwargs = send.await_args
    assert args[1] == -100
    assert args[2] == [1]
    assert args[3] == "hello"
    assert kwargs == {}


def test_api_broadcast_empty_text_422(client, monkeypatch):
    _login(client, monkeypatch, 999)
    monkeypatch.setattr("bot.web.api.send_broadcast", AsyncMock())
    r = client.post(
        "/api/broadcast", json={"chat_id": -100, "thread_ids": [1], "text": ""}
    )
    assert r.status_code == 422
    r2 = client.post(
        "/api/broadcast", json={"chat_id": -100, "thread_ids": [1], "text": "   "}
    )
    assert r2.status_code == 422
