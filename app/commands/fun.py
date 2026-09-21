import random
import re

from ..schemas import Mention, Reply
from ..services import chat_members
from ..services.content import load_json
from .registry import CommandContext, command

_DICE = re.compile(r"^(\d{1,2})?d(\d{1,4})$")
MAX_DICE = 20
MAX_SIDES = 1000
CATEGORY = "Diversión"


@command("chiste", description="Cuenta un chiste al azar", category=CATEGORY)
def joke(ctx: CommandContext) -> str:
    return random.choice(load_json("jokes.json"))


@command(
    "dado",
    description="Tira dados (por defecto 1d6)",
    usage="dado [NdM | M]",
    aliases=("dados",),
    category=CATEGORY,
)
def roll(ctx: CommandContext) -> str:
    spec = (ctx.args[0] if ctx.args else "1d6").lower()
    if spec.isdigit():  # "!dado 20" means one twenty-sided die
        spec = f"1d{spec}"
    match = _DICE.match(spec)
    if not match:
        return f"Uso: {ctx.prefix}dado [NdM] — por ejemplo {ctx.prefix}dado 2d6 o {ctx.prefix}dado d20"

    count, sides = int(match.group(1) or 1), int(match.group(2))
    if not 1 <= count <= MAX_DICE or not 2 <= sides <= MAX_SIDES:
        return f"Usá entre 1 y {MAX_DICE} dados de entre 2 y {MAX_SIDES} caras."

    rolls = [random.randint(1, sides) for _ in range(count)]
    if count == 1:
        return f"🎲 {rolls[0]} (d{sides})"
    return f"🎲 {' + '.join(map(str, rolls))} = {sum(rolls)} ({count}d{sides})"


@command("moneda", description="Tira una moneda", aliases=("cara",), category=CATEGORY)
def flip(ctx: CommandContext) -> str:
    return f"🪙 {random.choice(['Cara', 'Cruz'])}"


@command(
    "elegir",
    description="Elige una opción al azar. Separalas con | o ,",
    usage="elegir <opción> | <opción> | ...",
    category=CATEGORY,
)
def choose(ctx: CommandContext) -> str:
    raw = ctx.raw_args
    separator = "|" if "|" in raw else "," if "," in raw else None
    options = [o.strip() for o in raw.split(separator)] if separator else raw.split()
    options = [o for o in options if o]
    if len(options) < 2:
        return f"Dame al menos dos opciones: {ctx.prefix}elegir pizza | sushi"
    return f"🤔 {random.choice(options)}"


@command(
    "m",
    description="Pregunta por la mamá de alguien del grupo, elegido al azar",
    category=CATEGORY,
)
def mother(ctx: CommandContext) -> str | Reply:
    if not ctx.message.is_group:
        return "Este comando es para grupos."
    target = chat_members.pick_random_other(ctx.db, ctx.message)
    if target is None:
        return "Todavía no conozco a nadie más en este chat para etiquetar."
    return Reply(
        text="y tu mamá donde está? {@0}",
        mentions=[Mention(user_id=target.user_id, user_name=target.user_name)],
    )
