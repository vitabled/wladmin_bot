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
from collections.abc import Callable
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
from bot.services.stats import StatsService
from bot.utils.targets import resolve_target
from bot.utils.text import build_mention, escape_html, format_duration

logger = logging.getLogger(__name__)

router = Router()

_PREFIX = "dm"
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
    ``awaiting_topics`` is the topic multi-select step shown after «вкл»
    when the group has tracked forum topics.
    """

    awaiting_config = State()
    awaiting_topics = State()


class DmWl(StatesGroup):
    """FSM for whitelist add/remove from the Рейтинги tab (target awaiting)."""

    awaiting_target = State()


class DmBroadcast(StatesGroup):
    """FSM for the broadcast text (after topics were picked)."""

    awaiting_text = State()


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


def _build_topics_kb(
    _raw: Callable[..., str],
    chat_id: int,
    topics: list[Any],
    selected: list[int],
) -> types.InlineKeyboardMarkup:
    """Topic multi-select keyboard: ✅/⚪ icons per thread + go + home."""
    builder = InlineKeyboardBuilder()
    sel = set(selected)
    for topic in topics:
        builder.button(
            text=f"#{topic.thread_id}",
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
        text=_raw("dm_menu_home"),
        callback_data=f"{_PREFIX}:menu",
        icon_custom_emoji_id=_ICON_HOME,  # 🏠
    )
    builder.adjust(1)
    return builder.as_markup()


def _build_sm_topics_kb(
    _raw: Callable[..., str],
    chat_id: int,
    topics: list[Any],
    selected: list[int],
) -> types.InlineKeyboardMarkup:
    """Slow-mode topic multi-select: ✅/☑️ per thread + all/done + nav.

    One row per tracked topic (``✅ #3 · 42 сообщ.``), then «Все ветки»
    (immediate save with ``topic_ids=[]``), «✅ Готово» (save with the picked
    threads) and a panel/home nav row.
    """
    builder = InlineKeyboardBuilder()
    sel = set(selected)
    for topic in topics:
        mark = "✅" if topic.thread_id in sel else "☑️"
        builder.button(
            text=f"{mark} #{topic.thread_id} · {topic.message_count} сообщ.",
            callback_data=f"{_PREFIX}:smb:{chat_id}:{topic.thread_id}",
        )
    builder.button(
        text=_raw("dm_sm_topics_all"),
        callback_data=f"{_PREFIX}:smball:{chat_id}",
    )
    builder.button(
        text=_raw("dm_sm_topics_done"),
        callback_data=f"{_PREFIX}:smbdone:{chat_id}",
    )
    builder.adjust(1)
    kb = builder.as_markup()
    kb.inline_keyboard.append(
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
    return kb


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
    """Start the slow-mode config flow for the SELECTED group."""
    try:
        chat_id = int(chat_id_token)
    except ValueError:
        await callback.answer()
        return
    cfg = await crud.get_slow_mode(session, chat_id)
    topics_summary = _sm_topics_summary(_, cfg.topic_ids if cfg is not None else None)
    if cfg is not None and cfg.enabled:
        current = _(
            "dm_sm_current_on",
            regular=cfg.regular_seconds // 3600,
            wl=cfg.wl_seconds // 3600,
            topics=topics_summary,
        )
    else:
        current = _("dm_sm_current_off", topics=topics_summary)
    await state.set_state(DmSlowMode.awaiting_config)
    await state.update_data(chat_id=chat_id)
    await _edit_or_answer(
        callback, _("dm_sm_prompt", current=current), _panel_kb(_raw, chat_id)
    )
    await callback.answer()


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
    if callback.message is not None:
        try:
            await callback.message.edit_reply_markup(
                reply_markup=_build_sm_topics_kb(_raw, chat_id, topics, selected)
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
    «вкл» stores the pending config and — when the group has tracked forum
    topics — moves to the topic multi-select (``DmSlowMode.awaiting_topics``)
    before saving; without tracked topics it saves right away with an
    all-topics scope (``topic_ids=[]``).
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

    # Tracked topics exist → topic multi-select step; otherwise save now.
    topics = await crud.list_topics(session, chat_id)
    if not topics:
        await _dm_sm_save(
            session,
            state,
            lambda text, kb_: message.answer(text, reply_markup=kb_),
            _,
            _raw,
            chat_id,
            enabled=True,
            regular_seconds=regular_seconds,
            wl_seconds=wl_seconds,
            topic_ids=[],
        )
        return

    current = list(cfg.topic_ids) if (cfg is not None and cfg.topic_ids) else []
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
    await message.answer(
        _("dm_sm_topics_prompt"),
        reply_markup=_build_sm_topics_kb(_raw, chat_id, topics, current),
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
