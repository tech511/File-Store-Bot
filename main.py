"""Application entry point: create the Pyrogram client and register handlers."""
import logging
import sys

from pyrogram import Client
import config
from callback import register_callbacks
from handler.start import register_start_handler


def validate_config():
    missing = []
    if not config.API_ID:
        missing.append("API_ID")
    if not config.API_HASH:
        missing.append("API_HASH")
    if not config.BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if missing:
        raise RuntimeError(
            "Missing environment variable(s): " + ", ".join(missing)
            + ". Set them in your hosting dashboard or .env file."
        )


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    validate_config()

    app = Client(
        "private_file_store_bot",
        api_id=config.API_ID,
        api_hash=config.API_HASH,
        bot_token=config.BOT_TOKEN,
    )

    register_start_handler(app)
    register_callbacks(app)

    logging.info("Private File Store Bot is starting...")
    app.run()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logging.error("Bot startup failed: %s", exc)
        sys.exit(1)
