"""`!yt <link>` sends the video of a YouTube link, `!ytm <link>` the audio.

The download takes seconds, not milliseconds, so both answer with a ⏳ and a job (the same way !claude does) and the
file is sent when it is there. The link can also be in the message being replied to, which is how it usually
arrives: somebody pastes it, somebody else asks for it.
"""

import logging
import threading

from ..schemas import Reply
from ..services import jobs, media_cache, youtube
from .registry import CommandContext, command

logger = logging.getLogger("tox.youtube")

CATEGORY = "Diversión"
WAITING_REACTION = "⏳"
FAILED_REACTION = "⚠️"


def _link(ctx: CommandContext) -> str | None:
    """The link the command was given, or the one in the message it replies to."""
    quoted = ctx.message.quoted.text if ctx.message.quoted else ""
    return youtube.link_in(ctx.raw_args) or youtube.link_in(quoted)


def _send(ctx: CommandContext, *, audio_only: bool) -> Reply | str:
    link = _link(ctx)
    if link is None:
        return f"Pasame un link de YouTube: {ctx.prefix}{ctx.invoked_as} <link>"

    settings = ctx.settings
    job_id = jobs.create(settings.YT_TIMEOUT_SECONDS + 120)

    def work() -> None:
        try:
            media = youtube.fetch(link, settings, audio_only=audio_only)
        except youtube.YoutubeError as error:
            jobs.finish(job_id, [Reply(reaction=FAILED_REACTION), Reply(text=str(error))])
            return
        except Exception:
            logger.exception("Could not fetch %s", link)
            jobs.finish(job_id, [Reply(reaction=FAILED_REACTION), Reply(text="Algo salió mal bajando ese video.")])
            return
        media_id = media_cache.store(media.data, media.mimetype, settings.MEDIA_CACHE_SECONDS)
        caption = f"🎬 {media.title} ({youtube.mmss(media.duration)} · {media.quality})"
        sent = Reply(audio=media_id) if audio_only else Reply(video=media_id)
        jobs.finish(job_id, [Reply(reaction=""), sent, Reply(text=caption)])

    threading.Thread(target=work, name="youtube", daemon=True).start()
    return Reply(reaction=WAITING_REACTION, job=job_id)


@command(
    "yt",
    description="Manda el video de un link de YouTube",
    usage="yt <link>   (o respondiendo a un mensaje con el link)",
    category=CATEGORY,
)
def video(ctx: CommandContext) -> Reply | str:
    return _send(ctx, audio_only=False)


@command(
    "ytm",
    description="Manda solo el audio de un link de YouTube",
    usage="ytm <link>   (o respondiendo a un mensaje con el link)",
    category=CATEGORY,
)
def audio(ctx: CommandContext) -> Reply | str:
    return _send(ctx, audio_only=True)
