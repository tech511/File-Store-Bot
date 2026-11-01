"""Owner-only /tbroadcast <time>s: like /broadcast, but each copy is deleted after the time.

Deletions are stored in MongoDB (`scheduled_deletes`) and executed by a background worker, so
they still happen if the bot restarts. Telegram only lets a bot delete messages younger than
48 hours, so the time is limited to that.
"""
import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

from pyrogram import errors, filters
from pyrogram.errors import FloodWait

from database.db import get_links_collection
from handler.ban import NOT_OWNER_TEXT, is_owner
from handler.broadcast import execute_broadcast

log = logging.getLogger(__name__)

TIME_RE = re.compile(r"^(\d{1,7})s?$", re.IGNORECASE)
MIN_SECONDS = 10
MAX_SECONDS = 48 * 3600
USAGE = (
    "⚠️ Usage: reply to a post with `/tbroadcast <time>s`\n"
    "Example: `/tbroadcast 3200s`\n"
    f"Allowed range: {MIN_SECONDS}s – {MAX_SECONDS}s (48 hours)."
)

POLL_INTERVAL = 5
DELETE_BATCH = 100
DELETE_DELAY = 0.05
MAX_ATTEMPTS = 5
RETRY_AFTER = 60  # seconds before a failed deletion is tried again
FLUSH_EVERY = 50

_indexes_ready = False
_worker_task = None


def _error_types(*names):
    return tuple(getattr(errors, n) for n in names if hasattr(errors, n))


# Nothing left to delete (message gone, chat unreachable): drop the job.
GONE_ERRORS = _error_types(
    "MessageIdInvalid", "MessageDeleteForbidden", "UserIsBlocked",
    "InputUserDeactivated", "UserDeactivated", "PeerIdInvalid",
)


async def get_schedule_collection():
    global _indexes_ready
    collection = get_links_collection().database["scheduled_deletes"]
    if not _indexes_ready:
        await collection.create_index("delete_at")
        _indexes_ready = True
    return collection


def parse_ttl(message):
    """Return (seconds, None) or (None, error_text) from `/tbroadcast 3200s`."""
    args = message.command or []
    if len(args) != 2:
        return None, USAGE
    match = TIME_RE.match(args[1])
    if not match:
        return None, USAGE
    seconds = int(match.group(1))
    if seconds < MIN_SECONDS or seconds > MAX_SECONDS:
        return None, USAGE
    return seconds, None


def format_duration(seconds):
    hours, rest = divmod(int(seconds), 3600)
    mins, secs = divmod(rest, 60)
    parts = [f"{hours}h" if hours else "", f"{mins}m" if mins else "", f"{secs}s" if secs or not (hours or mins) else ""]
    return " ".join(p for p in parts if p)


class DeleteRecorder:
    """Collects sent messages and stores their deletion time in MongoDB."""

    def __init__(self, ttl_seconds):
        self.ttl = timedelta(seconds=ttl_seconds)
        self.buffer = []
        self.failed = 0

    async def record(self, chat_id, message_id):
        self.buffer.append({
            "chat_id": int(chat_id),
            "message_id": int(message_id),
            "delete_at": datetime.now(timezone.utc) + self.ttl,
            "attempts": 0,
        })
        if len(self.buffer) >= FLUSH_EVERY:
            await self.flush()

    async def flush(self):
        if not self.buffer:
            return
        chunk, self.buffer = self.buffer, []
        try:
            collection = await get_schedule_collection()
            await collection.insert_many(chunk, ordered=False)
        except Exception:
            log.exception("Could not schedule %s deletions", len(chunk))
            self.failed += len(chunk)


async def _delete_job(client, job):
    """Return True when the job is finished (deleted or impossible), False to retry later."""
    try:
        await client.delete_messages(job["chat_id"], job["message_id"])
        return True
    except FloodWait as exc:
        await asyncio.sleep(int(exc.value) + 1)
        return False
    except GONE_ERRORS:
        return True
    except errors.RPCError:
        log.exception("Telegram error while deleting %s/%s", job["chat_id"], job["message_id"])
        return False
    except Exception:
        log.exception("Unexpected error while deleting %s/%s", job["chat_id"], job["message_id"])
        return False


async def _process_due(client):
    collection = await get_schedule_collection()
    while True:
        now = datetime.now(timezone.utc)
        jobs = await collection.find({"delete_at": {"$lte": now}}).sort("delete_at", 1).to_list(DELETE_BATCH)
        if not jobs:
            return
        finished = []
        for job in jobs:
            if await _delete_job(client, job):
                finished.append(job["_id"])
            elif job.get("attempts", 0) + 1 >= MAX_ATTEMPTS:
                log.error("Dropping deletion %s/%s after %s attempts", job["chat_id"], job["message_id"], MAX_ATTEMPTS)
                finished.append(job["_id"])
            else:
                await collection.update_one(
                    {"_id": job["_id"]},
                    {"$inc": {"attempts": 1},
                     "$set": {"delete_at": datetime.now(timezone.utc) + timedelta(seconds=RETRY_AFTER)}},
                )
            await asyncio.sleep(DELETE_DELAY)
        if finished:
            await collection.delete_many({"_id": {"$in": finished}})


async def _worker(client):
    while True:
        try:
            await _process_due(client)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Timed-delete worker error; will retry")
        await asyncio.sleep(POLL_INTERVAL)


async def start_tbroadcast_worker(client):
    """Start the background deleter. Call after the Telegram client has started."""
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(_worker(client))
        log.info("Timed-delete worker started.")


async def stop_tbroadcast_worker():
    global _worker_task
    if _worker_task is not None:
        _worker_task.cancel()
        await asyncio.gather(_worker_task, return_exceptions=True)
        _worker_task = None


def register_tbroadcast_handler(app):
    @app.on_message(filters.private & filters.command("tbroadcast"))
    async def tbroadcast_command(client, message):
        try:
            if not is_owner(message):
                await message.reply_text(NOT_OWNER_TEXT)
                return
            ttl, error = parse_ttl(message)
            if error:
                await message.reply_text(error)
                return
            await execute_broadcast(
                client,
                message,
                recorder=DeleteRecorder(ttl),
                title="Timed broadcast",
                extra_line=f"🗑 Auto-delete after: `{format_duration(ttl)}`",
            )
        except Exception:
            log.exception("Unhandled error in /tbroadcast")
            try:
                await message.reply_text("⚠️ Something went wrong. Check the logs.")
            except Exception:
                pass
