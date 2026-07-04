"""Moderation command handlers."""

from aiogram import Router, types
from aiogram.filters import Command

from bot.filters.is_admin import IsAdmin

router = Router()
router.message.filter(IsAdmin())


@router.message(Command("ban"))
async def cmd_ban(message: types.Message):
    """Handle /ban command."""
    text = "🚫 Ban command handler (not fully implemented)"
    await message.answer(text)


@router.message(Command("unban"))
async def cmd_unban(message: types.Message):
    """Handle /unban command."""
    text = "🔓 Unban command handler (not fully implemented)"
    await message.answer(text)


@router.message(Command("kick"))
async def cmd_kick(message: types.Message):
    """Handle /kick command."""
    text = "👢 Kick command handler (not fully implemented)"
    await message.answer(text)


@router.message(Command("mute"))
async def cmd_mute(message: types.Message):
    """Handle /mute command."""
    text = "🔇 Mute command handler (not fully implemented)"
    await message.answer(text)


@router.message(Command("unmute"))
async def cmd_unmute(message: types.Message):
    """Handle /unmute command."""
    text = "🔊 Unmute command handler (not fully implemented)"
    await message.answer(text)


@router.message(Command("warn"))
async def cmd_warn(message: types.Message):
    """Handle /warn command."""
    text = "⚠️ Warn command handler (not fully implemented)"
    await message.answer(text)


@router.message(Command("unwarn"))
async def cmd_unwarn(message: types.Message):
    """Handle /unwarn command."""
    text = "✅ Unwarn command handler (not fully implemented)"
    await message.answer(text)


@router.message(Command("warns"))
async def cmd_warns(message: types.Message):
    """Handle /warns command."""
    text = "📋 Warns command handler (not fully implemented)"
    await message.answer(text)
