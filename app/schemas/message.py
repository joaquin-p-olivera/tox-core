from typing import Literal

from pydantic import BaseModel, Field


class Participant(BaseModel):
    """A member of the chat, as known by the bot."""

    user_id: str = Field(min_length=1)
    user_name: str | None = None
    is_bot: bool = Field(
        default=False, description="True when this user is the bot itself (e.g. someone tagged the bot)."
    )
    is_group_admin: bool | None = Field(
        default=None,
        description="Whether this member administers the group, as the bot read it from the roster (WhatsApp). "
        "Null = unknown: moderation commands then refuse to act on them.",
    )
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

    sender_is_group_admin: bool | None = Field(
        default=None,
        description="In a group, whether the sender administers THAT group (WhatsApp reports it). Admin-only commands "
        "need it to be true on top of the sender being in ADMIN_USER_IDS. Null = the platform doesn't report it.",
    )
    mentioned: list[Participant] | None = Field(
        default=None,
        max_length=64,
        description="Users the sender tagged with @ in this message, as the platform reports them (e.g. for !gay).",
    )

    image: str | None = Field(
        default=None,
        max_length=8_000_000,
        description="Base64 of the picture the message carries or replies to, when there is one (WhatsApp). "
        "Only commands that work on a picture (!sticker) read it.",
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
    reaction: str | None = Field(
        default=None,
        description="An emoji the bot reacts with to the message that triggered the command (no text is sent).",
    )
    image: str | None = Field(
        default=None,
        description="Id of a generated image (e.g. !futbol -t's table) to send instead of text; the bot "
        "downloads it from GET /api/v1/images/{id}. Short-lived: fetch it right away.",
    )
    job: str | None = Field(
        default=None,
        description="Id of a long-running job (e.g. !claude). The bot sends `text` now, then polls "
        "GET /api/v1/jobs/{id} until it is done and sends the replies it returns.",
    )
    remove: list[str] = Field(
        default_factory=list,
        description="User ids (as in `mentioned`/`participants`) the bot must remove from the chat where the command "
        "ran. It needs to be a group admin there; if it can't, it says so instead of sending `text`.",
    )
    sweep_blacklist: bool = Field(
        default=False,
        description="Asks the bot to remove everyone on GET /api/v1/blacklist from every group it is in (sent by !permaban).",
    )


class MessageResponse(BaseModel):
    """What the bot must send back. Empty when the message isn't a command."""

    replies: list[Reply] = Field(default_factory=list)


class JobOut(BaseModel):
    """State of a long-running command: still running, or finished with the replies to send."""

    status: Literal["running", "done"]
    replies: list[Reply] = Field(default_factory=list)
