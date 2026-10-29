"""Inline keyboards only; callback actions are handled in callback.py."""
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
import config


def start_buttons():
    """The five buttons shown with /start."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📚 Help", callback_data="help"),
            InlineKeyboardButton("ℹ️ About", callback_data="about"),
        ],
        [
            InlineKeyboardButton("🤖 Create Your Own Bot", callback_data="create_bot_unhandled"),
        ],
        [
            InlineKeyboardButton("📢 Update Channel", url=config.UPDATE_CHANNEL_URL),
        ],
        [
            InlineKeyboardButton("✖️ Close", callback_data="close"),
        ],
    ])


def navigation_buttons():
    """Back and close buttons for Help/About screens."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⬅️ Back", callback_data="back"),
            InlineKeyboardButton("✖️ Close", callback_data="close"),
        ]
    ])
