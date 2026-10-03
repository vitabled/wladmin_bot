"""DM main-menu (button interface) for private chats.

Replaces the plain command UX in PM with an inline tabbed main menu
mirroring the webapp (admin.whitelistmarket.lol):

* ``/start`` in a private chat → welcome text + main-menu keyboard;
* any plain (non-command) text in PM → the menu again (кнопочный интерфейс);
* the main menu has 3 tabs — Администрирование / Рейтинги / Рассылки — plus
  ℹ️ info and ❓ help buttons;
* «Администрирование» (``dm:tab:admin``) → groups list → per-group panel
  with moderation (ban/kick/mute/warn/unban/unmute/unwarn/warns), settings
  toggles, slow mode AND statistics (totals + top) — every action operating
  on the SELECTED group's ``chat_id``;
* «Рейтинги» (``dm:tab:ratings``) → seller check (``DmScam`` FSM),
  whitelist add/remove (``DmWl`` FSM) and the scam/WL list;
* «Рассылки» (``dm:tab:broadcast``) → group picker → forum-topic
  multi-select (``DmBroadcast`` FSM) → raw-text broadcast;
* while a DM FSM (``DmScam``/``DmAdmin``/``DmWl``/``DmSlowMode``/
  ``DmBroadcast``) is active, the next message is consumed by its handler;
  ``dm:menu`` always bails out (``state.clear()``).

Group chat behavior is untouched: every handler here is private-chat-scoped.
The router MUST be included before ``common.router`` so private ``/start``
and private text hit it first. The PrivateAccessMiddleware already gates DM
to the owner whitelist, so no extra user checks are needed here.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from aiogram import Bot, F, Router, types
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.constants import SCAM_SOURCE_SCAM, SCAM_SOURCE_VERIFIED, TOP_DEFAULT
from bot.db import crud
from bot.filters.chat_type import IsPrivate
from bot.handlers import actions
from bot.handlers.menu import _TOGGLE_FIELDS, build_menu
from bot.handlers.moderation import _reason_suffix, prepare_action
from bot.handlers.scam import build_scam_verdict, map_scam_error
from bot.services.broadcast import send_broadcast
from bot.services.slow_mode import PUNISH_ACTIONS
from bot.services.stats import StatsService
from bot.utils.targets import resolve_target
from bot.utils.text import (
    build_mention,
    escape_html,
    format_duration,
    punish_action_label,
    punish_duration_label,
)

logger = logging.getLogger(__name__)

router = Router()

_PREFIX = "dm"

# Longest warning text accepted from the owner (Telegram caption/message limit
# leaves no room for a wall of text in a chat notice anyway).
_SM_WARN_TEXT_MAX = 1024
WEBAPP_URL = "https://admin.whitelistmarket.lol"

# Groups list pagination: groups per page in the DM list.
_GROUPS_PAGE_SIZE = 8

# Max entries shown by the «Список скама/WL» screen.
_RT_LIST_LIMIT = 15

# Panel actions that wait for a target message (FSM) — everything else on the
# panel (settings/slow-mode) acts immediately on the callback. ``wl`` /
# ``wl_remove`` are kept for the legacy ``dm:a:*`` callbacks (old keyboards);
# the panel itself no longer shows them (whitelist moved to the Рейтинги tab).
_TARGET_ACTIONS = frozenset(
    {
        "ban",
        "kick",
        "mute",
        "warn",
        "unban",
        "unmute",
        "unwarn",
        "warns",
        "wl",
        "wl_remove",
    }
)
# Actions whose prompt mentions a duration example ("@user 2h причина").
_DURATION_ACTIONS = frozenset({"ban", "mute"})

# Per-action prepare_action flags, copied 1:1 from the cmd_* handlers in
# moderation.py: (allow_duration, protect_target, need_restrict).
_ACTION_FLAGS: dict[str, tuple[bool, bool, bool]] = {
    "ban": (True, True, True),
    "kick": (False, True, True),
    "mute": (True, True, True),
    "warn": (False, True, True),
    "unban": (False, False, True),
    "unmute": (False, False, True),
    "unwarn": (False, False, False),
    "warns": (False, False, False),
}

# Moderation panel buttons in display order: (i18n label key, action name).
# Scam / WL moved to the Рейтинги tab; stats / top live on the panel
# (Статистика lives inside Администрирование).
_PANEL_ACTIONS: list[tuple[str, str]] = [
    ("dm_panel_ban", "ban"),
    ("dm_panel_kick", "kick"),
    ("dm_panel_mute", "mute"),
    ("dm_panel_warn", "warn"),
    ("dm_panel_unban", "unban"),
    ("dm_panel_unmute", "unmute"),
    ("dm_panel_warns", "warns"),
    ("dm_panel_unwarn", "unwarn"),
]


class DmScam(StatesGroup):
    """FSM for the in-menu seller-check flow (DM only)."""

    awaiting_target = State()


class DmAdmin(StatesGroup):
    """FSM for per-group admin actions in the DM panel (target awaiting)."""

    awaiting_target = State()


class DmSlowMode(StatesGroup):
    """FSM for the per-group slow-mode config (``dm:sm:<chat_id>``).

    ``awaiting_config`` parses the ``вкл|выкл [hours] [hours]`` line;
    ``awaiting_topics`` is the topic list step (scope toggles + per-topic
    ⚙️ buttons) shown after «вкл» and from the entry screen;
    ``awaiting_topic_id`` accepts a hand-typed thread id;
    ``awaiting_topic_params`` parses per-topic intervals
    (``6 3`` | ``вкл 6 3`` | ``выкл`` | ``сброс``); and
    ``awaiting_sm_punish_text`` takes the warning text one topic shows to its
    own violators (the state carries chat_id + thread_id).
    """

    awaiting_config = State()
    awaiting_topics = State()
    awaiting_topic_id = State()
    awaiting_topic_params = State()
    awaiting_sm_punish_text = State()


class DmWl(StatesGroup):
    """FSM for whitelist add/remove from the Рейтинги tab (target awaiting)."""

    awaiting_target = State()


class DmBroadcast(StatesGroup):
    """FSM for the broadcast text (after topics were picked)."""

    awaiting_text = State()
    awaiting_topic_id = State()


def build_main_menu(_raw: Callable[..., str]) -> types.InlineKeyboardMarkup:
    """Build the tabbed main-menu keyboard.

    Three tab buttons (one per row) mirroring the webapp, then a bottom row
    with ℹ️ info and ❓ help. Button labels go through ``_raw`` — Telegram
    does NOT parse HTML in button text, so no premium-emoji decoration is
    applied. Glyphs are replaced by premium ``icon_custom_emoji_id`` icons.
    """
    builder = InlineKeyboardBuilder()
    builder.button(
        text=_raw("dm_menu_panel"),
        web_app=types.WebAppInfo(url=WEBAPP_URL),
        icon_custom_emoji_id="5879585266426973039",  # 🌐
    )
    builder.button(
        text=_raw("dm_tab_admin"),
        callback_data=f"{_PREFIX}:tab:admin",
        icon_custom_emoji_id="5877260593903177342",  # ⚙
    )
    builder.button(
        text=_raw("dm_tab_ratings"),
        callback_data=f"{_PREFIX}:tab:ratings",
        icon_custom_emoji_id="5451682961831257285",  # 🛡 (remnawave)
    )
    builder.button(
        text=_raw("dm_tab_broadcast"),
        callback_data=f"{_PREFIX}:tab:broadcast",
        icon_custom_emoji_id="5424818078833715060",  # 📣 (NewsEmoji)
    )
    builder.button(
        text=_raw("dm_menu_info"),
        callback_data=f"{_PREFIX}:info",
        icon_custom_emoji_id="5879785854284599288",  # ℹ
    )
    builder.button(
        text=_raw("dm_menu_help"),
        callback_data=f"{_PREFIX}:help",
        icon_custom_emoji_id="5873121512445187130",  # ❓
    )
    builder.adjust(1, 1, 1, 1, 2)
    return builder.as_markup()


# Premium custom-emoji icons for the per-group admin-panel buttons,
# keyed by the action name (see _PANEL_ACTIONS below).
_PANEL_ICONS: dict[str, str] = {
    "ban": "5875450995332353523",  # 🔨
    "kick": "5877341274863832725",  # 🚪 (analog for 👢 — no boot in packs)
    "mute": "5890838600433536921",  # 🔇
    "warn": "5881702736843511327",  # ⚠
    "unban": "5776375003280838798",  # ✅
    "unmute": "5897554554894946515",  # 🎤
    "warns": "5886330010054168711",  # 📝
    "unwarn": "5879896690210639947",  # 🗑 (analog for ➖)
}

# ◀ back arrow (premium), 🏠 home, ⚪ topic unchecked — reused across keyboards.
_ICON_BACK = "5877629862306385808"  # ◀
_ICON_HOME = "5967822972931542886"  # 🏠
_ICON_TOPIC_OFF = "5352618591961226857"  # ⚪ (whiteemojikwii)
_ICON_TOPIC_ON = "5776375003280838798"  # ✅
_ICON_SETTINGS = "5877260593903177342"  # ⚙


def _back_kb(_raw: Callable[..., str]) -> types.InlineKeyboardMarkup:
    """Keyboard with a single «◀ В меню» button (bail out of the flow)."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text=_raw("dm_menu_back"),
        callback_data=f"{_PREFIX}:menu",
        icon_custom_emoji_id=_ICON_BACK,  # ◀
    )
    builder.adjust(1)
    return builder.as_markup()


def _home_kb(_raw: Callable[..., str]) -> types.InlineKeyboardMarkup:
    """Keyboard with a single «🏠 В меню» button."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text=_raw("dm_menu_home"),
        callback_data=f"{_PREFIX}:menu",
        icon_custom_emoji_id=_ICON_HOME,  # 🏠
    )
    builder.adjust(1)
    return builder.as_markup()


def _panel_kb(
    _raw: Callable[..., str], chat_id: int
) -> types.InlineKeyboardMarkup:
    """Back keyboard for the per-group flows: panel + home."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text=_raw("dm_panel_back"),
        callback_data=f"{_PREFIX}:g:{chat_id}",
        icon_custom_emoji_id=_ICON_BACK,  # ◀
    )
    builder.button(
        text=_raw("dm_menu_home"),
        callback_data=f"{_PREFIX}:menu",
        icon_custom_emoji_id=_ICON_HOME,  # 🏠
    )
    builder.adjust(1)
    return builder.as_markup()


def _build_groups_kb(
    _raw: Callable[..., str],
    chats: list[Any],
    page: int,
    *,
    group_cb: str = "g",
    page_cb: str = "gp",
) -> types.InlineKeyboardMarkup:
    """Groups-list keyboard: one row per group, nav + home rows.

    ``group_cb`` / ``page_cb`` pick the callback family: ``g``/``gp`` for the
    admin tab (``dm:g:<id>`` / ``dm:gp:<page>``), ``st``/``stp`` for the
    stats tab and ``bc``/``bcp`` for the broadcast tab.
    """
    start = page * _GROUPS_PAGE_SIZE
    page_chats = chats[start : start + _GROUPS_PAGE_SIZE]
    rows = [
        [
            types.InlineKeyboardButton(
                text=(chat.title or "").strip() or str(chat.chat_id),
                callback_data=f"{_PREFIX}:{group_cb}:{chat.chat_id}",
            )
        ]
        for chat in page_chats
    ]
    last_page = max(0, (len(chats) - 1) // _GROUPS_PAGE_SIZE)
    if len(chats) > _GROUPS_PAGE_SIZE:
        nav: list[types.InlineKeyboardButton] = []
        if page > 0:
            nav.append(
                types.InlineKeyboardButton(
                    text=_raw("dm_groups_prev"),
                    callback_data=f"{_PREFIX}:{page_cb}:{page - 1}",
                    icon_custom_emoji_id=_ICON_BACK,  # ◀
                )
            )
        if page < last_page:
            nav.append(
                types.InlineKeyboardButton(
                    text=_raw("dm_groups_next"),
                    callback_data=f"{_PREFIX}:{page_cb}:{page + 1}",
                    # ▶ stays a plain glyph — no premium ▶ exists in the packs.
                )
            )
        rows.append(nav)
    rows.append(
        [
            types.InlineKeyboardButton(
                text=_raw("dm_menu_home"),
                callback_data=f"{_PREFIX}:menu",
                icon_custom_emoji_id=_ICON_HOME,  # 🏠
            )
        ]
    )
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _build_panel_kb(
    _raw: Callable[..., str], chat_id: int
) -> types.InlineKeyboardMarkup:
    """Per-group admin panel keyboard (7 rows of 2 buttons).

    Rows: ban|kick, mute|warn, unban|unmute, warns|unwarn, settings|slow
    mode, stats|top, groups|home. The trailing adjust sizes are
    intentionally wider than the button count — aiogram ignores leftover
    widths, so the exact layout stays 7×2.
    """
    builder = InlineKeyboardBuilder()
    for label_key, action in _PANEL_ACTIONS:
        builder.button(
            text=_raw(label_key),
            callback_data=f"{_PREFIX}:a:{action}:{chat_id}",
            icon_custom_emoji_id=_PANEL_ICONS[action],
        )
    builder.button(
        text=_raw("dm_panel_settings"),
        callback_data=f"{_PREFIX}:a:settings:{chat_id}",
        icon_custom_emoji_id="5877260593903177342",  # ⚙
    )
    builder.button(
        text=_raw("dm_panel_slowmode"),
        callback_data=f"{_PREFIX}:sm:{chat_id}",
        icon_custom_emoji_id="5382194935057372936",  # ⏱ (FinanceEmoji)
    )
    builder.button(
        text=_raw("dm_panel_stats"),
        callback_data=f"{_PREFIX}:a:stats:{chat_id}",
        icon_custom_emoji_id="5877485980901971030",  # 📊
    )
    builder.button(
        text=_raw("dm_panel_top"),
        callback_data=f"{_PREFIX}:a:top:{chat_id}",
        icon_custom_emoji_id="5961051261204696786",  # 🥇 (analog for 🏆)
    )
    builder.button(
        text=_raw("dm_panel_groups"),
        callback_data=f"{_PREFIX}:groups",
        icon_custom_emoji_id=_ICON_BACK,  # ◀
    )
    builder.button(
        text=_raw("dm_menu_home"),
        callback_data=f"{_PREFIX}:menu",
        icon_custom_emoji_id=_ICON_HOME,  # 🏠
    )
    builder.adjust(2, 2, 2, 2, 2, 2, 2, 2, 1, 2)
    return builder.as_markup()


def _build_ratings_kb(_raw: Callable[..., str]) -> types.InlineKeyboardMarkup:
    """Рейтинги tab: seller check / whitelist / list buttons."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text=_raw("dm_rt_check"),
        callback_data=f"{_PREFIX}:rt:check",
        icon_custom_emoji_id="5231012545799666522",  # 🔍 (NewsEmoji)
    )
    builder.button(
        text=_raw("dm_rt_wl"),
        callback_data=f"{_PREFIX}:rt:wl",
        icon_custom_emoji_id="5267500801240092311",  # ⭐ (FinanceEmoji)
    )
    builder.button(
        text=_raw("dm_rt_wlrm"),
        callback_data=f"{_PREFIX}:rt:wlrm",
        icon_custom_emoji_id="5872829476143894491",  # 🚫
    )
    builder.button(
        text=_raw("dm_rt_list"),
        callback_data=f"{_PREFIX}:rt:list",
        icon_custom_emoji_id="5839323457015256759",  # 📄 (analog for 📋)
    )
    builder.button(
        text=_raw("dm_menu_home"),
        callback_data=f"{_PREFIX}:menu",
        icon_custom_emoji_id=_ICON_HOME,  # 🏠
    )
    builder.adjust(1)
    return builder.as_markup()


def _bc_groups_kb(_raw: Callable[..., str]) -> types.InlineKeyboardMarkup:
    """Back keyboard for the no-topics case: groups list + home."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text=_raw("dm_panel_groups"),
        callback_data=f"{_PREFIX}:tab:broadcast",
        icon_custom_emoji_id=_ICON_BACK,  # ◀
    )
    builder.button(
        text=_raw("dm_menu_home"),
        callback_data=f"{_PREFIX}:menu",
        icon_custom_emoji_id=_ICON_HOME,  # 🏠
    )
    builder.adjust(1)
    return builder.as_markup()


def _bc_text_kb(
    _raw: Callable[..., str], chat_id: int
) -> types.InlineKeyboardMarkup:
    """Back keyboard for the broadcast-text flow: topics + home."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text=_raw("dm_bc_topics_back"),
        callback_data=f"{_PREFIX}:bc:{chat_id}",
        icon_custom_emoji_id=_ICON_BACK,  # ◀
    )
    builder.button(
        text=_raw("dm_menu_home"),
        callback_data=f"{_PREFIX}:menu",
        icon_custom_emoji_id=_ICON_HOME,  # 🏠
    )
    builder.adjust(1)
    return builder.as_markup()


def _topic_label(topic: Any) -> str:
    """Human label for a tracked topic: its name when known, else ``#id``."""
    title = (getattr(topic, "title", None) or "").strip()
    return title or f"#{topic.thread_id}"


def _merge_topics(topics: list[Any], selected: list[int]) -> list[Any]:
    """Topics plus synthetic rows for manually added ids not yet in the DB.

    A thread id typed in by the operator always shows up in the keyboard, even
    before any message in that topic reached the bot.
    """
    known = {topic.thread_id for topic in topics}
    merged = list(topics)
    for thread_id in selected:
        if thread_id not in known:
            merged.append(
                SimpleNamespace(thread_id=thread_id, message_count=0, title=None)
            )
    return merged


def parse_thread_id(text: str) -> int | None:
    """Extract a thread id from a bare number or a ``t.me/c/.../<id>`` link."""
    numbers = re.findall(r"\d+", text or "")
    if not numbers:
        return None
    return int(numbers[-1])


def _build_topics_kb(
    _raw: Callable[..., str],
    chat_id: int,
    topics: list[Any],
    selected: list[int],
) -> types.InlineKeyboardMarkup:
    """Topic multi-select keyboard: ✅/⚪ icons per thread + go + home."""
    builder = InlineKeyboardBuilder()
    sel = set(selected)
    for topic in _merge_topics(topics, selected):
        builder.button(
            text=_topic_label(topic),
            callback_data=f"{_PREFIX}:bct:{chat_id}:{topic.thread_id}",
            icon_custom_emoji_id=(
                _ICON_TOPIC_ON if topic.thread_id in sel else _ICON_TOPIC_OFF
            ),  # ✅ / ⚪
        )
    builder.button(
        text=_raw("dm_bc_go"),
        callback_data=f"{_PREFIX}:bcgo:{chat_id}",
        icon_custom_emoji_id="5197269100878907942",  # ✍ (FinanceEmoji)
    )
    builder.button(
        text=_raw("dm_bc_topics_refresh"),
        callback_data=f"{_PREFIX}:bcr:{chat_id}",
    )
    builder.button(
        text=_raw("dm_bc_topics_add"),
        callback_data=f"{_PREFIX}:bcadd:{chat_id}",
    )
    builder.button(
        text=_raw("dm_menu_home"),
        callback_data=f"{_PREFIX}:menu",
        icon_custom_emoji_id=_ICON_HOME,  # 🏠
    )
    builder.adjust(1)
    return builder.as_markup()


def _sm_topic_status(_raw: Callable[..., str], override: Any) -> str:
    """Short per-topic mark for the list: «· ⛔» off here, «· ⚙️» own params."""
    if override is None:
        return ""
    if not override.enabled:
        return f" · {_raw('dm_sm_topic_status_off')}"
    if override.regular_seconds is None:
        return ""  # nothing set of its own: the topic follows the chat
    return f" · {_raw('dm_sm_topic_status_own')}"


def _topic_params_button(
    _raw: Callable[..., str], chat_id: int, thread_id: int
) -> types.InlineKeyboardButton:
    """«⚙️» button opening the per-topic slow-mode screen."""
    return types.InlineKeyboardButton(
        text=_raw("dm_sm_topic_params"),
        callback_data=f"{_PREFIX}:smt:{chat_id}:{thread_id}",
        icon_custom_emoji_id=_ICON_SETTINGS,  # ⚙
    )


def _sm_chat_rule_button(
    _raw: Callable[..., str], chat_id: int, cfg: Any
) -> types.InlineKeyboardButton:
    """The chat-wide rule as ONE button: shows its state, a tap flips it.

    The screen that used to hold this switch is gone (the typed ``вкл|выкл``
    line stays for messages already sent), so the switch lives here — the
    list is where the scope and the per-topic limits are handled anyway.
    """
    if cfg is not None and cfg.enabled:
        text = _raw(
            "dm_sm_chat_rule_on",
            regular=cfg.regular_seconds // 3600,
            wl=cfg.wl_seconds // 3600,
        )
    else:
        text = _raw("dm_sm_chat_rule_off")
    return types.InlineKeyboardButton(
        text=text, callback_data=f"{_PREFIX}:smc:{chat_id}"
    )


def _build_sm_topics_kb(
    _raw: Callable[..., str],
    chat_id: int,
    topics: list[Any],
    selected: list[int],
    overrides: dict[int, Any] | None = None,
    *,
    cfg: Any,
) -> types.InlineKeyboardMarkup:
    """Slow-mode topic list: ``[✅/☑️ topic] [⚙️]`` per thread + all/done + nav.

    Every topic row carries its own parameters button (``dm:smt:``), so each
    thread can be tuned without touching the chat-wide config. The leading
    mark is the chat scope (✅ = inside it); ``overrides`` appends «· ⛔» (rule
    off in that topic) or «· ⚙️» (own intervals) to the label.

    The first row is the chat-wide rule itself (off/on, one tap — ``cfg`` is
    required so the label can never lie), then «Все ветки» (immediate save
    with ``topic_ids=[]``), «✅ Готово» (save with the picked threads),
    «🔄 Обновить список» (re-read the DB), «✏️ Добавить ID ветки вручную» and a
    panel/home nav row.
    """
    overrides = overrides or {}
    sel = set(selected)
    rows: list[list[types.InlineKeyboardButton]] = [
        [_sm_chat_rule_button(_raw, chat_id, cfg)]
    ]
    for topic in _merge_topics(topics, selected):
        mark = "✅" if topic.thread_id in sel else "☑️"
        rows.append(
            [
                types.InlineKeyboardButton(
                    text=(
                        f"{mark} {_topic_label(topic)} · {topic.message_count} сообщ."
                        f"{_sm_topic_status(_raw, overrides.get(topic.thread_id))}"
                    ),
                    callback_data=f"{_PREFIX}:smb:{chat_id}:{topic.thread_id}",
                ),
                _topic_params_button(_raw, chat_id, topic.thread_id),
            ]
        )
    rows.append(
        [
            types.InlineKeyboardButton(
                text=_raw("dm_sm_topics_all"),
                callback_data=f"{_PREFIX}:smball:{chat_id}",
            )
        ]
    )
    rows.append(
        [
            types.InlineKeyboardButton(
                text=_raw("dm_sm_topics_done"),
                callback_data=f"{_PREFIX}:smbdone:{chat_id}",
            )
        ]
    )
    rows.append(
        [
            types.InlineKeyboardButton(
                text=_raw("dm_sm_topics_refresh"),
                callback_data=f"{_PREFIX}:smrefresh:{chat_id}",
            )
        ]
    )
    rows.append(
        [
            types.InlineKeyboardButton(
                text=_raw("dm_sm_topics_add"),
                callback_data=f"{_PREFIX}:smadd:{chat_id}",
            )
        ]
    )
    rows.append(
        [
            types.InlineKeyboardButton(
                text=_raw("dm_panel_back"),
                callback_data=f"{_PREFIX}:g:{chat_id}",
                icon_custom_emoji_id=_ICON_BACK,  # ◀
            ),
            types.InlineKeyboardButton(
                text=_raw("dm_menu_home"),
                callback_data=f"{_PREFIX}:menu",
                icon_custom_emoji_id=_ICON_HOME,  # 🏠
            ),
        ]
    )
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _sm_hours_label(_: Callable[..., str], hours: int) -> str:
    """Readable interval: «6 ч», or «∞ без лимита» when 0 (no limit)."""
    if hours <= 0:
        return _("dm_sm_topic_pick_unlimited")
    return _("dm_sm_hours", hours=hours)


def _sm_topic_hours(cfg: Any, override: Any) -> int:
    """Effective hours for a topic: its own limit wins, else the chat's.

    A topic has ONE interval for everyone, so a stored ``regular_seconds`` is
    what both regular members and sellers get there (a legacy ``wl_seconds``
    on the row is ignored). A stored 0 means «no limit» and comes back as is;
    without an own value the chat's regular interval applies (6h default when
    the chat has no config at all).
    """
    if override is not None and override.regular_seconds is not None:
        return int(override.regular_seconds) // 3600
    if cfg is None:
        return 21600 // 3600
    return int(cfg.regular_seconds) // 3600


def _sm_topic_state_text(_: Callable[..., str], cfg: Any, override: Any) -> str:
    """One line on the topic's rule: off here / own / inherit / nothing here.

    A topic's own row decides on its own — the chat-wide switch only governs
    topics WITHOUT a row, so this line never claims a per-topic limit is dead
    just because the chat's own rule is off.
    """
    # No chat row means the chat's defaults (the same 6 h / 3 h the FSM seeds),
    # so «как в чате» stays meaningful for a topic that has a row of its own.
    chat_regular = (cfg.regular_seconds // 3600) if cfg is not None else 21600 // 3600
    chat_wl = (cfg.wl_seconds // 3600) if cfg is not None else 10800 // 3600
    if override is not None:
        if not override.enabled:
            return _("dm_sm_topic_state_off")
        if override.regular_seconds is None:
            return _("dm_sm_topic_state_inherit", regular=chat_regular, wl=chat_wl)
        return _(
            "dm_sm_topic_state_own",
            hours=_sm_hours_label(_, override.regular_seconds // 3600),
        )
    if cfg is None or not cfg.enabled:
        return _("dm_sm_topic_state_chat_off")
    return _("dm_sm_topic_state_inherit", regular=chat_regular, wl=chat_wl)


def _sm_pick_text(_: Callable[..., str], cfg: Any, override: Any, label: str) -> str:
    """Body of the interval picker: heading, current value, topic state.

    Everything the buttons need is redrawn from the database, so the screen
    carries no FSM state of its own.
    """
    heading = _("dm_sm_topic_pick_all", topic=label)
    return (
        f"{heading}\n\n"
        f"{_('dm_sm_topic_pick_now', current=_sm_hours_label(_, _sm_topic_hours(cfg, override)))}"
        f"\n{_sm_topic_state_text(_, cfg, override)}"
    )


def _build_sm_topic_kb(
    _raw: Callable[..., str],
    chat_id: int,
    thread_id: int,
) -> types.InlineKeyboardMarkup:
    """Back/home row shared by the topic's settings screens.

    A topic has a single screen — the hour grid — so «назад» leads straight to
    the list of topics instead of an intermediate settings screen.
    """
    rows = [
        [
            types.InlineKeyboardButton(
                text=_raw("dm_sm_topic_pick_back"),
                callback_data=f"{_PREFIX}:smtl:{chat_id}",
                icon_custom_emoji_id=_ICON_BACK,  # ◀
            ),
            types.InlineKeyboardButton(
                text=_raw("dm_menu_home"),
                callback_data=f"{_PREFIX}:menu",
                icon_custom_emoji_id=_ICON_HOME,  # 🏠
            ),
        ]
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _build_sm_topic_pick_kb(
    _raw: Callable[..., str],
    chat_id: int,
    thread_id: int,
    current: int,
    own: bool,
) -> types.InlineKeyboardMarkup:
    """Hour grid for a topic: presets, ±1 ч, reset, manual, punishment, nav.

    The matching preset carries «✅», so the screen stays stateless — every
    press redraws it from the database. «±1 ч» is hidden for «∞» (0); «↩️ Как
    в чате» only shows when this topic has its own value; the typed prompt is
    one tap away for unusual values. There is no «⛔ Выключить здесь» button:
    a topic either has its own limit («↩️ Как в чате» drops it) or follows the
    chat — switching a single topic off is not a setting the owner wants.

    «⚠️ Наказание за нарушения» sits under the manual entry: the punishment for
    violating the topic's rule is part of THIS topic's settings, never a
    chat-wide switch that could surprise another group.
    """
    rows: list[list[types.InlineKeyboardButton]] = []
    row: list[types.InlineKeyboardButton] = []
    for hours in (1, 3, 6, 12, 24, 48):
        mark = "✅ " if hours == current else ""
        row.append(
            types.InlineKeyboardButton(
                text=f"{mark}{_raw('dm_sm_hours', hours=hours)}",
                callback_data=f"{_PREFIX}:smtvs:{chat_id}:{thread_id}:{hours}",
            )
        )
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(
        [
            types.InlineKeyboardButton(
                text=f"{'✅ ' if current == 0 else ''}"
                f"{_raw('dm_sm_topic_pick_unlimited')}",
                callback_data=f"{_PREFIX}:smtvs:{chat_id}:{thread_id}:0",
            )
        ]
    )
    if current > 0:
        rows.append(
            [
                types.InlineKeyboardButton(
                    text=_raw("dm_sm_topic_pick_minus"),
                    callback_data=f"{_PREFIX}:smtvm:{chat_id}:{thread_id}",
                ),
                types.InlineKeyboardButton(
                    text=_raw("dm_sm_topic_pick_plus"),
                    callback_data=f"{_PREFIX}:smtvp:{chat_id}:{thread_id}",
                ),
            ]
        )
    if own:
        rows.append(
            [
                types.InlineKeyboardButton(
                    text=_raw("dm_sm_topic_pick_inherit"),
                    callback_data=f"{_PREFIX}:smtvi:{chat_id}:{thread_id}",
                )
            ]
        )
    rows.append(
        [
            types.InlineKeyboardButton(
                text=_raw("dm_sm_topic_pick_manual"),
                callback_data=f"{_PREFIX}:smtp:{chat_id}:{thread_id}",
                icon_custom_emoji_id=_ICON_SETTINGS,  # ⚙
            )
        ]
    )
    rows.append(
        [
            types.InlineKeyboardButton(
                text=_raw("dm_sm_punish_btn"),
                callback_data=f"{_PREFIX}:smw:{chat_id}:{thread_id}",
            )
        ]
    )
    rows.extend(_build_sm_topic_kb(_raw, chat_id, thread_id).inline_keyboard)
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


# --- Punishment for slow-mode violations (per topic, dm:smw:*) ------------- #


def _sm_punish_limit(row: Any) -> int:
    """Warnings before the punishment; ``0`` (or junk) means «never punish»."""
    try:
        return max(0, int(getattr(row, "punish_limit", 0) or 0))
    except (TypeError, ValueError):
        return 0


def _sm_punish_action(row: Any) -> str:
    """Configured action, falling back to «mute» for anything unexpected."""
    action = str(getattr(row, "punish_action", None) or "mute").lower()
    return action if action in PUNISH_ACTIONS else "mute"


def _sm_punish_duration(row: Any) -> int | None:
    """Configured duration in seconds; ``None`` = «forever»."""
    try:
        return int(getattr(row, "punish_duration", None)) or None
    except (TypeError, ValueError):
        return None


def _sm_punish_text(_: Callable[..., str], row: Any, topic: str) -> str:
    """Body of one topic's punishment screen: the value of all four settings.

    Sent with ``parse_mode="HTML"`` (see ``_edit_or_answer``), so the stored
    warning text is escaped — it is user input, and a stray ``<`` would break
    the whole screen.
    """
    limit = _sm_punish_limit(row)
    count_line = (
        _("dm_sm_punish_count_off_line")
        if limit <= 0
        else _("dm_sm_punish_count_line", count=limit)
    )
    custom = (getattr(row, "punish_text", None) or "").strip()
    text = escape_html(custom[:120]) if custom else _("dm_sm_punish_text_default")
    action = punish_action_label(_, _sm_punish_action(row), _sm_punish_duration(row))
    return (
        f"{_('dm_sm_punish_title', topic=topic)}\n\n"
        f"{_('dm_sm_punish_now', count_line=count_line, action=action, text=text)}\n\n"
        f"{_('dm_sm_punish_hint')}"
    )


def _build_sm_punish_kb(
    _raw: Callable[..., str], chat_id: int, thread_id: int, row: Any
) -> types.InlineKeyboardMarkup:
    """One topic's punishment screen: warn text, count, action, duration.

    The current value carries «✅» exactly like the hour grid, so every press
    redraws the screen from the topic's row and no state has to be kept. The
    duration only matters for mute/ban — a kick is instant.
    """
    limit = _sm_punish_limit(row)
    action = _sm_punish_action(row)
    duration = _sm_punish_duration(row)
    rows: list[list[types.InlineKeyboardButton]] = [
        [
            types.InlineKeyboardButton(
                text=_raw("dm_sm_punish_text_btn"),
                callback_data=f"{_PREFIX}:smwt:{chat_id}:{thread_id}",
            )
        ]
    ]
    rows.append(
        [
            types.InlineKeyboardButton(
                text=f"{'✅ ' if value == limit else ''}{value}",
                callback_data=f"{_PREFIX}:smwc:{chat_id}:{thread_id}:{value}",
            )
            for value in (1, 2, 3, 5, 10)
        ]
    )
    rows.append(
        [
            types.InlineKeyboardButton(
                text=(
                    f"{'✅ ' if limit <= 0 else ''}" f"{_raw('dm_sm_punish_count_off')}"
                ),
                callback_data=f"{_PREFIX}:smwc:{chat_id}:{thread_id}:0",
            )
        ]
    )
    rows.append(
        [
            types.InlineKeyboardButton(
                text=(
                    f"{'✅ ' if value == action else ''}"
                    f"{_raw(f'dm_sm_punish_action_{value}')}"
                ),
                callback_data=f"{_PREFIX}:smwa:{chat_id}:{thread_id}:{value}",
            )
            for value in PUNISH_ACTIONS
        ]
    )
    rows.append(
        [
            types.InlineKeyboardButton(
                text=(
                    f"{'✅ ' if (duration or 0) == seconds else ''}"
                    f"{punish_duration_label(_raw, seconds)}"
                ),
                callback_data=f"{_PREFIX}:smwd:{chat_id}:{thread_id}:{seconds}",
            )
            for seconds in (3600, 86400, 604800, 0)
        ]
    )
    rows.extend(_sm_punish_nav_kb(_raw, chat_id, thread_id).inline_keyboard)
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _sm_punish_nav_kb(
    _raw: Callable[..., str], chat_id: int, thread_id: int
) -> types.InlineKeyboardMarkup:
    """Ways out of a topic's punishment screen and its text prompt."""
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=_raw("dm_sm_punish_back"),
                    callback_data=f"{_PREFIX}:smt:{chat_id}:{thread_id}",
                    icon_custom_emoji_id=_ICON_BACK,  # ◀
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=_raw("dm_sm_topics_back"),
                    callback_data=f"{_PREFIX}:smtl:{chat_id}",
                ),
                types.InlineKeyboardButton(
                    text=_raw("dm_menu_home"),
                    callback_data=f"{_PREFIX}:menu",
                    icon_custom_emoji_id=_ICON_HOME,  # 🏠
                ),
            ],
        ]
    )


def _sm_punish_locked_row(
    _raw: Callable[..., str], chat_id: int, thread_id: int
) -> types.InlineKeyboardButton:
    """«Set this topic's own limit first» — the only way into a punishment."""
    return types.InlineKeyboardButton(
        text=_raw("dm_sm_punish_set_limit"),
        callback_data=f"{_PREFIX}:smt:{chat_id}:{thread_id}",
        icon_custom_emoji_id=_ICON_SETTINGS,  # ⚙
    )


def _build_sm_topic_prompt_kb(
    _raw: Callable[..., str], chat_id: int, thread_id: int
) -> types.InlineKeyboardMarkup:
    """Bail-out keyboard for the typed per-topic interval prompt."""
    return _build_sm_topic_kb(_raw, chat_id, thread_id)


def _build_sm_topics_empty_kb(
    _raw: Callable[..., str], chat_id: int, *, cfg: Any
) -> types.InlineKeyboardMarkup:
    """Hint screen for a forum with no tracked topics yet.

    Telegram gives bots no way to enumerate topics, so instead of silently
    saving «all topics» we explain how topics show up and offer the ways out:
    the chat-wide rule switch, «Все ветки» (explicit whole-chat scope), add an
    id manually, or go back.
    """
    builder = InlineKeyboardBuilder()
    builder.button(
        text=_sm_chat_rule_button(_raw, chat_id, cfg).text,
        callback_data=f"{_PREFIX}:smc:{chat_id}",
    )
    builder.button(
        text=_raw("dm_sm_topics_all"),
        callback_data=f"{_PREFIX}:smball:{chat_id}",
    )
    builder.button(
        text=_raw("dm_sm_topics_add"),
        callback_data=f"{_PREFIX}:smadd:{chat_id}",
    )
    builder.button(
        text=_raw("dm_sm_topics_back"),
        callback_data=f"{_PREFIX}:smback:{chat_id}",
    )
    builder.adjust(1)
    return builder.as_markup()


def _sm_topics_summary(_: Callable[..., str], topic_ids: list[int] | None) -> str:
    """Human summary of a slow-mode topic scope: «все ветки» or «#3, #6»."""
    if not topic_ids:
        return _("dm_sm_topics_summary_all")
    return _(
        "dm_sm_topics_summary_list",
        ids=", ".join(f"#{tid}" for tid in topic_ids),
    )


def _build_settings_kb(
    settings: dict[str, Any],
    _raw: Callable[..., str],
    chat_id: int,
) -> types.InlineKeyboardMarkup:
    """Group-settings toggles (menu layout) wired to the DM ``dm:set`` flow.

    ``build_menu`` emits ``menu:t:<field>`` callbacks that would land in the
    group settings router (and read the DM chat's settings); rewrite them to
    ``dm:set:<chat_id>:<field>`` so the DM panel toggles the SELECTED group.
    """
    kb = build_menu(settings, _raw)
    for row in kb.inline_keyboard:
        for btn in row:
            if (
                btn.callback_data is not None
                and btn.callback_data.startswith("menu:t:")
            ):
                field = btn.callback_data.split(":", 2)[2]
                btn.callback_data = f"{_PREFIX}:set:{chat_id}:{field}"
    kb.inline_keyboard.append(
        [
            types.InlineKeyboardButton(
                text=_raw("dm_panel_back"),
                callback_data=f"{_PREFIX}:g:{chat_id}",
                icon_custom_emoji_id=_ICON_BACK,  # ◀
            )
        ]
    )
    kb.inline_keyboard.append(
        [
            types.InlineKeyboardButton(
                text=_raw("dm_menu_home"),
                callback_data=f"{_PREFIX}:menu",
                icon_custom_emoji_id=_ICON_HOME,  # 🏠
            )
        ]
    )
    return kb


async def _edit_or_answer(
    callback: types.CallbackQuery,
    text: str,
    kb: types.InlineKeyboardMarkup,
) -> None:
    """Edit the tapped message; fall back to a fresh answer if editing fails."""
    if callback.message is not None:
        try:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
            return
        except Exception:
            # Message too old / already edited — answer a new one instead.
            logger.debug("dm.edit_failed", exc_info=True)
    await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.message(IsPrivate(), CommandStart())
async def dm_start(message: types.Message, **data: Any) -> None:
    """/start in a private chat → welcome text + main-menu keyboard."""
    _ = data["_"]
    _raw = data["_raw"]
    await message.answer(_("dm_menu_welcome"), reply_markup=build_main_menu(_raw))


@router.message(IsPrivate(), F.text & ~F.text.startswith("/"), StateFilter(None))
async def dm_any_text(message: types.Message, **data: Any) -> None:
    """Any plain text in PM (no active FSM state) re-shows the main menu.

    Commands are excluded so ``/scam``, ``/addtowl`` and friends keep
    working in DM via their own routers.
    """
    _ = data["_"]
    _raw = data["_raw"]
    await message.answer(_("dm_menu_title"), reply_markup=build_main_menu(_raw))


@router.callback_query(F.data.startswith(f"{_PREFIX}:"))
async def on_dm_callback(
    callback: types.CallbackQuery, state: FSMContext, **data: Any
) -> None:
    """Handle a main-menu button press (prefix ``dm:``)."""
    _ = data["_"]
    _raw = data["_raw"]
    session: AsyncSession = data["session"]
    action = (callback.data or "")[len(_PREFIX) + 1 :]

    if action == "menu":
        # Return to the main menu; also bails out of any active FSM flow.
        await state.clear()
        await _edit_or_answer(callback, _("dm_menu_title"), build_main_menu(_raw))
        await callback.answer()
        return

    if action == "info":
        await _edit_or_answer(callback, _("cmd_info"), build_main_menu(_raw))
        await callback.answer()
        return

    if action == "help":
        await _edit_or_answer(callback, _("cmd_help_private"), build_main_menu(_raw))
        await callback.answer()
        return

    # Администрирование tab → groups list (dm:g:<id> panels). The legacy
    # dm:groups / dm:gp:<page> callbacks stay wired (panel nav uses them).
    if (
        action == "tab:admin"
        or action == "groups"
        or action.startswith("gp:")
    ):
        await _dm_groups(callback, action, _, _raw, session)
        await callback.answer()
        return

    if action == "tab:ratings":
        await _dm_ratings(callback, _, _raw)
        await callback.answer()
        return

    if action.startswith("rt:"):
        await _dm_ratings_action(callback, action[3:], state, _, _raw, session)
        return

    # Рассылки tab → groups list (dm:bc:<id> topic pickers).
    if action == "tab:broadcast" or action.startswith("bcp:"):
        await _dm_groups(
            callback,
            action,
            _,
            _raw,
            session,
            group_cb="bc",
            page_cb="bcp",
            title_key="dm_bc_groups_title",
        )
        await callback.answer()
        return

    if action.startswith("bc:"):
        await _dm_broadcast_topics(callback, action[3:], state, _, _raw, session)
        return

    if action.startswith("bct:"):
        await _dm_broadcast_toggle(callback, action, state, _, _raw, session)
        return

    if action.startswith("bcgo:"):
        await _dm_broadcast_go(callback, action[5:], state, _, _raw)
        return

    if action.startswith("bcr:"):
        await _dm_bc_refresh(callback, action, state, _, _raw, session)
        return

    if action.startswith("bcadd:"):
        await _dm_bc_add_id(callback, action, state, _, _raw, session)
        return

    # Slow-mode topic multi-select callbacks MUST be matched before the
    # generic ``sm:`` prefix (``smb:``/``smball:``/``smbdone:`` start with it).
    if action.startswith("smb:"):
        await _dm_sm_topic_toggle(callback, action, state, _, _raw, session)
        return

    if action.startswith("smball:"):
        await _dm_sm_topics_all(callback, action, state, _, _raw, session)
        return

    if action.startswith("smbdone:"):
        await _dm_sm_topics_done(callback, action, state, _, _raw, session)
        return

    if action.startswith("smrefresh:"):
        await _dm_sm_refresh(callback, action, state, _, _raw, session)
        return

    if action.startswith("smadd:"):
        await _dm_sm_add_id(callback, action, state, _, _raw, session)
        return

    if action.startswith("smback:"):
        await _dm_sm_back(callback, action, state, _, _raw, session)
        return

    if action.startswith("smtl:"):
        await _dm_sm_topics_list(callback, action, state, _, _raw, session)
        return

    if action.startswith("smtvs:"):
        await _dm_sm_topic_pick_set(callback, action, state, _, _raw, session)
        return

    if action.startswith(("smtvm:", "smtvp:")):
        await _dm_sm_topic_pick_step(callback, action, state, _, _raw, session)
        return

    if action.startswith("smtvi:"):
        await _dm_sm_topic_pick_inherit(callback, action, state, _, _raw, session)
        return

    if action.startswith("smtv:"):
        await _dm_sm_topic_pick(callback, action, state, _, _raw, session)
        return

    if action.startswith("smtp:"):
        await _dm_sm_topic_set(callback, action, state, _, _raw, session)
        return

    if action.startswith("smtr:"):
        await _dm_sm_topic_reset(callback, action, state, _, _raw, session)
        return

    if action.startswith("smt:"):
        await _dm_sm_topic(callback, action, state, _, _raw, session)
        return

    if action.startswith("smc:"):
        await _dm_sm_chat_rule_toggle(callback, action, state, _, _raw, session)
        return

    # Punishment for one topic's slow-mode violations (smw*): the text prompt,
    # then the three value families. The bare ``smw:`` screen is matched last.
    if action.startswith("smwt:"):
        await _dm_sm_punish_text_prompt(callback, action, state, _, _raw, session)
        return

    if action.startswith("smwc:"):
        await _dm_sm_punish_count(callback, action, state, _, _raw, session)
        return

    if action.startswith("smwa:"):
        await _dm_sm_punish_action(callback, action, state, _, _raw, session)
        return

    if action.startswith("smwd:"):
        await _dm_sm_punish_duration(callback, action, state, _, _raw, session)
        return

    if action.startswith("smw:"):
        await _dm_sm_punish(callback, action, state, _, _raw, session)
        return

    if action.startswith("sm:"):
        await _dm_slow_mode(callback, action[3:], state, _, _raw, session)
        return

    if action.startswith("g:"):
        await _dm_group_panel(callback, action[2:], _, _raw, session)
        await callback.answer()
        return

    if action.startswith("a:"):
        await _dm_action(callback, action, state, _, _raw, session)
        return

    if action.startswith("set:"):
        await _dm_settings_toggle(callback, action, _raw, session, data["redis"])
        return

    # Unknown dm:* action — acknowledge and ignore.
    await callback.answer()


async def _dm_groups(
    callback: types.CallbackQuery,
    action: str,
    _: Callable[..., str],
    _raw: Callable[..., str],
    session: AsyncSession,
    *,
    group_cb: str = "g",
    page_cb: str = "gp",
    title_key: str = "dm_groups_title",
) -> None:
    """Render a groups list (paginated) from the DB.

    ``group_cb``/``page_cb`` select the callback family (admin ``g``/``gp``,
    stats ``st``/``stp``, broadcast ``bc``/``bcp``).
    """
    chats = await crud.list_active_chats(session)
    if not chats:
        await _edit_or_answer(callback, _("dm_groups_empty"), _home_kb(_raw))
        return
    page = 0
    if action.startswith(f"{page_cb}:"):
        try:
            page = int(action[len(page_cb) + 1 :])
        except ValueError:
            page = 0
    page = max(0, min(page, (len(chats) - 1) // _GROUPS_PAGE_SIZE))
    await _edit_or_answer(
        callback,
        _(title_key),
        _build_groups_kb(_raw, chats, page, group_cb=group_cb, page_cb=page_cb),
    )


async def _dm_group_panel(
    callback: types.CallbackQuery,
    chat_id_token: str,
    _: Callable[..., str],
    _raw: Callable[..., str],
    session: AsyncSession,
) -> None:
    """Render the per-group admin panel."""
    try:
        chat_id = int(chat_id_token)
    except ValueError:
        return
    chat = await crud.get_chat(session, chat_id)
    if chat is None:
        await _edit_or_answer(callback, _("dm_panel_missing"), _home_kb(_raw))
        return
    title = (chat.title or "").strip() or str(chat_id)
    await _edit_or_answer(
        callback,
        _("dm_panel_title", title=title, chat_id=chat_id),
        _build_panel_kb(_raw, chat_id),
    )


async def _dm_action(
    callback: types.CallbackQuery,
    action: str,
    state: FSMContext,
    _: Callable[..., str],
    _raw: Callable[..., str],
    session: AsyncSession,
) -> None:
    """Dispatch a ``dm:a:<action>:<chat_id>`` panel button press."""
    parts = action.split(":")
    if len(parts) != 3:
        await callback.answer()
        return
    act, chat_id_token = parts[1], parts[2]
    try:
        chat_id = int(chat_id_token)
    except ValueError:
        await callback.answer()
        return

    if act == "settings":
        await _dm_settings(callback, chat_id, _, _raw, session)
        return

    if act == "stats":
        await _dm_stats(callback, chat_id, _, _raw, session)
        return

    if act == "top":
        await _dm_top(callback, chat_id, _, _raw, session)
        return

    if act not in _TARGET_ACTIONS:
        await callback.answer()
        return

    # Target-requiring moderation action: ask for the target next message.
    await state.set_state(DmAdmin.awaiting_target)
    await state.update_data(action=act, chat_id=chat_id)
    if act in _DURATION_ACTIONS:
        prompt = _("dm_action_prompt_duration", action=_raw(f"dm_panel_{act}"))
    else:
        prompt = _("dm_action_prompt", action=_raw(f"dm_panel_{act}"))
    await _edit_or_answer(callback, prompt, _panel_kb(_raw, chat_id))
    await callback.answer()


async def _dm_settings(
    callback: types.CallbackQuery,
    chat_id: int,
    _: Callable[..., str],
    _raw: Callable[..., str],
    session: AsyncSession,
) -> None:
    """Open the SELECTED group's settings toggles (DM-scoped callbacks)."""
    chat = await crud.get_chat(session, chat_id)
    if chat is None:
        await _edit_or_answer(callback, _("dm_panel_missing"), _home_kb(_raw))
        await callback.answer()
        return
    settings_obj = await crud.get_or_create_settings(session, chat_id)
    settings = crud.settings_to_dict(settings_obj)
    title = (chat.title or "").strip() or str(chat_id)
    await _edit_or_answer(
        callback,
        _("dm_settings_title", title=title),
        _build_settings_kb(settings, _raw, chat_id),
    )
    await callback.answer()


async def _dm_settings_toggle(
    callback: types.CallbackQuery,
    action: str,
    _raw: Callable[..., str],
    session: AsyncSession,
    redis: Any,
) -> None:
    """Toggle a SELECTED group's boolean setting (``dm:set:<chat_id>:<field>``)."""
    parts = action.split(":")
    if len(parts) != 3:
        await callback.answer()
        return
    try:
        chat_id = int(parts[1])
    except ValueError:
        await callback.answer()
        return
    field = parts[2]
    if field not in _TOGGLE_FIELDS:
        await callback.answer()
        return
    settings_obj = await crud.get_or_create_settings(session, chat_id)
    settings = crud.settings_to_dict(settings_obj)
    new_val = not settings.get(field)
    await crud.update_settings(session, chat_id, **{field: new_val})
    await redis.invalidate_settings(chat_id)
    settings[field] = new_val
    if callback.message is not None:
        try:
            await callback.message.edit_reply_markup(
                reply_markup=_build_settings_kb(settings, _raw, chat_id)
            )
        except Exception:
            logger.debug("dm.settings_edit_failed", exc_info=True)
    await callback.answer(_raw("menu_saved"))


# --------------------------------------------------------------------------- #
# Рейтинги tab
# --------------------------------------------------------------------------- #
async def _dm_ratings(
    callback: types.CallbackQuery,
    _: Callable[..., str],
    _raw: Callable[..., str],
) -> None:
    """Render the Рейтинги tab keyboard."""
    await _edit_or_answer(callback, _("dm_ratings_title"), _build_ratings_kb(_raw))


async def _dm_ratings_action(
    callback: types.CallbackQuery,
    action: str,
    state: FSMContext,
    _: Callable[..., str],
    _raw: Callable[..., str],
    session: AsyncSession,
) -> None:
    """Dispatch a ``dm:rt:*`` Рейтинги button press."""
    if action == "check":
        # Reuse the classic DmScam flow, unscoped (no risk_chat).
        await state.set_state(DmScam.awaiting_target)
        await state.update_data(chat_id=None)
        await _edit_or_answer(callback, _("dm_rt_check_prompt"), _home_kb(_raw))
        await callback.answer()
        return
    if action == "wl":
        await state.set_state(DmWl.awaiting_target)
        await state.update_data(action="add")
        await _edit_or_answer(callback, _("dm_rt_wl_prompt"), _home_kb(_raw))
        await callback.answer()
        return
    if action == "wlrm":
        await state.set_state(DmWl.awaiting_target)
        await state.update_data(action="remove")
        await _edit_or_answer(callback, _("dm_rt_wlrm_prompt"), _home_kb(_raw))
        await callback.answer()
        return
    if action == "list":
        await _dm_rt_list(callback, _, _raw, session)
        await callback.answer()
        return
    await callback.answer()


async def _dm_rt_list(
    callback: types.CallbackQuery,
    _: Callable[..., str],
    _raw: Callable[..., str],
    session: AsyncSession,
) -> None:
    """Show up to 15 scam/WL entries with source badges."""
    entries = await crud.list_scam_entries(session)
    if not entries:
        await _edit_or_answer(callback, _("dm_rt_list_empty"), _home_kb(_raw))
        return
    entries = entries[:_RT_LIST_LIMIT]
    names = await crud.get_users_by_ids(
        session, [entry.user_id for entry in entries]
    )
    lines = []
    for entry in entries:
        if entry.source == SCAM_SOURCE_SCAM:
            badge_key = "dm_rt_badge_scam"
        elif entry.source == SCAM_SOURCE_VERIFIED:
            badge_key = "dm_rt_badge_verified"
        else:
            badge_key = "dm_rt_badge_other"
        lines.append(
            _(
                "dm_rt_list_item",
                mention=build_mention(
                    entry.user_id, names.get(entry.user_id) or str(entry.user_id)
                ),
                source_badge=_raw(badge_key),
                reason=entry.reason or "",
            )
        )
    text = _("dm_rt_list_title") + "\n" + "\n".join(lines)
    await _edit_or_answer(callback, text, _home_kb(_raw))


# --------------------------------------------------------------------------- #
# Slow mode (dm:sm:<chat_id>)
# --------------------------------------------------------------------------- #
async def _dm_slow_mode(
    callback: types.CallbackQuery,
    chat_id_token: str,
    state: FSMContext,
    _: Callable[..., str],
    _raw: Callable[..., str],
    session: AsyncSession,
) -> None:
    """«Медленный режим» goes straight to the topic list — no in-between screen.

    The chat-wide defaults keep their button path inside that list («Все
    ветки» + «✅ Готово»), and the typed ``вкл|выкл …`` line stays available
    for the messages already sent (see the ``awaiting_config`` handler).
    """
    try:
        chat_id = int(chat_id_token)
    except ValueError:
        await callback.answer()
        return
    await _dm_sm_topics_list(callback, f"smtl:{chat_id}", state, _, _raw, session)


# --- slow-mode topic multi-select (dm:smb: / dm:smball: / dm:smbdone:) ----- #

def _dm_sm_send(callback: types.CallbackQuery) -> Callable[[str, types.InlineKeyboardMarkup], Any]:
    """Wrap ``_edit_or_answer`` as a (text, kb) sender for the save helper."""

    async def send(text: str, kb: types.InlineKeyboardMarkup) -> None:
        await _edit_or_answer(callback, text, kb)

    return send


async def _dm_sm_save(
    session: AsyncSession,
    state: FSMContext,
    send: Callable[[str, types.InlineKeyboardMarkup], Any],
    _: Callable[..., str],
    _raw: Callable[..., str],
    chat_id: int,
    *,
    enabled: bool,
    regular_seconds: int,
    wl_seconds: int,
    topic_ids: list[int],
) -> None:
    """Persist the slow-mode config (with topic scope), clear FSM, confirm."""
    await crud.set_slow_mode(
        session,
        chat_id,
        enabled=enabled,
        regular_seconds=regular_seconds,
        wl_seconds=wl_seconds,
        topic_ids=topic_ids,
    )
    await session.commit()
    await state.clear()
    await send(
        _(
            "dm_sm_saved",
            regular=regular_seconds // 3600,
            wl=wl_seconds // 3600,
            topics=_sm_topics_summary(_, topic_ids),
        ),
        _panel_kb(_raw, chat_id),
    )


async def _dm_sm_topic_toggle(
    callback: types.CallbackQuery,
    action: str,
    state: FSMContext,
    _: Callable[..., str],
    _raw: Callable[..., str],
    session: AsyncSession,
) -> None:
    """Toggle a thread in the slow-mode topic selection (``dm:smb:``)."""
    parts = action.split(":")
    if len(parts) != 3:
        await callback.answer()
        return
    try:
        chat_id, thread_id = int(parts[1]), int(parts[2])
    except ValueError:
        await callback.answer()
        return
    if await state.get_state() != DmSlowMode.awaiting_topics:
        await callback.answer()
        return
    state_data = await state.get_data()
    selected = list(state_data.get("selected_topics", []))
    if thread_id in selected:
        selected.remove(thread_id)
    else:
        selected.append(thread_id)
    await state.update_data(selected_topics=selected)
    topics = await crud.list_topics(session, chat_id)
    overrides = await crud.list_slow_mode_topics(session, chat_id)
    if callback.message is not None:
        try:
            await callback.message.edit_reply_markup(
                reply_markup=_build_sm_topics_kb(
                    _raw,
                    chat_id,
                    topics,
                    selected,
                    overrides,
                    cfg=await crud.get_slow_mode(session, chat_id),
                )
            )
        except Exception:
            logger.debug("dm.sm_toggle_edit_failed", exc_info=True)
    await callback.answer()


async def _dm_sm_topics_all(
    callback: types.CallbackQuery,
    action: str,
    state: FSMContext,
    _: Callable[..., str],
    _raw: Callable[..., str],
    session: AsyncSession,
) -> None:
    """«Все ветки»: save immediately with an empty topic scope (whole chat)."""
    parts = action.split(":")
    if len(parts) != 2:
        await callback.answer()
        return
    try:
        chat_id = int(parts[1])
    except ValueError:
        await callback.answer()
        return
    if await state.get_state() != DmSlowMode.awaiting_topics:
        await callback.answer()
        return
    pending = (await state.get_data()).get("pending_sm") or {}
    await _dm_sm_save(
        session,
        state,
        _dm_sm_send(callback),
        _,
        _raw,
        chat_id,
        enabled=bool(pending.get("enabled", True)),
        regular_seconds=int(pending.get("regular", 21600)),
        wl_seconds=int(pending.get("wl", 10800)),
        topic_ids=[],
    )


async def _dm_sm_refresh(
    callback: types.CallbackQuery,
    action: str,
    state: FSMContext,
    _: Callable[..., str],
    _raw: Callable[..., str],
    session: AsyncSession,
) -> None:
    """«🔄 Обновить список»: re-read the DB and redraw the topic keyboard."""
    parts = action.split(":")
    if len(parts) != 2:
        await callback.answer()
        return
    try:
        chat_id = int(parts[1])
    except ValueError:
        await callback.answer()
        return
    selected = list((await state.get_data()).get("selected_topics", []))
    topics = await crud.list_topics(session, chat_id)
    overrides = await crud.list_slow_mode_topics(session, chat_id)
    if not topics and not selected:
        await _edit_or_answer(
            callback,
            _("dm_sm_topics_empty"),
            _build_sm_topics_empty_kb(
                _raw, chat_id, cfg=await crud.get_slow_mode(session, chat_id)
            ),
        )
    else:
        await _edit_or_answer(
            callback,
            _("dm_sm_topics_prompt"),
            _build_sm_topics_kb(
                _raw,
                chat_id,
                topics,
                selected,
                overrides,
                cfg=await crud.get_slow_mode(session, chat_id),
            ),
        )
    await callback.answer()


async def _dm_sm_chat_rule_toggle(
    callback: types.CallbackQuery,
    action: str,
    state: FSMContext,
    _: Callable[..., str],
    _raw: Callable[..., str],
    session: AsyncSession,
) -> None:
    """The chat-wide rule button (``dm:smc:<chat>``): flip it, keep the values.

    Turning it on never invents a scope: the stored interval and topic list
    are reused (6 h / 3 h the first time), so one tap gives the whole chat a
    rule and the next one takes it away without losing anything.
    """
    parts = action.split(":")
    if len(parts) != 2:
        await callback.answer()
        return
    try:
        chat_id = int(parts[1])
    except ValueError:
        await callback.answer()
        return
    cfg = await crud.get_slow_mode(session, chat_id)
    enabled = cfg is None or not cfg.enabled
    await crud.set_slow_mode(
        session,
        chat_id,
        enabled=enabled,
        regular_seconds=cfg.regular_seconds if cfg is not None else 21600,
        wl_seconds=cfg.wl_seconds if cfg is not None else 10800,
        topic_ids=list(cfg.topic_ids) if cfg is not None and cfg.topic_ids else [],
    )
    await session.commit()
    logger.info("dm.sm_chat_rule_toggled", extra={"chat_id": chat_id, "on": enabled})
    await _dm_sm_topics_list(callback, f"smtl:{chat_id}", state, _, _raw, session)


async def _dm_sm_add_id(
    callback: types.CallbackQuery,
    action: str,
    state: FSMContext,
    _: Callable[..., str],
    _raw: Callable[..., str],
    session: AsyncSession,
) -> None:
    """«✏️ Добавить ID ветки вручную» → ask for the id (FSM step)."""
    parts = action.split(":")
    if len(parts) != 2:
        await callback.answer()
        return
    try:
        chat_id = int(parts[1])
    except ValueError:
        await callback.answer()
        return
    if await state.get_state() != DmSlowMode.awaiting_topics:
        await callback.answer()
        return
    await state.update_data(chat_id=chat_id)
    await state.set_state(DmSlowMode.awaiting_topic_id)
    await _edit_or_answer(callback, _("dm_topic_id_prompt"), _home_kb(_raw))
    await callback.answer()


async def _dm_sm_back(
    callback: types.CallbackQuery,
    action: str,
    state: FSMContext,
    _: Callable[..., str],
    _raw: Callable[..., str],
    session: AsyncSession,
) -> None:
    """«Назад» from the empty-topics hint: drop the FSM and show the panel."""
    parts = action.split(":")
    if len(parts) != 2:
        await callback.answer()
        return
    try:
        chat_id = int(parts[1])
    except ValueError:
        await callback.answer()
        return
    await state.clear()
    await _dm_group_panel(callback, str(chat_id), _, _raw, session)
    await callback.answer()


async def _dm_sm_topics_done(
    callback: types.CallbackQuery,
    action: str,
    state: FSMContext,
    _: Callable[..., str],
    _raw: Callable[..., str],
    session: AsyncSession,
) -> None:
    """«✅ Готово»: save with the picked threads (empty pick = whole chat)."""
    parts = action.split(":")
    if len(parts) != 2:
        await callback.answer()
        return
    try:
        chat_id = int(parts[1])
    except ValueError:
        await callback.answer()
        return
    if await state.get_state() != DmSlowMode.awaiting_topics:
        await callback.answer()
        return
    state_data = await state.get_data()
    selected = list(state_data.get("selected_topics", []))
    pending = state_data.get("pending_sm") or {}
    await _dm_sm_save(
        session,
        state,
        _dm_sm_send(callback),
        _,
        _raw,
        chat_id,
        enabled=bool(pending.get("enabled", True)),
        regular_seconds=int(pending.get("regular", 21600)),
        wl_seconds=int(pending.get("wl", 10800)),
        topic_ids=selected,
    )


# --------------------------------------------------------------------------- #
# Медленный режим: параметры ОТДЕЛЬНОЙ ветки
# (dm:smtl:<chat> — список веток, dm:smt:<chat>:<thread> — сразу сетка часов,
#  промежуточного экрана настроек ветки нет;
#  dm:smtvs:/dm:smtvm:/dm:smtvp: — выбрать часы / ±1 ч,
#  dm:smtvi: — как в чате,
#  dm:smtp: — ввод вручную (dm:smtr: — старая кнопка, оставлена для
#  уже отправленных сообщений))
# --------------------------------------------------------------------------- #
async def _sm_seed_topics_state(
    state: FSMContext, session: AsyncSession, chat_id: int
) -> Any:
    """Put the FSM on the slow-mode topic step; returns the chat's config.

    Both the topic list and the punishment screen live «inside» the slow-mode
    flow: seeding the same state means a screen opened from an old message still
    has a working «✅ Готово» and «⬅️ К веткам».
    """
    cfg = await crud.get_slow_mode(session, chat_id)
    selected = list(cfg.topic_ids) if cfg is not None and cfg.topic_ids else []
    await state.set_state(DmSlowMode.awaiting_topics)
    await state.update_data(
        chat_id=chat_id,
        selected_topics=selected,
        pending_sm={
            "enabled": cfg.enabled if cfg is not None else True,
            "regular": cfg.regular_seconds if cfg is not None else 21600,
            "wl": cfg.wl_seconds if cfg is not None else 10800,
        },
    )
    return cfg


async def _sm_punish_redraw(
    callback: types.CallbackQuery,
    chat_id: int,
    thread_id: int,
    _: Callable[..., str],
    _raw: Callable[..., str],
    session: AsyncSession,
) -> None:
    """Draw one topic's punishment screen from the DB (never from FSM state)."""
    topic = await _sm_topic_label(session, chat_id, thread_id)
    row = await crud.get_slow_mode_topic(session, chat_id, thread_id)
    if row is None:
        # Only reachable from an old message: a topic without its own limit has
        # no rule to punish, so point at the grid instead of inventing one.
        await _edit_or_answer(
            callback,
            _("dm_sm_punish_no_limit", topic=topic),
            types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [_sm_punish_locked_row(_raw, chat_id, thread_id)],
                    *_sm_punish_nav_kb(_raw, chat_id, thread_id).inline_keyboard,
                ]
            ),
        )
        return
    await _edit_or_answer(
        callback,
        _sm_punish_text(_, row, topic),
        _build_sm_punish_kb(_raw, chat_id, thread_id, row),
    )


async def _dm_sm_punish(
    callback: types.CallbackQuery,
    action: str,
    state: FSMContext,
    _: Callable[..., str],
    _raw: Callable[..., str],
    session: AsyncSession,
) -> None:
    """«⚠️ Наказание за нарушения» of ONE topic (``dm:smw:<chat>:<thread>``).

    Reached from that topic's hour grid, so the four settings belong to the
    topic that carries the limit — the same screen in another topic edits that
    topic's own values.
    """
    ids = _sm_ids(action, 2)
    if ids is None:
        await callback.answer()
        return
    chat_id, thread_id = ids
    await _sm_seed_topics_state(state, session, chat_id)
    await state.update_data(thread_id=thread_id)
    await _sm_punish_redraw(callback, chat_id, thread_id, _, _raw, session)
    await callback.answer()


async def _dm_sm_punish_text_prompt(
    callback: types.CallbackQuery,
    action: str,
    state: FSMContext,
    _: Callable[..., str],
    _raw: Callable[..., str],
    session: AsyncSession,
) -> None:
    """«📝 Текст предупреждения»: ask for the text shown to the topic's violators."""
    ids = _sm_ids(action, 2)
    if ids is None:
        await callback.answer()
        return
    chat_id, thread_id = ids
    await state.set_state(DmSlowMode.awaiting_sm_punish_text)
    await state.update_data(chat_id=chat_id, thread_id=thread_id)
    await _edit_or_answer(
        callback,
        _("dm_sm_punish_text_prompt"),
        _sm_punish_nav_kb(_raw, chat_id, thread_id),
    )
    await callback.answer()


async def _dm_sm_punish_count(
    callback: types.CallbackQuery,
    action: str,
    state: FSMContext,
    _: Callable[..., str],
    _raw: Callable[..., str],
    session: AsyncSession,
) -> None:
    """Warnings before the punishment (``dm:smwc:``; 0 = never punish)."""
    ids = _sm_ids(action, 3)
    if ids is None:
        await callback.answer()
        return
    chat_id, thread_id, value = ids
    await crud.set_slow_mode_topic(
        session, chat_id, thread_id, punish_limit=max(0, value)
    )
    await session.commit()
    await _sm_punish_redraw(callback, chat_id, thread_id, _, _raw, session)
    await callback.answer()


async def _dm_sm_punish_action(
    callback: types.CallbackQuery,
    action: str,
    state: FSMContext,
    _: Callable[..., str],
    _raw: Callable[..., str],
    session: AsyncSession,
) -> None:
    """Punishment type: mute / kick / ban (``dm:smwa:<chat>:<thread>:<action>``).

    Parsed by hand: the value is a word, so ``_sm_ids`` (numeric-only) cannot
    read this callback.
    """
    parts = action.split(":")
    value = parts[-1].lower() if parts else ""
    if len(parts) != 4 or value not in PUNISH_ACTIONS:
        await callback.answer()
        return
    try:
        chat_id, thread_id = int(parts[1]), int(parts[2])
    except ValueError:
        await callback.answer()
        return
    await crud.set_slow_mode_topic(session, chat_id, thread_id, punish_action=value)
    await session.commit()
    await _sm_punish_redraw(callback, chat_id, thread_id, _, _raw, session)
    await callback.answer()


async def _dm_sm_punish_duration(
    callback: types.CallbackQuery,
    action: str,
    state: FSMContext,
    _: Callable[..., str],
    _raw: Callable[..., str],
    session: AsyncSession,
) -> None:
    """Punishment duration for mute/ban (``dm:smwd:``; 0 = forever)."""
    ids = _sm_ids(action, 3)
    if ids is None:
        await callback.answer()
        return
    chat_id, thread_id, seconds = ids
    await crud.set_slow_mode_topic(
        session, chat_id, thread_id, punish_duration=(seconds if seconds > 0 else None)
    )
    await session.commit()
    await _sm_punish_redraw(callback, chat_id, thread_id, _, _raw, session)
    await callback.answer()


def _sm_ids(action: str, count: int) -> list[int] | None:
    """Parse the numeric tail of a callback action (``smt:<chat>:<thread>``)."""
    parts = action.split(":")
    if len(parts) != count + 1:
        return None
    try:
        return [int(part) for part in parts[1:]]
    except ValueError:
        return None


async def _sm_topic_label(session: AsyncSession, chat_id: int, thread_id: int) -> str:
    """Heading for a topic: its stored name, or «#<thread_id>» when unknown."""
    for topic in await crud.list_topics(session, chat_id):
        if topic.thread_id == thread_id:
            return _topic_label(topic)
    return f"#{thread_id}"


async def _dm_sm_topics_list(
    callback: types.CallbackQuery,
    action: str,
    state: FSMContext,
    _: Callable[..., str],
    _raw: Callable[..., str],
    session: AsyncSession,
) -> None:
    """«⚙️ Ветки и их параметры»: the topic list with a ⚙️ per thread.

    Reachable straight from the slow-mode entry screen (no typing needed).
    Sets ``awaiting_topics`` seeded with the chat-level config, so the «Все
    ветки» / «✅ Готово» / «✏️ Добавить ID» buttons keep working from here as
    they do right after «вкл».
    """
    ids = _sm_ids(action, 1)
    if ids is None:
        await callback.answer()
        return
    chat_id = ids[0]
    topics = await crud.list_topics(session, chat_id)
    overrides = await crud.list_slow_mode_topics(session, chat_id)
    cfg = await _sm_seed_topics_state(state, session, chat_id)
    selected = list(cfg.topic_ids) if cfg is not None and cfg.topic_ids else []
    if not topics and not selected:
        await _edit_or_answer(
            callback,
            _("dm_sm_topics_empty"),
            _build_sm_topics_empty_kb(_raw, chat_id, cfg=cfg),
        )
    else:
        await _edit_or_answer(
            callback,
            _("dm_sm_topics_prompt"),
            _build_sm_topics_kb(_raw, chat_id, topics, selected, overrides, cfg=cfg),
        )
    await callback.answer()


async def _dm_sm_topic(
    callback: types.CallbackQuery,
    action: str,
    state: FSMContext,
    _: Callable[..., str],
    _raw: Callable[..., str],
    session: AsyncSession,
) -> None:
    """The topic's settings = its hour grid (``dm:smt:<chat_id>:<thread_id>``).

    Pressing «⚙️» in the topic list lands here directly: there is no separate
    per-topic screen between the list and the choice of the limit.
    """
    ids = _sm_ids(action, 2)
    if ids is None:
        await callback.answer()
        return
    chat_id, thread_id = ids
    await _sm_show_pick(callback, chat_id, thread_id, _, _raw, session)
    await callback.answer()


async def _dm_sm_topic_set(
    callback: types.CallbackQuery,
    action: str,
    state: FSMContext,
    _: Callable[..., str],
    _raw: Callable[..., str],
    session: AsyncSession,
) -> None:
    """«✍️ Ввести вручную»: ask for the topic's limit in hours (``dm:smtp:``)."""
    ids = _sm_ids(action, 2)
    if ids is None:
        await callback.answer()
        return
    chat_id, thread_id = ids
    label = await _sm_topic_label(session, chat_id, thread_id)
    await state.set_state(DmSlowMode.awaiting_topic_params)
    await state.update_data(chat_id=chat_id, thread_id=thread_id)
    await _edit_or_answer(
        callback,
        _("dm_sm_topic_params_prompt", topic=label),
        _build_sm_topic_prompt_kb(_raw, chat_id, thread_id),
    )
    await callback.answer()


def _sm_pick_ids(action: str) -> tuple[int, int] | None:
    """Parse ``smtv*:<chat>:<thread>`` (a legacy trailing role letter is dropped).

    Old buttons shipped a ``:r``/``:w`` segment when the topic had per-role
    intervals; those presses now set the topic's single «for everyone» value.
    """
    parts = action.split(":")
    if len(parts) == 4 and parts[3] in ("r", "w"):
        parts = parts[:3]
    if len(parts) != 3:
        return None
    try:
        return int(parts[1]), int(parts[2])
    except ValueError:
        return None


async def _sm_set_topic_interval(
    session: AsyncSession,
    chat_id: int,
    thread_id: int,
    seconds: int | None,
) -> None:
    """Write the topic's limit for EVERYONE (``None`` = follow the chat).

    ``regular_seconds`` is the single value the rule uses for every role in the
    topic; a legacy ``wl_seconds`` is cleared on every write so no stale split
    survives in the database. Choosing a limit also turns the rule on in this
    topic — otherwise a value set while the topic was off would look inert.
    """
    await crud.set_slow_mode_topic(
        session,
        chat_id,
        thread_id,
        enabled=True,
        regular_seconds=seconds,
        wl_seconds=None,
    )


async def _sm_clear_topic_interval(
    session: AsyncSession, chat_id: int, thread_id: int
) -> None:
    """«↩️ Как в чате»: drop the topic's own limit.

    A row that only carried the limit is removed outright, so the topic stops
    being marked as «own params» in the list and the chat's topic scope applies
    to it again (a leftover row would enlarge the scope). A row that switches
    the rule off here is kept — «⛔ выключено здесь» is a separate setting.
    """
    override = await crud.get_slow_mode_topic(session, chat_id, thread_id)
    if override is not None and not override.enabled:
        await crud.set_slow_mode_topic(
            session, chat_id, thread_id, regular_seconds=None, wl_seconds=None
        )
        return
    await crud.clear_slow_mode_topic(session, chat_id, thread_id)


async def _sm_show_pick(
    callback: types.CallbackQuery,
    chat_id: int,
    thread_id: int,
    _: Callable[..., str],
    _raw: Callable[..., str],
    session: AsyncSession,
) -> None:
    """Draw the topic's hour grid, marking the current value."""
    cfg = await crud.get_slow_mode(session, chat_id)
    override = await crud.get_slow_mode_topic(session, chat_id, thread_id)
    label = await _sm_topic_label(session, chat_id, thread_id)
    own = override is not None and override.regular_seconds is not None
    await _edit_or_answer(
        callback,
        _sm_pick_text(_, cfg, override, label),
        _build_sm_topic_pick_kb(
            _raw, chat_id, thread_id, _sm_topic_hours(cfg, override), own
        ),
    )


async def _dm_sm_topic_pick(
    callback: types.CallbackQuery,
    action: str,
    state: FSMContext,
    _: Callable[..., str],
    _raw: Callable[..., str],
    session: AsyncSession,
) -> None:
    """«🕒 Лимит для всех»: open the topic's hour grid (``dm:smtv:``)."""
    ids = _sm_pick_ids(action)
    if ids is None:
        await callback.answer()
        return
    chat_id, thread_id = ids
    await _sm_show_pick(callback, chat_id, thread_id, _, _raw, session)
    await callback.answer()


async def _dm_sm_topic_pick_set(
    callback: types.CallbackQuery,
    action: str,
    state: FSMContext,
    _: Callable[..., str],
    _raw: Callable[..., str],
    session: AsyncSession,
) -> None:
    """A preset press: pin the value (0 = «без лимита») and redraw (``smtvs:``)."""
    parts = action.split(":")
    if len(parts) == 5 and parts[3] in ("r", "w"):
        parts = parts[:3] + [parts[4]]  # legacy role segment
    if len(parts) != 4:
        await callback.answer()
        return
    try:
        chat_id, thread_id, hours = int(parts[1]), int(parts[2]), int(parts[3])
    except ValueError:
        await callback.answer()
        return
    if not 0 <= hours <= 720:
        await callback.answer(_("dm_sm_topic_pick_bad"), show_alert=True)
        return
    await _sm_set_topic_interval(session, chat_id, thread_id, hours * 3600)
    await session.commit()
    await _sm_show_pick(callback, chat_id, thread_id, _, _raw, session)
    await callback.answer()


async def _dm_sm_topic_pick_step(
    callback: types.CallbackQuery,
    action: str,
    state: FSMContext,
    _: Callable[..., str],
    _raw: Callable[..., str],
    session: AsyncSession,
) -> None:
    """«±1 ч»: step one hour, clamped to 1..720 (``dm:smtvm:`` / ``dm:smtvp:``)."""
    ids = _sm_pick_ids(action)
    if ids is None:
        await callback.answer()
        return
    chat_id, thread_id = ids
    delta = -1 if action.startswith("smtvm:") else 1
    cfg = await crud.get_slow_mode(session, chat_id)
    override = await crud.get_slow_mode_topic(session, chat_id, thread_id)
    current = _sm_topic_hours(cfg, override)
    if current <= 0:
        # «∞» has no arithmetic — step to the nearest finite end.
        hours = 1 if delta > 0 else 720
    else:
        hours = max(1, min(720, current + delta))
    await _sm_set_topic_interval(session, chat_id, thread_id, hours * 3600)
    await session.commit()
    await _sm_show_pick(callback, chat_id, thread_id, _, _raw, session)
    await callback.answer()


async def _dm_sm_topic_pick_inherit(
    callback: types.CallbackQuery,
    action: str,
    state: FSMContext,
    _: Callable[..., str],
    _raw: Callable[..., str],
    session: AsyncSession,
) -> None:
    """«↩️ Как в чате»: store NULL so the chat's rule applies in this topic."""
    ids = _sm_pick_ids(action)
    if ids is None:
        await callback.answer()
        return
    chat_id, thread_id = ids
    await _sm_clear_topic_interval(session, chat_id, thread_id)
    await session.commit()
    await _sm_show_pick(callback, chat_id, thread_id, _, _raw, session)
    await callback.answer()


async def _dm_sm_topic_reset(
    callback: types.CallbackQuery,
    action: str,
    state: FSMContext,
    _: Callable[..., str],
    _raw: Callable[..., str],
    session: AsyncSession,
) -> None:
    """«↩️ Как в чате»: drop the override row so the topic inherits again."""
    ids = _sm_ids(action, 2)
    if ids is None:
        await callback.answer()
        return
    chat_id, thread_id = ids
    await crud.clear_slow_mode_topic(session, chat_id, thread_id)
    await session.commit()
    await _sm_show_pick(callback, chat_id, thread_id, _, _raw, session)
    await callback.answer()


# --------------------------------------------------------------------------- #
# Статистика & Топ (dm:a:stats:<chat_id> / dm:a:top:<chat_id> — from the
# group panel; mirrors /stats + /top)
# --------------------------------------------------------------------------- #
async def _dm_stats(
    callback: types.CallbackQuery,
    chat_id: int,
    _: Callable[..., str],
    _raw: Callable[..., str],
    session: AsyncSession,
) -> None:
    """Show the SELECTED group's totals + top (mirrors /stats + /top)."""
    chat = await crud.get_chat(session, chat_id)
    if chat is None:
        await _edit_or_answer(callback, _("dm_panel_missing"), _home_kb(_raw))
        await callback.answer()
        return
    title = (chat.title or "").strip() or str(chat_id)
    total, users = await crud.chat_activity_totals(session, chat_id)
    banned = await crud.count_bans(session, chat_id)
    warns = await crud.count_warns_chat(session, chat_id)
    text = _(
        "dm_stats_text",
        title=title,
        total=total,
        users=users,
        banned=banned,
        warns=warns,
    )
    rows = await crud.top_active(session, chat_id, TOP_DEFAULT)
    if rows:
        names = await crud.get_users_by_ids(session, [uid for uid, _c in rows])
        lines = [
            _(
                "top_item",
                medal=StatsService.medal(idx),
                user=build_mention(uid, names.get(uid) or str(uid)),
                count=count,
            )
            for idx, (uid, count) in enumerate(rows, start=1)
        ]
        text += "\n\n" + _("top_header", count=len(rows)) + "\n" + "\n".join(lines)
    else:
        text += "\n\n" + _("top_empty")
    await _edit_or_answer(callback, text, _panel_kb(_raw, chat_id))
    await callback.answer()


async def _dm_top(
    callback: types.CallbackQuery,
    chat_id: int,
    _: Callable[..., str],
    _raw: Callable[..., str],
    session: AsyncSession,
) -> None:
    """Show the SELECTED group's most active users (mirrors /top)."""
    chat = await crud.get_chat(session, chat_id)
    if chat is None:
        await _edit_or_answer(callback, _("dm_panel_missing"), _home_kb(_raw))
        await callback.answer()
        return
    rows = await crud.top_active(session, chat_id, TOP_DEFAULT)
    if not rows:
        text = _("top_empty")
    else:
        names = await crud.get_users_by_ids(session, [uid for uid, _c in rows])
        lines = [
            _(
                "top_item",
                medal=StatsService.medal(idx),
                user=build_mention(uid, names.get(uid) or str(uid)),
                count=count,
            )
            for idx, (uid, count) in enumerate(rows, start=1)
        ]
        text = _("top_header", count=len(rows)) + "\n" + "\n".join(lines)
    await _edit_or_answer(callback, text, _panel_kb(_raw, chat_id))
    await callback.answer()


# --------------------------------------------------------------------------- #
# Рассылки tab (dm:bc:<chat_id> → topics → dm:bcgo:<chat_id>)
# --------------------------------------------------------------------------- #
async def _dm_broadcast_topics(
    callback: types.CallbackQuery,
    chat_id_token: str,
    state: FSMContext,
    _: Callable[..., str],
    _raw: Callable[..., str],
    session: AsyncSession,
) -> None:
    """Render the topic multi-select for a group's forum."""
    try:
        chat_id = int(chat_id_token)
    except ValueError:
        await callback.answer()
        return
    chat = await crud.get_chat(session, chat_id)
    if chat is None:
        await _edit_or_answer(callback, _("dm_panel_missing"), _home_kb(_raw))
        await callback.answer()
        return
    title = (chat.title or "").strip() or str(chat_id)
    topics = await crud.list_topics(session, chat_id)
    if not topics:
        await _edit_or_answer(callback, _("dm_bc_no_topics"), _bc_groups_kb(_raw))
        await callback.answer()
        return
    # Remember which chat the selection belongs to; reset on chat switch.
    state_data = await state.get_data()
    if state_data.get("bc_chat_id") != chat_id:
        await state.update_data(bc_chat_id=chat_id, selected=[])
        selected: list[int] = []
    else:
        selected = list(state_data.get("selected", []))
    # Leaving the topics screen (or arriving here from the text FSM) drops
    # any pending DmBroadcast state — the user must press «Ввести текст» again.
    await state.set_state(None)
    await _edit_or_answer(
        callback,
        _("dm_bc_topics_title", title=title),
        _build_topics_kb(_raw, chat_id, topics, selected),
    )
    await callback.answer()


async def _dm_broadcast_toggle(
    callback: types.CallbackQuery,
    action: str,
    state: FSMContext,
    _: Callable[..., str],
    _raw: Callable[..., str],
    session: AsyncSession,
) -> None:
    """Toggle a topic in the broadcast selection (``dm:bct:<chat>:<thread>``)."""
    parts = action.split(":")
    if len(parts) != 3:
        await callback.answer()
        return
    try:
        chat_id, thread_id = int(parts[1]), int(parts[2])
    except ValueError:
        await callback.answer()
        return
    state_data = await state.get_data()
    selected = list(state_data.get("selected", []))
    if thread_id in selected:
        selected.remove(thread_id)
    else:
        selected.append(thread_id)
    await state.update_data(selected=selected)
    topics = await crud.list_topics(session, chat_id)
    if callback.message is not None:
        try:
            await callback.message.edit_reply_markup(
                reply_markup=_build_topics_kb(_raw, chat_id, topics, selected)
            )
        except Exception:
            logger.debug("dm.bc_toggle_edit_failed", exc_info=True)
    await callback.answer()


async def _dm_bc_refresh(
    callback: types.CallbackQuery,
    action: str,
    state: FSMContext,
    _: Callable[..., str],
    _raw: Callable[..., str],
    session: AsyncSession,
) -> None:
    """«🔄 Обновить список» for the broadcast topic picker (re-read the DB)."""
    parts = action.split(":")
    if len(parts) != 2:
        await callback.answer()
        return
    try:
        chat_id = int(parts[1])
    except ValueError:
        await callback.answer()
        return
    chat = await crud.get_chat(session, chat_id)
    if chat is None:
        await _edit_or_answer(callback, _("dm_panel_missing"), _home_kb(_raw))
        await callback.answer()
        return
    title = (chat.title or "").strip() or str(chat_id)
    selected = list((await state.get_data()).get("selected", []))
    topics = await crud.list_topics(session, chat_id)
    if not topics and not selected:
        await _edit_or_answer(callback, _("dm_bc_no_topics"), _bc_groups_kb(_raw))
    else:
        await _edit_or_answer(
            callback,
            _("dm_bc_topics_title", title=title),
            _build_topics_kb(_raw, chat_id, topics, selected),
        )
    await callback.answer()


async def _dm_bc_add_id(
    callback: types.CallbackQuery,
    action: str,
    state: FSMContext,
    _: Callable[..., str],
    _raw: Callable[..., str],
    session: AsyncSession,
) -> None:
    """«✏️ Добавить ID ветки вручную» for broadcasts → ask for the id (FSM)."""
    parts = action.split(":")
    if len(parts) != 2:
        await callback.answer()
        return
    try:
        chat_id = int(parts[1])
    except ValueError:
        await callback.answer()
        return
    await state.update_data(bc_chat_id=chat_id)
    await state.set_state(DmBroadcast.awaiting_topic_id)
    await _edit_or_answer(callback, _("dm_topic_id_prompt"), _home_kb(_raw))
    await callback.answer()


async def _dm_broadcast_go(
    callback: types.CallbackQuery,
    chat_id_token: str,
    state: FSMContext,
    _: Callable[..., str],
    _raw: Callable[..., str],
) -> None:
    """Start the broadcast-text flow (``dm:bcgo:<chat_id>``)."""
    try:
        chat_id = int(chat_id_token)
    except ValueError:
        await callback.answer()
        return
    state_data = await state.get_data()
    selected = list(state_data.get("selected", []))
    if not selected:
        await callback.answer(_("dm_bc_none"))
        return
    await state.set_state(DmBroadcast.awaiting_text)
    await state.update_data(chat_id=chat_id, thread_ids=selected)
    await _edit_or_answer(
        callback, _("dm_bc_text_prompt"), _bc_text_kb(_raw, chat_id)
    )
    await callback.answer()


# --------------------------------------------------------------------------- #
# FSM message handlers
# --------------------------------------------------------------------------- #
@router.message(IsPrivate(), StateFilter(DmScam.awaiting_target))
async def dm_scam_target(
    message: types.Message, state: FSMContext, **data: Any
) -> None:
    """Treat the message as the seller target and answer with the /scam verdict.

    No ``F.text`` on purpose: a reply-to-seller message must reach us too, so
    ``resolve_target`` can use ``reply_to_message``. On any failure the FSM
    state is kept and the user gets the «◀️ В меню» keyboard to bail out.

    When the flow was started from a legacy per-group callback (state carries
    a ``chat_id``), the verdict's join-date risk factors are scoped to that
    SELECTED group via ``risk_chat`` and the result carries the panel
    keyboard. From the Рейтинги tab (``chat_id`` is ``None``) there is no
    ``risk_chat`` and the result carries «🏠 В меню».
    """
    _ = data["_"]
    _raw = data["_raw"]
    session: AsyncSession = data["session"]
    bot: Bot = message.bot

    state_data = await state.get_data()
    risk_chat_id = state_data.get("chat_id")
    risk_chat: types.Chat | None = None
    if risk_chat_id is not None:
        risk_chat = types.Chat(id=risk_chat_id, type="supergroup")

    if message.reply_to_message is not None:
        target, error_key, _consumed = await resolve_target(
            message, [], session, bot
        )
    else:
        target, error_key, _consumed = await resolve_target(
            message, (message.text or "").split(), session, bot
        )

    if error_key is not None or target is None:
        # Keep the state so the user can retry; offer the back button.
        kb = _panel_kb(_raw, risk_chat.id) if risk_chat is not None else _back_kb(_raw)
        await message.answer(_(map_scam_error(error_key)), reply_markup=kb)
        return

    # Собственный username бота — не цель, а её отсутствие (как в /scam).
    if target.user_id == bot.id:
        kb = _panel_kb(_raw, risk_chat.id) if risk_chat is not None else _back_kb(_raw)
        await message.answer(_("scam_no_target"), reply_markup=kb)
        return

    body = await build_scam_verdict(message, target, data, risk_chat=risk_chat)
    await state.clear()
    if risk_chat is not None:
        kb = _panel_kb(_raw, risk_chat.id)
    else:
        kb = _home_kb(_raw)
    await message.answer(
        f"{body}\n\n{_('scam_footer')}",
        parse_mode="HTML",
        reply_markup=kb,
    )


@router.message(IsPrivate(), StateFilter(DmAdmin.awaiting_target))
async def dm_admin_target(
    message: types.Message, state: FSMContext, **data: Any
) -> None:
    """Apply a panel-chosen moderation action to a target in the SELECTED group.

    The DM gate already restricts the panel to the allowed owner whitelist, so
    the panel passes ``is_admin=True`` into ``prepare_action``; ``is_owner``
    stays as-is, so Alex (non-owner) still passes the fresh ``is_user_admin``
    re-check against the SELECTED group. On a failed guard the FSM state is
    kept — the user can retry or press «◀️ Панель группы» / «🏠 В меню».
    """
    _ = data["_"]
    _raw = data["_raw"]
    session: AsyncSession = data["session"]
    bot: Bot = message.bot

    state_data = await state.get_data()
    action = state_data.get("action")
    chat_id = state_data.get("chat_id")
    if action is None or chat_id is None:
        # No action context (stale state) — drop back to the main menu.
        await state.clear()
        await message.answer(_("dm_menu_title"), reply_markup=build_main_menu(_raw))
        return

    args = (message.text or "").split() if message.text else []
    back_kb = _panel_kb(_raw, chat_id)

    if action in ("wl", "wl_remove"):
        target, error_key, _consumed = await resolve_target(
            message, args, session, bot
        )
        if error_key is not None or target is None:
            await message.answer(_("scam_no_target"), reply_markup=back_kb)
            return
        # Собственный username бота — не цель, а её отсутствие (как в /scam).
        if target.user_id == bot.id:
            await message.answer(_("scam_no_target"), reply_markup=back_kb)
            return
        mention = build_mention(target.user_id, target.name)
        if action == "wl":
            # Upsert: whitelisting a previously flagged user overrides source.
            await crud.upsert_scam_entry(
                session, target.user_id, SCAM_SOURCE_VERIFIED, None
            )
            key = "addtowl_added"
        else:
            removed = await crud.remove_scam_entry(session, target.user_id)
            key = "addtowl_removed" if removed else "addtowl_not_found"
        await state.clear()
        await message.answer(
            _(key, user=mention), parse_mode="HTML", reply_markup=back_kb
        )
        return

    flags = _ACTION_FLAGS.get(action)
    if flags is None:
        await state.clear()
        return
    allow_duration, protect_target, need_restrict = flags
    prep = await prepare_action(
        message,
        args,
        dict(data, is_admin=True),
        chat_id=chat_id,
        allow_duration=allow_duration,
        protect_target=protect_target,
        need_restrict=need_restrict,
    )
    if prep is None:
        # Guards already replied with a localized error; keep the state so the
        # user can retry or bail out via the back keyboard.
        return

    actor_id = message.from_user.id if message.from_user is not None else 0
    target = prep.target
    mention = build_mention(target.user_id, target.name)
    suffix = _reason_suffix(_, prep.reason)

    if action == "ban":
        ok = await actions.do_ban(
            bot,
            session,
            chat_id,
            actor_id,
            target.user_id,
            prep.duration,
            prep.reason,
        )
        if not ok:
            text = _("error_bot_not_admin")
        elif prep.duration:
            text = _(
                "mod_ban_temp",
                user=mention,
                duration=format_duration(prep.duration),
                reason=suffix,
            )
        else:
            text = _("mod_ban", user=mention, reason=suffix)
    elif action == "kick":
        ok = await actions.do_kick(
            bot, session, chat_id, actor_id, target.user_id, prep.reason
        )
        if not ok:
            text = _("error_bot_not_admin")
        else:
            text = _("mod_kick", user=mention, reason=suffix)
    elif action == "mute":
        ok = await actions.do_mute(
            bot,
            session,
            chat_id,
            actor_id,
            target.user_id,
            prep.duration,
            prep.reason,
        )
        if not ok:
            text = _("error_bot_not_admin")
        elif prep.duration:
            text = _(
                "mod_mute_temp",
                user=mention,
                duration=format_duration(prep.duration),
                reason=suffix,
            )
        else:
            text = _("mod_mute", user=mention, reason=suffix)
    elif action == "unban":
        ok = await actions.do_unban(
            bot, session, chat_id, actor_id, target.user_id
        )
        text = _("mod_unban" if ok else "mod_not_banned", user=mention)
    elif action == "unmute":
        await actions.do_unmute(bot, session, chat_id, actor_id, target.user_id)
        text = _("mod_unmute", user=mention)
    elif action == "warn":
        # data["settings"] in DM is None — load the SELECTED group's settings
        # so do_warn can apply warn_limit / warn_action correctly.
        settings_obj = await crud.get_or_create_settings(session, chat_id)
        settings = crud.settings_to_dict(settings_obj)
        outcome = await actions.do_warn(
            bot,
            session,
            chat_id,
            actor_id,
            target.user_id,
            prep.reason,
            settings,
        )
        if outcome.action_applied:
            text = _(
                "mod_warn_action",
                user=mention,
                count=outcome.count,
                limit=outcome.limit,
                action=outcome.action_applied,
            )
        else:
            text = _(
                "mod_warn",
                user=mention,
                count=outcome.count,
                limit=outcome.limit,
                reason=suffix,
            )
    elif action == "unwarn":
        # Mirror cmd_unwarn: no do_unwarn in actions.py — deactivate + recount.
        removed = await crud.deactivate_last_warn(
            session, chat_id, target.user_id
        )
        if not removed:
            text = _("mod_unwarn_none", user=mention)
        else:
            count = await crud.count_active_warns(
                session, chat_id, target.user_id
            )
            await crud.add_mod_log(
                session, chat_id, actor_id, target.user_id, "unwarn"
            )
            text = _("mod_unwarn", user=mention, count=count)
    else:  # action == "warns"
        warns = await crud.list_active_warns(session, chat_id, target.user_id)
        settings_obj = await crud.get_or_create_settings(session, chat_id)
        limit = int(crud.settings_to_dict(settings_obj)["warn_limit"])
        if not warns:
            text = _("mod_warns_none", user=mention)
        else:
            lines = [
                _(
                    "mod_warns_header",
                    user=mention,
                    count=len(warns),
                    limit=limit,
                )
            ]
            for idx, warn in enumerate(warns, start=1):
                lines.append(
                    _(
                        "mod_warns_item",
                        index=idx,
                        reason=(
                            escape_html(warn.reason)
                            if warn.reason
                            else _("no_reason")
                        ),
                        date=warn.created_at.strftime("%Y-%m-%d %H:%M"),
                    )
                )
            text = "\n".join(lines)

    await state.clear()
    await message.answer(text, parse_mode="HTML", reply_markup=back_kb)


@router.message(IsPrivate(), F.text, StateFilter(DmSlowMode.awaiting_config))
async def dm_slow_mode_config(
    message: types.Message, state: FSMContext, **data: Any
) -> None:
    """Parse the slow-mode config: ``вкл|выкл|on|off [hours_regular] [hours_wl]``.

    Bad input keeps the FSM state so the user can retry (or bail out via the
    panel keyboard). Hours are clamped to [1, 720]. «выкл» saves immediately;
    «вкл» stores the pending config and moves to the topic multi-select
    (``DmSlowMode.awaiting_topics``) before saving. When the forum has no
    tracked topics yet the multi-select is replaced by a hint screen — the bot
    never silently saves «all topics» on the user's behalf.
    """
    _ = data["_"]
    _raw = data["_raw"]
    session: AsyncSession = data["session"]

    state_data = await state.get_data()
    chat_id = state_data.get("chat_id")
    if chat_id is None:
        # Stale state — drop back to the main menu.
        await state.clear()
        await message.answer(_("dm_menu_title"), reply_markup=build_main_menu(_raw))
        return

    kb = _panel_kb(_raw, chat_id)
    tokens = (message.text or "").strip().lower().split()
    if not tokens or tokens[0] not in ("вкл", "выкл", "on", "off"):
        await message.answer(_("dm_sm_bad"), reply_markup=kb)
        return
    hours_args = tokens[1:]
    if len(hours_args) > 2 or any(not h.isdigit() for h in hours_args):
        await message.answer(_("dm_sm_bad"), reply_markup=kb)
        return

    def _clamp(hours: int) -> int:
        return max(1, min(720, hours))

    if tokens[0] in ("выкл", "off"):
        # Disable immediately; the stored topic scope stays untouched.
        cfg = await crud.get_slow_mode(session, chat_id)
        regular_h = (cfg.regular_seconds // 3600) if cfg is not None else 0
        wl_h = (cfg.wl_seconds // 3600) if cfg is not None else 0
        await crud.set_slow_mode(session, chat_id, enabled=False)
        await session.commit()
        await state.clear()
        await message.answer(
            _(
                "dm_sm_saved",
                regular=regular_h,
                wl=wl_h,
                topics=_sm_topics_summary(_, cfg.topic_ids if cfg is not None else None),
            ),
            reply_markup=kb,
        )
        return

    cfg = await crud.get_slow_mode(session, chat_id)
    if len(hours_args) >= 1:
        regular_h = _clamp(int(hours_args[0]))
    else:
        regular_h = _clamp((cfg.regular_seconds // 3600) if cfg is not None else 6)
    if len(hours_args) >= 2:
        wl_h = _clamp(int(hours_args[1]))
    else:
        wl_h = _clamp((cfg.wl_seconds // 3600) if cfg is not None else 3)
    regular_seconds = regular_h * 3600
    wl_seconds = wl_h * 3600

    # Tracked topics exist → topic multi-select step. An EMPTY list must not
    # silently save «all topics» (the user never chose that): show a hint
    # screen explaining that Telegram doesn't hand bots the topic list, with
    # «Все ветки» / «Добавить ID» / «Назад» instead.
    topics = await crud.list_topics(session, chat_id)
    current = list(cfg.topic_ids) if (cfg is not None and cfg.topic_ids) else []
    overrides = await crud.list_slow_mode_topics(session, chat_id)
    await state.set_state(DmSlowMode.awaiting_topics)
    await state.update_data(
        chat_id=chat_id,
        selected_topics=current,
        pending_sm={
            "enabled": True,
            "regular": regular_seconds,
            "wl": wl_seconds,
        },
    )
    if not topics:
        await message.answer(
            _("dm_sm_topics_empty"),
            reply_markup=_build_sm_topics_empty_kb(
                _raw,
                chat_id,
                cfg=await crud.get_slow_mode(session, chat_id),
            ),
        )
        return
    await message.answer(
        _("dm_sm_topics_prompt"),
        reply_markup=_build_sm_topics_kb(
            _raw,
            chat_id,
            topics,
            current,
            overrides,
            cfg=await crud.get_slow_mode(session, chat_id),
        ),
    )


@router.message(IsPrivate(), F.text, StateFilter(DmSlowMode.awaiting_topic_id))
async def dm_sm_topic_id(
    message: types.Message, state: FSMContext, **data: Any
) -> None:
    """Manual thread-id entry for the slow-mode topic scope.

    Accepts a bare number or a ``t.me/c/<...>/<id>`` link (last number wins).
    On success the id joins the selection and the picker is redrawn; bad input
    keeps the FSM state so the user can retry.
    """
    _ = data["_"]
    _raw = data["_raw"]
    session: AsyncSession = data["session"]

    state_data = await state.get_data()
    chat_id = state_data.get("chat_id")
    if chat_id is None:
        await state.clear()
        await message.answer(_("dm_menu_title"), reply_markup=build_main_menu(_raw))
        return

    thread_id = parse_thread_id(message.text or "")
    if thread_id is None:
        await message.answer(_("dm_topic_id_bad"), reply_markup=_home_kb(_raw))
        return

    selected = list(state_data.get("selected_topics", []))
    if thread_id not in selected:
        selected.append(thread_id)
    await state.set_state(DmSlowMode.awaiting_topics)
    await state.update_data(chat_id=chat_id, selected_topics=selected)
    topics = await crud.list_topics(session, chat_id)
    await message.answer(
        _("dm_sm_topics_prompt"),
        reply_markup=_build_sm_topics_kb(
            _raw,
            chat_id,
            topics,
            selected,
            await crud.list_slow_mode_topics(session, chat_id),
            cfg=await crud.get_slow_mode(session, chat_id),
        ),
    )


@router.message(IsPrivate(), F.text, StateFilter(DmSlowMode.awaiting_topic_params))
async def dm_sm_topic_params(
    message: types.Message, state: FSMContext, **data: Any
) -> None:
    """Topic's limit typed by hand (``dm:smtp:`` step; the buttons are the norm).

    A single number is the limit for EVERYONE in this topic (clamped to
    [1, 720] hours); «0» / «∞» / «без лимита» stands for «без лимита».
    «вкл» turns the rule on here with the chat's intervals, «выкл» switches it
    off in this topic only and «сброс» drops the override so the topic follows
    the chat again. Bad input keeps the FSM state.
    """
    _ = data["_"]
    _raw = data["_raw"]
    session: AsyncSession = data["session"]

    state_data = await state.get_data()
    chat_id = state_data.get("chat_id")
    thread_id = state_data.get("thread_id")
    if chat_id is None or thread_id is None:
        await state.clear()
        await message.answer(_("dm_menu_title"), reply_markup=build_main_menu(_raw))
        return

    cfg = await crud.get_slow_mode(session, chat_id)  # for the redraw below
    tokens = (message.text or "").strip().lower().split()

    async def redraw(bad: bool = False) -> None:
        """Redraw the topic's hour grid, or repeat the prompt on a bad value."""
        if bad:
            await message.answer(
                _("dm_sm_topic_params_bad"),
                reply_markup=_build_sm_topic_prompt_kb(_raw, chat_id, thread_id),
            )
            return
        await state.clear()
        override = await crud.get_slow_mode_topic(session, chat_id, thread_id)
        label = await _sm_topic_label(session, chat_id, thread_id)
        own = override is not None and override.regular_seconds is not None
        await message.answer(
            _sm_pick_text(_, cfg, override, label),
            reply_markup=_build_sm_topic_pick_kb(
                _raw, chat_id, thread_id, _sm_topic_hours(cfg, override), own
            ),
        )

    if not tokens:
        await redraw(bad=True)
        return

    head, args = tokens[0], tokens[1:]
    if head in ("сброс", "reset"):
        await crud.clear_slow_mode_topic(session, chat_id, thread_id)
        await session.commit()
        await redraw()
        return
    if head in ("выкл", "off"):
        await crud.set_slow_mode_topic(session, chat_id, thread_id, enabled=False)
        await session.commit()
        await redraw()
        return
    if head in ("вкл", "on"):
        if not args:
            # «вкл» без чисел — включить здесь на интервалах чата.
            await crud.set_slow_mode_topic(
                session,
                chat_id,
                thread_id,
                enabled=True,
                regular_seconds=None,
                wl_seconds=None,
            )
            await session.commit()
            await redraw()
            return
        rest = args
    else:
        rest = tokens

    # Одно число = лимит для всех: «6», «6ч», «6 h»; «0»/«∞»/«без лимита» — ∞.
    units = ("ч", "ч.", "h", "час", "часа", "часов", "hour", "hours")
    unlimited = ("0", "∞", "inf", "unlimited", "безлимита", "без лимита", "бесконечно")
    number = re.compile(r"(\d+)\s*(?:ч|h|час|часа|часов|hour|hours)?\.?")
    stripped = " ".join(token for token in rest if token not in units)
    match = number.fullmatch(stripped)
    if stripped in unlimited:
        seconds = 0
    elif match is not None:
        seconds = max(1, min(720, int(match.group(1)))) * 3600
    else:
        await redraw(bad=True)
        return

    await _sm_set_topic_interval(session, chat_id, thread_id, seconds)
    await session.commit()
    await redraw()


@router.message(IsPrivate(), F.text, StateFilter(DmSlowMode.awaiting_sm_punish_text))
async def dm_sm_punish_text(
    message: types.Message, state: FSMContext, **data: Any
) -> None:
    """Warning text typed by hand (the «📝 Текст предупреждения» step).

    «-» / «сброс» drop the custom text so violators get the built-in wording
    again; a text over ``_SM_WARN_TEXT_MAX`` characters is refused and the
    prompt is repeated. The cached settings are invalidated on every write —
    otherwise the running bot would keep the old text until the cache expires.
    """
    _ = data["_"]
    _raw = data["_raw"]
    session: AsyncSession = data["session"]

    state_data = await state.get_data()
    chat_id = state_data.get("chat_id")
    thread_id = state_data.get("thread_id")
    if chat_id is None or thread_id is None:
        await state.clear()
        await message.answer(_("dm_menu_title"), reply_markup=build_main_menu(_raw))
        return

    text = (message.text or "").strip()
    value: str | None
    if text.lower() in {"-", "—", "сброс", "reset", "по умолчанию", "default"}:
        value = None
    elif len(text) > _SM_WARN_TEXT_MAX:
        await message.answer(
            _("dm_sm_punish_text_long"),
            reply_markup=_sm_punish_nav_kb(_raw, chat_id, thread_id),
        )
        return
    else:
        value = text

    await crud.set_slow_mode_topic(session, chat_id, thread_id, punish_text=value)
    await session.commit()
    await state.clear()
    topic = await _sm_topic_label(session, chat_id, thread_id)
    row = await crud.get_slow_mode_topic(session, chat_id, thread_id)
    if row is None:
        await message.answer(
            _("dm_sm_punish_no_limit", topic=topic),
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [_sm_punish_locked_row(_raw, chat_id, thread_id)],
                    *_sm_punish_nav_kb(_raw, chat_id, thread_id).inline_keyboard,
                ]
            ),
        )
        return
    await message.answer(
        _sm_punish_text(_, row, topic),
        reply_markup=_build_sm_punish_kb(_raw, chat_id, thread_id, row),
    )


@router.message(IsPrivate(), F.text, StateFilter(DmBroadcast.awaiting_topic_id))
async def dm_bc_topic_id(
    message: types.Message, state: FSMContext, **data: Any
) -> None:
    """Manual thread-id entry for the broadcast topic selection."""
    _ = data["_"]
    _raw = data["_raw"]
    session: AsyncSession = data["session"]

    state_data = await state.get_data()
    chat_id = state_data.get("bc_chat_id")
    if chat_id is None:
        await state.clear()
        await message.answer(_("dm_menu_title"), reply_markup=build_main_menu(_raw))
        return

    thread_id = parse_thread_id(message.text or "")
    if thread_id is None:
        await message.answer(_("dm_topic_id_bad"), reply_markup=_home_kb(_raw))
        return

    selected = list(state_data.get("selected", []))
    if thread_id not in selected:
        selected.append(thread_id)
    await state.set_state(None)
    await state.update_data(bc_chat_id=chat_id, selected=selected)
    topics = await crud.list_topics(session, chat_id)
    chat = await crud.get_chat(session, chat_id)
    title = ((chat.title if chat else None) or "").strip() or str(chat_id)
    await message.answer(
        _("dm_bc_topics_title", title=title),
        reply_markup=_build_topics_kb(_raw, chat_id, topics, selected),
    )


@router.message(IsPrivate(), F.text, StateFilter(DmWl.awaiting_target))
async def dm_wl_target(
    message: types.Message, state: FSMContext, **data: Any
) -> None:
    """Whitelist add/remove from the Рейтинги tab (target awaiting).

    ``state_data["action"]`` is ``"add"`` or ``"remove"``. On a resolve
    failure (or the bot itself) the FSM state is kept so the user can retry.
    """
    _ = data["_"]
    _raw = data["_raw"]
    session: AsyncSession = data["session"]
    bot: Bot = message.bot

    state_data = await state.get_data()
    action = state_data.get("action")
    if action not in ("add", "remove"):
        # Stale state — drop back to the main menu.
        await state.clear()
        await message.answer(_("dm_menu_title"), reply_markup=build_main_menu(_raw))
        return

    target, error_key, _consumed = await resolve_target(
        message, (message.text or "").split(), session, bot
    )
    if error_key is not None or target is None:
        await message.answer(_("scam_no_target"), reply_markup=_home_kb(_raw))
        return
    # Собственный username бота — не цель, а её отсутствие (как в /scam).
    if target.user_id == bot.id:
        await message.answer(_("scam_no_target"), reply_markup=_home_kb(_raw))
        return

    mention = build_mention(target.user_id, target.name)
    if action == "add":
        # Upsert: whitelisting a previously flagged user overrides source.
        await crud.upsert_scam_entry(
            session, target.user_id, SCAM_SOURCE_VERIFIED, None
        )
        key = "addtowl_added"
    else:
        removed = await crud.remove_scam_entry(session, target.user_id)
        key = "addtowl_removed" if removed else "addtowl_not_found"
    await state.clear()
    await message.answer(
        _(key, user=mention), parse_mode="HTML", reply_markup=_home_kb(_raw)
    )


@router.message(IsPrivate(), F.text, StateFilter(DmBroadcast.awaiting_text))
async def dm_broadcast_text(
    message: types.Message, state: FSMContext, **data: Any
) -> None:
    """Send the broadcast text to the selected forum topics.

    Empty text keeps the FSM state. Otherwise ``send_broadcast`` is called
    per selected thread and the per-thread results are reported (first 10).
    """
    _ = data["_"]
    _raw = data["_raw"]

    state_data = await state.get_data()
    chat_id = state_data.get("chat_id")
    thread_ids = state_data.get("thread_ids") or []
    if chat_id is None or not thread_ids:
        # Stale state — drop back to the main menu.
        await state.clear()
        await message.answer(_("dm_menu_title"), reply_markup=build_main_menu(_raw))
        return

    kb = _bc_text_kb(_raw, chat_id)
    text = (message.text or "").strip()
    if not text:
        await message.answer(_("dm_bc_empty"), reply_markup=kb)
        return

    results = await send_broadcast(message.bot, chat_id, thread_ids, text)
    ok = sum(1 for r in results if r.get("ok"))
    fail = len(results) - ok
    lines = []
    for r in results[:10]:
        err = r.get("error") or ""
        mark = "✅" if r.get("ok") else "❌"
        lines.append(f"#{r['thread_id']}: {mark}{(' ' + err) if err else ''}")
    body = _("dm_bc_result", ok=ok, fail=fail)
    if lines:
        body += "\n" + "\n".join(lines)
    await state.clear()
    await message.answer(body, reply_markup=_home_kb(_raw))
