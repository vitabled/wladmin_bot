"""Pure matching logic for custom triggers / auto-replies (Phase 3).

Совпадение считается по нормализованному тексту (нижний регистр, схлопнутые
пробелы). Регулярки намеренно не поддерживаются — чтобы админ случайно не
задал катастрофический бэктрекинг (ReDoS); типы: contains / exact / starts.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from bot.constants import TRIGGER_CONTAINS, TRIGGER_EXACT, TRIGGER_STARTS


class TriggerService:
    """Stateless trigger matching over a chat's configured trigger list."""

    @staticmethod
    def normalize(text: str) -> str:
        """Lowercase and collapse whitespace for stable comparison."""
        return " ".join((text or "").lower().split())

    @classmethod
    def matches(cls, text: str, pattern: str, match_type: str) -> bool:
        """True when ``text`` matches ``pattern`` under ``match_type``."""
        normalized = cls.normalize(text)
        needle = cls.normalize(pattern)
        if not normalized or not needle:
            return False
        if match_type == TRIGGER_EXACT:
            return normalized == needle
        if match_type == TRIGGER_STARTS:
            return normalized.startswith(needle)
        return needle in normalized  # TRIGGER_CONTAINS (default)

    @classmethod
    def find_reply(cls, text: str, triggers: Iterable[Mapping[str, str]]) -> str | None:
        """Return the reply of the first matching trigger, else ``None``."""
        for trg in triggers:
            if cls.matches(
                text,
                trg.get("pattern", ""),
                trg.get("match_type", TRIGGER_CONTAINS),
            ):
                return trg.get("reply_text") or None
        return None
