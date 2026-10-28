"""What Tox picks up about the people of a chat while it talks to them (`person_notes`, `!memoria`).

Until now everything it knew about anybody was written by the owner by hand in `CHATS_FILE`. This is the other
half: the small things that come up in a conversation -- what somebody does, what they are into, an inside joke --
remembered without anybody having to write them down.

**No extra question is asked for it.** The answer Claude is already giving may end with lines of its own,

    [memoria] Nombre: dato

which are taken off before the chat sees a word of it and stored as notes. That is the whole mechanism: one
question, one answer, and whatever it learned rides along. The stripping is deliberately greedy -- *any* line
beginning with the marker goes, wherever it is and however malformed -- because the one failure that matters is a
marker reaching the chat.

What keeps this honest:

- **A note belongs to one chat.** The chat is part of the key, so what somebody says in one group is never read in
  another, and nothing said in the personal mode is kept at all.
- **Only the no-access mode learns.** The admins' full-access session is doing work on a machine, not chatting, and
  its prompt is not asked for any of this.
- **A name has to be somebody who is there.** Notes are resolved against the people of that chat and dropped when
  they are not about one of them, so Claude cannot invent a person to remember things about.
- **Each person owns theirs.** `!memoria` is what Tox knows about you here and `!memoria olvidame` deletes it, with
  no admin needed: that is the only reason a bot is allowed to keep notes about people at all.
- A chat can turn it off entirely (`!config memoria no`), and then nothing is stored or read there.

Notes are read back into the prompt as what they are: data that came out of a conversation, in the untrusted block
with the rest of it, never in the owner's own description of the chat.
"""

import logging
import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import PersonNote
from ..utils import utcnow

logger = logging.getLogger("tox.memory")

# "[memoria] Juan: juega al fútbol los martes". Case and spacing are whatever the model felt like.
LINE = re.compile(r"^\s*\[\s*memoria\s*\]\s*(?P<who>[^:]{1,40}?)\s*:\s*(?P<what>.+?)\s*$", re.IGNORECASE)
MARKER = re.compile(r"^\s*\[\s*memoria\s*\]", re.IGNORECASE)  # what is taken off even when the rest is unreadable

ASK = ("Si en esta charla aprendiste algo que valga la pena recordar de alguien (a qué se dedica, qué le gusta, "
       "una joda interna, un dato suyo), agregá al final de tu respuesta, en líneas aparte:\n"
       "[memoria] Nombre: el dato en pocas palabras\n"
       "Como máximo 2 líneas y solo si es algo duradero: nada de lo que está haciendo ahora, ni de lo que ya "
       "sabés, ni cosas del momento. Si no aprendiste nada, no agregues ninguna línea. El Nombre tiene que ser "
       "uno de los integrantes del grupo. Esas líneas no se muestran en el chat.")


def strip(answer: str) -> tuple[str, list[tuple[str, str]]]:
    """Takes the `[memoria]` lines off an answer. Returns (what the chat sees, [(name, note)...]).

    Every line that begins with the marker is removed, parsed or not: a line that cannot be read is a line that was
    not learned, which costs nothing, while a marker left in the text is in front of the whole group."""
    kept, learned = [], []
    for line in answer.splitlines():
        if not MARKER.match(line):
            kept.append(line)
            continue
        match = LINE.match(line)
        if match:
            learned.append((match.group("who").strip(), match.group("what").strip()))
        else:
            logger.info("MEMORY dropped an unreadable note line: %r", line[:120])
    return "\n".join(kept).strip(), learned


def _plain(text: str) -> str:
    lowered = unicodedata.normalize("NFD", text.casefold())
    return "".join(c for c in lowered if unicodedata.category(c) != "Mn")


def _same_note(one: str, other: str) -> bool:
    return _plain(one) == _plain(other)


def remember(db: Session, chat_key: str, learned: list[tuple[str, str]], people: dict[str, str],
             *, max_notes: int, max_chars: int) -> int:
    """Stores what an answer learned. `people` maps a name, as Claude wrote it, to that person's user_key; a name
    that is not in it is dropped. A note that is already there is only counted again, and past `max_notes` the
    least confirmed and oldest ones go. Returns how many were stored."""
    by_name = {_plain(name): key for name, key in people.items() if name}
    stored = 0
    for name, text in learned:
        user_key = by_name.get(_plain(name))
        if user_key is None:
            logger.info("MEMORY dropped a note about %r, who is not in %s", name[:40], chat_key)
            continue
        text = " ".join(text.split())[:max_chars]
        if not text:
            continue
        rows = list(db.scalars(select(PersonNote).where(
            PersonNote.chat_key == chat_key, PersonNote.user_key == user_key)))
        same = next((row for row in rows if _same_note(row.text, text)), None)
        if same is not None:
            same.seen += 1
            same.learned_at = utcnow()
            continue
        db.add(PersonNote(chat_key=chat_key, user_key=user_key, text=text))
        stored += 1
        if len(rows) + 1 > max_notes:
            # The ones that never came up again go first, oldest among those.
            for row in sorted(rows, key=lambda r: (r.seen, r.learned_at))[: len(rows) + 1 - max_notes]:
                db.delete(row)
    if learned:
        db.commit()
    return stored


def notes_for(db: Session, chat_key: str, user_keys: list[str]) -> dict[str, list[str]]:
    """What is known about these people in this chat, newest last, by user_key."""
    if not user_keys:
        return {}
    found: dict[str, list[str]] = {}
    rows = db.scalars(select(PersonNote).where(
        PersonNote.chat_key == chat_key, PersonNote.user_key.in_(set(user_keys)))
        .order_by(PersonNote.learned_at))
    for row in rows:
        found.setdefault(row.user_key, []).append(row.text)
    return found


def of_person(db: Session, chat_key: str, user_key: str) -> list[PersonNote]:
    return list(db.scalars(select(PersonNote).where(
        PersonNote.chat_key == chat_key, PersonNote.user_key == user_key).order_by(PersonNote.learned_at)))


def forget(db: Session, chat_key: str, user_key: str) -> int:
    """Everything Tox knew about one person in one chat, gone. Returns how many notes there were."""
    rows = of_person(db, chat_key, user_key)
    for row in rows:
        db.delete(row)
    if rows:
        db.commit()
    return len(rows)
