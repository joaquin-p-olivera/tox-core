"""Reminders asked with !recordar: parsing "HH:MM" into the next time it happens, and turning the ones that are due
into PendingAlerts (the same queue proactive alerts use, so the bots needed no new endpoint).

Nothing runs in the background: the bots already poll for pending alerts on their own timer, and each poll first
moves the reminders that are due into the queue (see routers/alerts.py). A reminder is therefore delivered on the
first poll after its time, and one that came due while the API was down is delivered on the first poll after it
starts. The delay is, at most, the bots' ALERTS_POLL_INTERVAL.
"""

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import PendingAlert, Reminder
from ..utils import utcnow

MAX_TEXT_CHARS = 200
MAX_PENDING_PER_USER = 10
LATE_AFTER = timedelta(minutes=5)  # a reminder this late says which time it was for

_TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def split_time_and_text(raw: str) -> tuple[tuple[int, int], str] | None:
    """"18:30 sacar la basura" or "sacar la basura 18:30" -> ((18, 30), "sacar la basura"). None when there is no
    valid HH:MM at either end or nothing to remember."""
    words = raw.split()
    if len(words) < 2:
        return None
    for index, rest in ((0, words[1:]), (-1, words[:-1])):
        match = _TIME.match(words[index])
        if match:
            return (int(match.group(1)), int(match.group(2))), " ".join(rest)
    return None


def next_occurrence(hour: int, minute: int, now_utc: datetime, timezone_name: str) -> datetime:
    """The next time the wall clock reads hour:minute in `timezone_name` (today if still ahead, else tomorrow),
    as naive UTC. Naive in, naive out: `now_utc` is utcnow()-style."""
    zone = ZoneInfo(timezone_name)
    now_local = now_utc.replace(tzinfo=timezone.utc).astimezone(zone)
    day = now_local.date()
    candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
    if candidate <= now_local:
        day += timedelta(days=1)
        candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
    return candidate.astimezone(timezone.utc).replace(tzinfo=None)


def is_tomorrow(due_utc: datetime, now_utc: datetime, timezone_name: str) -> bool:
    zone = ZoneInfo(timezone_name)
    as_local = lambda value: value.replace(tzinfo=timezone.utc).astimezone(zone).date()  # noqa: E731
    return as_local(due_utc) > as_local(now_utc)


def pending_count(db: Session, platform: str, user_id: str) -> int:
    return db.scalar(select(func.count()).select_from(Reminder).where(Reminder.platform == platform, Reminder.user_id == user_id)) or 0


def queue_due(db: Session, timezone_name: str, now_utc: datetime | None = None) -> int:
    """Moves every reminder whose time has come into the PendingAlert queue (tagging who asked) and returns how many."""
    now_utc = now_utc or utcnow()
    due = list(db.scalars(select(Reminder).where(Reminder.due_at <= now_utc).order_by(Reminder.due_at)))
    zone = ZoneInfo(timezone_name)
    for reminder in due:
        text = f"⏰ Recordatorio para {{@0}}: {reminder.text}"
        if now_utc - reminder.due_at > LATE_AFTER:
            at = reminder.due_at.replace(tzinfo=timezone.utc).astimezone(zone)
            text += f" (era para las {at:%H:%M})"
        db.add(PendingAlert(platform=reminder.platform, chat_id=reminder.chat_id, text=text, mentions=reminder.user_id))
        db.delete(reminder)
    if due:
        db.commit()
    return len(due)
