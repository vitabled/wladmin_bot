"""Text utilities for message formatting and parsing."""

from typing import Optional


DEFAULT_WELCOME = """Welcome to {chat_title}! 👋

Hello {first_name}! We're glad to have you here.

Rules:
1. Be respectful
2. No spam
3. No advertisements

Enjoy!"""


def format_welcome(
    text: Optional[str],
    first_name: str,
    mention: str,
    username: Optional[str],
    chat_title: str,
    members_count: int,
) -> str:
    """Format welcome message with placeholders."""
    if not text:
        text = DEFAULT_WELCOME

    text = text.replace("{first_name}", first_name)
    text = text.replace("{mention}", mention)
    text = text.replace("{username}", username or "unknown")
    text = text.replace("{chat_title}", chat_title)
    text = text.replace("{members_count}", str(members_count))

    return text


def truncate_text(text: str, max_length: int = 4096) -> str:
    """Truncate text to Telegram message limit."""
    if len(text) > max_length:
        return text[: max_length - 3] + "..."
    return text


def escape_html(text: str) -> str:
    """Escape HTML special characters."""
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text
