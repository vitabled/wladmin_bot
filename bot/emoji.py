"""Premium (custom) emoji for the bot interface — copied from ai-router.

Source of truth: /opt/ai-router/common/ai_router_common/bot_emoji.py (verified
ids from the dozaifut set, collected via /emoji by the operator). Every glyph
the admin-bot renders that has a verified premium variant is decorated with
<tg-emoji emoji-id="…">glyph</tg-emoji>; unmapped glyphs stay plain emoji, so
a half-filled table is a valid state. Only messages sent with parse_mode=HTML
render these tags — the i18n wrapper applies decorate() and callers that want
premium emoji must use parse_mode="HTML".
"""

from __future__ import annotations

import re

# custom_emoji_id is a numeric string (currently 19 digits); keep the bound loose
CUSTOM_EMOJI_ID_RE = re.compile(r"^\d{1,32}$")

_VS16 = "\ufe0f"


def _normalise(glyph: str) -> str:
    return glyph.replace(_VS16, "")


# glyph -> custom_emoji_id (verified from ai-router dozaifut set)
IDS: dict[str, str] = {
    "👤": "5771887475421090729",
    "🧠": "5864019342873598613",
    "💳": "5967548335542767952",
    "📊": "5877485980901971030",
    "⚙": "5877260593903177342",
    "🧹": "5845947563601041174",
    "❓": "5873121512445187130",
    "📍": "5944940516754853337",
    "🤔": "5924719252379537729",
    "🤖": "5931415565955503486",
    "🔒": "5832546462478635761",
    "🔓": "6034962180875490251",
    "✅": "5776375003280838798",
    "❌": "5778527486270770928",
    "📅": "5967412305338568701",
    "🎫": "5206461086806594098",
    "🎁": "6032937473162614352",
    "💎": "5963312935148195483",
    "🚀": "5452013034362925287",
    "🔥": "6008118472066732010",
    "💬": "5886666250158870040",
    "🖼": "5888799736508454231",
    "🔄": "5839200986022812209",
    "🏷": "5854776233950188167",
    "🎤": "5897554554894946515",
    "🔊": "5890997763331591703",
    "🎨": "5814690801665446789",
    "⭐": "5958376256788502078",
    "🧾": "5204242830687494041",
    "🔔": "5909201569898827582",
    "⏳": "5377413747398681693",
    "💸": "5415634090135153325",
    "🚫": "5872829476143894491",
    "⬅": "5875082500023258804",
    "⚡": "5843553939672274145",
    "⚠": "5881702736843511327",
    # 2026-08-25: расширение набора для max-admin-bot. ID собраны через
    # searchCustomEmoji/Animated Emoji и КАЖДЫЙ проверен через Bot API
    # (setMyDescription с <tg-emoji> → ok:True), alt документа совпадает с глифом.
    # 👋/🧩 — из наборов TgAndroidIcons / tgiosicons (t.me/addemoji/...) — там же
    # лежат базовые dozaifut-глифы (⚙ ✅ ❌ ⚠), стиль совпадает с ai-router.
    "👋": "5994750571041525522",
    "📚": "5373098009640836781",
    "🌐": "5879585266426973039",
    "🏆": "5422546251587527850",
    "🌊": "5386798809286189971",
    "🆕": "5886306834410640699",
    "🗓": "5413879192267805083",
    "📝": "5886330010054168711",
    "🔇": "5890838600433536921",
    "🧩": "5837069325034331827",
}


def _pattern(ids: dict[str, str]) -> re.Pattern[str] | None:
    keys = sorted({_normalise(k) for k in ids if k}, key=len, reverse=True)
    if not keys:
        return None
    return re.compile("|".join(re.escape(k) + _VS16 + "?" for k in keys))


# Split off markup so substitution only ever touches visible text. The tg-emoji
# branch comes first so an already-decorated span is skipped whole — decorating
# is therefore idempotent, and an operator's own <tg-emoji> is left alone.
_SEG_RE = re.compile(
    r"(<tg-emoji\b[^>]*>.*?</tg-emoji\s*>|<[^>]*>)",
    re.IGNORECASE | re.DOTALL,
)


def decorate(text: str | None, ids: dict[str, str] | None = None) -> str | None:
    """Wrap every mapped glyph in <tg-emoji>, leaving the glyph as fallback.

    Safe to apply twice, and safe on text that carries HTML: attributes and tag
    names are never rewritten. Requires parse_mode="HTML" on the send call.
    """
    ids = IDS if ids is None else ids
    if not text or not ids:
        return text
    pattern = _pattern(ids)
    if pattern is None:
        return text

    lookup = {_normalise(k): v for k, v in ids.items()}

    def _repl(m: re.Match[str]) -> str:
        glyph = m.group(0)
        emoji_id = lookup.get(_normalise(glyph))
        if not emoji_id:
            return glyph
        return f'<tg-emoji emoji-id="{emoji_id}">{glyph}</tg-emoji>'

    parts = _SEG_RE.split(text)
    for i in range(0, len(parts), 2):
        parts[i] = pattern.sub(_repl, parts[i])
    return "".join(parts)
