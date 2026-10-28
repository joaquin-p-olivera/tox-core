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
        # Everywhere, not just here: asking to be forgotten means forgotten.
        gone = person_memory.forget(ctx.db, message.user_key)
        if not gone:
            return "No me acuerdo de nada tuyo, así que no hay nada que olvidar."
        return (f"🧽 Listo, me olvidé de {gone} cosa{'s' if gone > 1 else ''} tuya{'s' if gone > 1 else ''}, "
                "en todos los chats.")

    if not chat_config.value(ctx.db, ctx.settings, message.chat_key, "CLAUDE_MEMORY"):
        return "En este chat tengo la memoria apagada, así que no guardo nada de nadie."
    everywhere, here = person_memory.of_person(ctx.db, message.chat_key, message.user_key)
    if not everywhere and not here:
        return "Todavía no me acuerdo de nada tuyo."
    blocks = [f"🧠 Lo que sé de {message.display_name}:"]
    if everywhere:
        blocks.append("*De vos, en cualquier chat:*\n" + "\n".join(f"· {note.text}" for note in everywhere))
    if here:
        blocks.append("*Solo en este chat:*\n" + "\n".join(f"· {note.text}" for note in here))
    blocks.append(f"Si querés que me olvide de todo: {ctx.prefix}memory forget")
    return "\n\n".join(blocks)
