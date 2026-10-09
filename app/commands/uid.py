import logging

from sqlalchemy import select

from ..models import ChatUser
from ..schemas import Participant, Reply
from ..services import chat_members
from .registry import CommandContext, command

logger = logging.getLogger("tox.uid")

REACTION = "👍"
PHONE_SUFFIX = "@s.whatsapp.net"


def _describe_alias(alias: str) -> str:
    """"59899123456@s.whatsapp.net" -> "tel +59899123456"; anything else (e.g. a LID) is shown as it is."""
    return f"tel +{alias[:-len(PHONE_SUFFIX)]}" if alias.endswith(PHONE_SUFFIX) else alias


@command(
    "uid",
    description="(Admin) Imprime en el log de la API el id, nombre y teléfono de cada usuario del chat",
    category="Administración",
    admin_only=True,
    hidden=True,
)
def uid(ctx: CommandContext) -> Reply:
    """Meant for building lists (e.g. ADMIN_USER_IDS): the output goes to the API's log, not to the chat,
    and the bot only reacts to the command. The platform's own member list never includes the sender, so they
    are added explicitly.

    WhatsApp often gives no display name for a group member, so a missing name is completed with the one the API
    already knows from that user's earlier commands in this chat; the platform's other ids for the same person
    (its phone number, when WhatsApp shares it) are printed too, to tell people apart."""
    message = ctx.message
    people = {p.user_id: p for p in chat_members.known_members(ctx.db, message)}
    people[message.user_id] = Participant(user_id=message.user_id, user_name=message.user_name)
    known_names = {
        row.user_id: row.user_name
        for row in ctx.db.scalars(select(ChatUser).where(ChatUser.chat_key == message.chat_key))
        if row.user_name
    }

    logger.info("UID %s (%d users), as platform:user_id — name | other ids:", message.chat_key, len(people))
    for person in people.values():
        name = person.user_name or next((known_names[i] for i in (person.user_id, *person.aliases) if i in known_names), None)
        others = [_describe_alias(alias) for alias in person.aliases if alias != person.user_id]
        logger.info("UID %s:%s — %s%s", message.platform, person.user_id, name or "(sin nombre)",
                    f" | {', '.join(others)}" if others else "")
    return Reply(reaction=REACTION)
