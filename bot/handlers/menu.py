"""Inline settings menu (Phase 6).

``/menu`` открывает клавиатуру-переключатели основных булевых настроек;
нажатие кнопки инвертирует настройку через ``crud.update_settings``,
инвалидирует кэш и перерисовывает клавиатуру. Значения-числа (лимиты, сроки)
по-прежнему меняются командами — меню закрывает только тумблеры.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.cache.redis import RedisClient
from bot.db import crud
from bot.filters.chat_type import IsGroup

logger = logging.getLogger(__name__)

router = Router()

_PREFIX = "menu"

# (settings field, i18n label key) — boolean toggles only.
_TOGGLES: list[tuple[str, str]] = [
    ("welcome_enabled", "menu_welcome"),
    ("captcha_enabled", "menu_captcha"),
    ("filter_links", "menu_links"),
    ("filter_forwards", "menu_forwards"),
    ("filter_stopwords", "menu_stopwords"),
    ("antiflood_enabled", "menu_antiflood"),
    ("newbie_media_enabled", "menu_newbie"),
    ("triggers_enabled", "menu_triggers"),
    ("stats_enabled", "menu_stats"),
]
_TOGGLE_FIELDS = frozenset(field for field, _ in _TOGGLES)


def build_menu(
    settings: dict[str, Any], translate: Callable[..., str]
) -> types.InlineKeyboardMarkup:
    """Build the settings keyboard reflecting each toggle's current state."""
    builder = InlineKeyboardBuilder()
    for field, label_key in _TOGGLES:
        mark = "✅" if settings.get(field) else "❌"
        builder.button(
            text=f"{mark} {translate(label_key)}",
            callback_data=f"{_PREFIX}:t:{field}",
        )
    builder.button(text=translate("menu_close"), callback_data=f"{_PREFIX}:close")
    builder.adjust(1)
    return builder.as_markup()


@router.message(IsGroup(), Command("menu"))
async def cmd_menu(message: types.Message, **data: Any) -> None:
    """Open the inline settings menu (admins only)."""
    _ = data["_"]
    _raw = data["_raw"]
    if not data.get("is_admin"):
        await message.reply(_("error_not_admin"))
        return
    settings = data.get("settings") or {}
    # Кнопки не парсят HTML: подписи берём через _raw (без <tg-emoji>).
    await message.reply(_("menu_title"), reply_markup=build_menu(settings, _raw))


@router.callback_query(F.data.startswith(f"{_PREFIX}:"))
async def on_menu_callback(callback: types.CallbackQuery, **data: Any) -> None:
    """Apply a menu button press: toggle a setting or close the menu."""
    _ = data["_"]
    _raw = data["_raw"]
    if not data.get("is_admin"):
        # Тост/алерт не парсит HTML — _raw, чтобы <tg-emoji> не был виден сырым.
        await callback.answer(_raw("error_not_admin"), show_alert=True)
        return

    parts = (callback.data or "").split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "close":
        if callback.message is not None:
            try:
                await callback.message.delete()
            except Exception:
                pass
        await callback.answer()
        return

    if action == "t" and len(parts) == 3 and parts[2] in _TOGGLE_FIELDS:
        field = parts[2]
        chat = data.get("event_chat")
        if chat is None:
            await callback.answer()
            return
        settings = data.get("settings") or {}
        new_val = not settings.get(field)
        session: AsyncSession = data["session"]
        redis: RedisClient = data["redis"]
        await crud.update_settings(session, chat.id, **{field: new_val})
        await redis.invalidate_settings(chat.id)
        settings[field] = new_val
        try:
            await callback.message.edit_reply_markup(
                reply_markup=build_menu(settings, _raw)
            )
        except Exception:
            pass
        await callback.answer(_raw("menu_saved"))
        return

    await callback.answer()
