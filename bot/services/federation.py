"""Pure helpers for federation names (Phase 8)."""

from __future__ import annotations

import re

from bot.constants import FED_NAME_MAX, FED_NAME_MIN

# Letters/digits/underscore/dash, plus spaces collapsed — a human-readable slug.
_ALLOWED = re.compile(r"[^\w\- ]", re.UNICODE)


class FederationService:
    """Stateless validation/normalization of federation names."""

    @staticmethod
    def normalize_name(name: str) -> str:
        """Trim, collapse inner whitespace, drop disallowed characters."""
        cleaned = _ALLOWED.sub("", name or "")
        return " ".join(cleaned.split())

    @classmethod
    def is_valid_name(cls, name: str) -> bool:
        """True when the normalized name is within the allowed length range."""
        normalized = cls.normalize_name(name)
        return FED_NAME_MIN <= len(normalized) <= FED_NAME_MAX
