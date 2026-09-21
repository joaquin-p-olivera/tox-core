from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..utils import utcnow


class Score(Base):
    """Per-chat leaderboard row for one user."""

    __tablename__ = "scores"
    __table_args__ = (UniqueConstraint("chat_key", "user_key", name="uq_scores_chat_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_key: Mapped[str] = mapped_column(String, index=True)
    user_key: Mapped[str] = mapped_column(String)
    user_name: Mapped[str] = mapped_column(String)
    points: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
