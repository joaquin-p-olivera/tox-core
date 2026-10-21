from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..utils import utcnow


class CensoredUser(Base):
    """Someone under censorship (!censura) in one chat: the bot deletes every message they send there, until an admin
    runs the command again. Unlike the blacklist this is per chat, and nobody is removed from the group."""

    __tablename__ = "censored_users"
    __table_args__ = (UniqueConstraint("chat_key", "user_id", name="uq_censored_users_chat_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_key: Mapped[str] = mapped_column(String, index=True)
    user_id: Mapped[str] = mapped_column(String)
    # Comma-separated other ids of the same person (LID and phone number): the message the bot is about to delete may
    # come under a different one than the id they were tagged with
    aliases: Mapped[str] = mapped_column(String, default="")
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    added_by: Mapped[str] = mapped_column(String)  # user_key of the admin
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    @property
    def all_ids(self) -> list[str]:
        return list(dict.fromkeys([self.user_id, *[a for a in self.aliases.split(",") if a]]))
