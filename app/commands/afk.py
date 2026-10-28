"""`!afk [motivo]`: tell the chats you are away. See services/afk.py for how it ends on its own."""

from ..services import afk
from .registry import CommandContext, command

CATEGORY = "General"


@command(
    "afk",
    description="Avisa que estás ausente: el bot contesta por vos si te buscan",
    usage="afk [motivo]",
    aliases=("ausente",),
    category=CATEGORY,
)
def away(ctx: CommandContext) -> str:
    row = afk.mark(ctx.db, ctx.message, ctx.raw_args)
    reason = f" ({row.reason})" if row.reason else ""
    return f"🌙 Listo, {row.user_name}: quedás afk{reason}. Vuelvo a contar cuando escribas."
