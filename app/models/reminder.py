from datetime import datetime

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..utils import utcnow


class Reminder(Base):
    """Something a user asked (!recordar) to be reminded of at a given time. Once due it becomes a PendingAlert
    for the bot to deliver in the same chat, and the row is deleted."""

    __tablename__ = "reminders"
    __table_args__ = (Index("ix_reminders_due_at", "due_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String)
    chat_id: Mapped[str] = mapped_column(String)
    user_id: Mapped[str] = mapped_column(String)  # who asked: tagged when the reminder fires
    text: Mapped[str] = mapped_column(String)
    due_at: Mapped[datetime] = mapped_column(DateTime)  # naive UTC, like everything stored
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
