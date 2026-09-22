from datetime import datetime

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..utils import utcnow


class PendingAlert(Base):
    """One proactive alert queued for a bot to deliver. A bot's poll fetches and deletes its own
    platform's rows in one transaction, so delivery is at-most-once (a missed poll just drops it,
    which is fine for this: the next health check will say so again if the problem is still there)."""

    __tablename__ = "pending_alerts"
    __table_args__ = (Index("ix_pending_alerts_platform", "platform"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String)  # "whatsapp" / "telegram"
    chat_id: Mapped[str] = mapped_column(String)
    text: Mapped[str] = mapped_column(String)
    # Comma-separated platform user ids to tag (referenced from `text` as {@0}, {@1}... same convention as Reply.mentions)
    mentions: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
