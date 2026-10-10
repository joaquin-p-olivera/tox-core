import logging
import random
from datetime import timedelta

from sqlalchemy import select

from ..models import BlacklistedUser, PendingBan
from ..schemas import Mention, Participant, Reply
from ..utils import utcnow
from .registry import CommandContext, command

logger = logging.getLogger("tox.moderation")

CATEGORY = "Administración"
MAX_TARGETS = 5
ROULETTE_CONFIRM_SECONDS = 120
PHONE_SUFFIX = "@s.whatsapp.net"


def _is_listed_admin(ctx: CommandContext, person: Participant) -> bool:
    keys = ctx.settings.admin_user_keys
    return any(f"{ctx.message.platform}:{i}" in keys for i in (person.user_id, *person.aliases))


def _is_sender(ctx: CommandContext, person: Participant) -> bool:
    return ctx.message.user_id in (person.user_id, *person.aliases)


def _protected_reason(ctx: CommandContext, person: Participant) -> str | None:
    """Why this person can't be banned, or None. Fails closed: a member whose group-admin status is unknown is refused."""
    if person.is_bot:
        return "No me puedo banear a mí mismo."
    if _is_sender(ctx, person):
        return "No te podés banear a vos mismo."
    if _is_listed_admin(ctx, person) or person.is_group_admin:
        return "No se puede banear a un admin."
    if person.is_group_admin is None:
        return "No pude comprobar si es admin del grupo, así que no lo toco."
    return None


def _tagged_targets(ctx: CommandContext, usage: str) -> list[Participant] | str:
    """The people tagged in the command, or the message to answer with when the command can't go on."""
    if not ctx.message.is_group:
        return "Solo funciona en grupos."
    tagged = list(dict.fromkeys(p.user_id for p in (ctx.message.mentioned or [])))
    people = {p.user_id: p for p in (ctx.message.mentioned or [])}
    if not tagged:
        return usage
    if len(tagged) > MAX_TARGETS:
        return f"Máximo {MAX_TARGETS} personas por vez."
    for user_id in tagged:
        if reason := _protected_reason(ctx, people[user_id]):
            return reason
    return [people[user_id] for user_id in tagged]


def _mentions(people: list[Participant]) -> tuple[str, list[Mention]]:
    return ", ".join(f"{{@{i}}}" for i in range(len(people))), [Mention(user_id=p.user_id, user_name=p.user_name) for p in people]


def _label(entry: BlacklistedUser) -> str:
    phone = next((a[:-len(PHONE_SUFFIX)] for a in entry.all_ids if a.endswith(PHONE_SUFFIX)), None)
    return entry.name or (f"+{phone}" if phone else entry.user_id)


@command(
    "ban",
    description="(Admin) Saca del grupo a la persona etiquetada",
    usage="ban @usuario",
    category=CATEGORY,
    admin_only=True,
)
def ban(ctx: CommandContext) -> str | Reply:
    targets = _tagged_targets(ctx, f"Uso: {ctx.prefix}ban @usuario")
    if isinstance(targets, str):
        return targets
    names, mentions = _mentions(targets)
    logger.info("BAN by %s in %s: %s", ctx.message.user_key, ctx.message.chat_key, [p.user_id for p in targets])
    return Reply(text=f"🔨 Baneado: {names}.", mentions=mentions, remove=[p.user_id for p in targets])


@command(
    "permaban",
    description="(Admin) Banea a la persona etiquetada y la pone en la lista negra: se la saca de todos los grupos y si vuelve, se la saca de nuevo",
    usage="permaban @usuario",
    category=CATEGORY,
    admin_only=True,
)
def permaban(ctx: CommandContext) -> str | Reply:
    targets = _tagged_targets(ctx, f"Uso: {ctx.prefix}permaban @usuario")
    if isinstance(targets, str):
        return targets
    message = ctx.message
    for person in targets:
        entry = ctx.db.scalars(select(BlacklistedUser).where(
            BlacklistedUser.platform == message.platform, BlacklistedUser.user_id == person.user_id)).first()
        aliases = ",".join(dict.fromkeys([a for a in person.aliases if a != person.user_id]))
        if entry is None:
            ctx.db.add(BlacklistedUser(platform=message.platform, user_id=person.user_id, aliases=aliases,
                                       name=person.user_name, added_by=message.user_key))
        else:  # already there: refresh what we know about them
            entry.aliases = aliases or entry.aliases
            entry.name = person.user_name or entry.name
    ctx.db.commit()
    logger.warning("PERMABAN by %s: %s", message.user_key, [p.user_id for p in targets])
    names, mentions = _mentions(targets)
    return Reply(
        text=f"⛔ {names}: baneado y en la lista negra. Se lo saca de todos los grupos y, si vuelve a entrar, lo saco de nuevo.",
        mentions=mentions, remove=[p.user_id for p in targets], sweep_blacklist=True,
    )


@command(
    "unpermaban",
    description="(Admin) Saca a alguien de la lista negra del permaban; sin parámetros, muestra la lista",
    usage="unpermaban [número o nombre]",
    aliases=("despermaban",),
    category=CATEGORY,
    admin_only=True,
)
def unpermaban(ctx: CommandContext) -> str:
    platform = ctx.message.platform
    entries = list(ctx.db.scalars(select(BlacklistedUser).where(BlacklistedUser.platform == platform)
                                  .order_by(BlacklistedUser.id)))
    if not entries:
        return "La lista negra está vacía."
    query = ctx.raw_args.strip()
    if not query:
        lines = [f"{number}. {_label(entry)}" for number, entry in enumerate(entries, start=1)]
        return "⛔ Lista negra:\n\n" + "\n".join(lines) + f"\n\nPara sacar a alguien: {ctx.prefix}unpermaban <número o nombre>"

    if query.isdigit():
        matches = [entries[int(query) - 1]] if 1 <= int(query) <= len(entries) else []
    else:
        wanted = query.lower()
        matches = [e for e in entries if wanted in _label(e).lower() or wanted in e.user_id.lower()]
    if not matches:
        return f"No encontré a nadie así en la lista negra. Mirala con {ctx.prefix}unpermaban."
    if len(matches) > 1:
        return "Hay varios que coinciden, usá el número:\n" + "\n".join(
            f"{entries.index(e) + 1}. {_label(e)}" for e in matches)
    entry = matches[0]
    label = _label(entry)
    ctx.db.delete(entry)
    ctx.db.commit()
    logger.warning("UNPERMABAN by %s: %s", ctx.message.user_key, entry.user_id)
    return f"✅ Saqué a {label} de la lista negra: ya puede volver a unirse."


@command(
    "ruleta-ban",
    description="(Admin) Sortea a un integrante (nunca un admin) para banearlo; antes pide que un admin confirme con !si o !no",
    aliases=("ruletaban",),
    category=CATEGORY,
    admin_only=True,
)
def ban_roulette(ctx: CommandContext) -> str | Reply:
    message = ctx.message
    if not message.is_group:
        return "Solo funciona en grupos."
    if message.participants is None:
        return "No pude ver la lista de integrantes del grupo, así que no puedo sortear."
    candidates = [p for p in message.participants
                  if not p.is_bot and not _is_sender(ctx, p) and not _is_listed_admin(ctx, p) and p.is_group_admin is False]
    if not candidates:
        return "No hay a quién sortear (solo quedan admins)."
    chosen = random.choice(candidates)
    pending = ctx.db.get(PendingBan, message.chat_key)
    if pending is None:
        ctx.db.add(PendingBan(chat_key=message.chat_key, target_user_id=chosen.user_id, target_name=chosen.user_name,
                              started_by=message.user_key))
    else:
        pending.target_user_id, pending.target_name = chosen.user_id, chosen.user_name
        pending.started_by, pending.created_at = message.user_key, utcnow()
    ctx.db.commit()
    return Reply(
        text=(f"🎲 La ruleta eligió a {{@0}}.\nAdmins: {ctx.prefix}si para banearlo o {ctx.prefix}no para salvarlo "
              f"(vence en {ROULETTE_CONFIRM_SECONDS // 60} min)."),
        mentions=[Mention(user_id=chosen.user_id, user_name=chosen.user_name)],
    )


def _open_roulette(ctx: CommandContext) -> PendingBan | None:
    pending = ctx.db.get(PendingBan, ctx.message.chat_key)
    if pending is not None and utcnow() - pending.created_at > timedelta(seconds=ROULETTE_CONFIRM_SECONDS):
        ctx.db.delete(pending)
        ctx.db.commit()
        return None
    return pending


@command("si", description="(Admin) Confirma el ban sorteado por la ruleta", category=CATEGORY, admin_only=True, hidden=True)
def roulette_yes(ctx: CommandContext) -> str | Reply:
    pending = _open_roulette(ctx)
    if pending is None:
        return "No hay ninguna ruleta pendiente."
    target = Mention(user_id=pending.target_user_id, user_name=pending.target_name)
    ctx.db.delete(pending)
    ctx.db.commit()
    logger.warning("RULETA-BAN confirmed by %s in %s: %s", ctx.message.user_key, ctx.message.chat_key, target.user_id)
    return Reply(text="🔨 La ruleta decidió: {@0} queda afuera.", mentions=[target], remove=[target.user_id])


@command("no", description="(Admin) Cancela el ban sorteado por la ruleta", category=CATEGORY, admin_only=True, hidden=True)
def roulette_no(ctx: CommandContext) -> str | Reply:
    pending = _open_roulette(ctx)
    if pending is None:
        return "No hay ninguna ruleta pendiente."
    target = Mention(user_id=pending.target_user_id, user_name=pending.target_name)
    ctx.db.delete(pending)
    ctx.db.commit()
    return Reply(text="😮‍💨 Se salvó {@0}.", mentions=[target])
