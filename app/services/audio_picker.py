import random
from pathlib import Path

from sqlalchemy.orm import Session

from ..models import LastAudio


def pick_audio(db: Session, chat_key: str, audios: list[Path]) -> Path:
    """A random audio that isn't the last one sent in this chat, remembering the new choice.

    With a single audio there is nothing else to pick, so it repeats. ``audios`` must not be empty.
    """
    record = db.get(LastAudio, chat_key)
    last = record.name if record else None

    candidates = [audio for audio in audios if audio.name != last] or audios
    choice = random.choice(candidates)

    if record is None:
        db.add(LastAudio(chat_key=chat_key, name=choice.name))
    else:
        record.name = choice.name
    db.commit()
    return choice
