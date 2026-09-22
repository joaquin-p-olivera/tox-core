import random

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import ChatUser, MutedUser
from ..schemas import IncomingMessage, Participant
from ..utils import utcnow


def record_user(db: Session, message: IncomingMessage) -> None:
    """Remember that this user is in this chat (used when the platform can't list members)."""
    row = db.scalars(select(ChatUser).where(
        ChatUser.chat_key == message.chat_key, ChatUser.user_id == message.user_id)).first()
    if row is None:
        db.add(ChatUser(chat_key=message.chat_key, user_id=message.user_id, user_name=message.user_name))
    else:
        row.last_seen_at = utcnow()
        if message.user_name:
            row.user_name = message.user_name
    try:
        db.commit()
    except IntegrityError:
        db.rollback()  # a concurrent request inserted the same user first: nothing to do


def known_members(db: Session, message: IncomingMessage) -> list[Participant]:
    """Chat members: the bot-provided full list if any, else the users seen so far."""
    if message.participants:
        return list(message.participants)
    rows = db.scalars(select(ChatUser).where(ChatUser.chat_key == message.chat_key))
    return [Participant(user_id=row.user_id, user_name=row.user_name) for row in rows]


def muted_ids(db: Session, chat_key: str) -> set[str]:
    return set(db.scalars(select(MutedUser.user_id).where(MutedUser.chat_key == chat_key)))


def toggle_mute(db: Session, message: IncomingMessage) -> bool:
    """Mutes the sender if they weren't muted, un-mutes them if they were. Returns True when now muted."""
    row = db.scalars(select(MutedUser).where(
        MutedUser.chat_key == message.chat_key, MutedUser.user_id == message.user_id)).first()
    if row is not None:
        db.delete(row)
        db.commit()
        return False
    db.add(MutedUser(chat_key=message.chat_key, user_id=message.user_id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()  # a concurrent !mute inserted it first: the user is muted either way
    return True


def taggable_members(db: Session, message: IncomingMessage) -> list[Participant]:
    """Members that may be tagged: not the sender and not muted. Every command that tags someone
    must pick from here so !mute is respected everywhere."""
    muted = muted_ids(db, message.chat_key)
    return [
        p for p in known_members(db, message)
        if p.user_id != message.user_id and not ({p.user_id, *p.aliases} & muted)
    ]


def pick_random_other(db: Session, message: IncomingMessage) -> Participant | None:
    """A random taggable member (never the sender, never a muted user), or None if there is nobody."""
    candidates = taggable_members(db, message)
    return random.choice(candidates) if candidates else None
