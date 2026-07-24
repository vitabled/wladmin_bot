"""Pure business logic for anti-flood and newbie media restrictions (Phase 2).

Состояние (счётчики окна, отметка «новичка») живёт в Redis; здесь — только
чистые предикаты, тестируемые без aiogram и без внешних зависимостей.
"""

from __future__ import annotations

from bot.constants import NEWBIE_RESTRICTED_CONTENT


class AntifloodService:
    """Stateless predicates for per-message flood/newbie checks."""

    @staticmethod
    def is_flood(count: int, limit: int) -> bool:
        """True once ``count`` messages within the window reaches ``limit``.

        ``limit <= 0`` disables the check (never flood), guarding against a
        misconfigured/zero limit acting on the very first message.
        """
        if limit <= 0:
            return False
        return count >= limit

    @staticmethod
    def is_restricted_media(
        content_type: str,
        restricted: frozenset[str] = NEWBIE_RESTRICTED_CONTENT,
    ) -> bool:
        """True when a message's content type is media a newbie may not send."""
        return content_type in restricted
