"""Shared constants and string enums for chat settings.

Настройки в БД хранятся строками (расширяемость без миграций enum-типов),
здесь — канонические наборы допустимых значений и дефолты.
"""

from __future__ import annotations

from typing import Final

# --- Captcha -------------------------------------------------------------
CAPTCHA_BUTTON: Final = "button"
CAPTCHA_MATH: Final = "math"
CAPTCHA_EMOJI: Final = "emoji"
CAPTCHA_TYPES: Final = frozenset({CAPTCHA_BUTTON, CAPTCHA_MATH, CAPTCHA_EMOJI})

# --- Actions -------------------------------------------------------------
ACTION_DELETE: Final = "delete"
ACTION_WARN: Final = "warn"
ACTION_MUTE: Final = "mute"
ACTION_KICK: Final = "kick"
ACTION_BAN: Final = "ban"

CAPTCHA_FAIL_ACTIONS: Final = frozenset({ACTION_KICK, ACTION_BAN, ACTION_MUTE})
WARN_ACTIONS: Final = frozenset({ACTION_MUTE, ACTION_KICK, ACTION_BAN})
ANTISPAM_ACTIONS: Final = frozenset(
    {ACTION_DELETE, ACTION_WARN, ACTION_MUTE, ACTION_BAN}
)

# --- Antispam reasons ----------------------------------------------------
REASON_LINK: Final = "link"
REASON_FORWARD: Final = "forward"
REASON_STOPWORD: Final = "stopword"

# --- Limits (Telegram / sanity) -----------------------------------------
MAX_MESSAGE_LENGTH: Final = 4096
MAX_WELCOME_LENGTH: Final = 4096
WARN_LIMIT_MIN: Final = 1
WARN_LIMIT_MAX: Final = 100
CAPTCHA_NUM_MIN: Final = 1
CAPTCHA_NUM_MAX: Final = 20
CAPTCHA_OPTIONS_COUNT: Final = 4

# Telegram minimum mute/ban duration is 30s; below that Telegram treats it as
# permanent, so we clamp temporary restrictions to this floor.
MIN_RESTRICT_SECONDS: Final = 30

# --- Anti-flood (Phase 2) -----------------------------------------------
ANTIFLOOD_ACTIONS: Final = frozenset({ACTION_MUTE, ACTION_KICK, ACTION_BAN})
ANTIFLOOD_LIMIT_MIN: Final = 2
ANTIFLOOD_LIMIT_MAX: Final = 100
ANTIFLOOD_WINDOW_MIN: Final = 1
ANTIFLOOD_WINDOW_MAX: Final = 3600
# How long (seconds) a flooder stays muted when antiflood_action == "mute".
ANTIFLOOD_MUTE_SECONDS: Final = 3600

# --- Newbie media restriction (Phase 2) ---------------------------------
NEWBIE_PERIOD_MIN: Final = 60
NEWBIE_PERIOD_MAX: Final = 604_800  # 7 days
# Content types a member on probation (recently joined) may not send.
NEWBIE_RESTRICTED_CONTENT: Final = frozenset(
    {
        "photo",
        "video",
        "animation",
        "document",
        "audio",
        "voice",
        "video_note",
        "sticker",
        "poll",
        "game",
        "dice",
        "contact",
        "location",
    }
)

# --- Triggers / auto-replies (Phase 3) ----------------------------------
TRIGGER_CONTAINS: Final = "contains"
TRIGGER_EXACT: Final = "exact"
TRIGGER_STARTS: Final = "starts"
TRIGGER_MATCH_TYPES: Final = frozenset(
    {TRIGGER_CONTAINS, TRIGGER_EXACT, TRIGGER_STARTS}
)
TRIGGER_MAX_PER_CHAT: Final = 100
TRIGGER_PATTERN_MAX: Final = 255
TRIGGER_REPLY_MAX: Final = 4096
# Separator between pattern and reply in /addtrigger.
TRIGGER_SEPARATOR: Final = "|"

# --- Statistics (Phase 4) -----------------------------------------------
TOP_DEFAULT: Final = 10
TOP_MAX: Final = 25

# --- Scheduled posting (Phase 5) ----------------------------------------
SCHEDULE_MAX_PER_CHAT: Final = 50
# Minimum recurring interval (seconds) — avoids hammering a chat.
SCHEDULE_MIN_INTERVAL: Final = 300
# How often the background worker scans for due posts (seconds).
SCHEDULER_TICK_SECONDS: Final = 60
# Preview length of a post's text in listings.
SCHEDULE_PREVIEW_LEN: Final = 50

# --- Federation (Phase 8) -----------------------------------------------
FED_NAME_MIN: Final = 3
FED_NAME_MAX: Final = 32

# --- Scam list / seller reputation (Phase 9) ----------------------------
SCAM_SOURCE_SCAM: Final = "scam"
SCAM_SOURCE_VERIFIED: Final = "verified"
SCAM_SOURCE_MANUAL: Final = "manual"
# A join younger than this (days) is a /scam risk factor.
SCAM_JOINED_RISK_DAYS: Final = 14

# --- Default welcome text placeholders ----------------------------------
DEFAULT_WELCOME_TEXT: Final = "{mention}, добро пожаловать в {chat_title}!"

# Supported languages (for i18n fallbacks and /setlang validation).
SUPPORTED_LANGUAGES: Final = ("ru", "en")
DEFAULT_LANGUAGE: Final = "ru"
