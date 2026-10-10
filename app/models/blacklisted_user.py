from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..utils import utcnow


class BlacklistedUser(Base):
    """Someone banned for good (!permaban): the bot removes them from every group it is in and again whenever they
    come back."""

    __tablename__ = "blacklisted_users"
    __table_args__ = (UniqueConstraint("platform", "user_id", name="uq_blacklisted_platform_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String)
    user_id: Mapped[str] = mapped_column(String)
    # Comma-separated other ids of the same person (LID and phone number): a group may know them under either one
    aliases: Mapped[str] = mapped_column(String, default="")
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    added_by: Mapped[str] = mapped_column(String)  # user_key of the admin
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    @property
    def all_ids(self) -> list[str]:
        return list(dict.fromkeys([self.user_id, *[a for a in self.aliases.split(",") if a]]))
