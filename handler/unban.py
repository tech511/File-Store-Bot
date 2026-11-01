"""Owner-only /unban: lift a ban so the user (and their clone bots) work again."""
import logging

from pyrogram import filters

from database.user_info import unban_user
from handler.ban import NOT_OWNER_TEXT, is_owner, parse_user_id

log = logging.getLogger(__name__)


def register_unban_handler(app):
    @app.on_message(filters.private & filters.command("unban"))
    async def unban_command(client, message):
        try:
            if not is_owner(message):
                await message.reply_text(NOT_OWNER_TEXT)
                return

            user_id, error = parse_user_id(message)
            if error:
                await message.reply_text(error)
                return

            try:
                status = await unban_user(user_id)
            except Exception:
                log.exception("Database error while unbanning %s", user_id)
                await message.reply_text("⚠️ Database error. The user was not unbanned, try again later.")
                return

            if status == "unbanned":
                log.info("Owner unbanned user %s", user_id)
                await message.reply_text(
                    f"✅ User `{user_id}` has been unbanned. They and their clone bots can work again."
                )
            elif status == "not_banned":
                await message.reply_text(f"ℹ️ User `{user_id}` is not banned.")
            else:
                await message.reply_text(f"❌ User `{user_id}` was not found in the database.")
        except Exception:
            log.exception("Unhandled error in /unban")
            try:
                await message.reply_text("⚠️ Something went wrong. Check the logs.")
            except Exception:
                pass
