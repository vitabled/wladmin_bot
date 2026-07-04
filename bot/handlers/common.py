"""Common handlers: /start, /help."""

from aiogram import Router, types
from aiogram.filters import Command
from aiogram.filters.command import CommandStart

router = Router()


@router.message(CommandStart())
async def cmd_start(message: types.Message):
    """Handle /start command."""
    text = """👋 Welcome to Telegram Group Admin Bot!

I help manage your Telegram groups with:
• 📋 Moderation commands (/ban, /mute, /kick, /warn)
• 🤖 Captcha for new members
• 🚫 Antispam filters
• 📝 Custom welcome messages
• ⚙️ Flexible settings

Use /help to see all commands."""
    await message.answer(text)


@router.message(Command("help"))
async def cmd_help(message: types.Message):
    """Handle /help command."""
    if message.chat and message.chat.type == "private":
        text = """📚 Available Commands:

/start - Show welcome message
/help - Show this help

More commands in group settings."""
    else:
        text = """📚 Moderation Commands (admins only):

/ban [duration] [reason] - Ban user
/unban - Unban user
/kick - Remove user
/mute [duration] [reason] - Mute user
/unmute - Unmute user
/warn [reason] - Warn user
/unwarn - Remove last warning
/warns - Show user warnings
/settings - Show chat settings"""

    await message.answer(text)
