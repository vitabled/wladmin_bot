"""Public private-chat storefront for users without a staff role."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

from aiogram import F, Router, types
from aiogram.filters import BaseFilter
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.filters.chat_type import IsPrivate
from bot.filters.staff import IsStaff

router = Router()

_PREFIX: Final = "shop"
_ADMIN_COMMANDS: Final = (
    "menu",
    "settings",
    "stats",
    "top",
    "schedule",
    "schedules",
    "unschedule",
    "addtowl",
    "welcome",
    "captcha",
    "setwelcome",
    "setcaptcha",
    "setwarnlimit",
    "setwarnaction",
    "antispam",
    "antiflood",
    "newbie",
    "addstop",
    "delstop",
    "stopwords",
    "addtrigger",
    "deltrigger",
    "triggers",
    "ban",
    "unban",
    "kick",
    "mute",
    "unmute",
    "warn",
    "unwarn",
    "warns",
    "fcreate",
    "fjoin",
    "fleave",
    "finfo",
    "fban",
    "funban",
)


class _Command(BaseFilter):
    """Match ``/name`` (optionally ``/name@bot``) from the message text.

    aiogram's built-in ``Command`` filter requires ``bot`` in the middleware
    data, but the storefront also runs on lightweight dispatch paths (and in
    its tests) that do not inject ``bot``. This matcher needs only the message.
    """

    def __init__(self, *names: str) -> None:
        self.names = {name.lower() for name in names}

    async def __call__(self, message: types.Message) -> bool:
        text = (message.text or "").lstrip()
        if not text.startswith("/"):
            return False
        token = text[1:].split(maxsplit=1)[0].split("@", 1)[0].lower()
        return token in self.names


def build_shop_menu(_raw: Callable[..., str]) -> types.InlineKeyboardMarkup:
    """Build the four-action public services keyboard."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text=_raw("shop_pin"),
        callback_data=f"{_PREFIX}:pin",
    )
    builder.button(
        text=_raw("shop_early"),
        callback_data=f"{_PREFIX}:early",
    )
    builder.button(
        text=_raw("shop_orders"),
        callback_data=f"{_PREFIX}:orders",
    )
    builder.button(
        text=_raw("shop_how"),
        callback_data=f"{_PREFIX}:help",
    )
    builder.adjust(1)
    return builder.as_markup()


async def answer_shop(
    message: types.Message,
    _: Callable[..., str],
    _raw: Callable[..., str],
) -> None:
    """Show the public storefront."""
    await message.answer(_("shop_title"), reply_markup=build_shop_menu(_raw))


async def answer_shop_help(
    message: types.Message,
    _: Callable[..., str],
    _raw: Callable[..., str],
) -> None:
    """Explain the future paid-service flow."""
    await message.answer(_("shop_help"), reply_markup=build_shop_menu(_raw))


@router.message(IsPrivate(), ~IsStaff(), _Command("start"))
@router.message(IsPrivate(), ~IsStaff(), _Command("services"))
async def dm_shop_start(message: types.Message, **data: Any) -> None:
    """Show the storefront to a nonstaff private-chat user."""
    await answer_shop(message, data["_"], data["_raw"])


@router.message(IsPrivate(), ~IsStaff(), _Command("help"))
async def dm_shop_help(message: types.Message, **data: Any) -> None:
    """Show public service help to a nonstaff private-chat user."""
    await answer_shop_help(message, data["_"], data["_raw"])


@router.message(IsPrivate(), ~IsStaff(), _Command(*_ADMIN_COMMANDS))
async def deny_admin_command(message: types.Message, **data: Any) -> None:
    """Stop private admin commands before downstream admin routers."""
    await message.answer(data["_"]("dm_staff_command_denied"))


@router.callback_query(
    F.data.in_(
        {
            f"{_PREFIX}:pin",
            f"{_PREFIX}:early",
            f"{_PREFIX}:orders",
        }
    ),
    ~IsStaff(),
)
async def on_shop_preparing(callback: types.CallbackQuery, **data: Any) -> None:
    """Acknowledge service actions until ordering is implemented."""
    await callback.answer(data["_"]("shop_preparing"), show_alert=True)


@router.callback_query(F.data == f"{_PREFIX}:help", ~IsStaff())
async def on_shop_help(callback: types.CallbackQuery, **data: Any) -> None:
    """Show public service help from the storefront keyboard."""
    if callback.message is not None:
        await callback.message.edit_text(
            data["_"]("shop_help"),
            reply_markup=build_shop_menu(data["_raw"]),
        )
    await callback.answer()


@router.callback_query(F.data.startswith(f"{_PREFIX}:"), ~IsStaff())
async def on_shop_unknown(callback: types.CallbackQuery, **data: Any) -> None:
    """Politely acknowledge stale or unknown storefront callbacks."""
    await callback.answer(data["_"]("shop_unknown"), show_alert=True)
