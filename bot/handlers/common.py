"""Common handlers: /start, /help (localized, work in PM and groups)."""

from __future__ import annotations

from collections.abc import Callable

from aiogram import Router, types
from aiogram.filters import Command, CommandStart

from bot.middlewares.base import is_group

router = Router()


@router.message(CommandStart())
async def cmd_start(message: types.Message, _: Callable[..., str]) -> None:
    """Handle /start."""
    await message.answer(_("cmd_start"))


@router.message(Command("help"))
async def cmd_help(message: types.Message, _: Callable[..., str]) -> None:
    """Handle /help — group variant lists moderation commands."""
    if is_group(message.chat):
        await message.answer(_("cmd_help_group"))
    else:
        await message.answer(_("cmd_help_private"))


@router.message(Command("info"))
async def cmd_info(message: types.Message, _: Callable[..., str]) -> None:
    """Handle /info — bot overview, commands and scam-safety rules (everyone)."""
    await message.answer(_("cmd_info"))
