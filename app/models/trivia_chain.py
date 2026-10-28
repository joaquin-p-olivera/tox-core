from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..utils import utcnow


class TriviaChain(Base):
    """The category a chat's streak of trivia rounds is playing, when it asked for one.

    A streak outlives the rounds in it, which is why this is not a column of `trivia_rounds`: asking for
    `!trivia ciencia` is asking the *chat* to play ciencia, and the rounds that follow on their own have to know
    that. No row means the streak takes questions from everything, which is what a plain `!trivia` starts."""

    __tablename__ = "trivia_chains"

    chat_key: Mapped[str] = mapped_column(String, primary_key=True)
    category: Mapped[str] = mapped_column(String)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
