"""Small, deterministic developer utilities. Nothing here touches the network or the database."""

import hashlib
from urllib.parse import quote, unquote

from .registry import CommandContext, command

CATEGORY = "Herramientas"
MAX_OUTPUT_CHARS = 1000


def _truncate(text: str) -> str:
    return text if len(text) <= MAX_OUTPUT_CHARS else text[:MAX_OUTPUT_CHARS] + "…"


# ---- hash ---------------------------------------------------------------------------------------

HASH_ALGORITHMS = ("md5", "sha1", "sha256", "sha512")


@command(
    "hash",
    description="Calcula el hash de un texto (md5, sha1, sha256, sha512)",
    usage="hash <md5|sha1|sha256|sha512> <texto>",
    category=CATEGORY,
)
def hash_command(ctx: CommandContext) -> str:
    usage = f"Uso: {ctx.prefix}hash <md5|sha1|sha256|sha512> <texto>"
    if len(ctx.args) < 2:
        return usage
    algorithm = ctx.args[0].lower().replace("-", "")
    if algorithm not in HASH_ALGORITHMS:
        return usage
    text = ctx.raw_args.split(maxsplit=1)[1]
    digest = hashlib.new(algorithm, text.encode("utf-8"), usedforsecurity=False).hexdigest()
    return f"{algorithm}: {digest}"


# ---- url ----------------------------------------------------------------------------------------

@command(
    "url",
    description="Codifica o decodifica texto para usar en una URL",
    usage="url codificar|decodificar <texto>",
    category=CATEGORY,
)
def url_command(ctx: CommandContext) -> str:
    usage = f"Uso: {ctx.prefix}url codificar|decodificar <texto>"
    if len(ctx.args) < 2:
        return usage
    action = ctx.args[0].lower()
    text = ctx.raw_args.split(maxsplit=1)[1]
    if action in ("codificar", "encode"):
        return _truncate(quote(text, safe=""))
    if action in ("decodificar", "decode"):
        return _truncate(unquote(text))
    return usage
