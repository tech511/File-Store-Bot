# Private File Store Bot — starter menu

Python + Pyrogram project with separate configuration, buttons, callbacks, and `/start` handler.

## Files

- `config.py` — credentials, start/help/about text, optional video URLs and update channel.
- `buttons.py` — the five start-menu buttons and Help/About navigation keyboard.
- `callback.py` — Help, About, Back and Close callbacks. Create Your Own Bot is intentionally not implemented.
- `handler/start.py` — `/start` command only.
- `main.py` — creates the Pyrogram client and registers handlers.

## Run

Python 3.10+ recommended.

```bash
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in `API_ID`, `API_HASH`, and `BOT_TOKEN`, then run:

```bash
python main.py
```

For hosting, set the same values as environment variables in the service dashboard. Never publish `.env` or commit your bot token.

## Media

Set `START_VIDEO`, `HELP_VIDEO`, and `ABOUT_VIDEO` to direct media URLs that Telegram can fetch, or leave them empty for text-only screens. The sample URLs in `config.py` are placeholders from the supplied example and should be replaced with media you control.

## Current scope

This starter only implements the start menu and its navigation. It does not yet implement `/batch`, file storage, clone creation, a database, or user-specific bot tokens.
