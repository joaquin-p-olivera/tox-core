from datetime import date, datetime

from sqlalchemy import Date, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..utils import utcnow


class Birthday(Base):
    """A person's birthday (!cumple). Per person, not per chat: one date for every group they are in."""

    __tablename__ = "birthdays"
    __table_args__ = (UniqueConstraint("platform", "user_id", name="uq_birthdays_platform_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String)
    user_id: Mapped[str] = mapped_column(String)
    user_name: Mapped[str | None] = mapped_column(String, nullable=True)
    # With no year given, the year is commands/utilities.NO_YEAR (1904)
    born_on: Mapped[date] = mapped_column(Date)
    set_by: Mapped[str] = mapped_column(String)  # user_key of whoever typed it (the person, or an admin)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
