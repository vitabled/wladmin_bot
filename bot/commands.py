"""Register the Telegram command menu (the ☰ button) on startup.

Без ``set_my_commands`` клиент Telegram не показывает список команд и
автоподсказки — бот выглядит «пустым». Здесь регистрируется базовый набор для
личных чатов и полный набор для админов групп, локализованный (ru/en).
"""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllPrivateChats,
)

logger = logging.getLogger(__name__)

# command -> {lang: description}
_PRIVATE: list[tuple[str, dict[str, str]]] = [
    ("start", {"en": "About the bot", "ru": "О боте"}),
    ("help", {"en": "Command help", "ru": "Справка по командам"}),
]

_ADMIN: list[tuple[str, dict[str, str]]] = [
    ("help", {"en": "Command help", "ru": "Справка"}),
    ("settings", {"en": "Show current settings", "ru": "Текущие настройки"}),
    ("ban", {"en": "Ban a user", "ru": "Забанить"}),
    ("unban", {"en": "Unban a user", "ru": "Разбанить"}),
    ("kick", {"en": "Kick a user", "ru": "Выгнать"}),
    ("mute", {"en": "Mute a user", "ru": "Замутить"}),
    ("unmute", {"en": "Unmute a user", "ru": "Размутить"}),
    ("warn", {"en": "Warn a user", "ru": "Предупредить"}),
    ("unwarn", {"en": "Remove last warn", "ru": "Снять предупреждение"}),
    ("warns", {"en": "Show warnings", "ru": "Показать предупреждения"}),
    ("stats", {"en": "My activity stats", "ru": "Моя статистика"}),
    ("top", {"en": "Most active users", "ru": "Самые активные"}),
    ("welcome", {"en": "Toggle welcome", "ru": "Приветствие вкл/выкл"}),
    ("captcha", {"en": "Toggle captcha", "ru": "Капча вкл/выкл"}),
    ("antispam", {"en": "Configure antispam", "ru": "Настроить антиспам"}),
    ("antiflood", {"en": "Configure anti-flood", "ru": "Настроить антифлуд"}),
    ("newbie", {"en": "Restrict newbie media", "ru": "Медиа новичков"}),
    ("stopwords", {"en": "List stopwords", "ru": "Стоп-слова"}),
]

_LANGS = ("en", "ru")


def _commands(spec: list[tuple[str, dict[str, str]]], lang: str) -> list[BotCommand]:
    return [
        BotCommand(command=name, description=desc.get(lang, desc["en"]))
        for name, desc in spec
    ]


async def setup_bot_commands(bot: Bot) -> None:
    """Populate the ☰ menu: basics in private chats, full set for group admins.

    Best-effort — a transient Telegram error here must not block startup.
    """
    private_scope = BotCommandScopeAllPrivateChats()
    admin_scope = BotCommandScopeAllChatAdministrators()
    try:
        for lang in _LANGS:
            await bot.set_my_commands(
                _commands(_PRIVATE, lang), scope=private_scope, language_code=lang
            )
            await bot.set_my_commands(
                _commands(_ADMIN, lang), scope=admin_scope, language_code=lang
            )
        # Language-agnostic fallback so unmatched locales still see a menu.
        await bot.set_my_commands(_commands(_PRIVATE, "en"), scope=private_scope)
        await bot.set_my_commands(_commands(_ADMIN, "en"), scope=admin_scope)
        logger.info("bot_commands.set")
    except Exception:
        logger.warning("bot_commands.failed", exc_info=True)
