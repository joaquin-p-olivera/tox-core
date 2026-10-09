"""A small local directory of the people in the chats: name -> their platform ids ("platform:user_id").

It lets a command give someone specific a special behaviour (see !gay) without depending on the display name
the platform shows, which anyone can change. The same person may be listed under several ids on purpose:
WhatsApp identifies one person by LID in some places and by phone number in others (the same reason !mute keeps
aliases). Read on every call, so edits apply without restarting the API. Build the entries from `!uid`'s output.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger("tox.users")


def load_users(path: str) -> dict[str, frozenset[str]]:
    """name -> ids. A missing or broken file means "nobody listed": never an error for the command using it."""
    file = Path(path)
    if not file.is_file():
        return {}
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        logger.warning("Could not read %s: %s", file.name, error)
        return {}
    if not isinstance(raw, dict):
        logger.warning("%s must be a JSON object {\"name\": [\"platform:user_id\", ...]}", file.name)
        return {}
    return {
        name: frozenset(ids)
        for name, ids in raw.items()
        if isinstance(name, str) and isinstance(ids, list) and all(isinstance(i, str) for i in ids)
    }


def name_of(users: dict[str, frozenset[str]], platform: str, *user_ids: str) -> str | None:
    """The listed name of whoever has any of these ids on this platform, or None if nobody does."""
    keys = {f"{platform}:{user_id}" for user_id in user_ids}
    return next((name for name, ids in users.items() if ids & keys), None)
