from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..utils import utcnow


class MutedUser(Base):
    """A user who asked (!mute) not to be tagged in a chat."""

    __tablename__ = "muted_users"
    __table_args__ = (UniqueConstraint("chat_key", "user_id", name="uq_muted_users_chat_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_key: Mapped[str] = mapped_column(String, index=True)
    user_id: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
