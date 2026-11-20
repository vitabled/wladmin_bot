"""Web dashboard access tests for owner, env, and database staff roles."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from bot.web.app import create_app


class _SessionCM:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc):
        return False


def _settings(**overrides):
    values = {
        "TELEGRAM_BOT_TOKEN": "123:abc",
        "OWNER_ID": 999,
        "default_staff_ids": (700,),
        "WEB_BOT_USERNAME": "mybot",
        "WEB_SESSION_SECRET": "test-secret",
        "WEBHOOK_SECRET": "wh",
        "WEB_DEV_LOGIN": False,
        "ALLOWED_DM_IDS": "",
        "allowed_dm_ids": (),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def redis():
    return SimpleNamespace(get=AsyncMock(return_value=None))


def _client(redis, monkeypatch, *, role=None, settings=None):
    monkeypatch.setattr("bot.web.app.DIST", Path("/nonexistent/web/dist"))
    monkeypatch.setattr(
        "bot.web.auth.get_role_cached",
        AsyncMock(return_value=role),
        raising=False,
    )
    app = create_app(
        settings or _settings(),
        lambda: _SessionCM(AsyncMock()),  # noqa: E731
        redis,
    )
    return TestClient(app)


def _telegram_login(client, monkeypatch, user_id: int):
    monkeypatch.setattr("bot.web.app.verify_telegram_login", lambda *args, **kwargs: True)
    return client.get(
        f"/auth/telegram?id={user_id}&first_name=T&auth_date=1&hash=x",
        follow_redirects=False,
    )


def _webapp_init_data(user_id: int) -> str:
    data = {
        "query_id": "AAH9x7TEST",
        "user": json.dumps({"id": user_id, "first_name": "T"}),
        "auth_date": str(int(time.time())),
    }
    secret = hmac.new(b"WebAppData", b"123:abc", hashlib.sha256).digest()
    check = "\n".join(f"{key}={data[key]}" for key in sorted(data))
    data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(data)


@pytest.mark.parametrize("user_id", [999, 700])
def test_owner_and_env_staff_can_login_without_whitelist(
    redis, monkeypatch, user_id
):
    client = _client(redis, monkeypatch)

    response = _telegram_login(client, monkeypatch, user_id)

    assert response.status_code == 303
    assert client.get("/api/me").status_code == 200


def test_database_staff_can_login_and_use_api(redis, monkeypatch):
    client = _client(redis, monkeypatch, role="moderator")

    response = _telegram_login(client, monkeypatch, 500)

    assert response.status_code == 303
    assert client.get("/api/me").json()["user_id"] == 500


def test_allowlisted_outsider_cannot_login(redis, monkeypatch):
    client = _client(
        redis,
        monkeypatch,
        settings=_settings(ALLOWED_DM_IDS="500", allowed_dm_ids=(500,)),
    )

    response = _telegram_login(client, monkeypatch, 500)

    assert response.status_code == 403
    assert client.get("/api/me").status_code == 401


def test_whitelist_further_restricts_owner(redis, monkeypatch):
    client = _client(
        redis,
        monkeypatch,
        settings=_settings(ALLOWED_DM_IDS="700", allowed_dm_ids=(700,)),
    )

    response = _telegram_login(client, monkeypatch, 999)

    assert response.status_code == 403


def test_whitelist_further_restricts_database_staff(redis, monkeypatch):
    client = _client(
        redis,
        monkeypatch,
        role="moderator",
        settings=_settings(ALLOWED_DM_IDS="700", allowed_dm_ids=(700,)),
    )

    response = _telegram_login(client, monkeypatch, 500)

    assert response.status_code == 403


def test_staff_role_revocation_invalidates_existing_session(redis, monkeypatch):
    role = AsyncMock(return_value="admin")
    monkeypatch.setattr("bot.web.app.DIST", Path("/nonexistent/web/dist"))
    monkeypatch.setattr("bot.web.auth.get_role_cached", role, raising=False)
    app = create_app(
        _settings(),
        lambda: _SessionCM(AsyncMock()),  # noqa: E731
        redis,
    )
    client = TestClient(app)
    assert _telegram_login(client, monkeypatch, 500).status_code == 303
    role.return_value = None

    response = client.get("/api/me")

    assert response.status_code == 403


def test_webapp_login_requires_staff_not_just_whitelist(redis, monkeypatch):
    client = _client(
        redis,
        monkeypatch,
        settings=_settings(ALLOWED_DM_IDS="500", allowed_dm_ids=(500,)),
    )

    response = client.post(
        "/auth/webapp",
        data={"initData": _webapp_init_data(user_id=500)},
    )

    assert response.status_code == 403
    assert client.get("/api/me").status_code == 401


def test_database_staff_can_login_through_webapp(redis, monkeypatch):
    client = _client(redis, monkeypatch, role="moderator")

    response = client.post(
        "/auth/webapp",
        data={"initData": _webapp_init_data(user_id=500)},
    )

    assert response.status_code == 200
    assert client.get("/api/me").status_code == 200


def test_dev_login_owner_uses_same_access_gate(redis, monkeypatch):
    client = _client(
        redis,
        monkeypatch,
        settings=_settings(WEB_DEV_LOGIN=True),
    )
    assert client.get("/dev-login", follow_redirects=False).status_code == 303

    response = client.get("/api/me")

    assert response.status_code == 200


def test_login_page_and_public_config_remain_public(redis, monkeypatch):
    client = _client(redis, monkeypatch)

    page = client.get("/")
    config = client.get("/api/login-config")

    assert page.status_code == 200
    assert "telegram-widget.js" in page.text
    assert config.status_code == 200
    assert config.json() == {"bot_username": "mybot"}
