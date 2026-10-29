"""The /start command handler."""
from pyrogram import filters
import config
from buttons import start_buttons


def register_start_handler(app):
    @app.on_message(filters.private & filters.command("start"))
    async def start_command(client, message):
        user = message.from_user
        first_name = user.first_name if user else "there"
        text = config.START_TEXT.format(first_name=first_name)

        if config.START_VIDEO:
            try:
                await message.reply_video(
                    video=config.START_VIDEO,
                    caption=text,
                    reply_markup=start_buttons(),
                )
                return
            except Exception:
                # A bad/unavailable media URL should not stop the bot's /start.
                pass

        await message.reply_text(text, reply_markup=start_buttons())
