from ..schemas import Reply
from ..services import trivia_service
from .claude import ask_claude
from .registry import CommandContext, command

CATEGORY = "Juegos"


@command(
    "trivia",
    description="Lanza una pregunta de trivia para todo el grupo: gana el primero que acierte",
    usage="trivia [categoría]",
    aliases=("preguntas",),
    category=CATEGORY,
)
def trivia(ctx: CommandContext) -> list[str]:
    category = ctx.args[0] if ctx.args else None
    return trivia_service.start_round(ctx.db, ctx.message, ctx.settings, ctx.prefix, category)


# "a".."d" are shortcuts: "!b" is the same as "!responder b". A plain "b" with no prefix at all works too,
# while a round is open: services/command_router.py routes it here.
@command(
    "responder",
    description="Responde la pregunta de trivia abierta",
    usage="responder <A-D>   (o directamente a, b, c, d)",
    aliases=("a", "b", "c", "d"),
    category=CATEGORY,
)
def answer(ctx: CommandContext) -> list[str] | str | Reply:
    if ctx.invoked_as == "c" and ctx.args:
        # "!c" alone is the answer C; "!c <question>" is the short way of asking Claude (!claude)
        return ask_claude(ctx)
    if ctx.invoked_as in ("a", "b", "c", "d") and not ctx.args:
        letter = ctx.invoked_as
    elif ctx.args:
        letter = ctx.args[0].strip(").:").lower()
    else:
        return [f"Uso: {ctx.prefix}responder <A-D>"]

    if len(letter) != 1:
        return [f"Uso: {ctx.prefix}responder <A-D>"]
    return trivia_service.submit_answer(ctx.db, ctx.message, letter, ctx.settings, ctx.prefix)


@command(
    "ranking",
    description="Muestra el ranking de trivia de este chat",
    aliases=("top", "puntajes"),
    category=CATEGORY,
)
def ranking(ctx: CommandContext) -> str:
    rows = trivia_service.leaderboard(ctx.db, ctx.message.chat_key)
    if not rows:
        return f"Todavía no hay puntajes. Jugá una ronda con {ctx.prefix}trivia"
    medals = ["🥇", "🥈", "🥉"]
    lines = [
        f"{medals[i] if i < len(medals) else f'{i + 1}.'} {row.user_name} — {row.points} pts "
        f"({row.wins} {'victoria' if row.wins == 1 else 'victorias'})"
        for i, row in enumerate(rows)
    ]
    return "🏆 Ranking\n" + "\n".join(lines)
