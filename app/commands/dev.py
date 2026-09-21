import base64
import binascii
import uuid
from http import HTTPStatus

from .registry import CommandContext, command

MAX_OUTPUT_CHARS = 1000
CATEGORY = "Herramientas"

# Spanish descriptions for the status codes people actually run into. The standard
# names (e.g. "Not Found") stay in English on purpose: that's what shows up in logs and docs.
HTTP_DESCRIPTIONS = {
    200: "La solicitud se procesó con éxito.",
    201: "Se creó un recurso nuevo.",
    204: "Salió bien, pero no hay nada para devolver.",
    301: "El recurso se movió de forma permanente a otra URL.",
    302: "Redirección temporal a otra URL.",
    304: "Lo que tenés en caché sigue siendo válido.",
    400: "El servidor no entendió la solicitud (sintaxis inválida).",
    401: "Falta autenticación, o es inválida.",
    403: "Estás autenticado, pero no tenés permiso.",
    404: "No existe lo que buscás.",
    405: "Ese método HTTP no está permitido para este recurso.",
    408: "El servidor se cansó de esperar la solicitud.",
    409: "La solicitud choca con el estado actual del recurso.",
    410: "Existió, pero se eliminó para siempre.",
    413: "El cuerpo de la solicitud es demasiado grande.",
    415: "El servidor no acepta ese tipo de contenido.",
    418: "Soy una tetera y no puedo preparar café (chiste del RFC 2324).",
    422: "La solicitud está bien formada, pero tiene errores de contenido.",
    429: "Hiciste demasiadas solicitudes: bajá el ritmo.",
    451: "No disponible por razones legales.",
    500: "Falló algo en el servidor.",
    501: "El servidor no sabe hacer eso todavía.",
    502: "Un servidor intermedio recibió una respuesta inválida.",
    503: "El servidor no está disponible ahora (sobrecarga o mantenimiento).",
    504: "Un servidor intermedio no obtuvo respuesta a tiempo.",
}


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


@command(
    "http",
    description="Explica un código de estado HTTP",
    usage="http <código>",
    category=CATEGORY,
)
def http_status(ctx: CommandContext) -> str:
    if not ctx.args or not ctx.args[0].isdigit():
        return f"Uso: {ctx.prefix}http <código> — por ejemplo {ctx.prefix}http 404"
    try:
        status = HTTPStatus(int(ctx.args[0]))
    except ValueError:
        return f"{ctx.args[0]} no es un código de estado HTTP estándar."
    description = HTTP_DESCRIPTIONS.get(status.value, "(sin descripción en español)")
    return f"{status.value} {status.phrase}\n{description}"
