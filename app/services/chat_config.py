"""Settings one chat may be given its own value for (!config, commands/config.py).

Everything in config.Settings is global and read once when the API starts, which is the right thing for a port or a
token but not for how talkative Tox is: a quiet group and a group of friends that writes all day do not want the same
numbers. A row of `chat_settings` overrides one setting in one chat, and `value()` is what the code asks instead of
reading the attribute: this chat's row, or what the .env said.

OVERRIDABLE is a whitelist and has to stay one. An admin typing into a group reaches this, so only what changes how
the bot talks belongs here -- never a key, a path, a limit on money or who the admins are. Each value is read back
through the field's own rules in Settings (its type and its ge/le), so a chat cannot be given a number the .env itself
would have been refused for.
"""

from typing import Annotated, Any

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import ChatSetting
from ..utils import utcnow

# What !config is allowed to touch: how often Tox butts in, how long it stays quiet afterwards, how much it is told
# about the people, whether it answers when replied to, how often it speaks its answers, and how much of the
# conversation it reads. Nothing else, and nothing that is not in Settings.
OVERRIDABLE: tuple[str, ...] = (
    "CLAUDE_INTERJECT_CHANCE",
    "CLAUDE_INTERJECT_COOLDOWN_MINUTES",
    "CLAUDE_PROFILE_PEOPLE_CHANCE",
    "CLAUDE_ON_REPLY",
    "CLAUDE_AUDIO_CHANCE",
    "CLAUDE_CONTEXT_MESSAGES",
    "CLAUDE_CONTEXT_HOURS",
)


def _adapter(setting: str) -> TypeAdapter:
    """The field of Settings as something that can validate one typed-in string: its type plus its own ge/le, so
    there is a single place where the rules of each setting live."""
    field = Settings.model_fields[setting]
    annotation = Annotated[tuple([field.annotation, *field.metadata])] if field.metadata else field.annotation
    return TypeAdapter(annotation)


def parse(setting: str, raw: str) -> Any:
    """`raw` as the setting's own type, or ValueError when it is not a value that setting accepts."""
    if setting not in OVERRIDABLE:
        raise ValueError(f"{setting} cannot be set per chat")
    try:
        return _adapter(setting).validate_python(raw.strip())
    except ValidationError as error:
        raise ValueError(error.errors()[0]["msg"]) from error


def overrides(db: Session, chat_key: str) -> dict[str, Any]:
    """What this chat overrides, parsed. A row whose setting left OVERRIDABLE, or whose value no longer passes (a
    range narrowed in a new version), is ignored rather than crashing a chat: the global value applies again."""
    found: dict[str, Any] = {}
    for row in db.scalars(select(ChatSetting).where(ChatSetting.chat_key == chat_key)):
        try:
            found[row.key] = parse(row.key, row.value)
        except ValueError:
            continue
    return found


def value(db: Session, settings: Settings, chat_key: str, setting: str) -> Any:
    """What `setting` is worth in this chat: its row if there is one, else what the .env says.

    The chat is keyed the way message.chat_key is written, like the interjection cooldown and the question counter:
    a group is one id and never comes in two forms, so there is nothing to resolve the way !h has to for the private
    chats WhatsApp names twice."""
    row = db.get(ChatSetting, (chat_key, setting))
    if row is not None:
        try:
            return parse(setting, row.value)
        except ValueError:
            pass
    return getattr(settings, setting)


def set_value(db: Session, chat_key: str, setting: str, raw: str, by: str) -> Any:
    """Gives `setting` its own value in this chat and returns it parsed. ValueError when the value is refused."""
    parsed = parse(setting, raw)
    row = db.get(ChatSetting, (chat_key, setting))
    if row is None:
        db.add(ChatSetting(chat_key=chat_key, key=setting, value=raw.strip(), set_by=by))
    else:
        row.value, row.set_by, row.set_at = raw.strip(), by, utcnow()
    db.commit()
    return parsed


def clear(db: Session, chat_key: str, setting: str) -> bool:
    """Drops this chat's own value, back to the global one. False when there was nothing to drop."""
    row = db.get(ChatSetting, (chat_key, setting))
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True
