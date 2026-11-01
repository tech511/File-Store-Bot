"""Main entry point: validate config, connect MongoDB, register handlers."""
import asyncio
import logging
import sys

from pyrogram import Client, idle

import config
from callback import register_callbacks
from database import user_info
from database.db import connect_database, close_database
from handler.start import register_start_handler, ensure_channel_access
from handler.genlink import register_genlink_handler
from handler.batch import register_batch_handler
from handler.ban import register_user_gate, register_ban_handler
from handler.unban import register_unban_handler
from handler.broadcast import register_broadcast_handler
from handler.tbroadcast import (
    register_tbroadcast_handler,
    start_tbroadcast_worker,
    stop_tbroadcast_worker,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)


user_info.install_log_redaction()  # mask bot tokens / MongoDB URIs in every log line


def validate_config():
    missing = []
    if not config.API_ID:
        missing.append("API_ID")
    if not config.API_HASH:
        missing.append("API_HASH")
    if not config.BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not config.OWNER_ID:
        missing.append("OWNER_ID")
    if not config.DATABASE_CHANNEL_ID:
        missing.append("DATABASE_CHANNEL_ID")
    if not config.MONGO_URI:
        missing.append("MONGO_URI")
    if missing:
        raise RuntimeError(
            "Missing or invalid configuration: " + ", ".join(missing)
            + ". Check your .env file (numeric values must be plain integers)."
        )
    try:
        user_info.init_crypto()
    except user_info.UserInfoError as exc:
        raise RuntimeError(exc.message)


async def run_bot():
    validate_config()
    await connect_database()
    logging.info("MongoDB connected.")

    app = Client(
        "private_file_store_bot",
        api_id=config.API_ID,
        api_hash=config.API_HASH,
        bot_token=config.BOT_TOKEN,
    )
    register_user_gate(app)  # tracks users + blocks banned ones (runs before every handler)
    register_start_handler(app)
    register_callbacks(app)
    register_genlink_handler(app)
    register_batch_handler(app)
    register_ban_handler(app)
    register_unban_handler(app)
    register_broadcast_handler(app)
    register_tbroadcast_handler(app)

    started = False
    try:
        logging.info("Private File Store Bot starting...")
        await app.start()
        started = True

        # Warm the channel peer cache so sending works right after a restart.
        if not await ensure_channel_access(app):
            logging.warning(
                "Database channel is not reachable yet. Add the bot as admin to the channel; "
                "forwarding a post from it to the bot will also fix this."
            )

        user_info.bind_client(app)  # lets user_info post/edit reports in the log channel
        if not config.LOG_CHANNEL_ID:
            logging.warning("LOG_CHANNEL_ID is not set: user status reports are disabled.")
        else:
            try:
                await app.get_chat(config.LOG_CHANNEL_ID)
            except Exception:
                logging.warning("Bot cannot access LOG_CHANNEL_ID; add it as an admin that can post.")
        await start_tbroadcast_worker(app)  # deletes /tbroadcast messages when their time is up

        logging.info("Bot is running.")
        await idle()  # runs until SIGINT/SIGTERM
    finally:
        await stop_tbroadcast_worker()
        if started:
            try:
                await app.stop()
            except Exception:
                logging.exception("Error while stopping the Telegram client")
        await close_database()


if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logging.info("Bot stopped.")
    except Exception as exc:
        logging.exception("Bot startup/runtime failed: %s", exc)
        sys.exit(1)
