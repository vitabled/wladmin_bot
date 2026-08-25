"""Дата вступления пользователя в группу через MTProto (бот-токен).

Bot API не отдаёт ``joined_date`` для участников (в aiogram 3.29 поля у
ChatMember нет вообще), поэтому единственный честный источник даты вступления
— MTProto ``channels.getParticipant``, доступный боту через ``bot_token``
(Telethon). При любой недоступности (нет прав, сети, telethon не установлен)
возвращаем ``None`` — риск-фактор просто опускается, ничего не выдумываем.

Кэш в памяти: дата вступления стабильна, повторные /scam по одному юзеру не
дёргают MTProto.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

_client = None
_client_lock = asyncio.Lock()
# Кэш: (chat_id, user_id) -> (datetime | None, записан_в). None кэшируется
# НЕДОЛГО (60с) — временный сбой не должен навсегда заставлять /scam
# говорить «риск минимален» из-за старого None.
_CACHE: dict[tuple[int, int], tuple[datetime | None, float]] = {}
_CACHE_MAX = 2048
_NONE_TTL = 60.0

_REQUEST_TIMEOUT = 8.0
_START_TIMEOUT = 15.0


def _env(name: str) -> str | None:
    return os.environ.get(name) or None


async def _get_client():
    """Ленивый Telethon-клиент с бот-токеном (in-memory сессия)."""
    global _client
    if _client is not None:
        return _client
    async with _client_lock:
        if _client is not None:
            return _client
        try:
            from telethon import TelegramClient
        except ImportError:
            logger.warning("join_date: telethon not installed")
            return None
        api_id_raw = _env("TELETHON_API_ID")
        api_hash = _env("TELETHON_API_HASH")
        token = _env("TELEGRAM_BOT_TOKEN")
        if not api_id_raw or not api_id_raw.isdigit() or not api_hash or not token:
            logger.warning("join_date: missing TELETHON_API_ID/HASH or bot token")
            return None
        try:
            client = TelegramClient(None, int(api_id_raw), api_hash)
            await asyncio.wait_for(client.start(bot_token=token), timeout=_START_TIMEOUT)
        except Exception as e:
            logger.warning("join_date: mtproto client init failed: %s", e)
            return None
        _client = client
        return client


async def get_joined_date(chat_id: int, user_id: int) -> datetime | None:
    """Вернуть дату вступления (timezone-aware UTC) или ``None``.

    Для создателя чата date отсутствует у Telegram (None) — тоже ``None``.
    """
    key = (chat_id, user_id)
    now = asyncio.get_event_loop().time()
    cached = _CACHE.get(key)
    if cached is not None:
        value, ts = cached
        if value is not None or now - ts < _NONE_TTL:
            return value

    joined: datetime | None = None
    try:
        from telethon.tl import functions

        client = await _get_client()
        if client is None:
            return None
        res = await asyncio.wait_for(
            client(
                functions.channels.GetParticipantRequest(
                    channel=chat_id, participant=user_id
                )
            ),
            timeout=_REQUEST_TIMEOUT,
        )
        raw = getattr(res.participant, "date", None)
        if isinstance(raw, (int, float)):
            joined = datetime.fromtimestamp(raw, tz=UTC)
        elif isinstance(raw, datetime):
            joined = raw
            if joined.tzinfo is None:
                joined = joined.replace(tzinfo=UTC)
    except Exception as e:
        logger.warning("join_date: getParticipant chat=%s user=%s: %s", chat_id, user_id, e)

    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.clear()
    _CACHE[key] = (joined, asyncio.get_event_loop().time())
    return joined
