from ..schemas import Reply
from ..services import gta_countdown
from .registry import CommandContext, command


@command(
    "vi",
    description="Cuánto falta para Grand Theft Auto VI",
    aliases=("gta6",),
    category="Diversión",
)
def gta_six(ctx: CommandContext) -> Reply:
    """By hand, the same card the countdown sends to the alert groups on its own (services/gta_countdown.py).
    It answers in the chat it was asked in, which is what makes it work in the personal mode too, where the bot
    never polls for anything and no alert ever lands. The text is the answer when the card cannot be drawn."""
    image = gta_countdown.card(ctx.settings)
    return Reply(image=image) if image else Reply(text=gta_countdown.message())
