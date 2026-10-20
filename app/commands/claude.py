import logging
import random
import re
from datetime import datetime, timedelta

from sqlalchemy import select

from ..models import ClaudeQuery
from ..schemas import Reply
from ..services import claude_context, claude_runner, jobs, media_cache, tts_generator
from ..utils import utcnow
from .registry import CommandContext, command

logger = logging.getLogger("tox.claude")

WINDOW = timedelta(hours=1)
NEW_FLAGS = ("-n", "--nuevo")
WAITING_REACTION = "⏳"  # on the command message while Claude works (no placeholder message)
CLEAR_REACTION = ""  # when the answer arrives the ⏳ is taken off, not replaced: the answer itself says it is done
FAILED_REACTION = "⚠️"  # ... except when there is no answer (the runner starts every error message with it)


def _finished(text: str, context: claude_context.Context | None = None, speak_with=None) -> list[Reply]:
    """The answer first (so a failing reaction can never lose it), then what becomes of the ⏳: it is removed, or it
    turns into ⚠️ when there is no answer. With a `context`, the @Names in the answer become real mentions. With
    `speak_with` (the settings), the answer is sent as a voice note instead; see `_spoken`."""
    failed = text.startswith(FAILED_REACTION)
    mentions = []
    if context is not None and not failed:
        text, mentions = claude_context.tag_mentions(context, text)
    answer = Reply(text=text, mentions=mentions)
    if speak_with is not None and not failed and not mentions:
        audio = _spoken(text, speak_with)
        if audio is not None:
            answer = Reply(audio=audio)
    return [answer, Reply(reaction=FAILED_REACTION if failed else CLEAR_REACTION)]


def _spoken(text: str, settings) -> str | None:
    """The answer as a voice note (the id of the cached audio), or None to send it as text after all: too long to
    speak, or the voice service failed. An answer that tags somebody is never spoken: the tag would be lost."""
    try:
        audio = tts_generator.synthesize(text, settings.CLAUDE_AUDIO_VOICE, settings.TTS_TIMEOUT_SECONDS)
    except (ValueError, tts_generator.TtsError) as error:  # TextTooLong is a ValueError
        logger.info("CLAUDE answer sent as text, it could not be spoken: %s", error)
        return None
    return media_cache.store(audio, tts_generator.MIMETYPE, settings.MEDIA_CACHE_SECONDS)


def _limit_message(limit: int, oldest, what: str) -> str:
    """`oldest` is when the oldest question still inside the window was made: that is when a place frees up."""
    minutes = max(1, int((oldest + WINDOW - utcnow()).total_seconds() // 60) + 1)
    return f"Se alcanzó el límite de {limit} {what}. Probá de nuevo en unos {minutes} min."


def _limit_reached(ctx: CommandContext, full_access: bool, user_key: str) -> str | None:
    """What to answer when there is no room for one more question this hour, or None when there is. `user_key` is who
    the question is charged to (the bot itself, for the remarks it makes on its own)."""
    rows = ctx.db.execute(select(ClaudeQuery.user_key, ClaudeQuery.created_at)
                          .where(ClaudeQuery.created_at > utcnow() - WINDOW).order_by(ClaudeQuery.created_at)).all()
    admins = ctx.settings.admin_user_keys
    # Admins share one pool; the others share another one, so a busy group can't use up the admins' questions
    pool = [at for user, at in rows if (user in admins) == full_access]
    pool_limit = ctx.settings.CLAUDE_QUESTIONS_PER_HOUR if full_access else ctx.settings.CLAUDE_PUBLIC_QUESTIONS_PER_HOUR
    if len(pool) >= pool_limit:
        return _limit_message(pool_limit, pool[0], "preguntas por hora")
    if not full_access:
        mine = [at for user, at in rows if user == user_key]
        per_person = ctx.settings.CLAUDE_PUBLIC_QUESTIONS_PER_PERSON_PER_HOUR
        if len(mine) >= per_person:
            return _limit_message(per_person, mine[0], "preguntas por hora por persona")
    return None


@command(
    "claude",
    description="Le hace una pregunta a Claude (IA); responde resumido. Los admins además pueden pedirle cosas sobre la PC",
    usage="claude [-n] <pregunta o pedido>   (o !c <pregunta>, o respondiendo a un mensaje de Tox)",
    category="Utilidades",
)
def ask_claude(ctx: CommandContext) -> str | Reply:
    """Answers at once with a ⏳ reaction to the command and a job id: the bot reacts, then polls the job and sends the
    answer when it is ready (a question can take minutes, longer than a chat request should wait).

    Who asks decides what Claude can do: admins (ctx.is_admin) get the full-access mode in the work directory; anybody
    else a plain chat with no access to this PC (claude_runner.execute_public), with a per-person limit on top.

    It is also what answers a reply to one of the bot's own messages, with no command typed (ctx.implicit): the whole
    message is then the question."""
    settings, message = ctx.settings, ctx.message
    full_access = ctx.is_admin
    first, _, rest = ctx.raw_args.strip().partition(" ")
    # A reply to the bot (ctx.implicit) is plain chat: a "-n" in it is part of what was written, not a flag.
    new_conversation = full_access and not ctx.implicit and first.lower() in NEW_FLAGS
    question = (rest if new_conversation else ctx.raw_args).strip()
    if not question:
        usage = f"Uso: {ctx.prefix}claude <pregunta>"
        if full_access:
            usage += (f"\n{ctx.prefix}claude -n <pregunta>: empieza una conversación nueva (sin el contexto anterior)."
                      f"\nPodés pedirle también cosas sobre la PC y los proyectos.")
        return usage
    if full_access and not claude_runner.workdir(settings).is_dir():
        return f"No existe la carpeta de trabajo ({settings.CLAUDE_WORKDIR})."

    reached = _limit_reached(ctx, full_access, message.user_key)
    if reached is not None:
        return reached

    # Names of the group and the replied-to message always; the latest messages only for people without access to the PC
    context = claude_context.gather(ctx.db, settings, message, with_history=not full_access)
    # Decided before asking, not after: an answer that is going to be heard is asked for short and without tags
    speak = random.random() < settings.CLAUDE_AUDIO_CHANCE
    job_id = jobs.create(settings.CLAUDE_TIMEOUT_SECONDS + 300)

    def done(text: str) -> None:
        if not text.startswith(FAILED_REACTION):
            # (in the history, when it is on). A reply to the bot is not a command, so the bot already saved it.
            claude_context.remember(settings, context, message, text, job_id, save_question=not ctx.implicit)
        jobs.finish(job_id, _finished(text, context, settings if speak else None))

    try:
        claude_runner.start(
            message.chat_key, context.prompt(question, speak=speak), new_conversation, settings,
            on_done=done, full_access=full_access,
        )
    except claude_runner.Busy:
        jobs.discard(job_id)
        return claude_runner.BUSY_MESSAGE
    ctx.db.add(ClaudeQuery(user_key=message.user_key, chat_key=message.chat_key))
    ctx.db.commit()
    _spoke_in[message.chat_key] = utcnow()  # it just took part: no remark of its own for a while (see `interject`)
    logger.info("CLAUDE question by %s in %s (%d chars%s%s%s%s)", message.user_key, message.chat_key, len(question),
                ", new conversation" if new_conversation else "", "" if full_access else ", no PC access",
                ", spoken" if speak else "", ", replying to the bot" if ctx.implicit else "")
    return Reply(reaction=WAITING_REACTION, job=job_id)


# --- Butting in: now and then Tox says something nobody asked for (CLAUDE_INTERJECT_CHANCE) ---

PASS_ANSWER = re.compile(r"^[«\"\'(]*paso[.!…»\"\')]*$", re.IGNORECASE)  # Claude's way of saying "not worth it"
SIGNATURE = "\n> Tox"  # marks a remark nobody asked for, so it is never taken for an answer to somebody
_spoke_in: dict[str, datetime] = {}  # chat_key -> when Claude last said something there, asked or not


def interject(ctx: CommandContext) -> Reply | None:
    """A remark nobody asked for, on the message that happened to get Claude talking: it reads the conversation of the
    group and sends one message of its own. Always text (never a voice note) and always the no-access mode, whoever
    wrote that message: nobody asked for anything, so it must not run commands on this PC.

    Everything about it is quiet: it is dropped without a word (no reaction, no "I am busy", no limit message) when the
    dice says no, when it already spoke in this chat within CLAUDE_INTERJECT_COOLDOWN_MINUTES, when there is no saved
    conversation to read, or when Claude itself answers that there is nothing worth saying."""
    settings, message = ctx.settings, ctx.message
    if message.personal or not message.is_group or random.random() >= settings.CLAUDE_INTERJECT_CHANCE:
        return None
    spoke = _spoke_in.get(message.chat_key)
    if spoke is not None and utcnow() - spoke < timedelta(minutes=settings.CLAUDE_INTERJECT_COOLDOWN_MINUTES):
        return None
    user_key = f"{message.platform}:{claude_context.BOT_ID}"  # what it says on its own is charged to the bot itself
    if _limit_reached(ctx, full_access=False, user_key=user_key) is not None:
        return None
    context = claude_context.gather(ctx.db, settings, message, with_history=True)
    if context.history_key is None or not context.transcript:
        return None  # the history of this chat is off: there is nothing to read, and nobody asked to be read
    job_id = jobs.create(settings.CLAUDE_TIMEOUT_SECONDS + 300)

    def done(text: str) -> None:
        if text.startswith(FAILED_REACTION) or not text.strip() or PASS_ANSWER.match(text.strip()):
            jobs.finish(job_id, [])  # nothing is sent to the chat, and nobody is told why
            return
        claude_context.remember(settings, context, message, text, job_id, save_question=False)
        tagged, mentions = claude_context.tag_mentions(context, text)
        jobs.finish(job_id, [Reply(text=tagged + SIGNATURE, mentions=mentions)])

    try:
        claude_runner.start(message.chat_key, context.interjection(), False, settings, on_done=done, full_access=False)
    except claude_runner.Busy:
        jobs.discard(job_id)
        return None
    ctx.db.add(ClaudeQuery(user_key=user_key, chat_key=message.chat_key))
    ctx.db.commit()
    _spoke_in[message.chat_key] = utcnow()
    logger.info("CLAUDE butting in on %s, after a message of %s", message.chat_key, message.user_key)
    return Reply(job=job_id)  # no reaction: the message it answers is not a command, and nobody is waiting
