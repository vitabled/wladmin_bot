"""Unit tests for Telegram Login verification (Phase 7)."""

from __future__ import annotations

import hashlib
import hmac

from bot.web.auth import verify_telegram_login, verify_telegram_webapp

_TOKEN = "123456:test-bot-token"


def _sign(data: dict, token: str = _TOKEN) -> str:
    secret = hashlib.sha256(token.encode()).digest()
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data) if k != "hash")
    return hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()


def _payload(auth_date: int = 1000) -> dict:
    data = {"id": "42", "first_name": "Alice", "auth_date": str(auth_date)}
    data["hash"] = _sign(data)
    return data


def test_valid_signature_passes():
    data = _payload(auth_date=1000)
    assert verify_telegram_login(data, _TOKEN, max_age_seconds=0, now=1000)


def test_tampered_field_fails():
    data = _payload()
    data["id"] = "99"  # changed after signing
    assert not verify_telegram_login(data, _TOKEN, max_age_seconds=0)


def test_missing_hash_fails():
    data = {"id": "42", "auth_date": "1000"}
    assert not verify_telegram_login(data, _TOKEN)


def test_wrong_token_fails():
    data = _payload()
    assert not verify_telegram_login(data, "999:other-token", max_age_seconds=0)


def test_stale_auth_date_fails():
    data = _payload(auth_date=1000)
    # now is far past auth_date + max_age
    assert not verify_telegram_login(data, _TOKEN, max_age_seconds=60, now=100000)


# --------------------------------------------------------------------------- #
# Telegram WebApp initData (Mini App flow)
# --------------------------------------------------------------------------- #

def _sign_webapp(data: dict, token: str = _TOKEN) -> str:
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data) if k != "hash")
    return hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()


def _init_data(auth_date: int = 1000, token: str = _TOKEN) -> str:
    data = {
        "query_id": "AAH9x7",
        "user": '{"id":42,"first_name":"Alice","username":"alice"}',
        "auth_date": str(auth_date),
    }
    data["hash"] = _sign_webapp(data, token)
    return "&".join(f"{k}={v}" for k, v in sorted(data.items()))


def test_webapp_valid_init_data_passes():
    fields = verify_telegram_webapp(_init_data(), _TOKEN, max_age_seconds=0, now=1000)
    assert fields is not None
    assert fields["user"].find('"id":42') >= 0


def test_webapp_tampered_user_fails():
    bad = _init_data().replace("42", "99")  # user id changed after signing
    assert verify_telegram_webapp(bad, _TOKEN, max_age_seconds=0) is None


def test_webapp_wrong_token_fails():
    assert verify_telegram_webapp(_init_data(), "999:other-token", max_age_seconds=0) is None


def test_webapp_missing_hash_fails():
    data = {"query_id": "AAH9x7", "user": "{}", "auth_date": "1000"}
    qs = "&".join(f"{k}={v}" for k, v in sorted(data.items()))
    assert verify_telegram_webapp(qs, _TOKEN) is None


def test_webapp_empty_fails():
    assert verify_telegram_webapp("", _TOKEN) is None


def test_webapp_stale_fails():
    assert verify_telegram_webapp(_init_data(), _TOKEN, max_age_seconds=60, now=100000) is None


def test_fresh_auth_date_passes():
    data = _payload(auth_date=1000)
    assert verify_telegram_login(data, _TOKEN, max_age_seconds=3600, now=1500)


def test_non_numeric_auth_date_fails():
    data = {"id": "42", "auth_date": "not-a-number"}
    data["hash"] = _sign(data)
    assert not verify_telegram_login(data, _TOKEN, max_age_seconds=60, now=1000)
