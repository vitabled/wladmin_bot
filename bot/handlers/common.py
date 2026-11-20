"""Common handlers: /start, /help (localized, work in PM and groups)."""

from __future__ import annotations

from collections.abc import Callable

from aiogram import Router, types
from aiogram.filters import Command, CommandStart

from bot.handlers import dm_menu, dm_shop
from bot.middlewares.base import is_group

router = Router()


@router.message(CommandStart())
async def cmd_start(
    message: types.Message,
    _: Callable[..., str],
    staff_role: str | None = None,
    _raw: Callable[..., str] | None = None,
) -> None:
    """Handle /start."""
    if is_group(message.chat):
        await message.answer(_("cmd_start"))
    elif staff_role is not None:
        await message.answer(
            _("dm_menu_welcome"),
            reply_markup=dm_menu.build_main_menu(_raw or _),
        )
    else:
        await dm_shop.answer_shop(message, _, _raw or _)


@router.message(Command("help"))
async def cmd_help(
    message: types.Message,
    _: Callable[..., str],
    staff_role: str | None = None,
    _raw: Callable[..., str] | None = None,
) -> None:
    """Handle /help — group variant lists moderation commands."""
    if is_group(message.chat):
        await message.answer(_("cmd_help_group"))
    elif staff_role is not None:
        await message.answer(_("cmd_help_private"))
    else:
        await dm_shop.answer_shop_help(message, _, _raw or _)


@router.message(Command("info"))
async def cmd_info(
    message: types.Message,
    _: Callable[..., str],
    _raw: Callable[..., str] | None = None,
) -> None:
    """Handle /info — bot overview, commands and scam-safety rules (everyone)."""
    await message.answer(_("cmd_info"))
