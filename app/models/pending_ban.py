from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..utils import utcnow


class PendingBan(Base):
    """The user !ruleta-ban picked in a chat, waiting for an admin to confirm (!si) or cancel (!no). One per chat."""

    __tablename__ = "pending_bans"

    chat_key: Mapped[str] = mapped_column(String, primary_key=True)
    target_user_id: Mapped[str] = mapped_column(String)
    target_name: Mapped[str | None] = mapped_column(String, nullable=True)
    started_by: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
