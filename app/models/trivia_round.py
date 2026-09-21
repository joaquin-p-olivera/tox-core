from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..utils import utcnow

STATUS_OPEN = "open"
STATUS_WON = "won"
STATUS_EXPIRED = "expired"


class TriviaRound(Base):
    """One trivia question asked in one chat."""

    __tablename__ = "trivia_rounds"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_key: Mapped[str] = mapped_column(String, index=True)
    question_id: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default=STATUS_OPEN)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    winner_user_key: Mapped[str | None] = mapped_column(String, nullable=True)


class TriviaAttempt(Base):
    """A player's single answer to a round. The unique constraint enforces one attempt each."""

    __tablename__ = "trivia_attempts"
    __table_args__ = (UniqueConstraint("round_id", "user_key", name="uq_attempt_round_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("trivia_rounds.id"), index=True)
    user_key: Mapped[str] = mapped_column(String)
    choice: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
