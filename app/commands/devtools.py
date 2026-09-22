"""Small, deterministic developer utilities. Nothing here touches the network or the database."""

import hashlib
import json
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


# ---- json ---------------------------------------------------------------------------------------

# Phones love turning straight quotes into curly ones, which makes valid JSON look invalid.
_CURLY_QUOTES = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"})


@command("json", description="Valida y formatea JSON", usage="json <json>", category=CATEGORY)
def json_command(ctx: CommandContext) -> str:
    if not ctx.raw_args.strip():
        return f"Uso: {ctx.prefix}json <json>"
    try:
        data = json.loads(ctx.raw_args.translate(_CURLY_QUOTES))
    except json.JSONDecodeError as error:
        return f"❌ JSON inválido: {error.msg} (línea {error.lineno}, columna {error.colno})"
    except RecursionError:
        return "❌ JSON demasiado anidado."
    return "✅ JSON válido\n" + _truncate(json.dumps(data, indent=2, ensure_ascii=False))


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


# ---- base ---------------------------------------------------------------------------------------

@command(
    "base",
    description="Convierte un número entre decimal, hexadecimal, binario y octal",
    usage="base <número>   (acepta 255, 0xff, 0b1010, 0o17)",
    category=CATEGORY,
)
def base_command(ctx: CommandContext) -> str:
    usage = f"Uso: {ctx.prefix}base <número> — por ejemplo {ctx.prefix}base 255 o {ctx.prefix}base 0xff"
    raw = ctx.raw_args.strip().replace("_", "")
    if not raw or len(raw) > 40:
        return usage
    try:
        value = int(raw, 0)
    except ValueError:
        try:
            value = int(raw, 10)  # "007": int(..., 0) rejects leading zeros
        except ValueError:
            return usage
    return (f"🔢 {value}\n"
            f"• Hex: {value:#x}\n• Binario: {value:#b}\n• Octal: {value:#o}")
