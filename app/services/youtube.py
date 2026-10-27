"""!yt / !ytm: the video or the audio of a YouTube link, sent into the chat.

Nothing is kept on this machine. yt-dlp writes into a temp folder inside `YT_WORK_DIR` (`/dev/shm`, which is RAM,
so the disk is never touched), the bytes are read out of it and the folder is deleted in a `finally` before the
reply is even built; from there they live in media_cache only until the bot has downloaded them (a couple of
seconds). A video has to be written somewhere first because YouTube no longer serves a single stream with picture
and sound in it: yt-dlp downloads the two and ffmpeg puts them in one mp4, which it cannot do into a pipe.

What may be sent is bounded twice over, both before anything is downloaded: `YT_MAX_DURATION_SECONDS`, and
`YT_MAX_BYTES` over the formats yt-dlp lists (`pick_video` / `pick_audio`). A video short enough to be allowed can
still be far too big for WhatsApp -- eight minutes at 360p is around 24 MB -- so the quality is stepped down until
it fits, and what does not fit even then is refused with a message instead of failing halfway up an upload.

One download at a time (`_slot`): these go out over a home connection, and three people pasting links at once must
not take it. yt-dlp is in requirements.txt rather than the system's one on purpose: YouTube breaks old versions
every few months, and that way dependabot keeps it current.
"""

import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings

logger = logging.getLogger("tox.youtube")

# Only YouTube: this is not a general-purpose downloader, and the host is checked before yt-dlp is ever run.
LINK = re.compile(
    r"https?://(?:www\.|m\.|music\.)?(?:youtube\.com/(?:watch\?\S*?v=|shorts/|live/|embed/|v/)|youtu\.be/)"
    r"(?P<id>[\w-]{11})",
    re.IGNORECASE,
)
VIDEO_MIME = "video/mp4"
AUDIO_MIME = "audio/mp4"
MAX_HEIGHT = 720  # nobody needs 4K in a chat bubble, and it would never fit anyway
MIN_AUDIO_KBPS = 48  # under this, music sounds like a phone call
# Picture and sound are remuxed into one mp4 without being re-encoded, which this machine could not afford: only
# H.264 video and AAC audio go into an mp4 as they are (a VP9 or AV1 stream would have to be transcoded).
H264 = "avc1"
AAC = "mp4a"

_slot = threading.BoundedSemaphore(1)


class YoutubeError(Exception):
    """Something the chat is told as it is: every message here is written for the group to read."""


@dataclass(frozen=True)
class Media:
    data: bytes
    mimetype: str
    title: str
    duration: int
    quality: str  # "360p", "129 kbps": what was actually sent, which is not always what was asked for


def link_in(text: str) -> str | None:
    """The first YouTube link in a message, or None. What the command is given, and what `!yt` finds in the
    message it replies to."""
    match = LINK.search(text)
    return match.group(0) if match else None


def mmss(seconds: float) -> str:
    minutes, rest = divmod(int(seconds), 60)
    return f"{minutes}:{rest:02d}"


def _size(fmt: dict, duration: float) -> int:
    """How big this format is. yt-dlp gives an exact size for some and an estimate for others; for the rest the
    bitrate and the duration are what there is, and a format with no number at all is assumed to be huge, so it
    is never the one picked over a format whose size is known."""
    for key in ("filesize", "filesize_approx"):
        value = fmt.get(key)
        if value:
            return int(value)
    rate = fmt.get("tbr") or fmt.get("vbr") or fmt.get("abr")
    if rate and duration:
        return int(rate * 1000 / 8 * duration)
    return 1 << 40


def _audio_only(formats: list[dict]) -> list[dict]:
    return [f for f in formats
            if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")]


def pick_audio(formats: list[dict], duration: float, max_bytes: int) -> dict | None:
    """The best-sounding audio-only format that fits. AAC first: it is what WhatsApp plays everywhere."""
    pool = _audio_only(formats)
    aac = [f for f in pool if str(f.get("acodec") or "").startswith(AAC) or f.get("ext") == "m4a"]
    for candidates in (aac, pool):
        ordered = sorted(candidates, key=lambda f: -(f.get("abr") or f.get("tbr") or 0))
        for fmt in ordered:
            if _size(fmt, duration) <= max_bytes:
                return fmt
    return None


def pick_video(formats: list[dict], duration: float, max_bytes: int) -> tuple[dict, dict | None] | None:
    """The best-looking way to send this video inside `max_bytes`, as ``(video, audio)``. `audio` is None when the
    format already carries both (YouTube rarely serves those any more, and when it does there is nothing to merge).

    The height is stepped down -- 720, 480, 360, 240 -- until picture plus sound fit, so a long video arrives
    looking worse instead of not arriving. None when even the smallest one is over the limit."""
    together = [f for f in formats
                if f.get("acodec") not in (None, "none") and f.get("vcodec") not in (None, "none")
                and f.get("ext") == "mp4" and (f.get("height") or 0) <= MAX_HEIGHT]
    pictures = [f for f in formats
                if str(f.get("vcodec") or "").startswith(H264) and f.get("acodec") in (None, "none")
                and 0 < (f.get("height") or 0) <= MAX_HEIGHT]
    # The smallest sound that is still worth listening to: a video is mostly picture, and every kbps here is one
    # the picture does not get.
    sounds = sorted((f for f in _audio_only(formats)
                     if str(f.get("acodec") or "").startswith(AAC) or f.get("ext") == "m4a"),
                    key=lambda f: _size(f, duration))
    sound = next((f for f in sounds if (f.get("abr") or f.get("tbr") or 0) >= MIN_AUDIO_KBPS), None) or \
        (sounds[0] if sounds else None)

    best: tuple[int, dict, dict | None] | None = None
    for fmt in together:
        if _size(fmt, duration) <= max_bytes:
            height = fmt.get("height") or 0
            if best is None or height > best[0]:
                best = (height, fmt, None)
    if sound is not None:
        room = max_bytes - _size(sound, duration)
        for fmt in pictures:
            if _size(fmt, duration) <= room:
                height = fmt.get("height") or 0
                # A tie goes to the format that needs no merging at all (`>` and not `>=`, `together` came first).
                if best is None or height > best[0]:
                    best = (height, fmt, sound)
    if best is None:
        return None
    return best[1], best[2]


def _run(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    """yt-dlp out of this venv (requirements.txt), never whatever the system has lying around."""
    return subprocess.run([sys.executable, "-m", "yt_dlp", "--no-playlist", "--no-warnings", *args],
                          capture_output=True, text=True, timeout=timeout)


def _refusal(stderr: str) -> str:
    """yt-dlp's complaint, turned into something the chat can do something about."""
    lowered = stderr.lower()
    if "private video" in lowered:
        return "Ese video es privado."
    if "age" in lowered and "confirm" in lowered or "sign in to confirm your age" in lowered:
        return "Ese video pide confirmar la edad, así que no lo puedo bajar."
    if "sign in to confirm" in lowered or "bot" in lowered and "cookies" in lowered:
        return "YouTube me está pidiendo iniciar sesión para este video."
    if "video unavailable" in lowered or "not available" in lowered:
        return "Ese video no está disponible."
    if "members-only" in lowered or "join this channel" in lowered:
        return "Ese video es solo para miembros del canal."
    return "No pude bajar ese video."


def _metadata(url: str, timeout: int) -> dict:
    done = _run(["-J", url], timeout)
    if done.returncode != 0:
        logger.warning("yt-dlp could not read %s: %s", url, done.stderr.strip()[:400])
        raise YoutubeError(_refusal(done.stderr))
    try:
        return json.loads(done.stdout)
    except json.JSONDecodeError as error:
        raise YoutubeError("No pude leer la información de ese video.") from error


def _work_dir(settings: Settings) -> Path | None:
    """Where the download is written: RAM if it is there, which keeps this off an eMMC that has little room and
    wears out. None lets tempfile decide (the system's temp folder)."""
    configured = settings.YT_WORK_DIR.strip()
    if not configured:
        return None
    path = Path(configured)
    return path if path.is_dir() else None


def fetch(url: str, settings: Settings, *, audio_only: bool) -> Media:
    """Downloads one YouTube link and hands over the bytes. Raises YoutubeError with the message the chat gets."""
    if link_in(url) is None:
        raise YoutubeError("Ese link no es de YouTube.")
    if not _slot.acquire(blocking=False):
        raise YoutubeError("Ya estoy bajando otro. Probá en un momento.")
    try:
        return _fetch(url, settings, audio_only=audio_only)
    finally:
        _slot.release()


def _fetch(url: str, settings: Settings, *, audio_only: bool) -> Media:
    info = _metadata(url, settings.YT_TIMEOUT_SECONDS)
    title = (info.get("title") or "").strip() or "sin título"
    if info.get("is_live"):
        raise YoutubeError("Eso es un vivo, no un video.")
    duration = info.get("duration")
    if not duration:
        raise YoutubeError("No sé cuánto dura ese video, así que no lo bajo.")
    limit = settings.YT_MAX_DURATION_SECONDS
    if duration > limit:
        raise YoutubeError(f"Ese video dura {mmss(duration)} y el máximo es {mmss(limit)}.")

    formats = info.get("formats") or []
    max_bytes = settings.YT_MAX_BYTES
    if audio_only:
        chosen = pick_audio(formats, duration, max_bytes)
        if chosen is None:
            raise YoutubeError("El audio de ese video no entra en un mensaje.")
        selector, quality, mimetype = chosen["format_id"], f"{int(chosen.get('abr') or 0)} kbps", AUDIO_MIME
        extra = ["--extract-audio", "--audio-format", "m4a"] if chosen.get("ext") != "m4a" else []
    else:
        picked = pick_video(formats, duration, max_bytes)
        if picked is None:
            raise YoutubeError("Ese video no entra en un mensaje ni en la calidad más baja.")
        video, sound = picked
        selector = f"{video['format_id']}+{sound['format_id']}" if sound else video["format_id"]
        quality = f"{video.get('height') or '?'}p"
        mimetype = VIDEO_MIME
        extra = ["--merge-output-format", "mp4"]

    folder = Path(tempfile.mkdtemp(prefix="tox-yt-", dir=_work_dir(settings)))
    try:
        args = ["-f", selector, *extra, "--no-progress", "--no-part", "--retries", "2", "--socket-timeout", "20",
                "--max-filesize", str(max_bytes), "-o", str(folder / "media.%(ext)s"), url]
        if settings.FFMPEG_PATH.strip():
            args = ["--ffmpeg-location", settings.FFMPEG_PATH.strip(), *args]
        done = _run(args, settings.YT_TIMEOUT_SECONDS)
        files = sorted(folder.iterdir())
        if done.returncode != 0 or not files:
            logger.warning("yt-dlp could not download %s: %s", url, done.stderr.strip()[:400])
            raise YoutubeError(_refusal(done.stderr))
        data = files[0].read_bytes()
    finally:
        shutil.rmtree(folder, ignore_errors=True)

    if len(data) > max_bytes:
        # The estimate was short: better said here than by a failed upload.
        raise YoutubeError(f"Quedó en {len(data) / 1_000_000:.1f} MB y no lo puedo mandar.")
    logger.info("YT %s: %r %s %s, %.1f MB", "audio" if audio_only else "video", title[:60],
                mmss(duration), quality, len(data) / 1_000_000)
    return Media(data=data, mimetype=mimetype, title=title, duration=int(duration), quality=quality)
