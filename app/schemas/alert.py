from pydantic import BaseModel, Field

from .message import Mention


class PendingAlertOut(BaseModel):
    """One proactive message a bot must send on its own, with no prior message from a user.
    ``{@0}``, ``{@1}``... in ``text`` stand for ``mentions[0]``, ``mentions[1]``..., same as Reply."""

    chat_id: str
    text: str
    mentions: list[Mention] = Field(default_factory=list)
