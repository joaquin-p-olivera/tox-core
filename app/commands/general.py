from ..services import chat_members
from .registry import CommandContext, all_commands, command, get_command


@command(
    "ayuda",
    description="Lista los comandos, o explica uno",
    usage="ayuda [comando]",
    aliases=("help", "comandos"),
)
def help_command(ctx: CommandContext) -> str:
    p = ctx.prefix

    def listed(cmd) -> bool:
        """Don't advertise what the caller can't use."""
        return not (cmd.hidden or (cmd.admin_only and not ctx.is_admin)
                    or (cmd.owner_only and not (ctx.message.personal or ctx.is_admin)))

    if ctx.args:
        cmd = get_command(ctx.args[0].lower().lstrip("!/"))
        if cmd is None or not listed(cmd):
            return f"No conozco ese comando. Probá {p}ayuda"
        text = f"{p}{cmd.usage}\n{cmd.description}"
        if cmd.aliases:
            text += f"\nTambién: {', '.join(p + a for a in cmd.aliases)}"
        return text

    by_category: dict[str, list[str]] = {}
    for cmd in all_commands():
        if not listed(cmd):
            continue
        by_category.setdefault(cmd.category, []).append(f"{p}{cmd.name} — {cmd.description}")
    sections = [f"{category}\n" + "\n".join(lines) for category, lines in by_category.items()]
    return "Comandos disponibles\n\n" + "\n\n".join(sections) + f"\n\nUsá {p}ayuda <comando> para más detalles."


@command("id", description="Muestra los IDs de chat y de usuario que ve el bot", category="General")
def whoami(ctx: CommandContext) -> str:
    m = ctx.message
    if ctx.is_admin:
        role = "admin"
    elif ctx.is_listed_admin:  # on the list, but not an administrator of this group
        role = "usuario (estás en la lista de admins, pero no sos admin de este grupo)"
    else:
        role = "usuario"
    return (
        f"Plataforma: {m.platform}\n"
        f"ID del chat: {m.chat_id} ({'grupo' if m.is_group else 'privado'})\n"
        f"ID de usuario: {m.user_id}\n"
        f"Nombre: {m.user_name or '-'}\n"
        f"Rol: {role}"
    )


@command(
    "mute",
    description="Dejás de ser etiquetado en este chat. Usalo de nuevo para volver a aparecer",
    category="General",
)
def mute(ctx: CommandContext) -> str:
    if not ctx.message.is_group:
        return "Este comando es para grupos."
    name = ctx.message.display_name
    if chat_members.toggle_mute(ctx.db, ctx.message):
        return f"🔇 Listo, {name}: ya no te voy a etiquetar en este chat. Mandá {ctx.prefix}mute de nuevo para volver."
    return f"🔔 {name}, volvés a estar en las etiquetas."
