"""Central configuration. Set secrets and channel details using environment variables."""
import os
from dotenv import load_dotenv

load_dotenv()


def _int_env(name, default=0):
    """Read an integer env var safely; empty or invalid values fall back to default."""
    raw = (os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


API_ID = _int_env("API_ID")
API_HASH = (os.getenv("API_HASH", "") or "").strip()
BOT_TOKEN = (os.getenv("BOT_TOKEN", "") or "").strip()

OWNER_ID = _int_env("OWNER_ID")
DATABASE_CHANNEL_ID = _int_env("DATABASE_CHANNEL_ID")
LOG_CHANNEL_ID = _int_env("LOG_CHANNEL_ID")  # main bot's log channel for user status reports

# Secret used to encrypt clone bot tokens and database passwords (min. 32 random characters).
# Losing or changing it makes stored secrets unreadable, so back it up.
ENCRYPTION_KEY = (os.getenv("ENCRYPTION_KEY", "") or "").strip()
MAX_CLONES_PER_USER = _int_env("MAX_CLONES_PER_USER", 5) or 5

MONGO_URI = (os.getenv("MONGO_URI", "") or "").strip()
DB_NAME = os.getenv("DB_NAME", "private_file_store")

START_TEXT = """
ʜᴇʏ, {first_name}! 👋

ᴡᴇʟᴄᴏᴍᴇ ᴛᴏ ᴘʀɪᴠᴀᴛᴇ ғɪʟᴇ sᴛᴏʀᴇ ʙᴏᴛ 📦

ᴜsᴇ ᴛʜᴇ ʙᴜᴛᴛᴏɴs ʙᴇʟᴏᴡ ᴛᴏ ᴇxᴘʟᴏʀᴇ ᴛʜᴇ ʙᴏᴛ.
"""

HELP_CAPTION = """
📚 ʜᴇʟᴘ

This bot creates shareable links for files stored in its configured Telegram database channel.

Only the bot owner can create links on the main bot. Anyone with a valid share link can open it.
"""

ABOUT_CAPTION = """
ℹ️ ᴀʙᴏᴜᴛ

• Project: Private File Store Bot
• Language: Python
• Telegram library: Pyrogram
• Metadata storage: MongoDB
"""

START_VIDEO = os.getenv("START_VIDEO", "")
HELP_VIDEO = os.getenv("HELP_VIDEO", "")
ABOUT_VIDEO = os.getenv("ABOUT_VIDEO", "")
UPDATE_CHANNEL_URL = os.getenv("UPDATE_CHANNEL_URL", "https://t.me/your_update_channel")
BOT_NAME = os.getenv("BOT_NAME", "Private File Store Bot")

MAX_BATCH_MESSAGES = _int_env("MAX_BATCH_MESSAGES", 500) or 500
