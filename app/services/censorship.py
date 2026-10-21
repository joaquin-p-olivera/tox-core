"""Censorship (!censura): the chats where somebody's messages are deleted as they arrive.

The decision is NOT made here, message by message: a bot reads the whole list (GET /api/v1/censored) and deletes on
its own. It has to, because a censored person's ordinary chatter never reaches the API unless the history of that chat
is on -- and censorship cannot depend on that.
"""

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import CensoredUser
from ..schemas import IncomingMessage, Participant


def censored_ids(db: Session, chat_key: str) -> set[str]:
    """Every id (and alias) under censorship in this chat."""
    rows = db.scalars(select(CensoredUser).where(CensoredUser.chat_key == chat_key))
    return {person_id for row in rows for person_id in row.all_ids}


def toggle(db: Session, message: IncomingMessage, person: Participant) -> bool:
    """Censors this person in this chat if they weren't, lifts it if they were. Returns True when now censored."""
    row = db.scalars(select(CensoredUser).where(
        CensoredUser.chat_key == message.chat_key, CensoredUser.user_id == person.user_id)).first()
    if row is not None:
        db.delete(row)
        db.commit()
        return False
    aliases = ",".join(dict.fromkeys(a for a in person.aliases if a != person.user_id))
    db.add(CensoredUser(chat_key=message.chat_key, user_id=person.user_id, aliases=aliases,
                        name=person.user_name, added_by=message.user_key))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()  # a concurrent !censura inserted it first: the person is censored either way
    return True


def by_chat(db: Session, platform: str) -> dict[str, list[str]]:
    """chat id (without the platform prefix) -> every id of everyone censored there. Only the chats with somebody in
    them appear, so a bot that gets an empty answer knows it has nothing to delete."""
    prefix = f"{platform}:"
    found: dict[str, list[str]] = defaultdict(list)
    rows = db.scalars(select(CensoredUser).where(CensoredUser.chat_key.startswith(prefix)).order_by(CensoredUser.id))
    for row in rows:
        found[row.chat_key[len(prefix):]].extend(row.all_ids)
    return {chat: list(dict.fromkeys(ids)) for chat, ids in found.items()}
