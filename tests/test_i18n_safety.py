"""Regression guards for premium-emoji / HTML-safety work.

* Every i18n string must not contain raw ``<...>`` outside the Telegram HTML
  tags we explicitly allow — a stray ``<telegram-id>`` in a usage string made
  ``/scam`` crash with ``Bad Request: can't parse entities`` under the global
  parse_mode="HTML" (that exact failure shipped once; this file locks it out).
* ``_()`` decorates premium emoji for EVERY language (owner has Premium), so an
  en client sees the same <tg-emoji> entities as a ru client.
* ``render_welcome`` decorates the template so welcome/trigger texts with plain
  mapped glyphs (⚙️ ✅ …) turn into premium emoji on send.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from bot.emoji import IDS, decorate
from bot.i18n.loader import get_i18n
from bot.utils.text import render_welcome

_I18N_DIR = Path(__file__).resolve().parents[1] / "bot" / "i18n"

# Telegram HTML parse mode: tags the bot may intentionally emit.
_ALLOWED_TAG = re.compile(
    r"</?(?:b|strong|i|em|u|ins|s|strike|del|a|code|pre|tg-emoji|blockquote|span)\b[^>]*>",
    re.IGNORECASE,
)


def _dangerous_angles(text: str) -> list[str]:
    """Return every raw <...> fragment that is NOT an allowed HTML tag."""
    return [m.group(0) for m in re.finditer(r"<([^<>]*)>", text) if not _ALLOWED_TAG.fullmatch(m.group(0))]


def _load(lang: str) -> dict:
    with open(_I18N_DIR / f"{lang}.json", encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- #
# 1. i18n strings are HTML-safe (no stray angle brackets)
# --------------------------------------------------------------------------- #
def _check_lang(lang: str) -> None:
    data = _load(lang)
    for key, value in data.items():
        if not isinstance(value, str):
            continue
        bad = _dangerous_angles(value)
        assert not bad, f"{lang}.{key} has raw HTML: {bad}"


def test_ru_strings_have_no_stray_angle_brackets():
    _check_lang("ru")


def test_en_strings_have_no_stray_angle_brackets():
    _check_lang("en")


def test_usage_strings_still_readable_after_escaping():
    """&lt;...&gt; must render as <...> for the user, not as literal entities."""
    no_target = get_i18n().get("scam_no_target", "ru")
    assert "&lt;telegram-id&gt;" in no_target
    assert "telegram-id" in no_target
    usage = get_i18n().get("antiflood_usage", "en")
    assert "&lt;limit&gt;" in usage


# --------------------------------------------------------------------------- #
# 2. decorate applies for every language (not only ru)
# --------------------------------------------------------------------------- #
def test_decorate_applies_for_en():
    text = get_i18n().get("scam_ok", "en")  # "✅ Everything looks fine…"
    decorated = decorate(text) or text
    assert '<tg-emoji emoji-id="5776375003280838798">' in decorated
    assert "✅" in decorated  # glyph stays as fallback


def test_i18n_middleware_decorates_for_en():
    """The middleware's _() must wrap mapped glyphs for lang=en too."""
    from types import SimpleNamespace

    from bot.middlewares.i18n import I18nMiddleware

    i18n = I18nMiddleware()
    user = SimpleNamespace(language_code="en")
    event = SimpleNamespace(from_user=user)
    captured: dict = {}

    async def handler(event, data):
        captured.update(data)
        return None

    import asyncio

    asyncio.run(i18n(handler, event, {"event_user": user}))

    text = captured["_"]("scam_ok")
    assert '<tg-emoji emoji-id="5776375003280838798">' in text


# --------------------------------------------------------------------------- #
# 3. render_welcome decorates the template
# --------------------------------------------------------------------------- #
def test_render_welcome_decorates_mapped_glyphs():
    text = render_welcome(
        "⚙️ Привет, {first_name}!",
        first_name="Иван",
        user_id=1,
        username=None,
        chat_title="Чат",
        members_count=5,
    )
    assert '<tg-emoji emoji-id="5877260593903177342">' in text
    assert "Привет, Иван" in text


def test_render_welcome_leaves_existing_tags_alone():
    """An operator's own <tg-emoji> must not be double-decorated or mangled."""
    text = render_welcome(
        '<tg-emoji emoji-id="5877260593903177342">⚙️</tg-emoji> {mention}',
        first_name="Иван",
        user_id=1,
        username=None,
        chat_title="Чат",
        members_count=5,
    )
    assert text.count("<tg-emoji") == 1
    assert 'emoji-id="5877260593903177342"' in text


def test_all_ids_have_emoji_entries():
    """Table sanity: every premium ID maps to a real emoji glyph."""
    for glyph in IDS:
        assert glyph, "empty glyph in IDS"
