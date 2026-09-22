import base64
import binascii
import uuid

from .registry import CommandContext, command

MAX_OUTPUT_CHARS = 1000
CATEGORY = "Herramientas"


@command("uuid", description="Genera un UUID aleatorio (v4)", category=CATEGORY)
def uuid_command(ctx: CommandContext) -> str:
    return str(uuid.uuid4())


@command(
    "base64",
    description="Codifica o decodifica en Base64",
    usage="base64 codificar|decodificar <texto>",
    aliases=("b64",),
    category=CATEGORY,
)
def base64_command(ctx: CommandContext) -> str:
    usage = f"Uso: {ctx.prefix}base64 codificar|decodificar <texto>"
    if len(ctx.args) < 2:
        return usage
    action = ctx.args[0].lower()
    payload = ctx.raw_args.split(maxsplit=1)[1]

    if action in ("codificar", "encode"):
        result = base64.b64encode(payload.encode("utf-8")).decode("ascii")
    elif action in ("decodificar", "decode"):
        try:
            result = base64.b64decode(payload, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return "Eso no es Base64 válido (o no es texto UTF-8)."
    else:
        return usage

    return result if len(result) <= MAX_OUTPUT_CHARS else result[:MAX_OUTPUT_CHARS] + "…"
