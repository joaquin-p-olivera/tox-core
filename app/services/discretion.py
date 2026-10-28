"""Whether Tox should keep its remark to itself, decided **before** Claude is called.

A dice roll and a cooldown (`CLAUDE_INTERJECT_CHANCE`, `CLAUDE_INTERJECT_COOLDOWN_MINUTES`) make a remark of its
own rare. Rare is not the same as welcome: a bot that is rare but talks at four in the morning, keeps going when
nobody answers it, or makes a joke while the group is talking about somebody's funeral, is a bot people turn off.

What is here is a table of objections (`OBJECTIONS`), each one a small function that looks at a `Moment` -- a
snapshot of a chat as it is right now -- and either says nothing or says, in words, why this is not the moment.
`objection()` walks the table and returns the first answer it gets, which is also what gets logged. Keeping them
apart from each other means each one can be read, argued with and tested on its own, and keeping them out of the
prompt means none of them can be talked out of it.

The one thing remembered between messages is whether the last remark was picked up (`_standing`), and it lives in
this process only, like the cooldown it sits beside: a restart forgets it and the worst that follows is one remark.
"""

import logging
import threading
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from .chat_history import Saved

logger = logging.getLogger("tox.discretion")

# Enough of each word to catch what it is built from ("falleci" covers falleció, fallecimiento, fallecida). The list
# is short and blunt on purpose, and it is wrong in the direction that is cheap: mistaking a joke for bad news
# costs one remark nobody would have missed, while the other way round costs a joke about somebody's grandmother.
HEAVY_WORDS = (
    "falleci", "murio", "muerte", "muerto", "velorio", "funeral", "entierro", "luto", "condolencia",
    "cancer", "quimio", "internado", "internada", "uci", "cti", "terapia intensiva", "respirador",
    "accidente", "choque", "atropell", "incendio", "robaron", "asaltaron",
    "suicid", "depresion", "ansiedad", "psiquiatr", "operacion", "operaron", "biopsia", "tumor",
    "divorcio", "separamos", "despidieron", "despido", "desalojo",
)
BOT_NICKNAMES = ("tox", "toxi")  # what somebody calls it when they are talking to it without typing a command


def _plain(text: str) -> str:
    """Lowercase and without accents: what people type is never what a word list has."""
    lowered = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in lowered if unicodedata.category(c) != "Mn")


# --- the snapshot ------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Moment:
    """A chat as it is right now, which is everything the objections are allowed to look at."""

    chat_key: str
    now_utc: datetime
    timezone_name: str
    start_hour: int  # the window it may talk in, read in `timezone_name`
    end_hour: int
    patience: int  # remarks of its own that may go unanswered in a row (0 = it never gives up)
    share_limit: float  # times the average person's messages it may have written
    bot_id: str
    recent: list[Saved] = field(default_factory=list)
    game_open: bool = False


# --- what each objection reads ----------------------------------------------------------------------------------

def within_hours(now_utc: datetime, timezone_name: str, start_hour: int, end_hour: int) -> bool:
    """Whether the clock in `timezone_name` is inside the window it talks in. The window runs past midnight when
    `end_hour` is the smaller of the two: 7 to 2 is "from seven in the morning to two at night"."""
    if start_hour == end_hour:
        return True  # a window of the whole day: no hour is ever the wrong hour
    hour = now_utc.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(timezone_name)).hour
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour


def heavy_topic(messages: list[Saved]) -> str | None:
    """The first word in these messages that says the group is not in the mood, or None."""
    for message in messages:
        text = _plain(message.text)
        for word in HEAVY_WORDS:
            if word in text:
                return word
    return None


def over_its_share(messages: list[Saved], bot_id: str, share_limit: float) -> bool:
    """Whether the bot has written more than `share_limit` times what the average person there has.

    A window with nobody in it counts as over: with no conversation there is nothing to join, which is also what
    keeps it from remarking into a chat that has been asleep for hours."""
    mine = sum(1 for message in messages if message.sender_id == bot_id)
    theirs = [message for message in messages if message.sender_id != bot_id]
    people = {message.sender_id for message in theirs}
    if not people:
        return True
    return mine > share_limit * (len(theirs) / len(people))


def names_the_bot(text: str) -> bool:
    """Whether a message with no command in it is still aimed at the bot, by calling it by name."""
    words = set(_plain(text).replace(",", " ").replace("?", " ").replace("!", " ").split())
    return any(nickname in words for nickname in BOT_NICKNAMES)


# --- whether the last remark landed (per chat, this process only) -----------------------------------------------

@dataclass
class _LastWord:
    at: datetime
    picked_up: bool = False


@dataclass
class _Standing:
    """How Tox is doing in one chat: what it said last, and how many before that fell flat."""

    latest: _LastWord | None = None
    unanswered: int = 0


_lock = threading.Lock()
_standing: dict[str, _Standing] = {}


def spoke_up(chat_key: str, now_utc: datetime) -> None:
    """Tox just said something of its own there. Whether it was worth saying is decided by what happens next."""
    with _lock:
        standing = _standing.setdefault(chat_key, _Standing())
        if standing.latest is not None and not standing.latest.picked_up:
            standing.unanswered += 1
        standing.latest = _LastWord(at=now_utc)


def was_answered(chat_key: str) -> None:
    """Somebody talked to the bot there -- a command, a reply to it, or its name -- so whatever it said last was
    picked up and a run of remarks falling flat is over."""
    with _lock:
        standing = _standing.setdefault(chat_key, _Standing())
        if standing.latest is not None:
            standing.latest.picked_up = True
        standing.unanswered = 0


def unanswered(chat_key: str) -> int:
    """How many remarks of its own nobody picked up, in a row, counting the one still waiting."""
    with _lock:
        standing = _standing.get(chat_key)
        if standing is None:
            return 0
        waiting = 1 if standing.latest is not None and not standing.latest.picked_up else 0
        return standing.unanswered + waiting


def reset(chat_key: str | None = None) -> None:
    """Forgets where it stands, in one chat or everywhere. For the tests, and for a chat that is gone."""
    with _lock:
        if chat_key is None:
            _standing.clear()
        else:
            _standing.pop(chat_key, None)


# --- the objections ----------------------------------------------------------------------------------------------

Objection = Callable[[Moment], str | None]


def _wrong_hour(moment: Moment) -> str | None:
    if within_hours(moment.now_utc, moment.timezone_name, moment.start_hour, moment.end_hour):
        return None
    return "it is outside the hours it talks in"


def _mid_game(moment: Moment) -> str | None:
    # While a round is open the answers *are* the conversation, and a remark lands in the middle of it.
    return "a game is open" if moment.game_open else None


def _nobody_is_picking_it_up(moment: Moment) -> str | None:
    if moment.patience <= 0 or unanswered(moment.chat_key) < moment.patience:
        return None
    return f"its last {moment.patience} remarks went unanswered"


def _bad_news_in_the_room(moment: Moment) -> str | None:
    word = heavy_topic(moment.recent)
    return f"the chat is talking about something serious ({word})" if word else None


def _it_has_said_enough(moment: Moment) -> str | None:
    if not over_its_share(moment.recent, moment.bot_id, moment.share_limit):
        return None
    return "it has already talked more than the people there"


# In this order on purpose: the ones that cost nothing to check and settle it outright come first.
OBJECTIONS: tuple[Objection, ...] = (
    _wrong_hour,
    _mid_game,
    _nobody_is_picking_it_up,
    _bad_news_in_the_room,
    _it_has_said_enough,
)


def objection(moment: Moment) -> str | None:
    """The first reason Tox should keep this one to itself, in words for the log, or None when it may speak."""
    for rule in OBJECTIONS:
        reason = rule(moment)
        if reason is not None:
            return reason
    return None
