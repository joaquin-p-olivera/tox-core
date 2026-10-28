from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..utils import utcnow


class PersonNote(Base):
    """Something Tox picked up about somebody while talking in a chat (services/person_memory.py).

    The chat is part of the key, not a detail of it: what somebody says in one group stays in that group, so a note
    learned in one chat is never read in another and nothing said in a private chat is kept at all. Each person can
    read their own notes and have them forgotten (`!memory`), which is the only reason this is allowed to exist."""

    __tablename__ = "person_notes"
    __table_args__ = (Index("ix_person_notes_chat_user", "chat_key", "user_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_key: Mapped[str] = mapped_column(String)
    user_key: Mapped[str] = mapped_column(String)
    text: Mapped[str] = mapped_column(String)
    learned_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # How many times it came up again: a note that keeps being confirmed is the last one to be dropped.
    seen: Mapped[int] = mapped_column(Integer, default=1)
