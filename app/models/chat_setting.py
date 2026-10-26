from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..utils import utcnow


class ChatSetting(Base):
    """One setting given a different value in one chat (!config). A row exists only while that chat overrides it:
    -reset deletes it and what the .env says applies again, so there is no way to tell "never touched" from "set to
    the same value as the default" -- and nothing to migrate when a default changes."""

    __tablename__ = "chat_settings"

    chat_key: Mapped[str] = mapped_column(String, primary_key=True)  # "whatsapp:<chat id>", like message.chat_key
    key: Mapped[str] = mapped_column(String, primary_key=True)  # the name in Settings, e.g. "CLAUDE_INTERJECT_CHANCE"
    value: Mapped[str] = mapped_column(String)  # as it was typed; read back through the Settings field's own rules
    set_by: Mapped[str] = mapped_column(String)
    set_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
