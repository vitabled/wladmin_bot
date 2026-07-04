import re
from typing import Optional
from urllib.parse import urlparse


class AntispamService:
    """Pure business logic for antispam detection."""

    @staticmethod
    def has_link(text: str) -> bool:
        """Detect URLs, t.me links, and Telegram channel mentions."""
        if not text:
            return False

        url_pattern = r"https?://|t\.me/|@[a-zA-Z0-9_]{5,}"
        return bool(re.search(url_pattern, text))

    @staticmethod
    def has_forward(forward_origin) -> bool:
        """Check if message is forwarded."""
        return forward_origin is not None

    @staticmethod
    def _normalize_text(text: str) -> str:
        """Normalize text for stopword matching (basic approach)."""
        text = text.lower()
        text = re.sub(r"[^\w\s]", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    @staticmethod
    def has_stopword(text: str, stopwords: list[str]) -> Optional[str]:
        """Check if text contains any stopword, return first match."""
        if not text or not stopwords:
            return None

        normalized = AntispamService._normalize_text(text)
        normalized_words = set(normalized.split())

        for stopword in stopwords:
            normalized_stopword = AntispamService._normalize_text(stopword)
            if normalized_stopword in normalized_words:
                return stopword

        return None

    @classmethod
    def check_message(
        cls,
        text: str,
        forward_origin: Optional[object] = None,
        stopwords: Optional[list[str]] = None,
        filter_links: bool = False,
        filter_forwards: bool = False,
        filter_stopwords: bool = False,
    ) -> tuple[bool, Optional[str]]:
        """
        Check message against enabled filters.

        Returns:
            (is_spam, reason)
        """
        if filter_links and cls.has_link(text):
            return True, "link"

        if filter_forwards and cls.has_forward(forward_origin):
            return True, "forward"

        if filter_stopwords:
            matched = cls.has_stopword(text, stopwords or [])
            if matched:
                return True, f"stopword:{matched}"

        return False, None
