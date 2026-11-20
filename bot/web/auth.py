"""Telegram Login Widget verification (Phase 7).

Telegram подписывает данные логина HMAC-SHA256, где ключ = SHA256(bot_token).
Проверяем подпись в постоянном времени и свежесть ``auth_date``. Функции
чистые (``now`` инъектируется) — тестируются без сети и без часов.

Docs: https://core.telegram.org/widgets/login#checking-authorization
"""

from __future__ import annotations

import hashlib
import hmac
import time
import urllib.parse
from collections.abc import Mapping
from typing import Any

from fastapi import Request

# Default max age of a login payload (24h) before it's considered stale.
DEFAULT_MAX_AGE = 86_400


async def get_role_cached(session: Any, redis: Any, user_id: int) -> Any:
    """Resolve a database staff role through the shared cached service."""
    from bot.services.staff import get_role_cached as resolve_role

    return await resolve_role(session, redis, user_id)


async def has_web_access(request: Request, user_id: int) -> bool:
    """Return whether a user is staff and passes the optional legacy whitelist.

    Staff identity is the union of the configured owner, env-provided default
    staff, and active database roles.  A non-empty ``ALLOWED_DM_IDS`` narrows
    that union; it never grants access by itself.
    """
    settings = request.app.state.settings
    allowed_ids = settings.allowed_dm_ids
    if allowed_ids and user_id not in allowed_ids:
        return False
    if user_id == settings.OWNER_ID or user_id in getattr(
        settings, "default_staff_ids", ()
    ):
        return True
    async with request.app.state.session_maker() as session:
        role = await get_role_cached(session, request.app.state.redis, user_id)
    return role is not None


def build_data_check_string(data: Mapping[str, str]) -> str:
    """Join all fields except ``hash`` as sorted ``key=value`` lines."""
    return "\n".join(f"{key}={data[key]}" for key in sorted(data) if key != "hash")


def verify_telegram_login(
    data: Mapping[str, str],
    bot_token: str,
    max_age_seconds: int = DEFAULT_MAX_AGE,
    now: int | None = None,
) -> bool:
    """Return True if the login payload is authentic and fresh.

    * signature: HMAC-SHA256(data_check_string, key=SHA256(bot_token)) == hash
    * freshness: ``now - auth_date <= max_age_seconds`` (skipped if max_age<=0)
    """
    provided = data.get("hash")
    if not provided:
        return False

    secret_key = hashlib.sha256(bot_token.encode()).digest()
    check_string = build_data_check_string(data)
    computed = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed, str(provided)):
        return False

    if max_age_seconds and max_age_seconds > 0:
        try:
            auth_date = int(data.get("auth_date", 0))
        except (TypeError, ValueError):
            return False
        current = now if now is not None else int(time.time())
        if current - auth_date > max_age_seconds:
            return False
    return True


def verify_telegram_webapp(
    init_data: str,
    bot_token: str,
    max_age_seconds: int = DEFAULT_MAX_AGE,
    now: int | None = None,
) -> dict[str, str] | None:
    """Verify Telegram WebApp ``initData`` (Mini App flow).

    Telegram signs initData with HMAC-SHA256 where the key is
    ``HMAC_SHA256(key="WebAppData", msg=bot_token)`` and the check string is
    the same sorted ``key=value`` lines (minus ``hash``) as the Login Widget.
    On success return the parsed fields (``user`` is a JSON object); otherwise
    return ``None``. Also enforces ``auth_date`` freshness.
    """
    if not init_data:
        return None
    try:
        params = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
    except Exception:
        return None
    provided = params.get("hash")
    if not provided:
        return None

    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    check_string = build_data_check_string(params)
    computed = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed, str(provided)):
        return None

    if max_age_seconds and max_age_seconds > 0:
        try:
            auth_date = int(params.get("auth_date", 0))
        except (TypeError, ValueError):
            return None
        current = now if now is not None else int(time.time())
        if current - auth_date > max_age_seconds:
            return None

    return {k: v for k, v in params.items() if k != "hash"}
