from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..utils import utcnow


class ChatUser(Base):
    """A user seen using the bot in a chat. Fallback member list for platforms that can't list members."""

    __tablename__ = "chat_users"
    __table_args__ = (UniqueConstraint("chat_key", "user_id", name="uq_chat_users_chat_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_key: Mapped[str] = mapped_column(String, index=True)
    user_id: Mapped[str] = mapped_column(String)
    user_name: Mapped[str | None] = mapped_column(String, nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
