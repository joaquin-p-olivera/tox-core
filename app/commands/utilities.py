from datetime import date, datetime

from sqlalchemy import select

from ..models import Birthday, Reminder
from ..schemas import Mention, Reply
from ..services import media_cache, qr_code, reminders
from ..utils import utcnow
from .registry import CommandContext, command

CATEGORY = "Utilidades"


@command(
    "qr",
    description="Genera un código QR con el texto o link que le pases",
    usage="qr <texto o link>",
    category=CATEGORY,
)
def qr(ctx: CommandContext) -> str | Reply:
    if not ctx.raw_args.strip():
        return f"Uso: {ctx.prefix}qr <texto o link> — por ejemplo {ctx.prefix}qr https://example.com"
    try:
        image = qr_code.render_qr(ctx.raw_args)
    except qr_code.TextTooLong as error:
        return str(error)
    return Reply(image=media_cache.store(image, "image/png", ctx.settings.MEDIA_CACHE_SECONDS))


@command(
    "recordar",
    description="Te recuerda algo a una hora (HH:MM): hoy si todavía no pasó, si no mañana",
    usage="recordar <HH:MM> <qué>",
    category=CATEGORY,
)
def remind(ctx: CommandContext) -> str:
    usage = f"Uso: {ctx.prefix}recordar <HH:MM> <qué> — por ejemplo {ctx.prefix}recordar 18:30 sacar la basura"
    parsed = reminders.split_time_and_text(ctx.raw_args)
    if parsed is None:
        return usage
    (hour, minute), text = parsed
    if len(text) > reminders.MAX_TEXT_CHARS:
        return f"Es demasiado largo (máx. {reminders.MAX_TEXT_CHARS} caracteres)."

    message = ctx.message
    if reminders.pending_count(ctx.db, message.platform, message.user_id) >= reminders.MAX_PENDING_PER_USER:
        return f"Ya tenés {reminders.MAX_PENDING_PER_USER} recordatorios pendientes: esperá a que salte alguno."

    now = utcnow()
    due = reminders.next_occurrence(hour, minute, now, ctx.settings.TIMEZONE)
    ctx.db.add(Reminder(platform=message.platform, chat_id=message.chat_id, user_id=message.user_id, text=text, due_at=due))
    ctx.db.commit()
    day = "mañana" if reminders.is_tomorrow(due, now, ctx.settings.TIMEZONE) else "hoy"
    return f"⏰ Listo, te lo recuerdo {day} a las {hour:02d}:{minute:02d}: {text}"


OLDEST_YEAR = 1905
# A birthday given without the year is stored with this one (a leap year, so 29/02 is valid; below OLDEST_YEAR, so it
# can never be a real birth year). Keeps the column a plain Date: no schema change.
NO_YEAR = 1904


def _parse_date(word: str) -> date | None:
    """"15/03/1995" or "15/03" (no year). None when it isn't a real date, or the year is in the future or too old."""
    try:
        if word.count("/") == 1:
            return datetime.strptime(f"{word}/{NO_YEAR}", "%d/%m/%Y").date()
        parsed = datetime.strptime(word, "%d/%m/%Y").date()
    except ValueError:
        return None
    return parsed if OLDEST_YEAR <= parsed.year and parsed <= date.today() else None


def _show_date(value: date) -> str:
    return value.strftime("%d/%m") if value.year == NO_YEAR else value.strftime("%d/%m/%Y")


@command(
    "cumple",
    description="Registra tu cumpleaños (dd/mm/aaaa, o dd/mm sin el año). Los admins pueden cargar el de otra persona etiquetándola",
    usage="cumple <dd/mm/aaaa | dd/mm>",
    category=CATEGORY,
)
def birthday(ctx: CommandContext) -> str | Reply:
    message = ctx.message
    usage = (f"Uso: {ctx.prefix}cumple <dd/mm/aaaa> — por ejemplo {ctx.prefix}cumple 15/03/1995 (o sin el año: "
             f"{ctx.prefix}cumple 15/03)\nLos admins también: {ctx.prefix}cumple @usuario <dd/mm/aaaa>")
    tagged = (message.mentioned or [])
    words = [w for w in ctx.raw_args.split() if not w.startswith("@")]  # the "@name" text of a tag is not the date
    own = ctx.db.scalars(select(Birthday).where(Birthday.platform == message.platform,
                                                Birthday.user_id == message.user_id)).first()
    if not words:
        if own and not tagged:
            return f"🎂 Tu cumpleaños está registrado: {_show_date(own.born_on)}.\n{usage}"
        return usage
    born_on = _parse_date(words[-1])
    if born_on is None:
        return f"No entendí la fecha. Tiene que ser dd/mm/aaaa (o dd/mm) y ya pasada.\n{usage}"

    if tagged:
        if not ctx.is_admin:
            return "Solo los admins pueden cargar el cumpleaños de otra persona."
        if len(tagged) > 1:
            return "Etiquetá a una sola persona."
        person_id, person_name = tagged[0].user_id, tagged[0].user_name
    else:
        person_id, person_name = message.user_id, message.user_name

    row = ctx.db.scalars(select(Birthday).where(Birthday.platform == message.platform,
                                                Birthday.user_id == person_id)).first()
    before = row.born_on if row else None
    if row is None:
        ctx.db.add(Birthday(platform=message.platform, user_id=person_id, user_name=person_name, born_on=born_on,
                            set_by=message.user_key))
    else:
        row.born_on, row.set_by = born_on, message.user_key
        row.user_name = person_name or row.user_name
    ctx.db.commit()

    shown = _show_date(born_on)
    old = f" (antes: {_show_date(before)})" if before else ""
    verb = "Actualicé" if before else "Registré"
    return Reply(text=f"🎂 {verb} el cumpleaños de {{@0}}: {shown}{old}.",
                 mentions=[Mention(user_id=person_id, user_name=person_name)])
