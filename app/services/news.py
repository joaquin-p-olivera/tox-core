"""What is new in Tox, told to the chats on its own (`app/data/news.json`, `!novedades`).

A release note is written by hand, in Spanish, for the group to read: the CHANGELOG is in English, technical and
long, and nobody in a chat wants to read that one commit by commit. The entry ships in the same commit as the
feature it announces, so what is said and what was built cannot drift apart.

Nothing is scheduled: like the reminders and the countdown, each poll the bots make for pending alerts queues
whatever is new (routers/alerts.py). What is remembered is the id of the newest entry already queued
(`news_states`), not when it went out, so a restart announces what the file gained and never the same thing twice.

The first run on a machine announces **nothing**: with no row there is no way to tell an entry that was missed from
the whole history of the file, and nobody wants every release note at once the first time this starts. It writes
down where the file stands, and what is added after that is the first thing announced -- the same rule the
countdown follows.
"""

import logging

from sqlalchemy.orm import Session

from ..config import Settings
from ..models import NewsState, PendingAlert
from .alert_monitor import destinations
from .content import load_json

logger = logging.getLogger("tox.news")

KEY = "news"
FILE = "news.json"
HEADER = "✨ Novedades de Tox"
# The versions an entry announces, named the way the chat knows the two apps. They are written in the entry itself,
# in the commit that ships the feature: the API cannot read the bot's package.json (another repo), and a number
# copied into a setting drifts from the release it was supposed to name.
APPS = ("whatsapp-bot", "api-core")
MAX_AT_ONCE = 3  # several deploys without a poll in between: the latest ones go out, not a wall of text


def entries() -> list[dict]:
    """The release notes, oldest first, skipping anything half-written: an entry needs an id and something to say.
    A broken one is logged and left out rather than sent as it is."""
    kept = []
    for entry in load_json(FILE):
        if isinstance(entry, dict) and str(entry.get("id") or "").strip() and str(entry.get("text") or "").strip():
            kept.append(entry)
        else:
            logger.warning("Skipping an unfinished release note: %r", entry)
    return kept


def pending(last_id: str | None) -> list[dict]:
    """The entries after `last_id`. Everything when the id is not in the file any more (it was renamed or removed),
    which is as close to "we don't know" as this gets, and None means nothing has ever been announced."""
    all_entries = entries()
    if last_id is None:
        return []
    ids = [entry["id"] for entry in all_entries]
    if last_id not in ids:
        return all_entries
    return all_entries[ids.index(last_id) + 1:]


def versions(entry: dict) -> list[str]:
    """"whatsapp-bot 1.0.0", "api-core 1.0.0": the apps this entry says it released, in a fixed order, leaving
    out the one that did not change. An entry with no versions at all is announced without the block."""
    given = entry.get("versions") or {}
    if not isinstance(given, dict):
        logger.warning("Release note %r has versions that are not a mapping", entry.get("id"))
        return []
    named = [(app, str(given[app]).strip()) for app in APPS if str(given.get(app) or "").strip()]
    return [f"{app} {number}" for app, number in named]


def message(entry: dict) -> str:
    blocks = [HEADER]
    lines = versions(entry)
    if lines:
        blocks.append("\n".join(lines))
    title = str(entry.get("title") or "").strip()
    body = entry["text"].strip()
    blocks.append(f"*{title}*\n{body}" if title else body)
    return "\n\n".join(blocks)


def would_send(db: Session, settings: Settings) -> tuple[list[dict], int]:
    """What the next poll would announce, and to how many chats. What `!novedades -probar` shows, so the text can
    be read before a group reads it."""
    state = db.get(NewsState, KEY)
    waiting = pending(state.last_id if state else None)
    return waiting[-MAX_AT_ONCE:], len(destinations(settings))


def latest() -> dict | None:
    """The newest entry, for somebody asking `!novedades` whether it was announced or not."""
    all_entries = entries()
    return all_entries[-1] if all_entries else None


def queue_due(db: Session, settings: Settings) -> int:
    """Queues whatever the file gained since the last time, for every chat the alerts go to, and returns how many
    messages were queued. Called on each bots' poll for pending alerts."""
    all_entries = entries()
    if not all_entries:
        return 0
    newest = all_entries[-1]["id"]

    state = db.get(NewsState, KEY)
    if state is None:
        db.add(NewsState(key=KEY, last_id=newest))
        db.commit()
        logger.info("Release notes start at %s (nothing announced)", newest)
        return 0

    waiting = pending(state.last_id)
    if not waiting:
        return 0
    where = destinations(settings)
    if not where:
        # Nowhere to send it: the marker stays where it is, so turning the destinations on later still announces it.
        logger.info("%d release note(s) to announce and no chat to announce them to", len(waiting))
        return 0

    queued = 0
    for entry in waiting[-MAX_AT_ONCE:]:
        for platform, chat_id in where:
            db.add(PendingAlert(platform=platform, chat_id=chat_id, text=message(entry)))
            queued += 1
    state.last_id = newest
    db.commit()
    logger.info("Announced %d release note(s) to %d chats: %s", min(len(waiting), MAX_AT_ONCE), len(where),
                ", ".join(entry["id"] for entry in waiting[-MAX_AT_ONCE:]))
    return queued
