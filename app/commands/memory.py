"""`!memory`: what Tox picked up about you in this chat, and `!memory forget` to make it forget."""

from ..services import chat_config, person_memory
from .registry import CommandContext, command

CATEGORY = "General"
# The word is English like the command, and a couple of ways of saying it are taken all the same.
FORGET = ("forget", "forgetme", "forget-me")


@command(
    "memory",
    description="Qué cosas tuyas se acuerda Tox en este chat",
    usage="memory   (forget: borra todo lo tuyo)",
    category=CATEGORY,
)
def memory(ctx: CommandContext) -> str:
    message = ctx.message
    asked = ctx.raw_args.strip().lstrip("-").lower()
    if asked in FORGET:
        gone = person_memory.forget(ctx.db, message.chat_key, message.user_key)
        if not gone:
            return "No me acuerdo de nada tuyo, así que no hay nada que olvidar."
        return f"🧽 Listo, me olvidé de {gone} cosa{'s' if gone > 1 else ''} tuya{'s' if gone > 1 else ''}."

    if not chat_config.value(ctx.db, ctx.settings, message.chat_key, "CLAUDE_MEMORY"):
        return "En este chat tengo la memoria apagada, así que no guardo nada de nadie."
    notes = person_memory.of_person(ctx.db, message.chat_key, message.user_key)
    if not notes:
        return "Todavía no me acuerdo de nada tuyo en este chat."
    lines = "\n".join(f"· {note.text}" for note in notes)
    return (f"🧠 Lo que sé de {message.display_name} en este chat:\n{lines}\n\n"
            f"Si querés que me olvide: {ctx.prefix}memory forget")
