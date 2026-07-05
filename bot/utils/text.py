"""Text helpers: HTML-safe welcome rendering, mentions, truncation.

Приветствие рендерится в режиме HTML. Значения из пользовательских данных
(имя новичка, username) — недоверенные, поэтому экранируются перед подстановкой,
иначе `<`, `&`, `"` в имени сломают разметку или дадут инъекцию.
"""

from __future__ import annotations

import html

from bot.constants import DEFAULT_WELCOME_TEXT, MAX_MESSAGE_LENGTH


def escape_html(text: str) -> str:
    """Escape characters special to Telegram HTML parse mode."""
    return html.escape(text or "", quote=False)


def build_mention(user_id: int, name: str) -> str:
    """Build an HTML inline mention link with an escaped display name."""
    safe_name = escape_html(name) or "user"
    return f'<a href="tg://user?id={user_id}">{safe_name}</a>'


def render_welcome(
    template: str | None,
    *,
    first_name: str,
    user_id: int,
    username: str | None,
    chat_title: str,
    members_count: int,
) -> str:
    """Render a welcome template with placeholders, HTML-escaping user data.

    Placeholders: ``{first_name} {mention} {username} {chat_title}
    {members_count}``. Falls back to the default template when unset/blank.
    """
    text = template if (template and template.strip()) else DEFAULT_WELCOME_TEXT

    replacements = {
        "{first_name}": escape_html(first_name),
        "{mention}": build_mention(user_id, first_name),
        "{username}": (
            ("@" + escape_html(username)) if username else escape_html(first_name)
        ),
        "{chat_title}": escape_html(chat_title),
        "{members_count}": str(members_count),
    }
    for placeholder, value in replacements.items():
        text = text.replace(placeholder, value)
    return truncate_text(text)


def truncate_text(text: str, max_length: int = MAX_MESSAGE_LENGTH) -> str:
    """Truncate to Telegram's message length limit."""
    if len(text) > max_length:
        return text[: max_length - 1] + "…"
    return text


def format_duration(seconds: int | None) -> str:
    """Render seconds as a compact ``1d 2h 30m`` string (language-neutral)."""
    if not seconds or seconds <= 0:
        return "0m"
    parts: list[str] = []
    for label, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if seconds >= size:
            value, seconds = divmod(seconds, size)
            parts.append(f"{value}{label}")
    return " ".join(parts)
