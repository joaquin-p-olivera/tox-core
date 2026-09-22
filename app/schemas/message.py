from typing import Literal

from pydantic import BaseModel, Field


class Participant(BaseModel):
    """A member of the chat, as known by the bot."""

    user_id: str = Field(min_length=1)
    user_name: str | None = None
    aliases: list[str] = Field(
        default_factory=list,
        max_length=8,
        description="Other IDs of the same person (WhatsApp: LID and phone number). Used to match mutes.",
    )


class IncomingMessage(BaseModel):
    """A chat message as forwarded by a bot, normalised across platforms."""

    platform: Literal["whatsapp", "telegram"]
    chat_id: str = Field(min_length=1, description="Platform-specific chat/group identifier")
    user_id: str = Field(min_length=1, description="Platform-specific sender identifier")
    user_name: str | None = Field(default=None, description="Sender display name, if known")
    text: str = Field(max_length=4096)
    is_group: bool = True
    participants: list[Participant] | None = Field(
        default=None,
        max_length=2048,
        description="Full member list when the platform can provide it (WhatsApp). "
        "user_id must use the same form as the sender's.",
    )

    @property
    def chat_key(self) -> str:
        return f"{self.platform}:{self.chat_id}"

    @property
    def user_key(self) -> str:
        return f"{self.platform}:{self.user_id}"

    @property
    def display_name(self) -> str:
        return self.user_name or "Jugador"


class Mention(BaseModel):
    user_id: str
    user_name: str | None = None


class Reply(BaseModel):
    """One chat message to send. ``{@0}``, ``{@1}``... in ``text`` are replaced by the bot with
    a real mention of ``mentions[0]``, ``mentions[1]``..., rendered in the platform's own way."""

    text: str = ""
    mentions: list[Mention] = Field(default_factory=list)
    audio: str | None = Field(
        default=None,
        description="Name of an audio to send instead of text; the bot downloads it from GET /api/v1/audios/{name}.",
    )
    sticker: str | None = Field(
        default=None,
        description="Id of a generated sticker to send instead of text; the bot downloads it from "
        "GET /api/v1/stickers/{id}. Short-lived: fetch it right away.",
    )


class MessageResponse(BaseModel):
    """What the bot must send back. Empty when the message isn't a command."""

    replies: list[Reply] = Field(default_factory=list)
