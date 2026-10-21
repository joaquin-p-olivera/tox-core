"""Per-chat profile for `!claude`: what a chat is about and who its people are, written by the owner in CHATS_FILE.

One entry per chat, keyed by "platform:chat_id" (or the bare chat_id), the same shape USERS_FILE uses for people. A
private chat WhatsApp names by two ids (LID and phone number) is found by either one. Read on every call, so edits
apply without restarting the API.

Unlike the transcript of a chat, this file is NOT what the members wrote: only the owner can edit it. So the prompt
presents it as the owner's own description of the chat, and both modes of `!claude` get it.
"""

import json
import logging
from dataclasses import dataclass, replace
from pathlib import Path

logger = logging.getLogger("tox.profiles")

MAX_NAME_CHARS = 80
MAX_ABOUT_CHARS = 1_000
MAX_PERSON_CHARS = 60
MAX_BASICS_CHARS = 120
MAX_DETAIL_CHARS = 300
MAX_PEOPLE = 40
MAX_NOTES_CHARS = 800


@dataclass(frozen=True)
class Profile:
    """What the owner wrote about one chat. Empty (falsy) when there is nothing for it."""

    name: str = ""
    about: str = ""
    # (name, the plain facts, the rest of what to know), in the order they were written. The facts are the elementary
    # ones -- an age, where they live -- which Tox is told whoever is talking; the rest it only gets now and then
    # (CLAUDE_PROFILE_PEOPLE_CHANCE), so it doesn't make the same joke about somebody's life every time it speaks.
    people: tuple[tuple[str, str, str], ...] = ()
    notes: str = ""

    def __bool__(self) -> bool:
        return bool(self.name or self.about or self.people or self.notes)

    def block(self) -> str:
        """The profile as it goes into the prompt."""
        parts = []
        if self.name:
            parts.append(f"Nombre del chat: {self.name}")
        if self.about:
            parts.append(f"De qué va: {self.about}")
        if self.people:
            parts.append("Quiénes lo integran:\n" + "\n".join(
                f"- {name}: " + ". ".join(known) if (known := [p for p in (basics, detail) if p]) else f"- {name}"
                for name, basics, detail in self.people))
        if self.notes:
            parts.append(f"Indicaciones del dueño para este chat: {self.notes}")
        return "\n".join(parts)


    def without_people_details(self) -> "Profile":
        """The same profile with only the plain facts left of each person (see `people`). Everything else is kept: the
        chat still has a name, what it is about and the owner's notes."""
        return replace(self, people=tuple((name, basics, "") for name, basics, _ in self.people if name))


def _text(value: object, limit: int) -> str:
    """A field as one clean line, cut to `limit`. Anything that isn't text counts as empty."""
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _detail(value: object) -> str:
    """What to know about somebody: one line, or several that read fine joined into one."""
    if isinstance(value, list):
        value = ". ".join(item for item in value if isinstance(item, str))
    return _text(value, MAX_DETAIL_CHARS)


def _people(value: object) -> tuple[tuple[str, str, str], ...]:
    """`{"name": "what to know"}` (a line, or a list of them), or a plain list of names. A person can also be an object
    telling the elementary facts apart from the rest, which is what decides how often Tox is told them:

        "Nombre": {"basics": "34 años, vive en Montevideo", "about": "Hincha de Peñarol, organiza las juntadas"}

    Either way: at most MAX_PEOPLE, names, facts and details cut to their limits."""
    if isinstance(value, list):
        value = {name: "" for name in value if isinstance(name, str)}
    if not isinstance(value, dict):
        return ()
    found = []
    for name, detail in value.items():
        basics = ""
        if isinstance(detail, dict):
            basics, detail = _text(detail.get("basics"), MAX_BASICS_CHARS), _detail(detail.get("about"))
        else:
            detail = _detail(detail)
        name = _text(name, MAX_PERSON_CHARS)
        if name:
            found.append((name, basics, detail))
    return tuple(found[:MAX_PEOPLE])


def load_profiles(path: str) -> dict[str, Profile]:
    """chat key -> profile. A missing or broken file means "no profiles": never an error for the command using it."""
    file = Path(path).expanduser()
    if not file.is_file():
        return {}
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        logger.warning("Could not read %s: %s", file.name, error)
        return {}
    if not isinstance(raw, dict):
        logger.warning("%s must be a JSON object {\"platform:chat_id\": {\"name\": ..., \"about\": ...}}", file.name)
        return {}
    profiles = {}
    for key, entry in raw.items():
        if not isinstance(key, str) or not isinstance(entry, dict):
            continue
        profiles[key] = Profile(
            name=_text(entry.get("name"), MAX_NAME_CHARS),
            about=_text(entry.get("about"), MAX_ABOUT_CHARS),
            people=_people(entry.get("people")),
            notes=_text(entry.get("notes"), MAX_NOTES_CHARS),
        )
    return profiles


def profile_for(profiles: dict[str, Profile], platform: str, chat_ids: list[str]) -> Profile:
    """The profile of this chat by any of the ids it is known by, with or without the platform prefix."""
    for chat_id in chat_ids:
        for key in (f"{platform}:{chat_id}", chat_id):
            found = profiles.get(key)
            if found:
                return found
    return Profile()
