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
from collections.abc import Mapping

# Default max age of a login payload (24h) before it's considered stale.
DEFAULT_MAX_AGE = 86_400


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
