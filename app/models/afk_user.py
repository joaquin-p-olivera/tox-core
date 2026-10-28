from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..utils import utcnow


class AfkUser(Base):
    """Somebody who said they are away (!afk). One row per person, not per chat: being away is about the person,
    so whoever looks for them in any chat the bot is in is told the same thing. The row is deleted the moment they
    write again anywhere, which is what "being back" means here -- nobody has to remember to turn it off."""

    __tablename__ = "afk_users"

    user_key: Mapped[str] = mapped_column(String, primary_key=True)  # "whatsapp:123@lid"
    user_name: Mapped[str] = mapped_column(String, default="")  # what to call them when they are not here to ask
    reason: Mapped[str] = mapped_column(String, default="")
    since: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
