from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..utils import utcnow


class HistoryChat(Base):
    """A chat whose messages are being saved (!h -on). A row means "on"; turning it off deletes it (what was
    already saved stays until !h -delete or the size limit)."""

    __tablename__ = "history_chats"

    chat_key: Mapped[str] = mapped_column(String, primary_key=True)  # "whatsapp:<chat id>", as the chat was first seen
    aliases: Mapped[str] = mapped_column(String, default="")  # other ids of the same chat, comma separated
    enabled_by: Mapped[str] = mapped_column(String)
    enabled_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
