import logging
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..models import HistoryChat
from ..schemas import Reply
from ..services import chat_history, claude_runner, jobs
from ..utils import utcnow
from .claude import WAITING_REACTION, _finished
from .flags import normalize_flag
from .registry import CommandContext, command

logger = logging.getLogger("tox.history")

DONE_REACTION = "✅"
MAX_PERIOD = timedelta(days=30)
MAX_LINE_CHARS = 500  # one very long message must not use up the summary
MAX_TRANSCRIPT_CHARS = 60_000  # the oldest lines are dropped past this
_PERIOD = re.compile(r"^(\d{1,3})([hd])$", re.IGNORECASE)

# The flags of `h`: `.b h -on`. Written as the phone keyboard gives them (-on, --on, – and — are all fine).
ON_FLAG, OFF_FLAG, STATUS_FLAG, DELETE_FLAG = "on", "off", "status", "delete"
# The flags of `res`: only today's messages (the default) or the whole history
TODAY_FLAG, ALL_FLAG = "today", "all"


def _row(ctx: CommandContext) -> HistoryChat | None:
    """The saved-chat row of this chat, by any of the ids it is known by."""
    message = ctx.message
    key = chat_history.chat_key_for(ctx.db, message.platform, [message.chat_id, *message.chat_aliases])
    return ctx.db.get(HistoryChat, key)


@command(
    "h",
    description="Guarda (-on) o deja de guardar (-off) los mensajes de este chat; -status muestra cómo está y "
                "-delete borra lo guardado de este chat",
    usage="h -on | -off | -status | -delete",
    aliases=("historial",),
    category="Historial (solo el dueño)",
    owner_only=True,
)
def history(ctx: CommandContext) -> str | Reply:
    """Turns the saving of this chat on or off. owner_only: only the owner decides what is saved (from their own
    number, or as an admin through the bot of the groups)."""
    message = ctx.message
    flag = normalize_flag(ctx.args[0]) if ctx.args else ""
    action = flag.lstrip("-") if flag.startswith("-") else ""  # a bare "on" is not a flag: it shows the usage
    row = _row(ctx)
    key = row.chat_key if row else chat_history.chat_key_for(ctx.db, message.platform, [message.chat_id])

    if action == ON_FLAG:
        aliases = ",".join(a for a in message.chat_aliases if a != message.chat_id)
        if row is None:
            ctx.db.add(HistoryChat(chat_key=key, aliases=aliases, enabled_by=message.user_key))
        elif aliases:
            row.aliases = aliases
        ctx.db.commit()
        logger.info("HISTORY on for %s", key)
        return Reply(reaction=DONE_REACTION)  # no message in the chat: nobody is told, by design

    if action == OFF_FLAG:
        if row is not None:
            ctx.db.delete(row)
            ctx.db.commit()
            logger.info("HISTORY off for %s", key)
        return Reply(reaction=DONE_REACTION)

    if action == DELETE_FLAG:
        deleted = chat_history.delete_chat(ctx.settings, key)
        logger.info("HISTORY deleted %s messages of %s", deleted, key)
        return Reply(reaction=DONE_REACTION)

    if action == STATUS_FLAG:
        saved = chat_history.count(ctx.settings, key)
        total_mb = chat_history.total_bytes(ctx.settings) / (1024 * 1024)
        if row is None:
            state = "desactivado"
        else:
            since = row.enabled_at.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(ctx.settings.TIMEZONE))
            state = f"activado desde {since:%d/%m %H:%M}"
        return (f"📝 Historial de este chat: {state}\n"
                f"• Mensajes guardados de este chat: {saved}\n"
                f"• Espacio usado en total: {total_mb:.1f} MB de {ctx.settings.HISTORY_MAX_MB} MB")

    return (f"Uso: {ctx.prefix}h -on | -off | -status | -delete\n"
            f"-on / -off: guarda o deja de guardar los mensajes de este chat. -status: cómo está. "
            f"-delete: elimina lo guardado de este chat.")


def _scope(raw: str, settings, now: datetime) -> tuple[datetime | None, int] | None:
    """(since when, at most how many messages) from what was typed after `res`, or None when it makes no sense.
    "" or -today: today's messages (since midnight, in TIMEZONE). -all: the whole history. "6h" / "2d": that far back.
    "150": the last 150 messages. `now` is naive UTC, like everything stored."""
    most = settings.HISTORY_SUMMARY_MAX_MESSAGES
    flag = normalize_flag(raw)
    if flag.startswith("-"):
        flag = flag.lstrip("-")
        if flag == ALL_FLAG:
            return None, most
        if flag != TODAY_FLAG:
            return None
        raw = ""
    if not raw:
        zone = ZoneInfo(settings.TIMEZONE)
        local = now.replace(tzinfo=timezone.utc).astimezone(zone)
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        return midnight.astimezone(timezone.utc).replace(tzinfo=None), most
    match = _PERIOD.match(raw)
    if match:
        amount, unit = int(match.group(1)), match.group(2).lower()
        span = timedelta(hours=amount) if unit == "h" else timedelta(days=amount)
        return (now - min(span, MAX_PERIOD) if amount else None), most
    if raw.isdigit() and int(raw) > 0:
        return None, min(int(raw), most)
    return None


def _name(saved: chat_history.Saved) -> str:
    return saved.sender_name or saved.sender_id.split("@")[0]


@command(
    "res",
    description="Resume los mensajes guardados de este chat: los de hoy (por defecto o -today), todo el historial "
                "(-all), las últimas horas o días (6h, 2d) o los últimos N mensajes (150)",
    usage="res [-today | -all | 6h | 2d | 150]",
    aliases=("resumen",),
    category="Historial (solo el dueño)",
    owner_only=True,
)
def summary(ctx: CommandContext) -> str | Reply:
    """Summarises what was saved of this chat (`h -on`). Like !claude it answers with a ⏳ reaction and a job."""
    message, settings = ctx.message, ctx.settings
    scope = _scope(ctx.raw_args.strip(), settings, utcnow())
    if scope is None:
        p = ctx.prefix
        return (f"Uso: {p}res [-today | -all | horas o días | cantidad]\n"
                f"{p}res (o -today): los mensajes de hoy. {p}res -all: todo el historial guardado. "
                f"{p}res 6h, {p}res 2d (hasta 30 d) o {p}res 150 (los últimos 150 mensajes).")
    since, most = scope

    key = chat_history.chat_key_for(ctx.db, message.platform, [message.chat_id, *message.chat_aliases])
    saved = chat_history.recent(settings, key, since=since, limit=most)
    if not saved:
        return f"No tengo mensajes guardados de este chat en ese período. Se guardan con {ctx.prefix}h -on."

    transcript = chat_history.transcript(saved, ZoneInfo(settings.TIMEZONE), _name, max_line=MAX_LINE_CHARS,
                                         max_chars=MAX_TRANSCRIPT_CHARS)
    header = f"Conversación de WhatsApp ({len(saved)} mensajes, con la hora de cada uno):\n"
    if len(saved) == most:
        header += f"(Son solo los últimos {most} mensajes del período pedido: hay más antes de estos.)\n"
    header += "\n"

    job_id = jobs.create(settings.CLAUDE_TIMEOUT_SECONDS + 300)
    try:
        claude_runner.start_summary(header + transcript, settings, on_done=lambda text: jobs.finish(job_id, _finished(text)))
    except claude_runner.Busy:
        jobs.discard(job_id)
        return claude_runner.BUSY_MESSAGE
    logger.info("HISTORY summary of %s (%d messages)", key, len(saved))
    return Reply(reaction=WAITING_REACTION, job=job_id)
