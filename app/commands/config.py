"""`!config`: how talkative Tox is in THIS chat (services/chat_config.py).

Everything here edits one chat only. What the .env says stays the default for every chat that was never configured,
and -reset is how a chat goes back to it; there is no way to change the default from a chat, on purpose -- a value
typed into one group should not quietly become the rule everywhere else.
"""

import logging
from dataclasses import dataclass
from typing import get_args

from ..config import Settings
from ..schemas import Reply
from ..services import chat_config
from .flags import normalize_flag
from .registry import CommandContext, command

logger = logging.getLogger("tox.config")

CATEGORY = "Administración"
DONE_REACTION = "✅"
RESET_FLAG, OFF_FLAG, ON_FLAG = "reset", "off", "on"


@dataclass(frozen=True)
class Option:
    """One setting as it is typed and read in a chat. `kind` is only about showing and reading the value: a chance is
    written as a percentage because "0.3" means nothing to somebody typing on a phone."""

    name: str
    setting: str
    what: str
    kind: str  # "chance", "number", "yesno" or "choice" (one of the words the field itself allows)
    unit: str = ""


OPTIONS: tuple[Option, ...] = (
    Option("tono", "CLAUDE_TONE", "cómo habla: salado, picante, tranqui o normal", "choice"),
    Option("charla", "CLAUDE_INTERJECT_CHANCE", "cada cuántos mensajes se mete solo", "chance"),
    Option("espera", "CLAUDE_INTERJECT_COOLDOWN_MINUTES", "cuánto se calla después de hablar", "number", "min"),
    Option("perfiles", "CLAUDE_PROFILE_PEOPLE_CHANCE", "cada cuánto sabe quién es cada uno", "chance"),
    Option("respuestas", "CLAUDE_ON_REPLY", "contesta si le respondés un mensaje", "yesno"),
    Option("audio", "CLAUDE_AUDIO_CHANCE", "cada cuánto contesta con audio", "chance"),
    Option("contexto", "CLAUDE_CONTEXT_MESSAGES", "cuántos mensajes de la charla lee", "number", "mensajes"),
    Option("horas", "CLAUDE_CONTEXT_HOURS", "de cuántas horas atrás los lee", "number", "h"),
)
BY_NAME = {option.name: option for option in OPTIONS} | {option.setting.lower(): option for option in OPTIONS}
# `charla` is what -off and -on switch: turning the bot off in a group is this one, the rest keep working
SWITCH = BY_NAME["charla"]


def _shown(option: Option, value) -> str:
    """The value as it is read in the chat, not as Python holds it."""
    if option.kind == "choice":
        return str(value)
    if option.kind == "yesno":
        return "sí" if value else "no"
    if option.kind == "chance":
        return "nunca" if value == 0 else f"{value * 100:.0f}%"
    return f"{value} {option.unit}".strip()


def _typed(option: Option, raw: str) -> str:
    """What was typed, as chat_config can read it: a percentage is a share, and sí/no are booleans. Anything else is
    handed over as it came and the setting's own rules decide."""
    raw = raw.strip()
    if option.kind == "chance" and raw.endswith("%"):
        try:
            return str(float(raw[:-1].replace(",", ".")) / 100)
        except ValueError:
            return raw
    if option.kind == "chance" and raw.lower() in ("nunca", "no"):
        return "0"
    if option.kind == "choice":
        return raw.lower()
    if option.kind == "yesno":
        return {"sí": "true", "si": "true", "no": "false"}.get(raw.lower(), raw)
    return raw.replace(",", ".") if option.kind == "chance" else raw


def _list(ctx: CommandContext) -> str:
    """Every option with what it is worth here, and whether that is this chat's own value or the general one."""
    own = chat_config.overrides(ctx.db, ctx.message.chat_key)
    lines = ["⚙️ Config de este chat"]
    for option in OPTIONS:
        value = own.get(option.setting, getattr(ctx.settings, option.setting))
        where = "este chat" if option.setting in own else "general"
        lines.append(f"• *{option.name}*: {_shown(option, value)} _({where})_ — {option.what}")
    p = ctx.prefix
    lines.append(f"\nCambiar: `{p}config charla 15%` · Volver a lo general: `{p}config charla -reset`")
    lines.append(f"Apagar del todo acá: `{p}config -off`")
    return "\n".join(lines)


@command(
    "config",
    description="Cuánto habla Tox en este chat: lo lista, lo cambia solo acá (-reset vuelve a lo general) y "
                "-off / -on lo apagan o lo prenden en este chat",
    usage="config [clave valor | clave -reset | -off | -on]",
    aliases=("conf",),
    category=CATEGORY,
    admin_only=True,
)
def config(ctx: CommandContext) -> str | Reply:
    """Reads and writes what services/chat_config.py resolves. admin_only, and in a WhatsApp group `is_admin` also
    asks for the sender to administer THAT group: nobody configures a group they are not an admin of."""
    message = ctx.message
    args = ctx.args
    if not args:
        return _list(ctx)

    first = normalize_flag(args[0])
    if first.lstrip("-") in (OFF_FLAG, ON_FLAG) and first.startswith("-"):
        if first.lstrip("-") == OFF_FLAG:
            chat_config.set_value(ctx.db, message.chat_key, SWITCH.setting, "0", message.user_key)
            logger.info("CONFIG %s off in %s", SWITCH.setting, message.chat_key)
            return "🔇 Listo, no me meto más solo en este chat. Si me hablás o me respondés, sigo contestando."
        chat_config.clear(ctx.db, message.chat_key, SWITCH.setting)
        logger.info("CONFIG %s back to the general value in %s", SWITCH.setting, message.chat_key)
        general = _shown(SWITCH, getattr(ctx.settings, SWITCH.setting))
        return f"🔊 Listo, vuelvo a meterme cada tanto en este chat ({general}, lo general)."

    option = BY_NAME.get(normalize_flag(args[0]).lstrip("-"))
    if option is None:
        names = ", ".join(f"*{o.name}*" for o in OPTIONS)
        return f"No conozco esa opción. Las que hay: {names}.\nUso: {ctx.prefix}config {OPTIONS[0].name} 15%"

    rest = " ".join(args[1:]).strip()
    if not rest:
        value = chat_config.value(ctx.db, ctx.settings, message.chat_key, option.setting)
        return (f"*{option.name}*: {_shown(option, value)} — {option.what}\n"
                f"Cambiar: `{ctx.prefix}config {option.name} <valor>` · "
                f"Volver a lo general: `{ctx.prefix}config {option.name} -reset`")

    if normalize_flag(rest).lstrip("-") == RESET_FLAG and normalize_flag(rest).startswith("-"):
        chat_config.clear(ctx.db, message.chat_key, option.setting)
        logger.info("CONFIG %s reset in %s", option.setting, message.chat_key)
        general = _shown(option, getattr(ctx.settings, option.setting))
        return f"↩️ *{option.name}* vuelve a lo general: {general}."

    try:
        value = chat_config.set_value(ctx.db, message.chat_key, option.setting, _typed(option, rest), message.user_key)
    except ValueError:
        return f"Ese valor no sirve para *{option.name}*. {_hint(ctx, option)}"
    logger.info("CONFIG %s = %s in %s by %s", option.setting, value, message.chat_key, message.user_key)
    return f"✅ *{option.name}* en este chat: {_shown(option, value)}."


def _hint(ctx: CommandContext, option: Option) -> str:
    """What that option does take, written from the field's own rules so it can never say something the code does
    not actually accept."""
    if option.kind == "choice":
        return f"Las que hay: {', '.join(_choices(option.setting))}."
    if option.kind == "yesno":
        return f"Probá `{ctx.prefix}config {option.name} sí` o `{ctx.prefix}config {option.name} no`."
    if option.kind == "chance":
        return f"Va de 0% a 100%: `{ctx.prefix}config {option.name} 15%`."
    low, high = _limits(option.setting)
    return f"Va de {low} a {high}: `{ctx.prefix}config {option.name} {high}`."


def _choices(setting: str) -> tuple[str, ...]:
    """The words the field itself accepts (its Literal in config.py), so the message can never offer one it refuses."""
    return get_args(Settings.model_fields[setting].annotation)


def _limits(setting: str) -> tuple[int, int]:
    """The ge/le the field itself declares in config.py, so a hint can never promise a value that is refused."""
    metadata = Settings.model_fields[setting].metadata
    low = next((m.ge for m in metadata if hasattr(m, "ge")), 0)
    high = next((m.le for m in metadata if hasattr(m, "le")), 0)
    return low, high
