from ..services import gta_countdown
from .registry import CommandContext, command


@command(
    "vi",
    description="Cuánto falta para Grand Theft Auto VI",
    aliases=("gta6",),
    category="Diversión",
)
def gta_six(ctx: CommandContext) -> str:
    """By hand, the same message the countdown sends to the alert groups on its own (services/gta_countdown.py).
    It answers in the chat it was asked in, which is what makes it work in the personal mode too, where the bot
    never polls for anything and no alert ever lands."""
    return gta_countdown.message()
