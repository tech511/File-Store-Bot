"""Central configuration for the bot.

Set credentials and URLs as environment variables. Values below are safe defaults
for development; replace media/channel URLs with your own.
"""
import os
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Text shown by /start
START_TEXT = """
ʜᴇʏ, {first_name}! 👋

ᴡᴇʟᴄᴏᴍᴇ ᴛᴏ ᴘʀɪᴠᴀᴛᴇ ғɪʟᴇ sᴛᴏʀᴇ ʙᴏᴛ 📦

ᴜsᴇ ᴛʜᴇ ʙᴜᴛᴛᴏɴs ʙᴇʟᴏᴡ ᴛᴏ ʟᴇᴀʀɴ ᴍᴏʀᴇ ᴀʙᴏᴜᴛ ᴛʜᴇ ʙᴏᴛ.

ᴘᴏᴡᴇʀᴇᴅ ʙʏ ᴘʏʀᴏɢʀᴀᴍ ⚡
"""

HELP_CAPTION = """
📚 ʜᴇʟᴘ — ᴘʀɪᴠᴀᴛᴇ ғɪʟᴇ sᴛᴏʀᴇ

This bot's file-store and batch-sharing features will be added in a later step.

Use the buttons to navigate. Keep in mind that shareable links should not be used
for sensitive files unless access controls are implemented.
"""

ABOUT_CAPTION = """
ℹ️ ᴀʙᴏᴜᴛ ᴛʜɪs ʙᴏᴛ

• Language: Python
• Telegram library: Pyrogram
• Project: Private File Store Bot
• Architecture: Separate config, buttons, callbacks and handlers

User-based clone features are planned for a future version.
"""

# Optional media URLs. Leave empty ("") to send text-only messages.
START_VIDEO = os.getenv(
    "START_VIDEO",
    "https://graph.org/file/218f412cc841c4f8945b9-4223b8eef03e22ff13.mp4",
)
HELP_VIDEO = os.getenv(
    "HELP_VIDEO",
    "https://graph.org/file/218f412cc841c4f8945b9-4223b8eef03e22ff13.mp4",
)
ABOUT_VIDEO = os.getenv(
    "ABOUT_VIDEO",
    "https://graph.org/file/218f412cc841c4f8945b9-4223b8eef03e22ff13.mp4",
)

# Change this to your actual updates channel.
UPDATE_CHANNEL_URL = os.getenv(
    "UPDATE_CHANNEL_URL",
    "https://t.me/your_update_channel",
)

# Displayed in the about text if you want to customize it later.
BOT_NAME = os.getenv("BOT_NAME", "Private File Store Bot")
