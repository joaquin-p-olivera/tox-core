"""`!memory`: what Tox picked up about somebody in this chat, and `!memory forget` to make it forget.

Anybody may read anybody's: these are notes about the people of a chat, taken from what was said in front of all of
them, so they are the chat's to read. `!memory` with no name is your own, and a name or a mention is theirs --
read generously ("Lean" is "Leandro 1899"), because somebody asking about a person in the room is not a fact being
filed and the worst a loose match costs is the wrong answer to a question.

`forget` is for the admins for now, and only ever your own notes. Anybody reading them is the point of the command,
but wiping them is held back while the feature is new: what it learns is still being watched, and a note that
disappears cannot be looked at to see why it was kept. It is a restriction on a promise, not a change of mind about
it -- see the module docstring of services/person_memory.py.
"""

from ..services import chat_config, chat_members, person_memory, user_directory
from .registry import CommandContext, command

CATEGORY = "General"
# The word is English like the command, and a couple of ways of saying it are taken all the same.
FORGET = ("forget", "forgetme", "forget-me")


def _people(ctx: CommandContext) -> dict[str, str]:
    """Everybody in this chat by name, the curated one where there is one (USERS_FILE), as `{name: user_key}`."""
    users = user_directory.load_users(ctx.settings.USERS_FILE)
    platform = ctx.message.platform
    people = {}
    for member in chat_members.known_members(ctx.db, ctx.message):
        name = user_directory.name_of(users, platform, member.user_id, *member.aliases) or member.user_name
        if name:
            people[name] = f"{platform}:{member.user_id}"
    return people


def _whose(ctx: CommandContext, asked: str) -> tuple[str, str, str | None] | str:
    """(user_key, their name, how to address them) for the notes being asked for, or the answer to send instead."""
    message = ctx.message
    tagged = [person for person in (message.mentioned or []) if not person.is_bot]
    if tagged:
        users = user_directory.load_users(ctx.settings.USERS_FILE)
        target = tagged[0]
        name = (user_directory.name_of(users, message.platform, target.user_id, *target.aliases)
                or target.user_name or "esa persona")
        return f"{message.platform}:{target.user_id}", name, None
    if not asked:
        return message.user_key, message.display_name, "vos"
    people = _people(ctx)
    user_key, could_be = person_memory.whos_meant(asked, people)
    if user_key is None:
        if could_be:
            return f"¿Cuál de todos? Puede ser {', '.join(could_be)}."
        return f"No conozco a nadie así en este chat. Probá con {ctx.prefix}memory y el nombre de alguien de acá."
    name = next((n for n, key in people.items() if key == user_key), asked)
    if user_key == message.user_key:
        return user_key, name, "vos"
    return user_key, name, None


@command(
    "memory",
    description="Qué cosas se acuerda Tox de alguien en este chat",
    usage="memory [nombre|@usuario]   (forget: borra todo lo tuyo, solo admins)",
    category=CATEGORY,
)
def memory(ctx: CommandContext) -> str:
    message = ctx.message
    asked = ctx.raw_args.strip()
    if asked.lstrip("-").lower() in FORGET:
        if not ctx.is_admin:
            return ("Por ahora borrar la memoria es solo para los admins, mientras la feature es nueva. "
                    "Con *!memory* ves todo lo que sé de vos, y si querés que me olvide de algo, pedíselo a un admin.")
        # Everywhere, not just here: asking to be forgotten means forgotten.
        gone = person_memory.forget(ctx.db, message.user_key)
        if not gone:
            return "No me acuerdo de nada tuyo, así que no hay nada que olvidar."
        return (f"🧽 Listo, me olvidé de {gone} cosa{'s' if gone > 1 else ''} tuya{'s' if gone > 1 else ''}, "
                "en todos los chats.")

    if not chat_config.value(ctx.db, ctx.settings, message.chat_key, "CLAUDE_MEMORY"):
        return "En este chat tengo la memoria apagada, así que no guardo nada de nadie."
    whose = _whose(ctx, asked)
    if isinstance(whose, str):
        return whose
    user_key, name, addressed_as = whose
    mine = addressed_as == "vos"
    everywhere, here = person_memory.of_person(ctx.db, message.chat_key, user_key)
    if not everywhere and not here:
        return "Todavía no me acuerdo de nada tuyo." if mine else f"Todavía no me acuerdo de nada de {name}."
    blocks = [f"🧠 Lo que sé de {message.display_name if mine else name}:"]
    whom = "vos" if mine else name
    if everywhere:
        blocks.append(f"*De {whom}, en cualquier chat:*\n" + "\n".join(f"· {note.text}" for note in everywhere))
    if here:
        blocks.append("*Solo en este chat:*\n" + "\n".join(f"· {note.text}" for note in here))
    if mine and ctx.is_admin:  # not offered to somebody who would be turned down
        blocks.append(f"Si querés que me olvide de todo: {ctx.prefix}memory forget")
    return "\n\n".join(blocks)
