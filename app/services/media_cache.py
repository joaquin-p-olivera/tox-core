"""Short-lived, in-memory storage for generated media (stickers, text-to-speech audio): a command
renders the bytes once, stores them here, and returns the id in the reply; the bot fetches it right
after over GET /api/v1/stickers/{id} (stickers) or GET /api/v1/audios/{id} (tts, alongside the preset
audio files in AUDIOS_DIR).

Not meant to persist anything — entries expire quickly (MEDIA_CACHE_SECONDS) since the bot downloads
within seconds. A plain dict is safe here because the API runs as a single process (one uvicorn worker).
"""

import threading
import time
import uuid
from dataclasses import dataclass

_lock = threading.Lock()
_store: dict[str, "_Entry"] = {}


@dataclass(frozen=True)
class _Entry:
    data: bytes
    mimetype: str
    expires_at: float


def _prune(now: float) -> None:
    for key in [key for key, entry in _store.items() if entry.expires_at <= now]:
        del _store[key]


def store(data: bytes, mimetype: str, ttl_seconds: float) -> str:
    """Saves ``data`` and returns an id to fetch it with. Also prunes anything already expired."""
    now = time.monotonic()
    key = uuid.uuid4().hex
    with _lock:
        _prune(now)
        _store[key] = _Entry(data, mimetype, now + ttl_seconds)
    return key


def get(key: str) -> tuple[bytes, str] | None:
    """Returns ``(data, mimetype)`` for ``key``, or None if it never existed or already expired."""
    now = time.monotonic()
    with _lock:
        _prune(now)
        entry = _store.get(key)
        return (entry.data, entry.mimetype) if entry else None


def clear() -> None:
    """For tests: drops every cached entry."""
    with _lock:
        _store.clear()
