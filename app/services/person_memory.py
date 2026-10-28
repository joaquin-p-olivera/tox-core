"""What Tox picks up about the people of a chat while it talks to them (`person_notes`, `!memory`).

Until now everything it knew about anybody was written by the owner by hand in `CHATS_FILE`. This is the other
half: the small things that come up in a conversation -- what somebody does, what they are into, an inside joke --
remembered without anybody having to write them down.

**No extra question is asked for it.** The answer Claude is already giving ends with lines of its own,

    [memoria] Nombre: dato            <- in this chat only
    [memoria global] Nombre: dato     <- about the person, wherever they are
    [memoria] -                       <- nothing worth remembering came up, which is most of the time

which are taken off before the chat sees a word of it and stored as notes. That is the whole mechanism: one
question, one answer, and whatever it learned rides along. The stripping is deliberately greedy -- *any* line
beginning with the marker goes, wherever it is and however malformed -- because the one failure that matters is a
marker reaching the chat.

**The block is asked for on every answer, not when the model feels like it.** Asked as a favour -- *add this if you
learned something worth keeping* -- it was simply never added: an optional instruction at the end of a long prompt,
hedged with conditions, each one a reason to skip it. Asked as the way every answer ends, with an explicit way to
say nothing came up, it is one more thing to comply with rather than a judgement call. It also makes the failure
visible: an answer with no block at all did not follow the request, and that is logged.

**Two kinds of note, because people are not the same thing as groups.** What somebody *is* -- their job, their
city, what they are into -- is true in every chat they are in, and a bot that has to learn it again in each group
knows nobody. What belongs to a group -- an inside joke, a nickname only used there, something said among those
people -- is exactly what must not travel. So a note is either global (`chat_key` is `EVERYWHERE`) or tied to the
chat it was learned in, and which one it is is decided when it is learned, by the only party that heard the
conversation.

What keeps this honest:

- **A note about a group stays in that group.** The chat is part of the key, and nothing said in the personal mode
  is kept at all. Only a note about the person themselves is read elsewhere, which is the point of the two kinds.
- **Only a group learns.** A group is a conversation between people, and the admins are people in it like anybody
  else: their own questions are how Tox gets to know *them*, which is why the access level has nothing to do with
  it. A private chat is the other thing -- one person driving the machine, with no group to know -- and nothing is
  kept there.
- **A name has to be somebody who is there.** Notes are resolved against the people of that chat and dropped when
  they are not about one of them, so Claude cannot invent a person to remember things about. The matching is loose
  in one direction only: "Leandro" is the member WhatsApp calls "Leandro 1899", because that is what the model
  reads in the conversation -- but never when two members could be meant, since a fact filed under the wrong
  person is worse than one that was dropped.
- **Each person reads theirs.** `!memory` is what Tox knows about you here, for anybody who asks. Deleting it
  (`!memory forget`) is held with the admins while the feature is new -- what it learns is still being watched, and
  a note that is gone cannot be looked at to see why it was kept. This is the part to give back first: a bot that
  keeps notes about people and will not let them be deleted by the person they are about is relying on the owner's
  good manners, which is not the same as a rule.
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
# "[memoria] -": the block is asked for on every answer, so there has to be a way to say nothing was learned.
NOTHING = re.compile(r"^\s*\[\s*memoria\s*\]\s*-*\s*$", re.IGNORECASE)

# Said in the SYSTEM prompt, never with the question. The public prompt tells the model that anything inside the
# context may be an instruction somebody planted and must not be obeyed -- which is right, and which is exactly why
# a request of ours cannot travel there: from where the model sits it looks the same. So it goes where the model is
# allowed to trust it, next to the tone and the rules.
ASK = """
Además de tu respuesta, lo que aprendas de la gente:
SIEMPRE, sin excepción, lo último que escribís es una línea que empieza con [memoria]. Es obligatorio, en todas tus
respuestas, hayas aprendido algo o no.
- Si aprendiste algo de alguien que valga la pena recordar, como máximo 2 líneas, eligiendo entre estas dos:
  [memoria global] Nombre: el dato en pocas palabras
      para lo que la persona ES y es cierto en cualquier chat: a qué se dedica, de dónde es, qué le gusta.
  [memoria] Nombre: el dato en pocas palabras
      para lo que es de ESTE grupo y no debe salir de acá: una joda interna, un apodo que solo se usa acá, algo que
      se dijo entre esta gente.
  Si dudás entre las dos, usá la segunda: lo que queda acá no molesta a nadie. Solo cosas duraderas: nada de lo que
  la persona está haciendo ahora ni cosas del momento. Si el dato ya está en "Lo que fuiste sabiendo de cada uno
  charlando", no lo repitas. Lo que sepas por la descripción del grupo NO cuenta como sabido: eso es contexto, no
  una nota, así que si sale en la charla guardalo igual. El Nombre tiene que ser uno de los integrantes del grupo.
- Si no aprendiste nada, que es lo más común, escribí exactamente esta línea y nada más:
  [memoria] -
Estas líneas se borran antes de que el chat vea la respuesta, así que nadie las lee y NO cuentan para el máximo de
caracteres. No las menciones ni las expliques.
"""


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
        elif not NOTHING.match(line):  # "nothing learned" is the expected answer, not a line that failed to parse
            logger.info("MEMORY dropped an unreadable note line: %r", line[:120])
    return "\n".join(kept).strip(), learned


def carried_block(answer: str) -> bool:
    """Whether the answer ended with the block that is asked for on every one of them -- even if what it said was
    that nothing was learned. It is the one thing that tells a chat where nobody said anything memorable apart from
    an answer that ignored the request, which is otherwise silent and looks the same."""
    return any(MARKER.match(line) for line in answer.splitlines())


def _plain(text: str) -> str:
    lowered = unicodedata.normalize("NFD", text.casefold())
    return "".join(c for c in lowered if unicodedata.category(c) != "Mn")


def _words(name: str) -> frozenset[str]:
    """The words of a name, without the decoration people put in theirs: "Leandro 1899", "Joaquín.", "line🧃🐛"."""
    return frozenset(word for word in re.split(r"[^0-9a-z]+", _plain(name)) if word)


def _resolve(name: str, people: dict[str, str]) -> tuple[str | None, str]:
    """Which member of the chat the model meant, or None and why not, for the log.

    What somebody is called in a chat is not their display name: the model reads the conversation and writes
    "Leandro", while WhatsApp says "Leandro 1899". Matching the whole string would drop almost every note in a real
    group. So a name matches when one is contained in the other word for word -- and only when exactly one person
    can be meant, because a thing filed under the wrong person is worse than one that was dropped."""
    written = _words(name)
    if not written:
        return None, "it is not a name"
    found = {key for member, key in people.items() if _words(member) == written}
    if len(found) != 1:
        # Nobody by that exact name, or two people with it: try the loose way, both directions.
        found = {key for member, key in people.items()
                 # A shared word that is only digits says nothing: "Leandro 1899" and "Pedro 1899" are not the same
                 # person, and "1899" is not a name.
                 if _words(member) and (_words(member) <= written or written <= _words(member))
                 and any(not word.isdigit() for word in _words(member) & written)}
    if len(found) == 1:
        return found.pop(), ""
    if found:
        return None, f"{len(found)} people in {name[:40]!r} could be meant"
    return None, "nobody in the chat is called that"


def _same_note(one: str, other: str) -> bool:
    return _plain(one) == _plain(other)


def remember(db: Session, chat_key: str, learned: list[Learned], people: dict[str, str],
             *, max_notes: int, max_chars: int) -> int:
    """Stores what an answer learned. `people` maps a name, as Claude wrote it, to that person's user_key; a name
    that is not in it is dropped. A note about the person goes to the EVERYWHERE bucket and one about the group to
    this chat's, and each bucket has its own `max_notes`: what the group knows about somebody cannot crowd out what
    is true about them. A note that is already there is only counted again, and past the ceiling the least
    confirmed and oldest ones go. Returns how many were stored."""
    stored = 0
    for note in learned:
        name, text = note.name, note.text
        user_key, why = _resolve(name, {n: key for n, key in people.items() if n})
        if user_key is None:
            logger.info("MEMORY dropped a note about %r in %s: %s", name[:40], chat_key, why)
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
