import logging
import re

from sqlalchemy.orm import Session

from ..commands import CommandContext, get_command
from ..config import Settings
from ..schemas import IncomingMessage, Reply
from . import chat_members

logger = logging.getLogger("tox.router")

_COMMAND_TOKEN = re.compile(r"^[a-z0-9_]+$")


def parse_command(text: str, prefixes: list[str]) -> tuple[str, str, str] | None:
    """Split ``"!roll 2d6"`` into ``("!", "roll", "2d6")``. Returns None if it isn't a command."""
    text = text.strip()
    # Longest prefix first so a "!!" prefix would win over "!".
    for prefix in sorted(prefixes, key=len, reverse=True):
        if not text.startswith(prefix):
            continue
        rest = text[len(prefix):]
        # The command name must touch the prefix: "! ping" is just chat.
        if not rest or rest[0].isspace():
            return None
        parts = rest.split(maxsplit=1)
        # Telegram appends the bot username in groups: "/chiste@tox_bot".
        token = parts[0].split("@", 1)[0].lower()
        if not _COMMAND_TOKEN.match(token):
            return None
        return prefix, token, parts[1] if len(parts) > 1 else ""
    return None


def handle_message(message: IncomingMessage, db: Session, settings: Settings) -> list[Reply]:
    parsed = parse_command(message.text, settings.command_prefixes_list)
    if parsed is None:
        return []
    prefix, token, raw_args = parsed

    cmd = get_command(token)
    if cmd is None:
        # Stay silent on unknown commands: other bots may share the chat and the prefix.
        return []

    chat_members.record_user(db, message)

    ctx = CommandContext(
        message=message,
        args=raw_args.split(),
        raw_args=raw_args,
        invoked_as=token,
        prefix=prefix,
        db=db,
        settings=settings,
    )
    if cmd.admin_only and not ctx.is_admin:
        return [Reply(text="Este comando es solo para administradores.")]

    try:
        result = cmd.handler(ctx)
    except Exception:
        logger.exception("Command %r failed", cmd.name)
        db.rollback()
        return [Reply(text="Algo salió mal al ejecutar ese comando.")]

    if result is None:
        return []
    items = result if isinstance(result, list) else [result]
    return [Reply(text=item) if isinstance(item, str) else item for item in items]
