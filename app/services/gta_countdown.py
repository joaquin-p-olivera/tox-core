"""The countdown to Grand Theft Auto VI, sent on its own as a card (services/gta_card.py) to the same groups the
alerts go to, and -- when it is set up (services/instagram.py) -- published as an Instagram story at the same moments (ALERT_WHATSAPP_GROUP_JIDS
and ALERT_TELEGRAM_CHAT_IDS, see `alert_monitor.destinations`): once a day at midnight, and every hour over the last
four days, when waiting by the day stops being fun. The day it comes out, one last message welcomes everybody to Vice
City and the countdown is over for good -- `!vi` included.

Nothing runs in the background, the same way reminders work (services/reminders.py): the bots already poll for pending
alerts on their own timer, and each poll first queues whatever is due (routers/alerts.py). What is remembered is the
scheduled moment whose message was queued (`CountdownState.last_slot`), not when it was queued, which is what makes
this both catch up and not repeat itself: a poll after a restart still sends the slot nobody sent, and ten polls inside
the same slot send one message. A whole day the API spent down is one missed message, never a burst of them.

The release moment is a fact about the game, so it is written here as the local time it happens at in Uruguay and kept
as an absolute instant. The schedule around it (midnight, the top of each hour) is read in TIMEZONE, like every other
wall-clock time in the app.
"""

import calendar
import logging
import random
import threading
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from ..config import Settings
from ..models import CountdownState, PendingAlert
from ..utils import utcnow
from . import gta_card, instagram, media_cache
from .alert_monitor import destinations

logger = logging.getLogger("tox.gta_countdown")

KEY = "gta6"
GAME = "Grand Theft Auto VI"
RELEASE_ZONE = ZoneInfo("America/Montevideo")
RELEASE_LOCAL = datetime(2026, 11, 19, 0, 0)
# Naive UTC, like everything stored and like utcnow(): 2026-11-19 03:00Z
RELEASE = RELEASE_LOCAL.replace(tzinfo=RELEASE_ZONE).astimezone(timezone.utc).replace(tzinfo=None)
HOURLY_FROM = date(2026, 11, 15)  # from this day on, one message per hour instead of one per day
ARRIVED = "🌴 Bienvenidos a VICE CITY. 🌴"


def arrived(now_utc: datetime | None = None) -> bool:
    return (now_utc or utcnow()) >= RELEASE


def _plus_month(moment: datetime) -> datetime:
    """The same day and time of the next month, clamped to its last day (31 Jan -> 28 Feb)."""
    year, month = (moment.year + 1, 1) if moment.month == 12 else (moment.year, moment.month + 1)
    return moment.replace(year=year, month=month, day=min(moment.day, calendar.monthrange(year, month)[1]))


def remaining(now_utc: datetime) -> tuple[int, int, int, int, int]:
    """Months, days, hours, minutes and seconds left (the mm:dd:HH:MM:SS of the message). The months are calendar
    months, counted one at a time, so they shrink the way a person counts them instead of in 30-day blocks."""
    cursor, months = now_utc, 0
    while _plus_month(cursor) <= RELEASE:
        cursor, months = _plus_month(cursor), months + 1
    left = RELEASE - cursor
    hours, rest = divmod(left.seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return months, left.days, hours, minutes, seconds


def message(now_utc: datetime | None = None) -> str:
    """What is sent (or answered): how much is left, or the welcome once the day is here."""
    now_utc = now_utc or utcnow()
    if arrived(now_utc):
        return ARRIVED
    months, days, hours, minutes, seconds = remaining(now_utc)
    return f"🌴 Falta {months:02d}:{days:02d}:{hours:02d}:{minutes:02d}:{seconds:02d} para {GAME} 🦩"


def _left(now_utc: datetime) -> tuple[int, int, int, int]:
    """Days, hours, minutes and seconds left. Plain days, months and all: that is what the card counts, where there
    is no room for a fifth number and nobody reads "0 meses" anyway."""
    left = RELEASE - now_utc
    hours, rest = divmod(left.seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return left.days, hours, minutes, seconds


def _artwork(settings: Settings, now_utc: datetime) -> str | None:
    """Which picture goes on this card: one of GTA_ARTWORK_DIR at random, except the day it comes out, which is
    always the cover -- that one is the news, and a random screenshot would bury it."""
    if arrived(now_utc):
        return settings.GTA_COVER_FILE or None
    artworks = gta_card.list_artworks(settings.GTA_ARTWORK_DIR)
    return str(random.choice(artworks)) if artworks else None


def card(settings: Settings, now_utc: datetime | None = None, artwork: str | None = None) -> str | None:
    """The message as an image, stored for the bot to download, or None when it cannot be drawn -- in which case the
    text is what goes out. Drawing must never be what keeps the countdown from being sent. Without an `artwork` one
    is picked here, which is what `!vi` wants; the countdown picks it once and passes it, so the card that goes to
    the chats and the story that goes to Instagram show the same picture."""
    now_utc = now_utc or utcnow()
    artwork = artwork or _artwork(settings, now_utc)
    try:
        png = gta_card.render_arrived(artwork) if arrived(now_utc) else gta_card.render(*_left(now_utc), artwork)
    except Exception:  # a broken font, an artwork that is not an image, anything: the text still goes out
        logger.exception("Could not draw the GTA VI card")
        return None
    return media_cache.store(png, gta_card.MIMETYPE, settings.MEDIA_CACHE_SECONDS)


def _publish_story(settings: Settings, now_utc: datetime, artwork: str | None) -> None:
    """The same countdown as an Instagram story, in a thread of its own: this runs inside the bots' poll for pending
    alerts, and a slow network must not hold that request (or the chat message) up."""
    if not instagram.configured(settings):
        return

    def work() -> None:
        try:
            jpeg = (gta_card.render_arrived_story(artwork) if arrived(now_utc)
                    else gta_card.render_story(*_left(now_utc), artwork))
        except Exception:
            logger.exception("Could not draw the GTA VI story")
            return
        instagram.publish_story(settings, jpeg)  # says so itself when it fails; never raises

    threading.Thread(target=work, name="gta-story", daemon=True).start()


def _slot(now_utc: datetime, timezone_name: str) -> datetime:
    """The scheduled moment this instant belongs to: the top of the hour from HOURLY_FROM on, midnight before that.
    Naive UTC, so it can be compared with what is stored."""
    local = now_utc.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(timezone_name))
    if local.date() < HOURLY_FROM:
        local = local.replace(hour=0)
    return local.replace(minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)


def queue_due(db: Session, settings: Settings, now_utc: datetime | None = None) -> int:
    """Queues the countdown message for every alert destination when a scheduled moment has gone by without its
    message, and returns how many were queued. Called on each bots' poll for pending alerts.

    The very first call sends nothing: with no state there is no way to tell a slot that was missed from the one we
    are simply inside of, and nobody wants a countdown message the moment the API restarts. It writes down where the
    schedule stands, and the next slot is the first one sent."""
    now_utc = now_utc or utcnow()
    state = db.get(CountdownState, KEY)
    if state is not None and state.finished:
        return 0
    where = destinations(settings)
    if not where:
        return 0

    slot = _slot(now_utc, settings.TIMEZONE)
    if arrived(now_utc):
        text, slot = ARRIVED, max(slot, RELEASE)
    elif state is None:
        db.add(CountdownState(key=KEY, last_slot=slot))
        db.commit()
        logger.info("GTA countdown starts at %s (nothing sent yet)", slot)
        return 0
    elif state.last_slot >= slot:
        return 0
    else:
        text = message(now_utc)

    artwork = _artwork(settings, now_utc)
    image = card(settings, now_utc, artwork)  # the same one for every chat: the cache serves it as many times as asked
    for platform, chat_id in where:
        db.add(PendingAlert(platform=platform, chat_id=chat_id, text=text, image=image))
    _publish_story(settings, now_utc, artwork)
    if state is None:
        db.add(CountdownState(key=KEY, last_slot=slot, finished=True))
    else:
        state.last_slot, state.finished = slot, arrived(now_utc)
    db.commit()
    logger.info("GTA countdown queued for %d chats: %s", len(where), text)
    return len(where)
