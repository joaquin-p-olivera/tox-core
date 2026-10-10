"""In-memory results of long-running commands (!claude). The command answers right away with a job id; the work
finishes in the background and its replies are kept here until the bot has fetched them (GET /api/v1/jobs/{id}).

Like media_cache, nothing is persisted: an API restart loses running jobs and the bot just reports it. A plain dict is
safe because the API is a single process.
"""

import threading
import time
import uuid
from dataclasses import dataclass

from ..schemas import Reply

_lock = threading.Lock()
_jobs: dict[str, "_Job"] = {}


@dataclass
class _Job:
    expires_at: float
    replies: list[Reply] | None = None  # None while running


def _prune(now: float) -> None:
    for key in [key for key, job in _jobs.items() if job.expires_at <= now]:
        del _jobs[key]


def create(ttl_seconds: float) -> str:
    now = time.monotonic()
    job_id = uuid.uuid4().hex
    with _lock:
        _prune(now)
        _jobs[job_id] = _Job(expires_at=now + ttl_seconds)
    return job_id


def finish(job_id: str, replies: list[Reply], keep_seconds: float = 300) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job is not None:
            job.replies = replies
            job.expires_at = time.monotonic() + keep_seconds


def discard(job_id: str) -> None:
    with _lock:
        _jobs.pop(job_id, None)


def get(job_id: str) -> tuple[bool, list[Reply]] | None:
    """(done, replies) or None if it never existed or expired."""
    now = time.monotonic()
    with _lock:
        _prune(now)
        job = _jobs.get(job_id)
        if job is None:
            return None
        return job.replies is not None, list(job.replies or [])


def clear() -> None:
    with _lock:
        _jobs.clear()
