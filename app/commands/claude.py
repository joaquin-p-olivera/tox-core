import logging
from datetime import timedelta

from sqlalchemy import select

from ..models import ClaudeQuery
from ..schemas import Reply
from ..services import claude_context, claude_runner, jobs
from ..utils import utcnow
from .registry import CommandContext, command

logger = logging.getLogger("tox.claude")

WINDOW = timedelta(hours=1)
NEW_FLAGS = ("-n", "--nuevo")
WAITING_REACTION = "⏳"  # on the command message while Claude works (no placeholder message)
DONE_REACTION = "✅"  # replaces it when the answer arrives
FAILED_REACTION = "⚠️"  # ... or when there is none (the runner starts every error message with it)


def _finished(text: str, context: claude_context.Context | None = None) -> list[Reply]:
    """The answer first (so a failing reaction can never lose it), then the reaction that replaces the ⏳. With a
    `context`, the @Names in the answer become real mentions."""
    failed = text.startswith(FAILED_REACTION)
    mentions = []
    if context is not None and not failed:
        text, mentions = claude_context.tag_mentions(context, text)
    return [Reply(text=text, mentions=mentions), Reply(reaction=FAILED_REACTION if failed else DONE_REACTION)]


def _limit_message(limit: int, oldest, what: str) -> str:
    """`oldest` is when the oldest question still inside the window was made: that is when a place frees up."""
    minutes = max(1, int((oldest + WINDOW - utcnow()).total_seconds() // 60) + 1)
    return f"Se alcanzó el límite de {limit} {what}. Probá de nuevo en unos {minutes} min."


@command(
    "claude",
    description="Le hace una pregunta a Claude (IA); responde resumido. Los admins además pueden pedirle cosas sobre la PC",
    usage="claude [-n] <pregunta o pedido>   (o !c <pregunta>)",
    category="Utilidades",
)
def ask_claude(ctx: CommandContext) -> str | Reply:
    """Answers at once with a ⏳ reaction to the command and a job id: the bot reacts, then polls the job and sends the
    answer when it is ready (a question can take minutes, longer than a chat request should wait).

    Who asks decides what Claude can do: admins (ctx.is_admin) get the full-access mode in the work directory; anybody
    else a plain chat with no access to this PC (claude_runner.execute_public), with a per-person limit on top."""
    settings, message = ctx.settings, ctx.message
    full_access = ctx.is_admin
    first, _, rest = ctx.raw_args.strip().partition(" ")
    new_conversation = full_access and first.lower() in NEW_FLAGS
    question = (rest if first.lower() in NEW_FLAGS else ctx.raw_args).strip()
    if not question:
        usage = f"Uso: {ctx.prefix}claude <pregunta>"
        if full_access:
            usage += (f"\n{ctx.prefix}claude -n <pregunta>: empieza una conversación nueva (sin el contexto anterior)."
                      f"\nPodés pedirle también cosas sobre la PC y los proyectos.")
        return usage
    if full_access and not claude_runner.workdir(settings).is_dir():
        return f"No existe la carpeta de trabajo ({settings.CLAUDE_WORKDIR})."

    since = utcnow() - WINDOW
    rows = ctx.db.execute(select(ClaudeQuery.user_key, ClaudeQuery.created_at).where(ClaudeQuery.created_at > since)
                          .order_by(ClaudeQuery.created_at)).all()
    admins = settings.admin_user_keys
    # Admins share one pool; the others share another one, so a busy group can't use up the admins' questions
    pool = [at for user, at in rows if (user in admins) == full_access]
    pool_limit = settings.CLAUDE_QUESTIONS_PER_HOUR if full_access else settings.CLAUDE_PUBLIC_QUESTIONS_PER_HOUR
    if len(pool) >= pool_limit:
        return _limit_message(pool_limit, pool[0], "preguntas por hora")
    if not full_access:
        mine = [at for user, at in rows if user == message.user_key]
        per_person = settings.CLAUDE_PUBLIC_QUESTIONS_PER_PERSON_PER_HOUR
        if len(mine) >= per_person:
            return _limit_message(per_person, mine[0], "preguntas por hora por persona")

    # Names of the group and the replied-to message always; the latest messages only for people without access to the PC
    context = claude_context.gather(ctx.db, settings, message, with_history=not full_access)
    job_id = jobs.create(settings.CLAUDE_TIMEOUT_SECONDS + 300)

    def done(text: str) -> None:
        if not text.startswith(FAILED_REACTION):
            claude_context.remember(settings, context, message, text, job_id)  # (in the history, when it is on)
        jobs.finish(job_id, _finished(text, context))

    try:
        claude_runner.start(
            message.chat_key, context.prompt(question), new_conversation, settings,
            on_done=done, full_access=full_access,
        )
    except claude_runner.Busy:
        jobs.discard(job_id)
        return claude_runner.BUSY_MESSAGE
    ctx.db.add(ClaudeQuery(user_key=message.user_key, chat_key=message.chat_key))
    ctx.db.commit()
    logger.info("CLAUDE question by %s in %s (%d chars%s%s)", message.user_key, message.chat_key, len(question),
                ", new conversation" if new_conversation else "", "" if full_access else ", no PC access")
    return Reply(reaction=WAITING_REACTION, job=job_id)
