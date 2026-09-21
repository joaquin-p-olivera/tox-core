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


_COMMANDS: dict[str, Command] = {}  # keyed by name and by every alias


def command(
    name: str,
    *,
    description: str,
    usage: str | None = None,
    aliases: Sequence[str] = (),
    category: str = "General",
) -> Callable[[Handler], Handler]:
    def decorator(handler: Handler) -> Handler:
        cmd = Command(
            name=name,
            handler=handler,
            description=description,
            usage=usage or name,
            aliases=tuple(aliases),
            category=category,
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
