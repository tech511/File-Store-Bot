"""Owner-only /ban, plus the gate that records every user and blocks banned ones.

/ban works with just a user ID: an ID the bot has never seen is banned too (a placeholder
record is created), so the ban applies as soon as that person shows up. A banned user's clone
bots stop working as well, because clone bots check the owner's ban flag.
"""
import logging
import re

from pyrogram import StopPropagation, filters

import config
from database.user_info import ban_user, get_users_collection, record_user  # noqa: F401 (re-export)

log = logging.getLogger(__name__)

USER_ID_RE = re.compile(r"^\d{1,15}$")
NOT_OWNER_TEXT = "⛔ This command is only for the bot owner."
BANNED_TEXT = (
    "⛔ You are banned from using this bot and all its clones.\n"
    "আপনাকে এই bot এবং এর সব clone ব্যবহার করা থেকে ban করা হয়েছে।"
)


def is_owner(message):
    user = message.from_user
    return bool(user) and not user.is_bot and user.id == config.OWNER_ID


def parse_user_id(message):
    """Return (user_id, None) or (None, error_text) from `/cmd <user_id>`."""
    args = message.command or []
    name = args[0] if args else "command"
    if len(args) != 2 or not USER_ID_RE.match(args[1]):
        return None, f"⚠️ Usage: `/{name} <user_id>`\nThe user ID must be a number."
    user_id = int(args[1])
    if user_id <= 0:
        return None, "⚠️ Invalid user ID."
    return user_id, None


def register_user_gate(app):
    """Record users and block banned ones. Must be registered for /ban to take effect."""

    @app.on_message(filters.private, group=-1)
    async def message_gate(client, message):
        user = message.from_user
        if not user or user.is_bot or user.id == config.OWNER_ID:
            return
        try:
            record = await record_user(user)
        except Exception:
            # Fail open: a database hiccup must not lock everybody out. It is logged.
            log.exception("User tracking/ban check failed for %s", user.id)
            return
        if record.banned:
            try:
                await message.reply_text(BANNED_TEXT)
            except Exception:
                log.exception("Could not notify banned user %s", user.id)
            raise StopPropagation

    @app.on_callback_query(group=-1)
    async def callback_gate(client, query):
        user = query.from_user
        if not user or user.is_bot or user.id == config.OWNER_ID:
            return
        try:
            record = await record_user(user)
        except Exception:
            log.exception("Ban check failed for callback from %s", user.id)
            return
        if record.banned:
            try:
                await query.answer("⛔ You are banned from using this bot.", show_alert=True)
            except Exception:
                log.exception("Could not answer banned user's callback")
            raise StopPropagation


def register_ban_handler(app):
    @app.on_message(filters.private & filters.command("ban"))
    async def ban_command(client, message):
        try:
            if not is_owner(message):
                await message.reply_text(NOT_OWNER_TEXT)
                return

            user_id, error = parse_user_id(message)
            if error:
                await message.reply_text(error)
                return
            if user_id == config.OWNER_ID:
                await message.reply_text("⚠️ You cannot ban the bot owner.")
                return

            try:
                status, clone_count = await ban_user(user_id)
            except Exception:
                log.exception("Database error while banning %s", user_id)
                await message.reply_text("⚠️ Database error. The user was not banned, try again later.")
                return

            if status == "already":
                await message.reply_text(f"ℹ️ User `{user_id}` is already banned.")
                return
            log.info("Owner banned user %s (%s)", user_id, status)
            text = f"✅ User `{user_id}` has been banned."
            if status == "banned_new":
                text += "\nThey had no record yet; the ban will apply as soon as they contact the bot."
            if clone_count:
                text += f"\n🤖 Their {clone_count} clone bot(s) are now disabled too."
            await message.reply_text(text)
        except Exception:
            log.exception("Unhandled error in /ban")
            try:
                await message.reply_text("⚠️ Something went wrong. Check the logs.")
            except Exception:
                pass
