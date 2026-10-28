"""`!novedades`: what is new in Tox, for somebody who wants to read it instead of waiting to be told."""

from ..services import news
from .registry import CommandContext, command

CATEGORY = "General"


@command(
    "novedades",
    description="Muestra lo último que se le agregó a Tox",
    usage="novedades   (-probar: lo que está por anunciarse, solo admins)",
    aliases=("cambios",),
    category=CATEGORY,
)
def whats_new(ctx: CommandContext) -> str:
    if ctx.raw_args.strip().lstrip("-").lower() in ("probar", "pendiente", "pendientes"):
        if not ctx.is_admin:
            return "Eso es solo para los admins."
        waiting, chats = news.would_send(ctx.db, ctx.settings)
        if not chats:
            return "No hay ningún chat configurado para recibir novedades."
        if not waiting:
            return f"No hay novedades pendientes. Los {chats} chats configurados ya están al día."
        texts = "\n\n———\n\n".join(news.message(entry) for entry in waiting)
        return f"Esto saldría a {chats} chat(s):\n\n{texts}"

    entry = news.latest()
    if entry is None:
        return "Todavía no hay novedades para contar."
    return news.message(entry)
