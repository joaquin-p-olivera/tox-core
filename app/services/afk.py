"""!afk: somebody says they are away, and the bot answers for them.

Away is about the person and not about one chat, so there is a single row each (`afk_users`) and whoever looks for
them anywhere is told the same thing. It ends on its own: the first message they send, in any chat, deletes the row
and the chat is told they are back. Nobody has to remember to turn it off, which is the only way a command like
this survives contact with a group.
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import AfkUser
from ..schemas import IncomingMessage
from ..utils import utcnow

MAX_REASON_CHARS = 120


def mark(db: Session, message: IncomingMessage, reason: str) -> AfkUser:
    """Marks the sender away, or moves them if they already were (a new reason, counted from now)."""
    row = db.get(AfkUser, message.user_key)
    if row is None:
        row = AfkUser(user_key=message.user_key)
        db.add(row)
    row.user_name = message.display_name
    row.reason = reason.strip()[:MAX_REASON_CHARS]
    row.since = utcnow()
    try:
        db.commit()
    except IntegrityError:  # two messages at once: either row says the same thing
        db.rollback()
    return row


def clear(db: Session, user_key: str) -> AfkUser | None:
    """Takes somebody off the away list and returns the row they had, or None if they were not on it."""
    row = db.get(AfkUser, user_key)
    if row is None:
        return None
    db.delete(row)
    db.commit()
    return row


def of(db: Session, user_keys: list[str]) -> list[AfkUser]:
    """The away ones among these people, in the order they were asked for (which is the order they were tagged)."""
    if not user_keys:
        return []
    found = {row.user_key: row for row in db.scalars(select(AfkUser).where(AfkUser.user_key.in_(set(user_keys))))}
    seen: list[AfkUser] = []
    for key in user_keys:
        row = found.get(key)
        if row is not None and row not in seen:
            seen.append(row)
    return seen


def since_text(row: AfkUser) -> str:
    """"hace 2 h 15 min", or "hace un rato" under a minute: a number of seconds tells nobody anything."""
    minutes = int((utcnow() - row.since).total_seconds() // 60)
    if minutes < 1:
        return "hace un rato"
    if minutes < 60:
        return f"hace {minutes} min"
    hours, rest = divmod(minutes, 60)
    if hours < 24:
        return f"hace {hours} h" + (f" {rest} min" if rest else "")
    days, hours = divmod(hours, 24)
    return f"hace {days} día" + ("s" if days > 1 else "") + (f" {hours} h" if hours else "")


def away_line(row: AfkUser) -> str:
    reason = f": {row.reason}" if row.reason else ""
    return f"🌙 {row.user_name} está afk {since_text(row)}{reason}"


def back_line(row: AfkUser) -> str:
    return f"👋 {row.user_name} volvió ({since_text(row)} que se fue)"
