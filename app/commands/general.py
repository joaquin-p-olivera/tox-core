from .registry import CommandContext, all_commands, command, get_command


@command("ping", description="Comprueba que el bot está vivo", aliases=("p",))
def ping(ctx: CommandContext) -> str:
    return "pong"


@command(
    "ayuda",
    description="Lista los comandos, o explica uno",
    usage="ayuda [comando]",
    aliases=("help", "comandos"),
)
def help_command(ctx: CommandContext) -> str:
    p = ctx.prefix
    if ctx.args:
        cmd = get_command(ctx.args[0].lower().lstrip("!/"))
        if cmd is None:
            return f"No conozco ese comando. Probá {p}ayuda"
        text = f"{p}{cmd.usage}\n{cmd.description}"
        if cmd.aliases:
            text += f"\nTambién: {', '.join(p + a for a in cmd.aliases)}"
        return text

    by_category: dict[str, list[str]] = {}
    for cmd in all_commands():
        by_category.setdefault(cmd.category, []).append(f"{p}{cmd.name} — {cmd.description}")
    sections = [f"{category}\n" + "\n".join(lines) for category, lines in by_category.items()]
    return "Comandos disponibles\n\n" + "\n\n".join(sections) + f"\n\nUsá {p}ayuda <comando> para más detalles."


@command("id", description="Muestra los IDs de chat y de usuario que ve el bot", category="General")
def whoami(ctx: CommandContext) -> str:
    m = ctx.message
    return (
        f"Plataforma: {m.platform}\n"
        f"ID del chat: {m.chat_id} ({'grupo' if m.is_group else 'privado'})\n"
        f"ID de usuario: {m.user_id}\n"
        f"Nombre: {m.user_name or '-'}"
    )
