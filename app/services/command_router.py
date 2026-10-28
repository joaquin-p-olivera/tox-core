import logging
import re

from sqlalchemy.orm import Session

from ..commands import CommandContext, get_command
from ..commands.claude import interject
from ..config import Settings
from ..schemas import IncomingMessage, Reply
from . import chat_config, chat_members, discretion, trivia_service

logger = logging.getLogger("tox.router")

_COMMAND_TOKEN = re.compile(r"^[a-z0-9_]+(-[a-z0-9_]+)*$")  # "ruleta-ban": hyphens only between words
REFUSED_REACTION = "🚫"  # what the bot reacts with to an admin-only command used by someone who isn't an admin


# The prefix of the bot's personal mode (run on the owner's own number): ".b sticker hola". It replaces "!" and "/"
# there, so the owner's normal chats can't trigger anything by accident.
PERSONAL_PREFIX = ".b "

# What a reply to one of the bot's own messages is answered by (see `replying_to_the_bot`)
REPLY_COMMAND = "claude"


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


def replying_to_the_bot(message: IncomingMessage, db: Session, settings: Settings) -> bool:
    """Whether this message is somebody replying to one of the bot's own messages, which is the other way of talking to
    Claude: no command typed, the reply itself is the question (CLAUDE_ON_REPLY, which a chat may turn off for itself
    with !config). Never in the personal mode, where the bot writes as the owner, so a reply to it is the owner's own
    chat and none of the bot's business."""
    return (not message.personal and bool(message.text.strip())
            and message.quoted is not None and message.quoted.is_bot
            and chat_config.value(db, settings, message.chat_key, "CLAUDE_ON_REPLY"))


def _trivia_answer(message: IncomingMessage, db: Session, settings: Settings) -> list[Reply] | None:
    """A message that is just a letter answers the trivia round this chat has open -- typed on its own or as a reply
    to the bot's question -- so nobody has to remember the prefix mid-game. None when it isn't one, and the message
    goes on its way: outside a round a letter is just a letter, and in the personal mode the bot writes as the owner,
    where a stray "b" must never become a move in a game."""
    if message.personal:
        return None
    letter = trivia_service.answer_letter(message.text)
    if letter is None or not trivia_service.round_is_open(db, message.chat_key):
        return None
    chat_members.record_user(db, message)
    prefix = settings.command_prefixes_list[0]
    return [Reply(text=text) for text in trivia_service.submit_answer(db, message, letter, settings, prefix)]


def _butt_in(message: IncomingMessage, db: Session, settings: Settings) -> list[Reply]:
    """Nobody is talking to the bot: now and then it says something of its own anyway (commands/claude.py's
    `interject`, CLAUDE_INTERJECT_CHANCE), and the rest of the time this is where an ordinary message stops."""
    if message.personal or not message.is_group:
        return []
    if chat_config.value(db, settings, message.chat_key, "CLAUDE_INTERJECT_CHANCE") <= 0:
        return []
    ctx = CommandContext(message=message, args=[], raw_args="", invoked_as=REPLY_COMMAND,
                         prefix=settings.command_prefixes_list[0], db=db, settings=settings, implicit=True)
    try:
        remark = interject(ctx)
    except Exception:
        logger.exception("Could not butt into a conversation")
        db.rollback()
        return []
    return [remark] if remark is not None else []


def _talked_to_the_bot(message: IncomingMessage, db: Session, settings: Settings,
                       parsed: tuple[str, str, str] | None) -> bool:
    """Whether this message is aimed at the bot: a command, a reply to one of its messages, or its name in the
    text. It is what ends a run of remarks nobody picked up (services/discretion.py)."""
    if message.personal or not message.is_group:
        return False
    return (parsed is not None
            or (message.quoted is not None and message.quoted.is_bot)
            or discretion.names_the_bot(message.text))


def handle_message(message: IncomingMessage, db: Session, settings: Settings) -> list[Reply]:
    prefixes = [PERSONAL_PREFIX] if message.personal else settings.command_prefixes_list
    parsed = parse_command(message.text, prefixes)
    if _talked_to_the_bot(message, db, settings, parsed):
        discretion.was_answered(message.chat_key)
    if parsed is None:
        # Before anything else reads it: a bare "b" is an answer, not a question for Claude.
        answered = _trivia_answer(message, db, settings)
        if answered is not None:
            return answered
    implicit = parsed is None and replying_to_the_bot(message, db, settings)
    if parsed is None and not implicit:
        return _butt_in(message, db, settings)
    # A reply to the bot: the whole message is the question, and the prefix is only there for the usage messages.
    prefix, token, raw_args = parsed or (prefixes[0], REPLY_COMMAND, message.text.strip())

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
        implicit=implicit,
    )
    if (cmd.admin_only and not ctx.is_admin) or (cmd.owner_only and not (message.personal or ctx.is_admin)):
        return [Reply(reaction=REFUSED_REACTION)]  # no text: the bot just reacts to the command

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
