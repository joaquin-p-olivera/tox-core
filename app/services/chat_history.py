"""Saved text of the chats the owner turned history on for (!h): who said what and when, nothing else.

It lives in a SQLite file of its own (HISTORY_DB_PATH), not in tox.db: the size limit is the size of that file, wiping
it is deleting it, and it holds private conversations (so it is created readable by its owner only and git-ignored).
Past HISTORY_MAX_MB the oldest messages go first, checked at most once a minute and only when something is written
(nothing else makes it grow), so no background thread or second process has to share the file.

Only text is saved: a picture's caption counts, the picture itself never reaches this module.
"""

import logging
import os
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from ..config import Settings
from ..models import HistoryChat

logger = logging.getLogger("tox.history")

TRIM_TO = 0.9  # once over the limit, delete down to this fraction of it (so it doesn't trim on every message)
TRIM_EVERY_SECONDS = 60
_DELETE_BATCH = 500
_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"  # UTC, sortable as text

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_key TEXT NOT NULL,
    message_id TEXT NOT NULL,
    sender_id TEXT NOT NULL,
    sender_name TEXT,
    text TEXT NOT NULL,
    sent_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_messages_chat_message ON messages (chat_key, message_id);
CREATE INDEX IF NOT EXISTS ix_messages_chat_time ON messages (chat_key, sent_at);
CREATE INDEX IF NOT EXISTS ix_messages_time ON messages (sent_at);
"""

_last_trim = 0.0
_trim_lock = threading.Lock()


@dataclass(frozen=True)
class Saved:
    sent_at: datetime  # naive UTC
    sender_id: str
    sender_name: str | None
    text: str


@dataclass(frozen=True)
class Incoming:
    message_id: str
    sender_id: str
    sender_name: str | None
    text: str
    sent_at: datetime  # naive UTC


def _connect(settings: Settings) -> sqlite3.Connection:
    path = Path(settings.HISTORY_DB_PATH).expanduser()
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    if new:
        os.chmod(path, 0o600)  # private conversations
    # Only takes effect on a database with no tables yet; lets the file give space back when a chat is deleted
    conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
    conn.executescript(_SCHEMA)
    return conn


def _used_bytes(conn: sqlite3.Connection) -> int:
    """What the data takes: the file is never smaller than this, and it stops growing once it is over the limit
    (freed pages are reused)."""
    page_size = conn.execute("PRAGMA page_size").fetchone()[0]
    pages = conn.execute("PRAGMA page_count").fetchone()[0]
    free = conn.execute("PRAGMA freelist_count").fetchone()[0]
    return (pages - free) * page_size


# --- which chats are being saved ---------------------------------------------------------------------------------

def enabled_ids(db: Session, platform: str) -> dict[str, str]:
    """Every id a saved chat is known by (its own and its aliases) -> the chat_key its messages are kept under."""
    ids: dict[str, str] = {}
    for row in db.query(HistoryChat).filter(HistoryChat.chat_key.like(f"{platform}:%")):
        prefix = f"{platform}:"
        ids[row.chat_key[len(prefix):]] = row.chat_key
        for alias in filter(None, row.aliases.split(",")):
            ids[alias] = row.chat_key
    return ids


def chat_key_for(db: Session, platform: str, chat_ids: list[str]) -> str:
    """The key a chat's messages are kept under: the one it was enabled with when any of its ids matches, else the
    plain one from the first id."""
    known = enabled_ids(db, platform)
    for chat_id in chat_ids:
        if chat_id in known:
            return known[chat_id]
    return f"{platform}:{chat_ids[0]}"


# --- messages ----------------------------------------------------------------------------------------------------

def store(settings: Settings, chat_key: str, messages: list[Incoming]) -> int:
    """Saves the messages that are not saved yet (a message id is kept once per chat). Returns how many were new."""
    if not messages:
        return 0
    rows = [(chat_key, m.message_id, m.sender_id, m.sender_name, m.text, m.sent_at.strftime(_TIME_FORMAT)) for m in messages]
    with closing(_connect(settings)) as conn, conn:
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO messages (chat_key, message_id, sender_id, sender_name, text, sent_at) "
            "VALUES (?, ?, ?, ?, ?, ?)", rows)
        stored = conn.total_changes - before
    _trim_when_due(settings)
    return stored


def recent(settings: Settings, chat_key: str, *, since: datetime | None, limit: int) -> list[Saved]:
    """The latest `limit` messages of a chat (from `since` on, when given), oldest first."""
    query = "SELECT sent_at, sender_id, sender_name, text FROM messages WHERE chat_key = ?"
    args: list = [chat_key]
    if since is not None:
        query += " AND sent_at >= ?"
        args.append(since.strftime(_TIME_FORMAT))
    query += " ORDER BY sent_at DESC, id DESC LIMIT ?"
    args.append(limit)
    with closing(_connect(settings)) as conn:
        found = conn.execute(query, args).fetchall()
    return [Saved(datetime.strptime(at, _TIME_FORMAT), sender, name, text) for at, sender, name, text in reversed(found)]


def transcript(messages: list[Saved], zone: ZoneInfo, name: Callable[[Saved], str], *, max_line: int,
               max_chars: int) -> str:
    """One line per message ("[13/10 18:02] Ana: ..."), times in `zone`. Past `max_chars` the oldest lines are dropped."""
    lines = []
    for saved in messages:
        when = saved.sent_at.replace(tzinfo=timezone.utc).astimezone(zone)
        text = " ".join(saved.text.split())
        if len(text) > max_line:
            text = text[:max_line] + "…"
        lines.append(f"[{when:%d/%m %H:%M}] {name(saved)}: {text}")
    while sum(len(line) + 1 for line in lines) > max_chars and len(lines) > 1:
        lines.pop(0)
    return "\n".join(lines)


def count(settings: Settings, chat_key: str) -> int:
    with closing(_connect(settings)) as conn:
        return conn.execute("SELECT COUNT(*) FROM messages WHERE chat_key = ?", (chat_key,)).fetchone()[0]


def total_bytes(settings: Settings) -> int:
    with closing(_connect(settings)) as conn:
        return _used_bytes(conn)


def delete_chat(settings: Settings, chat_key: str) -> int:
    with closing(_connect(settings)) as conn:
        with conn:
            deleted = conn.execute("DELETE FROM messages WHERE chat_key = ?", (chat_key,)).rowcount
        conn.execute("PRAGMA incremental_vacuum")
    return deleted


# --- the size limit ----------------------------------------------------------------------------------------------

def trim(settings: Settings) -> int:
    """Deletes the oldest messages (of every chat) while the data is over HISTORY_MAX_MB, down to TRIM_TO of it.
    Returns how many were deleted."""
    limit = settings.HISTORY_MAX_MB * 1024 * 1024
    deleted = 0
    with closing(_connect(settings)) as conn:
        if _used_bytes(conn) <= limit:
            return 0
        while _used_bytes(conn) > limit * TRIM_TO:
            with conn:
                batch = conn.execute(
                    "DELETE FROM messages WHERE id IN (SELECT id FROM messages ORDER BY sent_at, id LIMIT ?)",
                    (_DELETE_BATCH,)).rowcount
            if batch <= 0:
                break
            deleted += batch
        conn.execute("PRAGMA incremental_vacuum")
    logger.info("HISTORY over %s MB: deleted the %s oldest messages", settings.HISTORY_MAX_MB, deleted)
    return deleted


def _trim_when_due(settings: Settings) -> None:
    global _last_trim
    with _trim_lock:
        now = time.monotonic()
        if now - _last_trim < TRIM_EVERY_SECONDS:
            return
        _last_trim = now
    try:
        trim(settings)
    except Exception:  # saving messages must never fail because of the cleanup
        logger.exception("HISTORY trim failed")
