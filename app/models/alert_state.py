from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..utils import utcnow


class AlertState(Base):
    """Last known health of one monitored service, so the alert monitor can tell an actual change
    (worth a message) from "still down since last time" (already told everyone once)."""

    __tablename__ = "alert_states"

    key: Mapped[str] = mapped_column(String, primary_key=True)  # e.g. "local:trip-trace-telegram-bot"
    ok: Mapped[bool] = mapped_column(Boolean)
    label: Mapped[str] = mapped_column(String, default="")  # last detail text, for the recovered/still-down message
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
