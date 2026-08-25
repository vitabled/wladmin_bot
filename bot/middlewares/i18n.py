"""i18n middleware: pick locale and inject a ``_`` translator into data."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from bot.constants import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES
from bot.i18n.loader import LocalizationManager, get_i18n
from bot.middlewares.base import extract_user, is_group


class I18nMiddleware(BaseMiddleware):
    """Resolve locale (chat language for groups, user language for PM).

    Injects ``data['lang']`` and ``data['_']`` — a callable
    ``_(key, **kwargs) -> str``.
    """

    def __init__(self, i18n: LocalizationManager | None = None) -> None:
        self.i18n = i18n or get_i18n()

    def _resolve_lang(self, event: TelegramObject, data: dict[str, Any]) -> str:
        chat = data.get("event_chat")
        if is_group(chat):
            chat_lang = data.get("chat_language")
            if chat_lang in SUPPORTED_LANGUAGES:
                return chat_lang
            return DEFAULT_LANGUAGE
        user = extract_user(event)
        code = (getattr(user, "language_code", None) or "")[:2].lower()
        return code if code in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        lang = self._resolve_lang(event, data)
        data["lang"] = lang

        def _(key: str, **kwargs: Any) -> str:
            text = self.i18n.get(key, lang, **kwargs)
            # Premium-эмодзи: <tg-emoji> теги (только если текст содержит глифы
            # из таблицы; plain-эмодзи без премиум-варианта остаются как есть).
            # Декорируем для ВСЕХ языков — владелец бота с Premium, глифы из
            # ai-router-набора одинаковы в ru/en.
            from bot.emoji import decorate

            return decorate(text) or text

        def _raw(key: str, **kwargs: Any) -> str:
            """Перевод без premium-декорации — для кнопок/инлайн-текста,
            где Telegram НЕ парсит HTML и теги <tg-emoji> показались бы сырыми."""
            return self.i18n.get(key, lang, **kwargs)

        data["_"] = _
        data["_raw"] = _raw
        return await handler(event, data)
