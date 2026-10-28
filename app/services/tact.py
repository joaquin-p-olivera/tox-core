"""When Tox may say something nobody asked for, decided **before** Claude is called.

Until now a remark of its own was only a dice roll and a cooldown (`CLAUDE_INTERJECT_CHANCE`,
`CLAUDE_INTERJECT_COOLDOWN_MINUTES`). That is enough to keep it rare and not enough to keep it welcome: a bot that
is rare but talks at four in the morning, keeps going when nobody answers it, or makes a joke while the group is
talking about somebody's funeral, is a bot people turn off.

The rules here are plain functions over a snapshot of the chat, so they cost nothing, cannot be talked out of it by
a prompt, and are testable without asking anything:

- **Hours.** Nothing between `CLAUDE_INTERJECT_UNTIL_HOUR` and `CLAUDE_INTERJECT_FROM_HOUR`, read in `TIMEZONE`.
  The window wraps midnight on purpose, because that is when a group is most alive.
- **Being ignored.** A remark nobody picked up counts once; after `CLAUDE_INTERJECT_GIVE_UP_AFTER` of those in a
  row it stops, until somebody talks to it again. Insisting is what makes a bot tiresome.
- **How much it talks.** Never more than `CLAUDE_INTERJECT_MAX_SHARE` times what the average person in that chat
  has written in the window, so in a quiet chat it stays quiet and in a busy one it can join in.
- **Serious talk.** A short list of words that say the group is not in the mood (a death, an illness, an accident).
  Deliberately crude, and wrong in the only direction that is cheap: a false positive costs one remark nobody
  would have missed, a false negative costs a joke about somebody's grandmother.
- **An open game.** While a trivia round is open the answers are the conversation, and a remark lands in the middle
  of it.

Being ignored is the only one with a memory, and it is in this process only (`_remarks`), like the cooldown it sits
next to: a restart forgets it, and the worst that follows is one remark.
"""

import logging
import threading
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .chat_history import Saved

logger = logging.getLogger("tox.tact")

# Enough of each word to catch what it is built from ("falleci" covers falleció, fallecimiento, fallecida).
SERIOUS_WORDS = (
    "falleci", "murio", "muerte", "muerto", "velorio", "funeral", "entierro", "luto", "condolencia",
    "cancer", "quimio", "internado", "internada", "uci", "cti", "terapia intensiva", "respirador",
    "accidente", "choque", "atropell", "incendio", "robaron", "asaltaron",
    "suicid", "depresion", "ansiedad", "psiquiatr", "operacion", "operaron", "biopsia", "tumor",
    "divorcio", "separamos", "despidieron", "despido", "desalojo",
)
# Said of the bot: what counts as somebody talking to it even with no command typed.
NAMES = ("tox", "toxi")


@dataclass
class _Remark:
    at: datetime
    answered: bool = False


@dataclass
class _ChatTact:
    last: _Remark | None = None
    ignored: int = 0


_lock = threading.Lock()
_remarks: dict[str, _ChatTact] = {}


def _plain(text: str) -> str:
    """Lowercase and without accents: what people type is not what a word list has."""
    lowered = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in lowered if unicodedata.category(c) != "Mn")


def awake(now_utc: datetime, timezone_name: str, from_hour: int, until_hour: int) -> bool:
    """Whether the clock in `timezone_name` is inside the window it may talk in. The window wraps midnight when
    `until_hour` is the smaller of the two (9 to 1 is "from nine in the morning to one at night")."""
    if from_hour == until_hour:
        return True  # a window of the whole day: no hour is ever the wrong hour
    hour = now_utc.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(timezone_name)).hour
    if from_hour < until_hour:
        return from_hour <= hour < until_hour
    return hour >= from_hour or hour < until_hour


def serious_talk(messages: list[Saved]) -> str | None:
    """The first word in these messages that says the group is not in the mood, or None."""
    for message in messages:
        text = _plain(message.text)
        for word in SERIOUS_WORDS:
            if word in text:
                return word
    return None


def talks_too_much(messages: list[Saved], bot_id: str, max_share: float) -> bool:
    """Whether the bot has already written more than `max_share` times what the average person there has.

    An empty window is "too much": with nobody talking there is no conversation to join, which is also what keeps
    it from remarking into a chat that has been asleep for hours."""
    mine = [m for m in messages if m.sender_id == bot_id]
    theirs = [m for m in messages if m.sender_id != bot_id]
    people = {m.sender_id for m in theirs}
    if not people:
        return True
    return len(mine) > max_share * (len(theirs) / len(people))


# --- being ignored (per chat, this process only) ---------------------------------------------------------------

def remarked(chat_key: str, now_utc: datetime) -> None:
    """Tox just said something of its own there. Whether it was worth it is decided by what happens next."""
    with _lock:
        state = _remarks.setdefault(chat_key, _ChatTact())
        if state.last is not None and not state.last.answered:
            state.ignored += 1
        state.last = _Remark(at=now_utc)


def answered(chat_key: str) -> None:
    """Somebody talked to the bot in that chat (a command, a reply to it, or its name): whatever it said last was
    picked up, and a streak of being ignored is over."""
    with _lock:
        state = _remarks.setdefault(chat_key, _ChatTact())
        if state.last is not None:
            state.last.answered = True
        state.ignored = 0


def ignored_in_a_row(chat_key: str) -> int:
    with _lock:
        state = _remarks.get(chat_key)
        if state is None:
            return 0
        pending = 1 if state.last is not None and not state.last.answered else 0
        return state.ignored + pending


def spoken_to(text: str) -> bool:
    """Whether a message with no command in it is still aimed at the bot, by naming it."""
    words = set(_plain(text).replace(",", " ").replace("?", " ").replace("!", " ").split())
    return any(name in words for name in NAMES)


def forget(chat_key: str | None = None) -> None:
    """For the tests, and for a chat that was deleted."""
    with _lock:
        if chat_key is None:
            _remarks.clear()
        else:
            _remarks.pop(chat_key, None)


# --- the one question the command asks ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Situation:
    chat_key: str
    now_utc: datetime
    timezone_name: str
    from_hour: int
    until_hour: int
    give_up_after: int
    max_share: float
    bot_id: str
    recent: list[Saved] = field(default_factory=list)
    game_open: bool = False


def reason_to_stay_quiet(situation: Situation) -> str | None:
    """Why Tox must not say anything of its own right now, in words for the log, or None when it may."""
    if not awake(situation.now_utc, situation.timezone_name, situation.from_hour, situation.until_hour):
        return "it is outside the hours it talks in"
    if situation.game_open:
        return "a game is open"
    if situation.give_up_after > 0 and ignored_in_a_row(situation.chat_key) >= situation.give_up_after:
        return f"its last {situation.give_up_after} remarks went unanswered"
    word = serious_talk(situation.recent)
    if word is not None:
        return f"the chat is talking about something serious ({word})"
    if talks_too_much(situation.recent, situation.bot_id, situation.max_share):
        return "it has already talked more than the people there"
    return None
