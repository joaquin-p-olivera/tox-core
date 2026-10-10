from datetime import datetime

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..utils import utcnow


class ClaudeQuery(Base):
    """One question sent to Claude with !claude. Only used to enforce the per-hour limit: the question itself
    is deliberately not stored."""

    __tablename__ = "claude_queries"
    __table_args__ = (Index("ix_claude_queries_created_at", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_key: Mapped[str] = mapped_column(String)
    chat_key: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
