"""Captcha flow for new members: restrict → challenge → verify / timeout."""

from __future__ import annotations

import asyncio
import logging
import secrets
from typing import Any

from aiogram import Bot, F, Router, types
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.cache.redis import RedisClient
from bot.constants import (
    ACTION_BAN,
    ACTION_MUTE,
    CAPTCHA_EMOJI,
    CAPTCHA_MATH,
)
from bot.db import crud
from bot.filters.chat_type import IsGroup
from bot.handlers.welcome import send_welcome
from bot.i18n.loader import get_i18n
from bot.services.captcha import CaptchaService
from bot.utils.tasks import spawn
from bot.utils.telegram import (
    safe_ban_member,
    safe_delete_message,
    safe_kick_member,
    safe_mute_member,
    safe_send_message,
    safe_unmute_member,
)
from bot.utils.text import build_mention, format_duration

logger = logging.getLogger(__name__)

router = Router()
router.message.filter(IsGroup())

_CB_PREFIX = "captcha"


def _build_keyboard(
    chat_id: int, user_id: int, ctype: str, options: list, button_label: str
) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if ctype in (CAPTCHA_MATH, CAPTCHA_EMOJI):
        for opt in options:
            builder.button(
                text=str(opt),
                callback_data=f"{_CB_PREFIX}:{chat_id}:{user_id}:{opt}",
            )
        builder.adjust(2)
    else:  # button captcha
        builder.button(
            text=button_label,
            callback_data=f"{_CB_PREFIX}:{chat_id}:{user_id}:ok",
        )
    return builder.as_markup()


async def _start_captcha(
    bot: Bot,
    redis: RedisClient,
    session_maker: async_sessionmaker[AsyncSession],
    chat: types.Chat,
    user: types.User,
    settings: dict[str, Any],
    translate: Any,
    lang: str,
    translate_raw: Any | None = None,
) -> bool:
    """Restrict the user and post a captcha challenge.

    Returns ``False`` (without posting/scheduling anything) if the bot can't
    actually restrict the member — so the caller can fall back to a plain
    welcome instead of punishing a user who was never really muted.
    """
    if not await safe_mute_member(bot, chat.id, user.id):
        return False

    ctype = settings.get("captcha_type", "button")
    timeout = int(settings.get("captcha_timeout", 300))
    mention = build_mention(user.id, user.first_name or "user")
    human_timeout = format_duration(timeout)

    answer = ""
    options: list = []
    if ctype == CAPTCHA_MATH:
        captcha = CaptchaService.generate_math_captcha()
        answer = str(captcha.answer)
        options = captcha.options()
        text = translate(
            "captcha_prompt_math",
            user=mention,
            timeout=human_timeout,
            question=captcha.question(),
        )
    elif ctype == CAPTCHA_EMOJI:
        correct, options = CaptchaService.generate_emoji_captcha()
        answer = correct
        text = translate(
            "captcha_prompt_emoji",
            user=mention,
            timeout=human_timeout,
            target=correct,
        )
    else:
        ctype = "button"
        text = translate("captcha_prompt_button", user=mention, timeout=human_timeout)

    keyboard = _build_keyboard(
        chat.id,
        user.id,
        ctype,
        options,
        # Кнопка не парсит HTML — подпись без <tg-emoji>.
        (translate_raw or translate)("captcha_button_label"),
    )
    msg = await safe_send_message(
        bot, chat.id, text, parse_mode="HTML", reply_markup=keyboard
    )

    # Per-challenge nonce: if the user leaves and rejoins, a new challenge
    # overwrites the same Redis key; the old timeout task compares its nonce
    # and bows out instead of punishing the fresh challenge prematurely.
    nonce = secrets.token_hex(8)
    pending = {
        "type": ctype,
        "answer": answer,
        "message_id": msg.message_id if msg else None,
        "name": user.first_name or "user",
        "fail_action": settings.get("captcha_fail_action", "kick"),
        "nonce": nonce,
    }
    # TTL a bit over the timeout so the timeout task still sees an unsolved key.
    await redis.set_captcha(chat.id, user.id, pending, ttl=timeout + 10)
    spawn(
        _captcha_timeout(
            bot, redis, session_maker, chat.id, user.id, timeout, lang, nonce
        )
    )
    return True


async def _captcha_timeout(
    bot: Bot,
    redis: RedisClient,
    session_maker: async_sessionmaker[AsyncSession],
    chat_id: int,
    user_id: int,
    timeout: int,
    lang: str,
    nonce: str,
) -> None:
    """Apply the fail action if the captcha wasn't solved in time."""
    await asyncio.sleep(timeout)
    pending = await redis.get_captcha(chat_id, user_id)
    if not pending:
        return  # solved or already handled
    if pending.get("nonce") != nonce:
        # A newer challenge (leave+rejoin) replaced ours; that one owns the key.
        return

    await redis.delete_captcha(chat_id, user_id)
    message_id = pending.get("message_id")
    if message_id:
        await safe_delete_message(bot, chat_id, message_id)

    fail_action = pending.get("fail_action", "kick")
    async with session_maker() as session:
        if fail_action == ACTION_BAN:
            await safe_ban_member(bot, chat_id, user_id)
            action_log, notice = "captcha_ban", "captcha_timeout_ban"
        elif fail_action == ACTION_MUTE:
            await safe_mute_member(bot, chat_id, user_id)
            action_log, notice = "captcha_mute", "captcha_timeout_mute"
        else:
            await safe_kick_member(bot, chat_id, user_id)
            action_log, notice = "captcha_kick", "captcha_timeout_kick"
        await crud.add_mod_log(session, chat_id, bot.id, user_id, action_log)
        await session.commit()

    mention = build_mention(user_id, pending.get("name", "user"))
    notice_text = get_i18n().get(notice, lang, user=mention)
    from bot.emoji import decorate

    notice_text = decorate(notice_text) or notice_text
    await safe_send_message(
        bot,
        chat_id,
        notice_text,
        parse_mode="HTML",
    )


@router.message(F.new_chat_members)
async def on_new_members(message: types.Message, **data: Any) -> None:
    """Route joining users to captcha (if enabled) or welcome."""
    settings = data.get("settings")
    if not settings:
        return
    bot: Bot = message.bot
    chat = message.chat
    session: AsyncSession = data["session"]
    redis: RedisClient = data["redis"]
    session_maker = data["session_maker"]
    translate = data["_"]
    translate_raw = data.get("_raw")
    lang = data["lang"]

    if settings.get("delete_service_messages"):
        await safe_delete_message(bot, chat.id, message.message_id)

    # Phase 8: federation bans are enforced on join for this chat's federation.
    federation = await crud.get_chat_federation(session, chat.id)

    for user in message.new_chat_members or []:
        if user.is_bot:
            continue
        await crud.upsert_user(session, user.id, user.first_name, user.username)
        if federation is not None and await crud.is_fedbanned(
            session, federation.id, user.id
        ):
            await safe_ban_member(bot, chat.id, user.id)
            continue
        if settings.get("newbie_media_enabled"):
            await redis.mark_newbie(
                chat.id, user.id, int(settings.get("newbie_period") or 3600)
            )
        started = False
        if settings.get("captcha_enabled"):
            started = await _start_captcha(
                bot,
                redis,
                session_maker,
                chat,
                user,
                settings,
                translate,
                lang,
                translate_raw,
            )
        # Welcome directly when captcha is off, or as a fallback when the bot
        # couldn't enforce the captcha (so the user isn't silently ignored).
        if not started:
            await send_welcome(bot, chat, user, settings, translate)


@router.callback_query(F.data.startswith(f"{_CB_PREFIX}:"))
async def on_captcha_answer(callback: types.CallbackQuery, **data: Any) -> None:
    """Verify a captcha button press."""
    translate = data["_"]
    # Тост/алерт не парсит HTML — _raw, чтобы <tg-emoji> не был виден сырым.
    translate_raw = data.get("_raw") or translate
    redis: RedisClient = data["redis"]
    bot: Bot = callback.bot

    parts = (callback.data or "").split(":", 3)
    if len(parts) < 4:
        await callback.answer()
        return
    _, chat_id_s, user_id_s, payload = parts
    try:
        chat_id, user_id = int(chat_id_s), int(user_id_s)
    except ValueError:
        await callback.answer()
        return

    # Only the target user may solve their own captcha.
    if callback.from_user.id != user_id:
        await callback.answer(translate_raw("captcha_not_for_you"), show_alert=True)
        return

    pending = await redis.get_captcha(chat_id, user_id)
    if not pending:
        # Expired or already solved (e.g. double press).
        await callback.answer()
        return

    ctype = pending.get("type", "button")
    answer = pending.get("answer", "")
    if ctype == CAPTCHA_MATH:
        ok = CaptchaService.verify_math_captcha(payload, int(answer))
    elif ctype == CAPTCHA_EMOJI:
        ok = CaptchaService.verify_emoji_captcha(payload, answer)
    else:
        ok = True  # button: pressing it is the proof

    if not ok:
        await callback.answer(translate_raw("captcha_wrong"), show_alert=True)
        return

    # Atomic claim: Redis DEL returns the number of keys removed, so of two
    # concurrent presses (webhook updates run in parallel) only the one that
    # actually removed the key proceeds — the other acks silently. Prevents a
    # genuine double-tap from double-unmuting and sending two welcomes.
    if not await redis.delete_captcha(chat_id, user_id):
        await callback.answer()
        return
    await safe_unmute_member(bot, chat_id, user_id)
    message_id = pending.get("message_id")
    if message_id:
        await safe_delete_message(bot, chat_id, message_id)
    await callback.answer(translate_raw("captcha_success", user=""))

    settings = data.get("settings")
    if settings and callback.message is not None:
        await send_welcome(
            bot, callback.message.chat, callback.from_user, settings, translate
        )
