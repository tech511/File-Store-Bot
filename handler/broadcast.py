"""Owner-only /broadcast: copy a replied post to every user, with a live progress bar.

The post is sent with copy_message, so it carries no "Forwarded from" header, while inline
buttons and captions are kept. The engine (`execute_broadcast`) is shared with /tbroadcast.
"""
import asyncio
import logging
import time

from pyrogram import errors, filters
from pyrogram.errors import FloodWait

from handler.ban import NOT_OWNER_TEXT, get_users_collection, is_owner

log = logging.getLogger(__name__)

WORKERS = 5               # parallel senders
SEND_DELAY = 0.25         # pause after each send per worker (~20 messages/second overall)
PROGRESS_INTERVAL = 2     # seconds between progress-bar refreshes
BAR_WIDTH = 20
MAX_FLOOD_RETRIES = 3

_lock = asyncio.Lock()  # only one broadcast at a time


def _error_types(*names):
    return tuple(getattr(errors, n) for n in names if hasattr(errors, n))


# The user can't be reached: blocked the bot, deleted account, or never started it.
USER_UNREACHABLE = _error_types(
    "UserIsBlocked", "InputUserDeactivated", "UserDeactivated", "UserDeactivatedBan", "PeerIdInvalid"
)
NOT_MODIFIED = _error_types("MessageNotModified")


def progress_bar(done, total):
    ratio = done / total if total else 1
    filled = int(BAR_WIDTH * ratio)
    return "█" * filled + "░" * (BAR_WIDTH - filled), ratio * 100


def render_progress(title, total, stats, extra_line):
    done = stats["sent"] + stats["blocked"] + stats["failed"]
    bar, percent = progress_bar(done, total)
    lines = [
        f"📢 **{title} in progress...**",
        "",
        f"`[{bar}]` {percent:.1f}%",
        "",
        f"👥 Total users: `{total}`",
        f"✅ Sent: `{stats['sent']}`",
        f"❌ Failed: `{stats['blocked'] + stats['failed']}`",
        f"⏳ Remaining: `{total - done}`",
    ]
    if extra_line:
        lines.append(extra_line)
    return "\n".join(lines)


async def _mark_blocked(users, user_id):
    """Remember unreachable users so later broadcasts skip them (reset when they write again)."""
    try:
        await users.update_one({"user_id": user_id}, {"$set": {"blocked_bot": True}})
    except Exception:
        log.exception("Could not flag user %s as unreachable", user_id)


async def _send_one(client, users, user_id, from_chat_id, message_id, flood, recorder):
    """Return "sent", "blocked" or "failed"."""
    for _ in range(MAX_FLOOD_RETRIES + 1):
        wait = flood["resume_at"] - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            sent = await client.copy_message(
                chat_id=user_id, from_chat_id=from_chat_id, message_id=message_id
            )
        except FloodWait as exc:
            flood["resume_at"] = max(flood["resume_at"], time.monotonic() + int(exc.value) + 1)
            continue
        except USER_UNREACHABLE:
            await _mark_blocked(users, user_id)
            return "blocked"
        except ValueError:
            log.warning("This message type cannot be copied (user %s)", user_id)
            return "failed"
        except errors.RPCError:
            log.exception("Telegram error while broadcasting to %s", user_id)
            return "failed"
        except Exception:
            log.exception("Unexpected error while broadcasting to %s", user_id)
            return "failed"

        if recorder is not None and sent is not None:
            try:
                await recorder.record(user_id, sent.id)
            except Exception:
                log.exception("Recorder failed for user %s", user_id)
        return "sent"
    log.error("Giving up on user %s after repeated FloodWait", user_id)
    return "failed"


async def _progress_loop(status, text_fn):
    last = None
    while True:
        await asyncio.sleep(PROGRESS_INTERVAL)
        text = text_fn()
        if text == last:
            continue
        try:
            await status.edit_text(text)
            last = text
        except FloodWait as exc:
            await asyncio.sleep(int(exc.value) + 1)
        except NOT_MODIFIED:
            last = text
        except Exception:
            log.debug("Progress edit failed", exc_info=True)


def _duration(seconds):
    seconds = int(seconds)
    mins, secs = divmod(seconds, 60)
    return f"{mins}m {secs}s" if mins else f"{secs}s"


async def execute_broadcast(client, command, recorder=None, title="Broadcast", extra_line=""):
    """Run a broadcast of the post the owner replied to. `command` is the /broadcast message."""
    source = command.reply_to_message
    if source is None or getattr(source, "empty", False) or getattr(source, "service", None):
        await command.reply_text(
            f"⚠️ Reply to the post you want to send with the command.\nUsage: reply + `/{command.command[0]}`"
        )
        return
    if _lock.locked():
        await command.reply_text("⏳ Another broadcast is still running. Wait until it finishes.")
        return

    async with _lock:
        try:
            users = await get_users_collection()
            user_ids = [
                doc["user_id"]
                async for doc in users.find(
                    {"banned": {"$ne": True}, "blocked_bot": {"$ne": True}},
                    {"user_id": 1, "_id": 0},
                )
            ]
        except Exception:
            log.exception("Could not load users for broadcast")
            await command.reply_text("⚠️ Database error. Broadcast cancelled.")
            return

        total = len(user_ids)
        if not total:
            await command.reply_text("ℹ️ There are no users to broadcast to yet.")
            return

        stats = {"sent": 0, "blocked": 0, "failed": 0}
        flood = {"resume_at": 0.0}
        status = await command.reply_text(render_progress(title, total, stats, extra_line))
        started = time.monotonic()

        queue = asyncio.Queue()
        for uid in user_ids:
            queue.put_nowait(uid)

        async def worker():
            while True:
                try:
                    uid = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                result = await _send_one(
                    client, users, uid, command.chat.id, source.id, flood, recorder
                )
                stats[result] += 1
                await asyncio.sleep(SEND_DELAY)

        tasks = [asyncio.create_task(worker()) for _ in range(min(WORKERS, total))]
        ticker = asyncio.create_task(
            _progress_loop(status, lambda: render_progress(title, total, stats, extra_line))
        )
        crashed = False
        try:
            await asyncio.gather(*tasks)
        except Exception:
            crashed = True
            log.exception("Broadcast aborted unexpectedly")
        finally:
            ticker.cancel()
            for task in tasks:
                task.cancel()
            await asyncio.gather(ticker, *tasks, return_exceptions=True)

        schedule_failed = 0
        if recorder is not None:
            try:
                await recorder.flush()
            except Exception:
                log.exception("Recorder flush failed")
            schedule_failed = getattr(recorder, "failed", 0)

        failed_total = stats["blocked"] + stats["failed"]
        done = stats["sent"] + failed_total
        header = "⚠️ **Broadcast stopped early**" if crashed else f"✅ **{title} completed**"
        bar, percent = progress_bar(done, total)
        lines = [
            header,
            "",
            f"`[{bar}]` {percent:.1f}%",
            "",
            f"👥 Total users: `{total}`",
            f"✅ Received the post: `{stats['sent']}`",
            f"❌ Did not receive / failed: `{failed_total}`",
            f"   ├ Blocked or deactivated: `{stats['blocked']}`",
            f"   └ Other errors: `{stats['failed']}`",
            f"⏱ Time taken: `{_duration(time.monotonic() - started)}`",
        ]
        if extra_line:
            lines.append(extra_line)
        if schedule_failed:
            lines.append(f"⚠️ Could not schedule auto-delete for `{schedule_failed}` messages.")
        final_text = "\n".join(lines)

        try:
            await status.edit_text(final_text)
        except NOT_MODIFIED:
            pass
        except Exception:
            log.exception("Could not edit the final broadcast report")
            try:
                await command.reply_text(final_text)
            except Exception:
                log.exception("Could not send the final broadcast report")


def register_broadcast_handler(app):
    @app.on_message(filters.private & filters.command("broadcast"))
    async def broadcast_command(client, message):
        try:
            if not is_owner(message):
                await message.reply_text(NOT_OWNER_TEXT)
                return
            await execute_broadcast(client, message)
        except Exception:
            log.exception("Unhandled error in /broadcast")
            try:
                await message.reply_text("⚠️ Something went wrong. Check the logs.")
            except Exception:
                pass
