import random

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import ChatUser
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


def pick_random_other(db: Session, message: IncomingMessage) -> Participant | None:
    """A random chat member that isn't the sender, or None if there is nobody else."""
    others = [p for p in known_members(db, message) if p.user_id != message.user_id]
    return random.choice(others) if others else None
