"""Short names for chats (models/chat_alias.py), so `!spy` can say "festejen" instead of a WhatsApp id.

A name grants nothing on its own: what a chat may read of another is chat_context_sources and only that.
"""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ChatAlias

VALID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")  # one word, so it can be typed as an argument


def normalize(alias: str) -> str:
    return alias.strip().lower()


def valid(alias: str) -> bool:
    return bool(VALID.match(alias))


def key_of(db: Session, alias: str) -> str | None:
    row = db.get(ChatAlias, normalize(alias))
    return row.chat_key if row else None


def alias_of(db: Session, chat_key: str) -> str | None:
    row = db.scalar(select(ChatAlias).where(ChatAlias.chat_key == chat_key))
    return row.alias if row else None


def all_aliases(db: Session) -> list[ChatAlias]:
    return list(db.scalars(select(ChatAlias).order_by(ChatAlias.alias)))


def name(db: Session, chat_key: str, alias: str, by: str) -> None:
    """Gives this chat its name, replacing whatever it or that name had: a chat has one alias and an alias one chat."""
    for row in db.scalars(select(ChatAlias).where((ChatAlias.chat_key == chat_key) | (ChatAlias.alias == alias))):
        db.delete(row)
    db.flush()
    db.add(ChatAlias(alias=alias, chat_key=chat_key, set_by=by))
    db.commit()
