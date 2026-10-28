from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..utils import utcnow


class NewsState(Base):
    """Which release note was the last one announced (services/news.py). One row, same idea as CountdownState: what
    is remembered is the entry already sent, not when it was sent, so a restart announces what is new and nothing
    twice. An empty table means this machine has never announced anything, which is deliberately not the same as
    "announce everything": the first run writes down where the file stands and sends nothing."""

    __tablename__ = "news_states"

    key: Mapped[str] = mapped_column(String, primary_key=True)  # always "news": one line of release notes
    last_id: Mapped[str] = mapped_column(String)  # the id of the newest entry already queued
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
