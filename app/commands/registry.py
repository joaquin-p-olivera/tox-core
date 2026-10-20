"""Command registry.

Commands register themselves with the ``@command`` decorator. A handler receives a
:class:`CommandContext` and returns the text to send back: a string, a list of strings
(one message each), a :class:`Reply` (needed to mention users), a list mixing both, or ``None`` for no reply.
"""

from dataclasses import dataclass
from typing import Callable, Sequence

from sqlalchemy.orm import Session

from ..config import Settings
from ..schemas import IncomingMessage, Reply


@dataclass(frozen=True)
class CommandContext:
    message: IncomingMessage
    args: list[str]
    raw_args: str
    invoked_as: str  # the token the user typed, e.g. "chiste" for the "joke" command
    prefix: str  # the prefix the user typed, e.g. "!" or "/"
    db: Session
    settings: Settings
    # Nobody typed a command: the message is a reply to one of the bot's own messages, which !claude answers as if it
    # had been asked (see services/command_router.py). Only !claude ever sees it set.
    implicit: bool = False

    @property
    def is_listed_admin(self) -> bool:
        # Never from the owner's personal number: there the replies land in a chat with a third party.
        return not self.message.personal and self.message.user_key in self.settings.admin_user_keys

    @property
    def is_admin(self) -> bool:
        """Listed in ADMIN_USER_IDS and, in a group where the platform reports it (WhatsApp), also an administrator of
        THAT group: being on the list isn't enough to use admin commands in a group where you aren't an admin."""
        if not self.is_listed_admin:
            return False
        return not (self.message.is_group and self.message.sender_is_group_admin is False)


HandlerResult = str | Reply | list[str | Reply] | None
Handler = Callable[[CommandContext], HandlerResult]


@dataclass(frozen=True)
class Command:
    name: str
    handler: Handler
    description: str
    usage: str
    aliases: tuple[str, ...]
    category: str
    admin_only: bool = False
    hidden: bool = False  # not listed by !ayuda, not even for admins
    owner_only: bool = False  # the owner only: from their personal bot (message.personal) or as an admin; else 🚫, unlisted


_COMMANDS: dict[str, Command] = {}  # keyed by name and by every alias


def command(
    name: str,
    *,
    description: str,
    usage: str | None = None,
    aliases: Sequence[str] = (),
    category: str = "General",
    admin_only: bool = False,
    hidden: bool = False,
    owner_only: bool = False,
) -> Callable[[Handler], Handler]:
    def decorator(handler: Handler) -> Handler:
        cmd = Command(
            name=name,
            handler=handler,
            description=description,
            usage=usage or name,
            aliases=tuple(aliases),
            category=category,
            admin_only=admin_only,
            hidden=hidden,
            owner_only=owner_only,
        )
        for token in (name, *aliases):
            if token in _COMMANDS:
                raise ValueError(f"Duplicate command token: {token!r}")
            _COMMANDS[token] = cmd
        return handler

    return decorator


def get_command(token: str) -> Command | None:
    return _COMMANDS.get(token)


def all_commands() -> list[Command]:
    """Unique commands, in registration order."""
    return list(dict.fromkeys(_COMMANDS.values()))
