"""What one chat may be told about another one's conversation (models/chat_context_source.py).

The rule is not "these two chats are connected": a row says that THIS chat may read THAT one, and the other direction
needs its own row. So a group can be given the background of another without the other learning anything about it.

And it is read only when the question asks for it. Each source carries the words that call it up (`triggers`, the
label by default); a question that names none of them is answered as if the source did not exist, and nothing of that
chat reaches the prompt at all. That is deliberate: an instruction not to bring something up is a control, not a
guarantee, while text that was never sent cannot be repeated by accident.
"""

import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ChatContextSource
from ..utils import utcnow


def _plain(text: str) -> str:
    """Lowercase and without accents, so "El Otro Grupo" and "el otro grupo" are the same word to look for."""
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


def words_of(source: ChatContextSource) -> list[str]:
    """What calls this source up: its triggers, or its label when it has none."""
    words = [w.strip() for w in source.triggers.split(",") if w.strip()]
    return words or [source.label]


def asked_for(db: Session, chat_key: str, question: str) -> list[ChatContextSource]:
    """The sources of this chat the question names. Empty for a question that names none, and for no question at all
    (a remark Tox makes on its own is never the moment to bring another chat up)."""
    if not question.strip():
        return []
    asked = _plain(question)
    rows = db.scalars(select(ChatContextSource).where(ChatContextSource.chat_key == chat_key))
    return [row for row in rows if any(_plain(word) in asked for word in words_of(row))]


def of_chat(db: Session, chat_key: str) -> list[ChatContextSource]:
    """Every chat this one may read, whether or not any question asked for them."""
    return list(db.scalars(select(ChatContextSource).where(ChatContextSource.chat_key == chat_key)
                           .order_by(ChatContextSource.label)))


def allow(db: Session, chat_key: str, source_chat_key: str, label: str, triggers: list[str], by: str) -> None:
    """Lets `chat_key` read `source_chat_key` when a question names one of `triggers`. One direction only: the row
    for the other way round is a different row, and this never writes it."""
    row = db.get(ChatContextSource, (chat_key, source_chat_key))
    words = ",".join(dict.fromkeys(w.strip() for w in triggers if w.strip())) or label
    if row is None:
        db.add(ChatContextSource(chat_key=chat_key, source_chat_key=source_chat_key, label=label,
                                 triggers=words, added_by=by))
    else:
        row.label, row.triggers, row.added_by, row.added_at = label, words, by, utcnow()
    db.commit()


def forbid(db: Session, chat_key: str, source_chat_key: str) -> bool:
    """Stops this chat from reading that one. False when it was not reading it."""
    row = db.get(ChatContextSource, (chat_key, source_chat_key))
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True
