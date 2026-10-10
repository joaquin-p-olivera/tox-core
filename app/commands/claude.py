import logging
from datetime import timedelta

from sqlalchemy import select

from ..models import ClaudeQuery
from ..schemas import Reply
from ..services import claude_runner, jobs
from ..utils import utcnow
from .registry import CommandContext, command

logger = logging.getLogger("tox.claude")

WINDOW = timedelta(hours=1)
NEW_FLAGS = ("-n", "--nuevo")
WAITING_TEXT = "⏳ Generando respuesta..."


@command(
    "claude",
    description="(Admin) Le hace una pregunta o pedido a Claude (acceso completo a ~/dev); responde resumido",
    usage="claude [-n] <pregunta o pedido>",
    category="Administración",
    admin_only=True,
)
def ask_claude(ctx: CommandContext) -> str | Reply:
    """Answers at once with "Generando respuesta..." and a job id: the bot sends that, then polls the job and sends the
    answer when it is ready (a question can take minutes, longer than a chat request should wait)."""
    settings, message = ctx.settings, ctx.message
    first, _, rest = ctx.raw_args.strip().partition(" ")
    new_conversation = first.lower() in NEW_FLAGS
    question = (rest if new_conversation else ctx.raw_args).strip()
    if not question:
        return (f"Uso: {ctx.prefix}claude <pregunta o pedido>\n"
                f"{ctx.prefix}claude -n <pregunta>: empieza una conversación nueva (sin el contexto anterior).")
    if not claude_runner.workdir(settings).is_dir():
        return f"No existe la carpeta de trabajo ({settings.CLAUDE_WORKDIR})."

    since = utcnow() - WINDOW
    recent = list(ctx.db.scalars(select(ClaudeQuery.created_at).where(ClaudeQuery.created_at > since)
                                 .order_by(ClaudeQuery.created_at)))
    if len(recent) >= settings.CLAUDE_QUESTIONS_PER_HOUR:
        minutes = max(1, int((recent[0] + WINDOW - utcnow()).total_seconds() // 60) + 1)
        return (f"Se alcanzó el límite de {settings.CLAUDE_QUESTIONS_PER_HOUR} preguntas por hora. "
                f"Probá de nuevo en unos {minutes} min.")

    job_id = jobs.create(settings.CLAUDE_TIMEOUT_SECONDS + 300)
    try:
        claude_runner.start(
            message.chat_key, question, new_conversation, settings,
            on_done=lambda text: jobs.finish(job_id, [Reply(text=text)]),
        )
    except claude_runner.Busy:
        jobs.discard(job_id)
        return "Ya hay una consulta en curso. Esperá a que termine."
    ctx.db.add(ClaudeQuery(user_key=message.user_key, chat_key=message.chat_key))
    ctx.db.commit()
    logger.info("CLAUDE question by %s in %s (%d chars%s)", message.user_key, message.chat_key, len(question),
                ", new conversation" if new_conversation else "")
    return Reply(text=WAITING_TEXT, job=job_id)
