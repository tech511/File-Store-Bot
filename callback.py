"""Callback actions for the start menu."""
import logging

from pyrogram import filters
from pyrogram.types import CallbackQuery

import config
from buttons import start_buttons, navigation_buttons

log = logging.getLogger(__name__)


async def _send_screen(client, query: CallbackQuery, caption: str, video_url: str):
    """Delete the previous menu message, then send the requested screen."""
    chat_id = query.message.chat.id
    try:
        await query.message.delete()
    except Exception:
        log.exception("Could not delete previous menu message")

    if video_url:
        try:
            await client.send_video(
                chat_id=chat_id,
                video=video_url,
                caption=caption,
                reply_markup=navigation_buttons(),
            )
            return
        except Exception:
            # If the remote video is unavailable, fall back to text.
            log.exception("Could not send configured video; falling back to text")

    await client.send_message(
        chat_id=chat_id,
        text=caption,
        reply_markup=navigation_buttons(),
    )


async def _send_start_screen(client, query: CallbackQuery):
    """Restore the /start screen with the five main buttons."""
    chat_id = query.message.chat.id
    user = query.from_user
    caption = config.START_TEXT.format(first_name=user.first_name or "there")

    try:
        await query.message.delete()
    except Exception:
        log.exception("Could not delete previous menu message")

    if config.START_VIDEO:
        try:
            await client.send_video(
                chat_id=chat_id,
                video=config.START_VIDEO,
                caption=caption,
                reply_markup=start_buttons(),
            )
            return
        except Exception:
            log.exception("Could not send start video; falling back to text")

    await client.send_message(
        chat_id=chat_id,
        text=caption,
        reply_markup=start_buttons(),
    )


def register_callbacks(app):
    @app.on_callback_query(filters.regex(r"^(help|about|back|close|create_bot_unhandled)$"))
    async def menu_callback(client, query: CallbackQuery):
        data = query.data or ""

        if data == "help":
            await query.answer()
            await _send_screen(client, query, config.HELP_CAPTION, config.HELP_VIDEO)

        elif data == "about":
            await query.answer()
            await _send_screen(client, query, config.ABOUT_CAPTION, config.ABOUT_VIDEO)

        elif data == "back":
            await query.answer()
            await _send_start_screen(client, query)

        elif data == "close":
            await query.answer("Menu closed.")
            try:
                await query.message.delete()
            except Exception:
                log.exception("Could not close menu message")

        elif data == "create_bot_unhandled":
            # Intentionally no feature/action is connected to this button yet.
            # Answering only stops Telegram's loading indicator.
            await query.answer()
