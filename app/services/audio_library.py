"""The sound clips !m can send. Files are read from disk on every call, so adding one needs no restart."""

from pathlib import Path

# extension -> mimetype. Only Ogg/Opus shows up as a voice note on WhatsApp; the rest are plain audio files.
MIMETYPES = {
    ".ogg": "audio/ogg; codecs=opus",
    ".opus": "audio/ogg; codecs=opus",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".aac": "audio/aac",
}
MAX_BYTES = 16 * 1024 * 1024  # WhatsApp's limit for audio


def list_audios(directory: str) -> list[Path]:
    root = Path(directory)
    if not root.is_dir():
        return []
    return sorted(
        path for path in root.iterdir()
        if path.is_file()
        and not path.name.startswith(".")
        and path.suffix.lower() in MIMETYPES
        and path.stat().st_size <= MAX_BYTES
    )


def find_audio(directory: str, name: str) -> Path | None:
    """Looks the name up in the listing instead of joining paths, so "../x" can never escape the folder."""
    return next((path for path in list_audios(directory) if path.name == name), None)


def mimetype_for(path: Path) -> str:
    return MIMETYPES[path.suffix.lower()]
