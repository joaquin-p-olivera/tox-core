from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..utils import utcnow


class ChatContextSource(Base):
    """Another chat whose conversation one chat may be told about (services/chat_sources.py).

    One row is one direction and nothing else: a row saying A reads B does not let B read A, which is the whole point
    of keeping them as rows instead of a flag on a pair. What is read is only what the history of the source chat
    already saved (!h -on there), and only when the question names one of `triggers`.
    """

    __tablename__ = "chat_context_sources"

    chat_key: Mapped[str] = mapped_column(String, primary_key=True)  # the chat that reads
    source_chat_key: Mapped[str] = mapped_column(String, primary_key=True)  # the chat that is read
    # How the source is named in the prompt: the real name of a group is not something the other one needs to know.
    label: Mapped[str] = mapped_column(String)
    # Comma separated, matched against the question without accents or case. Empty = the label is the only trigger.
    triggers: Mapped[str] = mapped_column(String, default="")
    added_by: Mapped[str] = mapped_column(String)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
