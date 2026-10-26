from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..utils import utcnow


class CountdownState(Base):
    """How far a countdown has got (services/gta_countdown.py). What is remembered is the slot already sent, not when
    it was sent: a poll that comes late still sends that slot's message, and no slot is ever sent twice. `finished`
    is the end of the line -- the release message went out and nothing else ever will."""

    __tablename__ = "countdown_states"

    key: Mapped[str] = mapped_column(String, primary_key=True)  # e.g. "gta6"
    last_slot: Mapped[datetime] = mapped_column(DateTime)  # naive UTC, the scheduled moment whose message was queued
    finished: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
