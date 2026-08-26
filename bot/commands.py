"""Register the Telegram command menu (the ☰ button) on startup.

Без ``set_my_commands`` клиент Telegram не показывает список команд и
автоподсказки — бот выглядит «пустым». Здесь регистрируются два набора,
локализованные (ru/en):

* ``_ALL_USERS`` (/start /help /info /scam) — для личных чатов (только
  разрешённые пользователи, см. ``allowed_dm_ids``) и всех участников групп
  (``BotCommandScopeAllGroupChats``);
* ``_ADMIN`` (весь набор модерации + /addtowl, поверх общего набора) — для
  администраторов групп (``BotCommandScopeAllChatAdministrators``).

При включённом DM-локдауне (``ALLOWED_DM_IDS`` не пуст) глобальный скоуп
личных чатов получает ПУСТОЙ список команд, а ``_ALL_USERS`` выдаётся каждому
разрешённому пользователю отдельным ``BotCommandScopeChat``.

Админский список содержит и общие команды: семантика слияния скоупов у
Telegram менялась, и админ не должен терять /scam или /info из-за того, что
более специфичный скоуп «перекрыл» групповой.
"""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
)

logger = logging.getLogger(__name__)

# command -> {lang: description}
# /start не показываем обычным пользователям (остаётся рабочим по прямому
# вводу), поэтому его нет в _ALL_USERS; админам он доступен через _ADMIN_ONLY.
_ALL_USERS: list[tuple[str, dict[str, str]]] = [
    ("help", {"en": "Command help", "ru": "Справка по командам"}),
    ("info", {"en": "Bot info & safety rules", "ru": "Инфо и правила безопасности"}),
    ("scam", {"en": "Check a seller for scam risk", "ru": "Проверить продавца на скам"}),
]

_ADMIN_ONLY: list[tuple[str, dict[str, str]]] = [
    ("start", {"en": "About the bot", "ru": "О боте"}),
    ("addtowl", {"en": "Whitelist a verified seller", "ru": "В белый список продавцов"}),
    ("settings", {"en": "Show current settings", "ru": "Текущие настройки"}),
    ("menu", {"en": "Settings menu (buttons)", "ru": "Меню настроек (кнопки)"}),
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
    ("schedules", {"en": "Scheduled posts", "ru": "Отложенные посты"}),
    ("finfo", {"en": "Federation info", "ru": "О федерации"}),
]

# Admin menu = everything a regular user sees + admin-only commands.
_ADMIN: list[tuple[str, dict[str, str]]] = _ALL_USERS + _ADMIN_ONLY

_LANGS = ("en", "ru")


def _commands(spec: list[tuple[str, dict[str, str]]], lang: str) -> list[BotCommand]:
    return [
        BotCommand(command=name, description=desc.get(lang, desc["en"]))
        for name, desc in spec
    ]


async def setup_bot_commands(
    bot: Bot, allowed_dm_ids: tuple[int, ...] = ()
) -> None:
    """Populate the ☰ menu per role scope (best-effort, non-fatal).

    Groups: every member sees ``_ALL_USERS``; administrators additionally see
    ``_ADMIN`` (full moderation + /addtowl). Private chats: when
    ``allowed_dm_ids`` is set, the global private scope gets an EMPTY list
    (non-allowed users see no menu) and each allowed user gets ``_ALL_USERS``
    via a per-chat scope; when it is empty, everyone sees ``_ALL_USERS``.
    """
    group_scope = BotCommandScopeAllGroupChats()
    admin_scope = BotCommandScopeAllChatAdministrators()
    private_scope = BotCommandScopeAllPrivateChats()
    try:
        # Groups and admins: unchanged regardless of the DM allowlist.
        for lang in _LANGS:
            await bot.set_my_commands(
                _commands(_ALL_USERS, lang), scope=group_scope, language_code=lang
            )
            await bot.set_my_commands(
                _commands(_ADMIN, lang), scope=admin_scope, language_code=lang
            )
        # Language-agnostic fallback so unmatched locales still see a menu.
        await bot.set_my_commands(_commands(_ALL_USERS, "en"), scope=group_scope)
        await bot.set_my_commands(_commands(_ADMIN, "en"), scope=admin_scope)

        if allowed_dm_ids:
            # DM lockdown: hide the menu from everyone by default, then expose
            # _ALL_USERS only to the allowlisted users via per-chat scopes.
            for lang in _LANGS:
                await bot.set_my_commands([], scope=private_scope, language_code=lang)
                for user_id in allowed_dm_ids:
                    await bot.set_my_commands(
                        _commands(_ALL_USERS, lang),
                        scope=BotCommandScopeChat(chat_id=user_id),
                        language_code=lang,
                    )
            await bot.set_my_commands([], scope=private_scope)
            for user_id in allowed_dm_ids:
                await bot.set_my_commands(
                    _commands(_ALL_USERS, "en"),
                    scope=BotCommandScopeChat(chat_id=user_id),
                )
        else:
            # Legacy: every private chat sees _ALL_USERS.
            for lang in _LANGS:
                await bot.set_my_commands(
                    _commands(_ALL_USERS, lang), scope=private_scope, language_code=lang
                )
            await bot.set_my_commands(_commands(_ALL_USERS, "en"), scope=private_scope)

        logger.info("bot_commands.set")
    except Exception:
        logger.warning("bot_commands.failed", exc_info=True)
