"""User records, encrypted clone secrets and per-user status reports.

Security design
---------------
* Bot tokens and MongoDB URIs (which contain the database password) are encrypted with
  AES-256-GCM before they are stored. The key is derived (HKDF-SHA256) from ENCRYPTION_KEY.
  Every ciphertext is bound to its owner/bot/field (AAD), so values cannot be swapped
  between records.
* Secrets only leave this module wrapped in `Secret`, which prints as "***" and cannot be
  pickled or JSON-encoded by accident. Call `.reveal()` only where the value is really needed.
* Every read used for display strips the secret fields, and the log filter installed by
  `install_log_redaction()` masks bot tokens and MongoDB URIs in all log output.
* The status report sent to the main bot's log channel contains counts and yes/no flags
  only, never a token or a password.
"""
import asyncio
import base64
import hmac
import html
import logging
import os
import re
import traceback
from collections import namedtuple
from datetime import datetime, timezone

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from pyrogram import enums, errors

import config
from database.db import get_links_collection

log = logging.getLogger(__name__)

MAX_DATABASE_CHANNELS = 10   # per clone
MAX_DATABASES = 3            # MongoDB databases per clone
TOKEN_RE = re.compile(r"^(\d{5,15}):([A-Za-z0-9_-]{30,})$")
MONGO_URI_RE = re.compile(r"^mongodb(?:\+srv)?://\S{3,2000}$", re.IGNORECASE)

UserRecord = namedtuple("UserRecord", "is_new changed banned")


class UserInfoError(Exception):
    """Raised with a message that is safe to show to the user (never contains secrets)."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


# --------------------------------------------------------------------------- Secret wrapper
class Secret:
    """Holds a sensitive string. Printing, formatting, logging and serialising show '***'."""

    __slots__ = ("_value",)

    def __init__(self, value):
        object.__setattr__(self, "_value", value)

    def reveal(self):
        return self._value

    def __repr__(self):
        return "Secret(***)"

    __str__ = __repr__

    def __format__(self, spec):
        return "***"

    def __bool__(self):
        return bool(self._value)

    def __reduce__(self):
        raise TypeError("Secret cannot be pickled")

    def __setattr__(self, name, value):
        raise AttributeError("Secret is read-only")


# --------------------------------------------------------------------------- encryption
class _Crypto:
    def __init__(self, master: bytes):
        salt = b"private-file-store-bot"
        self.aead = AESGCM(HKDF(hashes.SHA256(), 32, salt, b"pfsb/aes-gcm/v1").derive(master))
        self.mac_key = HKDF(hashes.SHA256(), 32, salt, b"pfsb/hmac/v1").derive(master)


_crypto = None


def init_crypto():
    """Validate ENCRYPTION_KEY and prepare the cipher. Call once at startup."""
    global _crypto
    if _crypto is not None:
        return
    raw = config.ENCRYPTION_KEY or ""
    if len(raw) < 32 or len(set(raw)) < 8:
        raise UserInfoError(
            "ENCRYPTION_KEY is missing or too weak. Use at least 32 random characters, e.g. "
            "python -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )
    _crypto = _Crypto(raw.encode("utf-8"))


def _aad(owner_id, bot_id, field):
    return f"{int(owner_id)}:{int(bot_id)}:{field}"


def encrypt_text(plaintext: str, aad: str) -> str:
    init_crypto()
    nonce = os.urandom(12)
    blob = _crypto.aead.encrypt(nonce, plaintext.encode("utf-8"), aad.encode("utf-8"))
    return "v1:" + base64.urlsafe_b64encode(nonce + blob).decode("ascii")


def decrypt_text(token: str, aad: str) -> str:
    init_crypto()
    try:
        version, _, payload = (token or "").partition(":")
        if version != "v1":
            raise ValueError("unknown version")
        raw = base64.urlsafe_b64decode(payload.encode("ascii"))
        return _crypto.aead.decrypt(raw[:12], raw[12:], aad.encode("utf-8")).decode("utf-8")
    except (InvalidTag, ValueError, TypeError):
        # Deliberately vague: wrong key, tampering and corruption look the same.
        raise UserInfoError("A stored secret could not be decrypted (wrong ENCRYPTION_KEY?).")


def token_fingerprint(bot_token: str) -> str:
    """Keyed hash used to spot duplicate tokens without decrypting anything."""
    init_crypto()
    return hmac.new(_crypto.mac_key, bot_token.encode("utf-8"), "sha256").hexdigest()


# --------------------------------------------------------------------------- log redaction
_SCRUBBERS = (
    (re.compile(r"\b\d{5,15}:[A-Za-z0-9_-]{30,}"), "<bot-token>"),
    (re.compile(r"mongodb(?:\+srv)?://[^\s'\"<>]+", re.IGNORECASE), "mongodb://<redacted>"),
)


def redact(text):
    for pattern, replacement in _SCRUBBERS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record):
        try:
            record.msg = redact(record.getMessage())
            record.args = None
            if record.exc_info:
                record.exc_text = redact("".join(traceback.format_exception(*record.exc_info)).rstrip())
            elif record.exc_text:
                record.exc_text = redact(record.exc_text)
        except Exception:
            record.msg = "<log message hidden: redaction failed>"
            record.args = None
        return True


def install_log_redaction():
    """Attach the redaction filter to every root log handler (call after logging.basicConfig)."""
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter())


# --------------------------------------------------------------------------- collection
_indexes_ready = False
_SECRET_PROJECTION = {"clones.token_enc": 0, "clones.token_hash": 0, "clones.databases.uri_enc": 0}


async def get_users_collection():
    """Return the `users` collection (indexes are created once)."""
    global _indexes_ready
    collection = get_links_collection().database["users"]
    if not _indexes_ready:
        await collection.create_index("user_id", unique=True)
        await collection.create_index("banned")
        await collection.create_index("clones.bot_id")
        # One token can belong to one clone only; users without clones are ignored.
        await collection.create_index(
            "clones.token_hash", unique=True,
            partialFilterExpression={"clones.token_hash": {"$exists": True}},
        )
        _indexes_ready = True
    return collection


def _now():
    return datetime.now(timezone.utc)


def _sanitize(doc):
    """Defence in depth: strip secret fields even if a query forgot the projection."""
    if not doc:
        return doc
    doc = dict(doc)
    doc.pop("_id", None)
    clones = []
    for clone in doc.get("clones") or []:
        clone = {k: v for k, v in clone.items() if k not in ("token_enc", "token_hash")}
        clone["databases"] = [
            {k: v for k, v in d.items() if k != "uri_enc"} for d in clone.get("databases") or []
        ]
        clones.append(clone)
    doc["clones"] = clones
    return doc


async def get_user_public(user_id):
    """Return the user's record with every secret removed (or None)."""
    users = await get_users_collection()
    return _sanitize(await users.find_one({"user_id": int(user_id)}, _SECRET_PROJECTION))


def summarize(doc):
    clones = (doc or {}).get("clones") or []
    channels = sum(len(c.get("database_channels") or []) for c in clones)
    logs = sum(1 for c in clones if c.get("log_channel_id"))
    dbs = sum(len(c.get("databases") or []) for c in clones)
    return {
        "clone_count": len(clones),
        "database_channels": channels,
        "log_channels": logs,
        "has_log_channel": logs > 0,
        "databases": dbs,
        "has_database": dbs > 0,
    }


# --------------------------------------------------------------------------- reporting
_client = None
_report_locks = {}
_bg_tasks = set()


def bind_client(client):
    """Give this module the main bot's client so it can post to the log channel."""
    global _client
    _client = client


def _error_types(*names):
    return tuple(getattr(errors, n) for n in names if hasattr(errors, n))


_NOT_MODIFIED = _error_types("MessageNotModified")
_MESSAGE_GONE = _error_types("MessageIdInvalid", "MessageAuthorRequired")


def _fmt_time(value):
    return value.strftime("%Y-%m-%d %H:%M UTC") if isinstance(value, datetime) else "N/A"


def render_status(doc, event=""):
    """Build the HTML report for the log channel. Contains no secrets by construction."""
    h = html.escape
    s = summarize(doc)
    uid = doc["user_id"]
    name = " ".join(p for p in (doc.get("first_name"), doc.get("last_name")) if p) or "Unknown"
    username = f"@{doc['username']}" if doc.get("username") else "None"
    lines = [
        f"👤 <b>User Info</b> — {'🔴 Banned' if doc.get('banned') else '🟢 Active'}",
        "",
        f"🆔 ID: <code>{uid}</code>",
        f'📛 Name: <a href="tg://user?id={uid}">{h(name)}</a>',
        f"🔗 Username: {h(username)}",
        f"🌐 Language: {h(doc.get('language_code') or 'N/A')}",
        f"💎 Premium: {'Yes' if doc.get('is_premium') else 'No'}",
        f"📅 Joined: {_fmt_time(doc.get('joined_at'))}",
        f"🕒 Last seen: {_fmt_time(doc.get('last_seen'))}",
        "",
        f"🤖 <b>Clone bots:</b> {s['clone_count']}",
    ]
    for clone in doc.get("clones") or []:
        label = f"@{clone['bot_username']}" if clone.get("bot_username") else f"ID {clone.get('bot_id')}"
        n_ch = len(clone.get("database_channels") or [])
        n_db = len(clone.get("databases") or [])
        lines.append(
            f"  • {h(label)} — DB channels: {n_ch} | Log channel: "
            f"{'✅' if clone.get('log_channel_id') else '❌'} | Database: "
            f"{f'✅ ({n_db})' if n_db else '❌'}"
        )
    lines += [
        "",
        "📊 <b>Totals</b>",
        f"  🗂 Database channels: {s['database_channels']}",
        f"  📝 Log channel: {'Yes' if s['has_log_channel'] else 'No'} ({s['log_channels']}/{s['clone_count']})",
        f"  🛢 Database: {'Yes' if s['has_database'] else 'No'}"
        + (f" ({s['databases']} added)" if s["has_database"] else ""),
    ]
    if event:
        lines += ["", f"📌 Last event: {h(event)}"]
    lines.append("🔐 Tokens and database passwords are encrypted and never shown.")
    return "\n".join(lines)[:4000]


async def _report_locked(client, user_id, event):
    doc = await get_user_public(user_id)
    if not doc:
        return False
    text = render_status(doc, event)
    message_id = doc.get("log_message_id")
    users = await get_users_collection()
    for _ in range(3):
        try:
            if message_id:
                try:
                    await client.edit_message_text(
                        config.LOG_CHANNEL_ID, message_id, text, parse_mode=enums.ParseMode.HTML
                    )
                    return True
                except _NOT_MODIFIED:
                    return True
                except _MESSAGE_GONE:
                    message_id = None  # the report was deleted: post a fresh one
            sent = await client.send_message(
                config.LOG_CHANNEL_ID, text, parse_mode=enums.ParseMode.HTML
            )
            await users.update_one({"user_id": int(user_id)}, {"$set": {"log_message_id": sent.id}})
            return True
        except errors.FloodWait as exc:
            await asyncio.sleep(int(exc.value) + 1)
    return False


async def report_user_status(user_id, event=""):
    """Create or edit this user's report in the main bot's log channel. Never raises."""
    client = _client
    if client is None or not config.LOG_CHANNEL_ID:
        return False
    user_id = int(user_id)
    lock = _report_locks.setdefault(user_id, asyncio.Lock())
    try:
        async with lock:
            return await _report_locked(client, user_id, event)
    except Exception:
        log.exception("Could not update the log-channel report for user %s", user_id)
        return False
    finally:
        if len(_report_locks) > 1000:
            for key, value in list(_report_locks.items()):
                if not value.locked():
                    _report_locks.pop(key, None)


def schedule_report(user_id, event=""):
    """Fire-and-forget version of report_user_status (does not slow the user down)."""
    if _client is None or not config.LOG_CHANNEL_ID:
        return
    task = asyncio.create_task(report_user_status(user_id, event))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


# --------------------------------------------------------------------------- users + bans
_ban_listeners = []


def add_ban_listener(callback):
    """clone.py can register `async def cb(user_id, banned)` to stop/start a user's clones."""
    _ban_listeners.append(callback)


async def _notify_ban(user_id, banned):
    for callback in list(_ban_listeners):
        try:
            await callback(user_id, banned)
        except Exception:
            log.exception("Ban listener failed for user %s", user_id)


async def record_user(user):
    """Save/refresh the user's profile on every contact (including a bare /start)."""
    uid = int(user.id)
    fields = {
        "first_name": user.first_name or "",
        "last_name": getattr(user, "last_name", None) or "",
        "username": user.username or "",
        "language_code": getattr(user, "language_code", None) or "",
        "is_premium": bool(getattr(user, "is_premium", False)),
    }
    users = await get_users_collection()
    now = _now()
    update = {
        "$set": {**fields, "last_seen": now, "blocked_bot": False},
        "$min": {"joined_at": now},
        "$setOnInsert": {"banned": False, "clones": []},
    }
    before = None
    for attempt in range(2):
        try:
            before = await users.find_one_and_update(
                {"user_id": uid}, update, upsert=True,
                return_document=ReturnDocument.BEFORE, projection=_SECRET_PROJECTION,
            )
            break
        except DuplicateKeyError:  # two first messages raced on the unique index
            if attempt:
                raise
    is_new = before is None
    changed = is_new or any(before.get(k) != v for k, v in fields.items())
    if changed:
        schedule_report(uid, "🆕 New user" if is_new else "✏️ Profile updated")
    return UserRecord(is_new, changed, bool(before and before.get("banned")))


async def is_banned(user_id):
    users = await get_users_collection()
    return bool(await users.find_one({"user_id": int(user_id), "banned": True}, {"_id": 1}))


async def ban_user(user_id):
    """Ban by ID alone. Returns (status, clone_count); status: banned | banned_new | already.

    An ID the bot has never seen gets a placeholder record, so the ban still applies when
    that person shows up later. The filter makes the operation atomic: a user who is already
    banned does not match, the upsert collides on the unique index, and we report "already".
    """
    user_id = int(user_id)
    users = await get_users_collection()
    try:
        before = await users.find_one_and_update(
            {"user_id": user_id, "banned": {"$ne": True}},
            {"$set": {"banned": True, "banned_at": _now()},
             "$setOnInsert": {"clones": [], "blocked_bot": True, "placeholder": True}},
            upsert=True, return_document=ReturnDocument.BEFORE, projection=_SECRET_PROJECTION,
        )
    except DuplicateKeyError:
        return "already", 0
    schedule_report(user_id, "🚫 Banned by owner")
    await _notify_ban(user_id, True)
    return ("banned" if before else "banned_new"), len((before or {}).get("clones") or [])


async def unban_user(user_id):
    """Returns unbanned | not_banned | not_found."""
    user_id = int(user_id)
    users = await get_users_collection()
    result = await users.update_one(
        {"user_id": user_id, "banned": True},
        {"$set": {"banned": False}, "$unset": {"banned_at": ""}},
    )
    if result.modified_count:
        schedule_report(user_id, "✅ Unbanned by owner")
        await _notify_ban(user_id, False)
        return "unbanned"
    exists = await users.find_one({"user_id": user_id}, {"_id": 1})
    return "not_banned" if exists else "not_found"


# --------------------------------------------------------------------------- clones
def _clone_match(owner_id, bot_id, extra=None):
    return {"user_id": int(owner_id),
            "clones": {"$elemMatch": {"bot_id": int(bot_id), **(extra or {})}}}


def _valid_channel_id(channel_id):
    if isinstance(channel_id, bool) or not isinstance(channel_id, int) or channel_id == 0:
        raise UserInfoError("Invalid channel ID.")
    return channel_id


async def add_clone(owner_id, bot_token, bot_username="", bot_name=""):
    """Store a new clone (token encrypted). Returns the bot ID."""
    match = TOKEN_RE.match((bot_token or "").strip())
    if not match:
        raise UserInfoError("Invalid bot token format.")
    bot_token = bot_token.strip()
    owner_id, bot_id = int(owner_id), int(match.group(1))
    entry = {
        "bot_id": bot_id,
        "bot_username": (bot_username or "").lstrip("@")[:32],
        "bot_name": (bot_name or "")[:64],
        "token_enc": encrypt_text(bot_token, _aad(owner_id, bot_id, "token")),
        "token_hash": token_fingerprint(bot_token),
        "database_channels": [],
        "log_channel_id": None,
        "databases": [],
        "created_at": _now(),
    }
    users = await get_users_collection()
    try:
        result = await users.update_one(
            {"user_id": owner_id, "banned": {"$ne": True}, "clones.bot_id": {"$ne": bot_id},
             f"clones.{config.MAX_CLONES_PER_USER - 1}": {"$exists": False}},
            {"$push": {"clones": entry}},
        )
    except DuplicateKeyError:
        raise UserInfoError("This bot token is already used by another clone.")
    if not result.modified_count:
        doc = await users.find_one({"user_id": owner_id}, _SECRET_PROJECTION)
        if not doc:
            raise UserInfoError("User record not found. Send /start first.")
        if doc.get("banned"):
            raise UserInfoError("You are banned from using this bot.")
        if any(c.get("bot_id") == bot_id for c in doc.get("clones") or []):
            raise UserInfoError("You already cloned this bot.")
        raise UserInfoError(f"Clone limit reached ({config.MAX_CLONES_PER_USER}).")
    schedule_report(owner_id, f"🤖 Clone added: @{entry['bot_username'] or bot_id}")
    return bot_id


async def update_clone_token(owner_id, bot_id, new_token):
    match = TOKEN_RE.match((new_token or "").strip())
    if not match or int(match.group(1)) != int(bot_id):
        raise UserInfoError("The new token must belong to the same bot.")
    new_token = new_token.strip()
    users = await get_users_collection()
    try:
        result = await users.update_one(
            _clone_match(owner_id, bot_id),
            {"$set": {"clones.$.token_enc": encrypt_text(new_token, _aad(owner_id, bot_id, "token")),
                      "clones.$.token_hash": token_fingerprint(new_token)}},
        )
    except DuplicateKeyError:
        raise UserInfoError("This bot token is already used by another clone.")
    if not result.modified_count:
        raise UserInfoError("Clone not found.")
    schedule_report(owner_id, f"🔑 Token updated for bot {int(bot_id)}")


async def remove_clone(owner_id, bot_id):
    users = await get_users_collection()
    result = await users.update_one(
        {"user_id": int(owner_id)}, {"$pull": {"clones": {"bot_id": int(bot_id)}}}
    )
    if result.modified_count:
        schedule_report(owner_id, f"🗑 Clone removed: bot {int(bot_id)}")
    return bool(result.modified_count)


async def set_log_channel(owner_id, bot_id, channel_id):
    """Set (or clear with None) the clone's log channel."""
    if channel_id is not None:
        _valid_channel_id(channel_id)
    users = await get_users_collection()
    result = await users.update_one(
        _clone_match(owner_id, bot_id), {"$set": {"clones.$.log_channel_id": channel_id}}
    )
    if not result.matched_count:
        raise UserInfoError("Clone not found.")
    schedule_report(owner_id, "📝 Log channel " + ("set" if channel_id else "removed"))


async def add_database_channel(owner_id, bot_id, channel_id):
    _valid_channel_id(channel_id)
    users = await get_users_collection()
    limit_key = f"database_channels.{MAX_DATABASE_CHANNELS - 1}"
    result = await users.update_one(
        _clone_match(owner_id, bot_id,
                     {"database_channels": {"$ne": channel_id}, limit_key: {"$exists": False}}),
        {"$push": {"clones.$.database_channels": channel_id}},
    )
    if not result.modified_count:
        raise UserInfoError(
            f"Could not add the channel (already added, limit {MAX_DATABASE_CHANNELS}, or clone not found)."
        )
    schedule_report(owner_id, "🗂 Database channel added")


async def remove_database_channel(owner_id, bot_id, channel_id):
    users = await get_users_collection()
    result = await users.update_one(
        _clone_match(owner_id, bot_id), {"$pull": {"clones.$.database_channels": channel_id}}
    )
    if result.modified_count:
        schedule_report(owner_id, "🗂 Database channel removed")
    return bool(result.modified_count)


async def add_database(owner_id, bot_id, mongo_uri):
    """Store a clone's own MongoDB URI encrypted. Returns the new database's short ID."""
    mongo_uri = (mongo_uri or "").strip()
    if not MONGO_URI_RE.match(mongo_uri):
        raise UserInfoError("Invalid MongoDB URI. It must start with mongodb:// or mongodb+srv://")
    db_id = os.urandom(4).hex()
    entry = {
        "db_id": db_id,
        "uri_enc": encrypt_text(mongo_uri, _aad(owner_id, bot_id, f"db:{db_id}")),
        "added_at": _now(),
    }
    users = await get_users_collection()
    limit_key = f"databases.{MAX_DATABASES - 1}"
    result = await users.update_one(
        _clone_match(owner_id, bot_id, {limit_key: {"$exists": False}}),
        {"$push": {"clones.$.databases": entry}},
    )
    if not result.modified_count:
        raise UserInfoError(f"Could not add the database (limit {MAX_DATABASES}, or clone not found).")
    schedule_report(owner_id, "🛢 Database added")
    return db_id


async def remove_database(owner_id, bot_id, db_id):
    users = await get_users_collection()
    result = await users.update_one(
        _clone_match(owner_id, bot_id), {"$pull": {"clones.$.databases": {"db_id": db_id}}}
    )
    if result.modified_count:
        schedule_report(owner_id, "🛢 Database removed")
    return bool(result.modified_count)


# ---- secret access: only for the code that actually runs a clone ----
async def _get_clone(owner_id, bot_id):
    users = await get_users_collection()
    doc = await users.find_one(
        {"user_id": int(owner_id), "banned": {"$ne": True}},
        {"clones": {"$elemMatch": {"bot_id": int(bot_id)}}},
    )
    clones = (doc or {}).get("clones") or []
    return clones[0] if clones else None


async def get_clone_token(owner_id, bot_id):
    """Return the clone's bot token as a Secret (None if missing or the owner is banned)."""
    clone = await _get_clone(owner_id, bot_id)
    if not clone:
        return None
    return Secret(decrypt_text(clone["token_enc"], _aad(owner_id, bot_id, "token")))


async def get_clone_databases(owner_id, bot_id):
    """Return the clone's MongoDB URIs as a list of Secret."""
    clone = await _get_clone(owner_id, bot_id)
    if not clone:
        return []
    return [
        Secret(decrypt_text(d["uri_enc"], _aad(owner_id, bot_id, f"db:{d['db_id']}")))
        for d in clone.get("databases") or []
    ]


async def list_runnable_clones():
    """Every clone whose owner is not banned, with secrets decrypted into Secret objects."""
    users = await get_users_collection()
    runnable = []
    async for doc in users.find(
        {"banned": {"$ne": True}, "clones.0": {"$exists": True}}, {"user_id": 1, "clones": 1}
    ):
        owner_id = doc["user_id"]
        for clone in doc.get("clones") or []:
            try:
                bot_id = clone["bot_id"]
                token = Secret(decrypt_text(clone["token_enc"], _aad(owner_id, bot_id, "token")))
                databases = [
                    Secret(decrypt_text(d["uri_enc"], _aad(owner_id, bot_id, f"db:{d['db_id']}")))
                    for d in clone.get("databases") or []
                ]
            except (UserInfoError, KeyError):
                log.error("Skipping clone %s of user %s: secrets unreadable", clone.get("bot_id"), owner_id)
                continue
            runnable.append({
                "owner_id": owner_id, "bot_id": bot_id,
                "bot_username": clone.get("bot_username", ""),
                "token": token, "databases": databases,
                "database_channels": list(clone.get("database_channels") or []),
                "log_channel_id": clone.get("log_channel_id"),
            })
    return runnable
