import random
import re

from ..schemas import Mention, Reply
from ..services import audio_library, audio_picker, chat_members
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
    description="Pregunta por la mamá de alguien del grupo (o manda un audio), al azar",
    category=CATEGORY,
)
def mother(ctx: CommandContext) -> str | Reply:
    if not ctx.message.is_group:
        return "Este comando es para grupos."

    audios = audio_library.list_audios(ctx.settings.AUDIOS_DIR)
    target = chat_members.pick_random_other(ctx.db, ctx.message)

    # Audio when the dice say so, or when there's nobody to tag but we still have something to send.
    if audios and (target is None or random.random() < ctx.settings.M_AUDIO_PROBABILITY):
        return Reply(audio=audio_picker.pick_audio(ctx.db, ctx.message.chat_key, audios).name)
    if target is None:
        return "No tengo a nadie para etiquetar todavía (o están todos en mute)."
    return Reply(
        text="y tu mamá donde está? {@0}",
        mentions=[Mention(user_id=target.user_id, user_name=target.user_name)],
    )
