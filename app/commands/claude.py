import logging
import random
import re
from datetime import datetime, timedelta

from sqlalchemy import select

from ..models import ClaudeQuery
from ..schemas import Reply
from ..services import (chat_config, chat_sources, claude_context, claude_runner, jobs, media_cache,
                        tact, trivia_service, tts_generator)
from ..utils import utcnow
from .flags import normalize_flag
from .registry import CommandContext, command

logger = logging.getLogger("tox.claude")

WINDOW = timedelta(hours=1)
NEW_FLAGS = ("-n", "--nuevo")
TEXT_FLAGS = ("-t", "--texto")
# Written at the head of a reply to one of the bot's own messages: that reply is for the people in the chat,
# not for Tox, and nothing is answered. Only there -- with a command typed, the question was asked on purpose.
QUIET_FLAGS = ("-no", "--no")
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


def _knows_who_everybody_is(ctx: CommandContext) -> bool:
    """Whether what the owner wrote about each member goes into the prompt this time (CLAUDE_PROFILE_PEOPLE_CHANCE,
    which a chat may set for itself with !config). Most of the time it does not: with everybody's job, city and team
    at hand every remark ends up being the same joke about them, and the way to stop that is not to hand the material
    over. What the chat itself is about always goes."""
    return random.random() < _setting(ctx, "CLAUDE_PROFILE_PEOPLE_CHANCE")


def _setting(ctx: CommandContext, name: str):
    """What `name` is worth in the chat this message came from (services/chat_config.py)."""
    return chat_config.value(ctx.db, ctx.settings, ctx.message.chat_key, name)


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


def _leading_flags(ctx: CommandContext, full_access: bool) -> tuple[bool, bool, bool, str]:
    """The flags written before the question, and what is left to ask: `-n` starts the conversation from scratch
    (admins only, see `ask_claude`), `-t` asks for the answer as text, never as a voice note, and `-no` says this
    is not for Tox at all. They may come together ("!c -n -t ..."), and anything else stops the reading: a "-n"
    typed by somebody without access, or a question that simply starts with a dash, is the question itself.

    A `-n` in a reply to the bot (ctx.implicit) is part of what was written too: nobody types a flag when they are
    only answering a message. `-t` is taken there all the same -- with no command written, it is the only way to
    ask for text. `-no` is the other way round: it only means anything in a reply, since somebody who wrote the
    command wants an answer, and there "-no sé" stays the question it looks like."""
    new_conversation = as_text = quiet = False
    question = ctx.raw_args.strip()
    while question.startswith("-"):
        flag, _, rest = question.partition(" ")
        if normalize_flag(flag) in NEW_FLAGS and full_access and not ctx.implicit:
            new_conversation = True
        elif normalize_flag(flag) in TEXT_FLAGS:
            as_text = True
        elif normalize_flag(flag) in QUIET_FLAGS and ctx.implicit:
            quiet = True
        else:
            break
        question = rest.strip()
    return new_conversation, as_text, quiet, question


@command(
    "claude",
    description="Le hace una pregunta a Claude (IA); responde resumido. Los admins además pueden pedirle cosas sobre "
                "la PC. Si le respondés un mensaje y empezás con -no, no contesta: ese mensaje no era para él",
    usage="claude [-n] [-t] <pregunta o pedido>   (o !c <pregunta>, o respondiendo a un mensaje de Tox)",
    category="Utilidades",
)
def ask_claude(ctx: CommandContext) -> str | Reply | None:
    """Answers at once with a ⏳ reaction to the command and a job id: the bot reacts, then polls the job and sends the
    answer when it is ready (a question can take minutes, longer than a chat request should wait).

    Who asks decides what Claude can do: admins (ctx.is_admin) get the full-access mode in the work directory; anybody
    else a plain chat with no access to this PC (claude_runner.execute_public), with a per-person limit on top.

    It is also what answers a reply to one of the bot's own messages, with no command typed (ctx.implicit): the whole
    message is then the question, unless it starts with `-no`, which is how somebody says that reply is for the
    people in the chat and not for Tox."""
    settings, message = ctx.settings, ctx.message
    full_access = ctx.is_admin
    new_conversation, as_text, quiet, question = _leading_flags(ctx, full_access)
    if quiet:
        return None  # "-no ...": the reply is for the chat, not for Tox. Nothing is sent and nobody is told
    if not question:
        usage = (f"Uso: {ctx.prefix}claude <pregunta>"
                 f"\n{ctx.prefix}claude -t <pregunta>: la respuesta como texto, nunca como nota de voz.")
        if full_access:
            usage += (f"\n{ctx.prefix}claude -n <pregunta>: empieza una conversación nueva (sin el contexto anterior)."
                      f"\nPodés pedirle también cosas sobre la PC y los proyectos.")
        return usage
    # A question about what was said in another chat is a question about a conversation, not about this PC, so it is
    # answered in the no-access mode even when an admin asks it. That is what lets an admin have the context at all:
    # the full-access mode never receives anything written by other people (see services/chat_sources.py), because it
    # runs the CLI with every permission on, and text from a chat is text anybody could have written.
    if full_access and chat_sources.asked_for(ctx.db, message.chat_key, question):
        full_access = False
        logger.info("CLAUDE answering without PC access: the question asks about another chat")

    if full_access and not claude_runner.workdir(settings).is_dir():
        return f"No existe la carpeta de trabajo ({settings.CLAUDE_WORKDIR})."

    reached = _limit_reached(ctx, full_access, message.user_key)
    if reached is not None:
        return reached

    # Names of the group and the replied-to message always; the latest messages only for people without access to the PC
    context = claude_context.gather(ctx.db, settings, message, with_history=not full_access,
                                    # Nobody typed anything, so this is chat, not a request: see `_knows_who_everybody_is`
                                    with_profile_people=not ctx.implicit or _knows_who_everybody_is(ctx),
                                    # Another chat's conversation only when this question asked about it
                                    question=question)
    # Decided before asking, not after: an answer that is going to be heard is asked for short and without tags
    speak = not as_text and random.random() < _setting(ctx, "CLAUDE_AUDIO_CHANCE")
    job_id = jobs.create(settings.CLAUDE_TIMEOUT_SECONDS + 300)

    def done(text: str) -> None:
        if not text.startswith(FAILED_REACTION):
            # (in the history, when it is on). A reply to the bot is not a command, so the bot already saved it.
            claude_context.remember(settings, context, message, text, job_id, save_question=not ctx.implicit)
        jobs.finish(job_id, _finished(text, context, settings if speak else None))

    try:
        claude_runner.start(
            message.chat_key, context.prompt(question, speak=speak), new_conversation, settings,
            on_done=done, full_access=full_access, tone=_setting(ctx, "CLAUDE_TONE"),
        )
    except claude_runner.Busy:
        jobs.discard(job_id)
        return claude_runner.BUSY_MESSAGE
    ctx.db.add(ClaudeQuery(user_key=message.user_key, chat_key=message.chat_key))
    ctx.db.commit()
    _spoke_in[message.chat_key] = utcnow()  # it just took part: no remark of its own for a while (see `interject`)
    logger.info("CLAUDE question by %s in %s (%d chars%s%s%s%s%s)", message.user_key, message.chat_key, len(question),
                ", new conversation" if new_conversation else "", "" if full_access else ", no PC access",
                ", spoken" if speak else "", ", text asked" if as_text else "",
                ", replying to the bot" if ctx.implicit else "")
    return Reply(reaction=WAITING_REACTION, job=job_id)


# --- Butting in: now and then Tox says something nobody asked for (CLAUDE_INTERJECT_CHANCE) ---

PASS_ANSWER = re.compile(r"^[«\"\'(]*paso[.!…»\"\')]*$", re.IGNORECASE)  # Claude's way of saying "not worth it"
SIGNATURE = "\n> Tox"  # marks a remark nobody asked for, so it is never taken for an answer to somebody
_spoke_in: dict[str, datetime] = {}  # chat_key -> when Claude last said something there, asked or not


def _tact_says_no(ctx: CommandContext, context: claude_context.Context) -> str | None:
    """Why Tox must not say anything of its own right now (services/tact.py), or None when it may. Read off the
    conversation that was gathered anyway, and before Claude is called: a remark that should not happen costs
    nothing but this."""
    settings, message = ctx.settings, ctx.message
    return tact.reason_to_stay_quiet(tact.Situation(
        chat_key=message.chat_key,
        now_utc=utcnow(),
        timezone_name=settings.TIMEZONE,
        from_hour=settings.CLAUDE_INTERJECT_FROM_HOUR,
        until_hour=settings.CLAUDE_INTERJECT_UNTIL_HOUR,
        give_up_after=settings.CLAUDE_INTERJECT_GIVE_UP_AFTER,
        max_share=settings.CLAUDE_INTERJECT_MAX_SHARE,
        bot_id=claude_context.BOT_ID,
        recent=context.recent,
        game_open=trivia_service.round_is_open(ctx.db, message.chat_key),
    ))


def interject(ctx: CommandContext) -> Reply | None:
    """A remark nobody asked for, on the message that happened to get Claude talking: it reads the conversation of the
    group and sends one message of its own. Always text (never a voice note) and always the no-access mode, whoever
    wrote that message: nobody asked for anything, so it must not run commands on this PC.

    Everything about it is quiet: it is dropped without a word (no reaction, no "I am busy", no limit message) when the
    dice says no, when it already spoke in this chat within CLAUDE_INTERJECT_COOLDOWN_MINUTES, when there is no saved
    conversation to read, or when Claude itself answers that there is nothing worth saying."""
    settings, message = ctx.settings, ctx.message
    if message.personal or not message.is_group or random.random() >= _setting(ctx, "CLAUDE_INTERJECT_CHANCE"):
        return None
    spoke = _spoke_in.get(message.chat_key)
    if spoke is not None and utcnow() - spoke < timedelta(minutes=_setting(ctx, "CLAUDE_INTERJECT_COOLDOWN_MINUTES")):
        return None
    user_key = f"{message.platform}:{claude_context.BOT_ID}"  # what it says on its own is charged to the bot itself
    if _limit_reached(ctx, full_access=False, user_key=user_key) is not None:
        return None
    context = claude_context.gather(ctx.db, settings, message, with_history=True,
                                    with_profile_people=_knows_who_everybody_is(ctx))
    if context.history_key is None or not context.transcript:
        return None  # the history of this chat is off: there is nothing to read, and nobody asked to be read
    quiet = _tact_says_no(ctx, context)
    if quiet is not None:
        logger.info("CLAUDE not butting in on %s: %s", message.chat_key, quiet)
        return None
    job_id = jobs.create(settings.CLAUDE_TIMEOUT_SECONDS + 300)

    def done(text: str) -> None:
        if text.startswith(FAILED_REACTION) or not text.strip() or PASS_ANSWER.match(text.strip()):
            jobs.finish(job_id, [])  # nothing is sent to the chat, and nobody is told why
            return
        claude_context.remember(settings, context, message, text, job_id, save_question=False)
        tagged, mentions = claude_context.tag_mentions(context, text)
        jobs.finish(job_id, [Reply(text=tagged + SIGNATURE, mentions=mentions)])

    try:
        claude_runner.start(message.chat_key, context.interjection(), False, settings, on_done=done,
                            full_access=False, tone=_setting(ctx, "CLAUDE_TONE"))
    except claude_runner.Busy:
        jobs.discard(job_id)
        return None
    ctx.db.add(ClaudeQuery(user_key=user_key, chat_key=message.chat_key))
    ctx.db.commit()
    _spoke_in[message.chat_key] = utcnow()
    tact.remarked(message.chat_key, utcnow())  # whether it was worth saying is decided by what happens next
    logger.info("CLAUDE butting in on %s, after a message of %s", message.chat_key, message.user_key)
    return Reply(job=job_id)  # no reaction: the message it answers is not a command, and nobody is waiting
