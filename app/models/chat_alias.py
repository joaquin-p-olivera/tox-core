from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..utils import utcnow


class ChatAlias(Base):
    """A short name for a chat, so one can be named from another one without typing a WhatsApp id (!spy).

    It is only a name: giving a chat an alias grants nothing. What one chat may read of another lives in
    chat_context_sources, and nothing here changes it.
    """

    __tablename__ = "chat_aliases"

    alias: Mapped[str] = mapped_column(String, primary_key=True)  # lowercase, no spaces: "festejen", "librecourse"
    chat_key: Mapped[str] = mapped_column(String, unique=True)  # one name per chat, so a listing is unambiguous
    set_by: Mapped[str] = mapped_column(String)
    set_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
