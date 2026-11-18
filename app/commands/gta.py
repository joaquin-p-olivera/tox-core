from ..schemas import Reply
from ..services import gta_countdown
from .flags import normalize_flag
from .registry import CommandContext, command

ECUADOR_FLAGS = {"-e", "--ecuador"}


@command(
    "vi",
    description="Cuánto falta para Grand Theft Auto VI; con -e, en hora de Ecuador",
    usage="vi [-e]",
    aliases=("gta6",),
    category="Diversión",
)
def gta_six(ctx: CommandContext) -> Reply:
    """By hand, the same card the countdown sends to the alert groups on its own (services/gta_countdown.py).
    It answers in the chat it was asked in, which is what makes it work in the personal mode too, where the bot
    never polls for anything and no alert ever lands. The text is the answer when the card cannot be drawn.

    `-e` counts to midnight in Ecuador instead, two hours later, and the card says so in its corner. Anything else
    after the command is ignored, as it always was."""
    if any(normalize_flag(arg) in ECUADOR_FLAGS for arg in ctx.args):
        release, badge = gta_countdown.ECUADOR_RELEASE, gta_countdown.ECUADOR_BADGE
        said = f"{gta_countdown.message(release=release)} (hora de Ecuador)"
    else:
        release, badge, said = gta_countdown.RELEASE, None, gta_countdown.message()
    image = gta_countdown.card(ctx.settings, release=release, badge=badge)
    # The card says it in numbers; the same words are what a reply to it gets to read
    return Reply(image=image, about=said) if image else Reply(text=said)
