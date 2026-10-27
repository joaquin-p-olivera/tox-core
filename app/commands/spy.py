"""`!spy`: which chats this one may be told about (services/chat_sources.py).

One row is one direction, and so is one command: `!spy festejen` lets THIS chat read that one, never the other way
round. Giving a chat a name (`-alias`) grants nothing by itself; it only saves typing a WhatsApp id.

Admin only, and in a WhatsApp group `is_admin` also asks for the sender to administer THAT group: reading another
conversation is decided from the chat that is going to read it, by somebody who runs it.
"""

import logging

from ..schemas import Reply
from ..services import chat_aliases, chat_history, chat_sources
from .flags import normalize_flag
from .registry import CommandContext, command

logger = logging.getLogger("tox.spy")

CATEGORY = "Administración"
ALIAS_FLAG, OFF_FLAG, LIST_FLAG = "alias", "off", "list"


def _usage(prefix: str) -> str:
    return (f"Uso: {prefix}spy [alias [palabras]] | -off <alias> | -alias <nombre> | -list\n"
            f"{prefix}spy: qué chats puede leer este. "
            f"{prefix}spy festejen risas,asado: lo lee cuando la pregunta diga alguna de esas palabras "
            f"(sin palabras, el alias es la palabra). "
            f"{prefix}spy -off festejen: deja de leerlo. "
            f"{prefix}spy -alias gamers: le pone nombre a ESTE chat. "
            f"{prefix}spy -list: los nombres que hay.")


def _listing(ctx: CommandContext) -> str:
    """What this chat reads, and what it is called, which is what somebody needs before writing anything."""
    mine = chat_aliases.alias_of(ctx.db, ctx.message.chat_key)
    lines = [f"🕵️ Este chat se llama *{mine}*" if mine else
             f"🕵️ Este chat no tiene nombre (`{ctx.prefix}spy -alias <nombre>`)"]
    sources = chat_sources.of_chat(ctx.db, ctx.message.chat_key)
    if not sources:
        lines.append("\nNo lee ningún otro chat.")
    else:
        lines.append("\nLee estos chats, cuando la pregunta los nombra:")
        for source in sources:
            saved = chat_history.count(ctx.settings, source.source_chat_key)
            words = ", ".join(chat_sources.words_of(source))
            lines.append(f"• *{source.label}* — con: {words} _({saved} mensajes guardados)_")
    lines.append(f"\nNadie lo lee a él salvo que se configure desde el otro chat.")
    return "\n".join(lines)


@command(
    "spy",
    description="Qué otros chats puede leer este (y con qué palabras). -alias le pone nombre a este chat, "
                "-off saca uno, -list muestra los nombres que hay",
    usage="spy [alias [palabras]] | -off <alias> | -alias <nombre> | -list",
    aliases=("espiar",),
    category=CATEGORY,
    admin_only=True,
)
def spy(ctx: CommandContext) -> str | Reply:
    db, message = ctx.db, ctx.message
    if not ctx.args:
        return _listing(ctx)

    flag = normalize_flag(ctx.args[0])
    action = flag.lstrip("-") if flag.startswith("-") else ""
    rest = ctx.args[1:]

    if action == LIST_FLAG:
        rows = chat_aliases.all_aliases(db)
        if not rows:
            return f"Todavía no hay ningún chat con nombre. Poné uno con `{ctx.prefix}spy -alias <nombre>`."
        here = message.chat_key
        return "🏷️ Chats con nombre:\n" + "\n".join(
            f"• *{row.alias}*" + (" _(este)_" if row.chat_key == here else "") for row in rows)

    if action == ALIAS_FLAG:
        # Two words is a mistake, not a name with the first word taken: silently keeping half of what was typed is
        # how somebody ends up pointing another chat at the wrong place.
        alias = chat_aliases.normalize(rest[0]) if len(rest) == 1 else ""
        if not chat_aliases.valid(alias):
            return "El nombre tiene que ser una sola palabra: letras, números, guiones. Ej: `festejen`, `librecourse`."
        chat_aliases.name(db, message.chat_key, alias, message.user_key)
        logger.info("SPY %s is now called %s", message.chat_key, alias)
        return f"🏷️ Listo, este chat se llama *{alias}*. Desde otro chat ya se puede escribir `{ctx.prefix}spy {alias}`."

    if action == OFF_FLAG:
        if not rest:
            return _usage(ctx.prefix)
        key = chat_aliases.key_of(db, rest[0])
        if key is None:
            return f"No conozco ningún chat llamado *{rest[0]}*. Mirá `{ctx.prefix}spy -list`."
        if not chat_sources.forbid(db, message.chat_key, key):
            return f"Este chat no estaba leyendo a *{chat_aliases.normalize(rest[0])}*."
        logger.info("SPY %s stops reading %s", message.chat_key, key)
        return f"🔒 Listo, este chat ya no lee a *{chat_aliases.normalize(rest[0])}*."

    if action:  # a flag nobody knows
        return _usage(ctx.prefix)

    alias = chat_aliases.normalize(ctx.args[0])
    key = chat_aliases.key_of(db, alias)
    if key is None:
        return (f"No conozco ningún chat llamado *{alias}*. Los nombres se ponen con `{ctx.prefix}spy -alias <nombre>` "
                f"escrito EN ese chat, y `{ctx.prefix}spy -list` muestra los que hay.")
    if key == message.chat_key:
        return "Un chat no se lee a sí mismo: eso ya es su propia conversación."
    words = [w for word in ctx.raw_args.split(maxsplit=1)[1:] for w in word.split(",")] if len(ctx.args) > 1 else []
    chat_sources.allow(db, message.chat_key, key, alias, words, message.user_key)
    logger.info("SPY %s now reads %s with %s", message.chat_key, key, words or [alias])
    shown = ", ".join(w.strip() for w in words if w.strip()) or alias
    return (f"🕵️ Listo. Este chat puede leer a *{alias}* cuando la pregunta diga: {shown}.\n"
            f"No al revés: allá no se sabe nada de este chat.")
