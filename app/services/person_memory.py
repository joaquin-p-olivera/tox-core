"""What Tox picks up about the people of a chat while it talks to them (`person_notes`, `!memory`).

Until now everything it knew about anybody was written by the owner by hand in `CHATS_FILE`. This is the other
half: the small things that come up in a conversation -- what somebody does, what they are into, an inside joke --
remembered without anybody having to write them down.

**No extra question is asked for it.** The answer Claude is already giving may end with lines of its own,

    [memoria] Nombre: dato            <- in this chat only
    [memoria global] Nombre: dato     <- about the person, wherever they are

which are taken off before the chat sees a word of it and stored as notes. That is the whole mechanism: one
question, one answer, and whatever it learned rides along. The stripping is deliberately greedy -- *any* line
beginning with the marker goes, wherever it is and however malformed -- because the one failure that matters is a
marker reaching the chat.

**Two kinds of note, because people are not the same thing as groups.** What somebody *is* -- their job, their
city, what they are into -- is true in every chat they are in, and a bot that has to learn it again in each group
knows nobody. What belongs to a group -- an inside joke, a nickname only used there, something said among those
people -- is exactly what must not travel. So a note is either global (`chat_key` is `EVERYWHERE`) or tied to the
chat it was learned in, and which one it is is decided when it is learned, by the only party that heard the
conversation.

What keeps this honest:

- **A note about a group stays in that group.** The chat is part of the key, and nothing said in the personal mode
  is kept at all. Only a note about the person themselves is read elsewhere, which is the point of the two kinds.
- **Only the no-access mode learns.** The admins' full-access session is doing work on a machine, not chatting, and
  its prompt is not asked for any of this.
- **A name has to be somebody who is there.** Notes are resolved against the people of that chat and dropped when
  they are not about one of them, so Claude cannot invent a person to remember things about.
- **Each person owns theirs.** `!memory` is what Tox knows about you here and `!memory forget` deletes it, with
  no admin needed: that is the only reason a bot is allowed to keep notes about people at all.
- A chat can turn it off entirely (`!config memory off`), and then nothing is stored or read there.

Notes are read back into the prompt as what they are: data that came out of a conversation, in the untrusted block
with the rest of it, never in the owner's own description of the chat.
"""

import logging
import re
import unicodedata
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import PersonNote
from ..utils import utcnow

logger = logging.getLogger("tox.memory")

# The chat a note that travels is filed under: not a chat at all, which is why it is a character no chat_key can
# have. Reading a chat's notes reads this bucket too.
EVERYWHERE = "*"

# "[memoria] Juan: juega al fútbol los martes", or "[memoria global] Juan: es veterinario". Case and spacing are
# whatever the model felt like.
LINE = re.compile(r"^\s*\[\s*memoria(?P<scope>\s+global)?\s*\]\s*(?P<who>[^:]{1,40}?)\s*:\s*(?P<what>.+?)\s*$",
                  re.IGNORECASE)
MARKER = re.compile(r"^\s*\[\s*memoria", re.IGNORECASE)  # what is taken off even when the rest is unreadable

ASK = ("Si en esta charla aprendiste algo que valga la pena recordar de alguien, agregalo al final de tu "
       "respuesta, en líneas aparte, y elegí entre estas dos:\n"
       "[memoria global] Nombre: el dato en pocas palabras\n"
       "    para lo que la persona ES y es cierto en cualquier chat: a qué se dedica, de dónde es, qué le gusta.\n"
       "[memoria] Nombre: el dato en pocas palabras\n"
       "    para lo que es de ESTE grupo y no debe salir de acá: una joda interna, un apodo que solo se usa acá, "
       "algo que se dijo entre esta gente.\n"
       "Si dudás, usá la segunda: lo que queda acá no molesta a nadie. Como máximo 2 líneas en total y solo si es "
       "algo duradero: nada de lo que está haciendo ahora, ni de lo que ya sabés, ni cosas del momento. Si no "
       "aprendiste nada, no agregues ninguna línea. El Nombre tiene que ser uno de los integrantes del grupo. "
       "Esas líneas no se muestran en el chat.")


@dataclass(frozen=True)
class Learned:
    """One thing an answer said it had learned, and whether it is about the person or about this group."""

    name: str  # as Claude wrote it: resolved against the people of the chat, and dropped if it is nobody there
    text: str
    everywhere: bool = False


def strip(answer: str) -> tuple[str, list[Learned]]:
    """Takes the `[memoria]` lines off an answer. Returns (what the chat sees, what it learned).

    Every line that begins with the marker is removed, parsed or not: a line that cannot be read is a line that was
    not learned, which costs nothing, while a marker left in the text is in front of the whole group."""
    kept, learned = [], []
    for line in answer.splitlines():
        if not MARKER.match(line):
            kept.append(line)
            continue
        match = LINE.match(line)
        if match:
            learned.append(Learned(name=match.group("who").strip(), text=match.group("what").strip(),
                                   everywhere=bool(match.group("scope"))))
        else:
            logger.info("MEMORY dropped an unreadable note line: %r", line[:120])
    return "\n".join(kept).strip(), learned


def _plain(text: str) -> str:
    lowered = unicodedata.normalize("NFD", text.casefold())
    return "".join(c for c in lowered if unicodedata.category(c) != "Mn")


def _same_note(one: str, other: str) -> bool:
    return _plain(one) == _plain(other)


def remember(db: Session, chat_key: str, learned: list[Learned], people: dict[str, str],
             *, max_notes: int, max_chars: int) -> int:
    """Stores what an answer learned. `people` maps a name, as Claude wrote it, to that person's user_key; a name
    that is not in it is dropped. A note about the person goes to the EVERYWHERE bucket and one about the group to
    this chat's, and each bucket has its own `max_notes`: what the group knows about somebody cannot crowd out what
    is true about them. A note that is already there is only counted again, and past the ceiling the least
    confirmed and oldest ones go. Returns how many were stored."""
    by_name = {_plain(name): key for name, key in people.items() if name}
    stored = 0
    for note in learned:
        name, text = note.name, note.text
        user_key = by_name.get(_plain(name))
        if user_key is None:
            logger.info("MEMORY dropped a note about %r, who is not in %s", name[:40], chat_key)
            continue
        text = " ".join(text.split())[:max_chars]
        if not text:
            continue
        filed_under = EVERYWHERE if note.everywhere else chat_key
        rows = list(db.scalars(select(PersonNote).where(
            PersonNote.chat_key == filed_under, PersonNote.user_key == user_key)))
        same = next((row for row in rows if _same_note(row.text, text)), None)
        if same is not None:
            same.seen += 1
            same.learned_at = utcnow()
            continue
        db.add(PersonNote(chat_key=filed_under, user_key=user_key, text=text))
        stored += 1
        if len(rows) + 1 > max_notes:
            # The ones that never came up again go first, oldest among those.
            for row in sorted(rows, key=lambda r: (r.seen, r.learned_at))[: len(rows) + 1 - max_notes]:
                db.delete(row)
    if learned:
        db.commit()
    return stored


def notes_for(db: Session, chat_key: str, user_keys: list[str]) -> dict[str, list[str]]:
    """What is known about these people in this chat: what is true about them anywhere, and then what this group
    knows. By user_key, newest last within each kind."""
    if not user_keys:
        return {}
    found: dict[str, list[str]] = {}
    rows = db.scalars(select(PersonNote).where(
        PersonNote.chat_key.in_((chat_key, EVERYWHERE)), PersonNote.user_key.in_(set(user_keys)))
        # The ones that travel first: they are what the person is, and the rest is what this group made of it.
        .order_by(PersonNote.chat_key != EVERYWHERE, PersonNote.learned_at))
    for row in rows:
        found.setdefault(row.user_key, []).append(row.text)
    return found


def of_person(db: Session, chat_key: str, user_key: str) -> tuple[list[PersonNote], list[PersonNote]]:
    """One person's notes as `!memory` shows them: (the ones that are true anywhere, the ones this chat keeps)."""
    rows = list(db.scalars(select(PersonNote).where(
        PersonNote.chat_key.in_((chat_key, EVERYWHERE)), PersonNote.user_key == user_key)
        .order_by(PersonNote.learned_at)))
    return ([row for row in rows if row.chat_key == EVERYWHERE],
            [row for row in rows if row.chat_key != EVERYWHERE])


def forget(db: Session, user_key: str) -> int:
    """Everything Tox knew about one person, in every chat and about them anywhere, gone. Asking to be forgotten
    means forgotten -- not forgotten in the room it was asked in. Returns how many notes there were."""
    rows = list(db.scalars(select(PersonNote).where(PersonNote.user_key == user_key)))
    for row in rows:
        db.delete(row)
    if rows:
        db.commit()
    return len(rows)
