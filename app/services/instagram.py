"""Publishes the GTA VI countdown card as an Instagram story (services/gta_countdown.py).

Instagram never receives the bytes: its publishing API downloads the picture from a public URL, so the card is parked
on imgbb for a few minutes (IMGBB_API_KEY, INSTAGRAM_IMAGE_TTL_SECONDS) and the upload itself asks for it to be
deleted again. Then two calls, the same shape as every other content there: a container is created
(`media_type=STORIES`) and then published.

Requirements, none of which this module can check for you: the account has to be a professional BUSINESS one (a
creator account cannot publish stories), reached with a token from the app dashboard, which lasts 60 days.

Everything here is best-effort and says so in its return value: the countdown's own message to the chats must go out
whether or not Instagram cooperates, so no failure raises. The token is never logged, not even partially.
"""

import logging
import time

import httpx

from ..config import Settings

logger = logging.getLogger("tox.instagram")

GRAPH = "https://graph.instagram.com/v23.0"
IMGBB = "https://api.imgbb.com/1/upload"
MEDIA_TYPE = "STORIES"
# A container is not publishable the moment it is created: Instagram has to fetch the picture first, and publishing
# too early is refused with "Media ID is not available" (code 9007). So its status is polled until it is FINISHED.
READY_POLL_SECONDS = 2
READY_MAX_SECONDS = 60


def configured(settings: Settings) -> bool:
    return bool(settings.INSTAGRAM_ENABLED and settings.INSTAGRAM_USER_ID
                and settings.INSTAGRAM_ACCESS_TOKEN and settings.IMGBB_API_KEY)


def _park(client: httpx.Client, settings: Settings, jpeg: bytes) -> str | None:
    """Puts the picture somewhere Instagram can reach it, and returns that URL. The expiration is asked of imgbb
    itself, so nothing is left behind even if this process dies right after."""
    response = client.post(
        IMGBB,
        params={"key": settings.IMGBB_API_KEY, "expiration": settings.INSTAGRAM_IMAGE_TTL_SECONDS},
        files={"image": ("story.jpg", jpeg, "image/jpeg")},
    )
    if response.status_code != 200:
        logger.error("imgbb refused the upload: HTTP %s %s", response.status_code, response.text[:200])
        return None
    url = response.json().get("data", {}).get("url")
    if not url:
        logger.error("imgbb answered without a url: %s", response.text[:200])
    return url


def _graph(client: httpx.Client, settings: Settings, path: str, data: dict) -> dict | None:
    """One call to Instagram. The token travels in the header, so it never ends up in a URL, a log or an exception."""
    response = client.post(f"{GRAPH}/{settings.INSTAGRAM_USER_ID}/{path}", data=data,
                           headers={"Authorization": f"Bearer {settings.INSTAGRAM_ACCESS_TOKEN}"})
    body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    if response.status_code != 200 or "error" in body:
        error = body.get("error", {})
        logger.error("Instagram refused %s: HTTP %s, %s (code %s)", path, response.status_code,
                     error.get("message", response.text[:200]), error.get("code"))
        return None
    return body


def _wait_until_ready(client: httpx.Client, settings: Settings, container_id: str) -> bool:
    """Whether the container became publishable before READY_MAX_SECONDS ran out."""
    deadline = time.monotonic() + READY_MAX_SECONDS
    while time.monotonic() < deadline:
        response = client.get(f"{GRAPH}/{container_id}", params={"fields": "status_code,status"},
                              headers={"Authorization": f"Bearer {settings.INSTAGRAM_ACCESS_TOKEN}"})
        body = response.json() if response.status_code == 200 else {}
        status = body.get("status_code")
        if status == "FINISHED":
            return True
        if status in ("ERROR", "EXPIRED"):
            logger.error("Instagram could not prepare the story: %s, %s", status, body.get("status"))
            return False
        time.sleep(READY_POLL_SECONDS)
    logger.error("Instagram never finished preparing the story (%ds)", READY_MAX_SECONDS)
    return False


def publish_story(settings: Settings, jpeg: bytes) -> str | None:
    """Publishes `jpeg` as a story and returns its media id, or None when anything went wrong (already logged).
    Never raises: the countdown goes on without it."""
    if not configured(settings):
        return None
    try:
        with httpx.Client(timeout=settings.INSTAGRAM_TIMEOUT_SECONDS) as client:
            url = _park(client, settings, jpeg)
            if url is None:
                return None
            container = _graph(client, settings, "media", {"media_type": MEDIA_TYPE, "image_url": url})
            if container is None or "id" not in container:
                return None
            if not _wait_until_ready(client, settings, container["id"]):
                return None
            published = _graph(client, settings, "media_publish", {"creation_id": container["id"]})
            if published is None or "id" not in published:
                return None
    except Exception:  # a timeout, a connection reset, a body that is not JSON: never the countdown's problem
        logger.exception("Could not publish the Instagram story")
        return None
    logger.info("INSTAGRAM story published: %s", published["id"])
    return published["id"]
