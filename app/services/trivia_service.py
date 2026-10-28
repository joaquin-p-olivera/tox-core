"""Multiplayer trivia: one open question per chat, everyone competes, first correct answer wins.

A round that ends while the chat was playing (somebody won, or the time ran out with at least one answer in)
is followed by the next question on its own; a round nobody answered ends the streak, so a chat that went quiet
is left alone. A win chains inside the very reply that announces it, while a timeout has nobody typing to answer
to: `queue_due` turns it into PendingAlerts on the bots' own poll, the way reminders and the GTA countdown do.
"""

import random
import re
import unicodedata
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import PendingAlert, Score, TriviaAttempt, TriviaChain, TriviaRound
from ..models.trivia_round import STATUS_EXPIRED, STATUS_OPEN, STATUS_WON
from ..schemas import IncomingMessage
from ..utils import utcnow
from . import flags
from .content import load_json

LETTERS = "ABCD"
# A bare "b" (or "B)") answers the open round: no command prefix needed, see services/command_router.py.
ANSWER_TEXT = re.compile(r"^([a-dA-D])[).:]?$")
BASE_POINTS = 10
MAX_SPEED_BONUS = 5
RECENT_QUESTIONS_TO_AVOID = 10


@dataclass(frozen=True)
class Question:
    id: str
    category: str
    text: str
    choices: list[str]  # already in display order
    correct_index: int  # index into ``choices`` (display order)


# Category keys are typed by users, so they're plain lowercase; labels are what the chat sees.
CATEGORY_LABELS = {
    "banderas": "Banderas",
    "geografia": "Geografía",
    "ciencia": "Ciencia",
    "historia": "Historia",
    "cultura": "Cultura",
    "deportes": "Deportes",
    "uruguay": "Uruguay",
    "juegos": "Juegos",
}


def categories() -> list[str]:
    """Every category that can be asked for, the written ones and the flags, which are made up as they are asked."""
    return sorted({q["category"] for q in load_json("trivia_questions.json")} | {flags.CATEGORY})


def _pool(category: str | None) -> list[dict]:
    """What a round may be drawn from. A flag is a seed rather than a question: it is written out once the round
    exists (see `_build_question`), because the wrong answers are drawn with the round id."""
    if category == flags.CATEGORY:
        return flags.seeds()
    written = [q for q in load_json("trivia_questions.json") if not category or q["category"] == category]
    return written + (flags.seeds() if category is None else [])


def normalize_category(value: str) -> str:
    """"Geografía" -> "geografia": accents and case don't matter when typing."""
    decomposed = unicodedata.normalize("NFD", value.strip().lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def _build_question(raw: dict, round_id: int) -> Question:
    if flags.is_flag(raw["id"]):
        raw = flags.question(raw["id"], round_id)
    # Shuffle per round (seeded by the round id) so the correct letter isn't predictable,
    # while staying reproducible without storing the order.
    order = list(range(len(raw["choices"])))
    random.Random(round_id).shuffle(order)
    return Question(
        id=raw["id"],
        category=raw["category"],
        text=raw["question"],
        choices=[raw["choices"][i] for i in order],
        correct_index=order.index(raw["answer"]),
    )


def _question_for_round(rnd: TriviaRound) -> Question:
    if flags.is_flag(rnd.question_id):
        return _build_question({"id": rnd.question_id}, rnd.id)
    raw = next(q for q in load_json("trivia_questions.json") if q["id"] == rnd.question_id)
    return _build_question(raw, rnd.id)


def _format_question(q: Question, seconds_left: int) -> str:
    lines = [f"🧠 TRIVIA · {CATEGORY_LABELS.get(q.category, q.category)}", q.text, ""]
    lines += [f"{LETTERS[i]}) {choice}" for i, choice in enumerate(q.choices)]
    lines += ["", f"Respondé con a, b, c o d — ¡gana el primero que acierte! ({seconds_left}s)"]
    return "\n".join(lines)


def _reveal(q: Question) -> str:
    return f"La respuesta era {LETTERS[q.correct_index]}) {q.choices[q.correct_index]}"


def _open_round(db: Session, chat_key: str) -> TriviaRound | None:
    return db.scalars(
        select(TriviaRound)
        .where(TriviaRound.chat_key == chat_key, TriviaRound.status == STATUS_OPEN)
        .order_by(TriviaRound.id.desc())
    ).first()


def _expire(db: Session, rnd: TriviaRound) -> str | None:
    """Close an overdue round. Returns the announcement, or None if someone else closed it first."""
    result = db.execute(
        update(TriviaRound)
        .where(TriviaRound.id == rnd.id, TriviaRound.status == STATUS_OPEN)
        .values(status=STATUS_EXPIRED)
    )
    db.commit()
    if result.rowcount == 0:
        return None
    return f"⏰ ¡Se acabó el tiempo! {_reveal(_question_for_round(rnd))}"


def start_round(db: Session, message: IncomingMessage, settings: Settings, prefix: str,
                category: str | None = None) -> list[str]:
    valid = categories()
    if category:
        category = normalize_category(category)
        if category not in valid:
            return [f"No conozco esa categoría. Disponibles: {', '.join(valid)}"]

    now = utcnow()
    replies: list[str] = []

    current = _open_round(db, message.chat_key)
    if current is not None:
        if current.expires_at > now:
            left = max(1, int((current.expires_at - now).total_seconds()))
            return ["¡Todavía hay una pregunta abierta!", _format_question(_question_for_round(current), left)]
        announcement = _expire(db, current)
        if announcement:
            replies.append(announcement)

    _play(db, message.chat_key, category)
    replies.append(_ask(db, message.chat_key, settings, category))
    return replies


def _ask(db: Session, chat_key: str, settings: Settings, category: str | None = None) -> str:
    """Opens a round in this chat with a question it hasn't seen lately, and returns it written out."""
    recent = set(db.scalars(
        select(TriviaRound.question_id)
        .where(TriviaRound.chat_key == chat_key)
        .order_by(TriviaRound.id.desc())
        .limit(RECENT_QUESTIONS_TO_AVOID)
    ))
    pool = _pool(category)
    fresh = [q for q in pool if q["id"] not in recent] or pool  # tiny pool: allow repeats
    raw = random.choice(fresh)

    timeout = settings.TRIVIA_TIMEOUT_SECONDS
    now = utcnow()
    rnd = TriviaRound(chat_key=chat_key, question_id=raw["id"],
                      started_at=now, expires_at=now + timedelta(seconds=timeout))
    db.add(rnd)
    db.commit()
    return _format_question(_build_question(raw, rnd.id), timeout)


def _playing(db: Session, chat_key: str) -> str | None:
    """The category this chat's streak is playing, or None when it takes questions from everything."""
    row = db.get(TriviaChain, chat_key)
    return row.category if row is not None else None


def _play(db: Session, chat_key: str, category: str | None) -> None:
    """Writes down what the streak that starts here is playing. A round asked for without a category starts a free
    streak, so the row goes: a chat that was on banderas and types `!trivia` is asking for anything again."""
    row = db.get(TriviaChain, chat_key)
    if category is None:
        if row is not None:
            db.delete(row)
            db.commit()
        return
    if row is None:
        db.add(TriviaChain(chat_key=chat_key, category=category))
    else:
        row.category = category
    db.commit()


def _answered(db: Session, round_id: int) -> bool:
    """Whether anybody tried this round at all: what tells a chat that is playing from one that moved on."""
    return bool(db.scalar(select(func.count()).select_from(TriviaAttempt).where(TriviaAttempt.round_id == round_id)))


def submit_answer(db: Session, message: IncomingMessage, letter: str, settings: Settings, prefix: str) -> list[str]:
    name = message.display_name
    rnd = _open_round(db, message.chat_key)
    if rnd is None:
        return [f"No hay ninguna pregunta abierta. Empezá una con {prefix}trivia"]

    if rnd.expires_at <= utcnow():
        # Beat the bots' poll to it (see `queue_due`): announce the timeout here, and carry the streak on.
        announcement = _expire(db, rnd)
        if announcement is None:
            return ["¡Llegaste tarde!", f"Empezá otra con {prefix}trivia"]
        if _answered(db, rnd.id):
            return [announcement, _ask(db, message.chat_key, settings, _playing(db, message.chat_key))]
        _play(db, message.chat_key, None)  # the streak is over: the next one starts wherever it wants
        return [announcement, f"Empezá otra con {prefix}trivia"]

    question = _question_for_round(rnd)
    choice = LETTERS.find(letter.upper())
    if choice < 0 or choice >= len(question.choices):
        return [f"Elegí una de estas: {', '.join(LETTERS[: len(question.choices)])}"]

    try:
        db.add(TriviaAttempt(round_id=rnd.id, user_key=message.user_key, choice=choice))
        db.commit()
    except IntegrityError:
        db.rollback()
        return [f"Ya respondiste, {name}. ¡Dejá que prueben los demás!"]

    if choice != question.correct_index:
        return [f"❌ No, {name}."]

    # Claim the round atomically: with concurrent correct answers only one UPDATE matches.
    claimed = db.execute(
        update(TriviaRound)
        .where(TriviaRound.id == rnd.id, TriviaRound.status == STATUS_OPEN)
        .values(status=STATUS_WON, winner_user_key=message.user_key)
    )
    if claimed.rowcount == 0:
        db.rollback()
        return [f"Tarde, {name} — ¡alguien fue más rápido!"]

    total_seconds = max(1, (rnd.expires_at - rnd.started_at).total_seconds())
    remaining = max(0.0, (rnd.expires_at - utcnow()).total_seconds())
    points = BASE_POINTS + round(MAX_SPEED_BONUS * remaining / total_seconds)

    score = db.scalars(select(Score).where(
        Score.chat_key == message.chat_key, Score.user_key == message.user_key)).first()
    if score is None:
        score = Score(chat_key=message.chat_key, user_key=message.user_key,
                      user_name=name, points=0, wins=0)
        db.add(score)
    score.user_name = name
    score.points += points
    score.wins += 1
    db.commit()

    # The chat is playing: the next question goes out with the announcement, no one has to ask for it.
    return [f"🎉 ¡{name} acertó! {_reveal(question)}\n+{points} pts (total: {score.points})",
            _ask(db, message.chat_key, settings, _playing(db, message.chat_key))]


def answer_letter(text: str) -> str | None:
    """The letter in a message that is nothing but one ("b", "B)"), or None when it isn't an answer."""
    match = ANSWER_TEXT.match(text.strip())
    return match.group(1) if match else None


def round_is_open(db: Session, chat_key: str) -> bool:
    """Whether this chat has a question open right now. Only then is a bare letter taken as an answer:
    a stray "a" in a chat with nothing going on is just somebody typing."""
    rnd = _open_round(db, chat_key)
    return rnd is not None and rnd.expires_at > utcnow()


def queue_due(db: Session, settings: Settings) -> int:
    """Closes every round whose time is up and queues the reveal -- plus the next question, when somebody did
    answer -- as PendingAlerts, which is how anything reaches a chat with no message to reply to. Called on each
    bots' poll (routers/alerts.py), so a timeout is announced within one ALERTS_POLL_INTERVAL of happening."""
    overdue = list(db.scalars(
        select(TriviaRound).where(TriviaRound.status == STATUS_OPEN, TriviaRound.expires_at <= utcnow())
    ))
    queued = 0
    for rnd in overdue:
        answered = _answered(db, rnd.id)
        announcement = _expire(db, rnd)
        if announcement is None:
            continue  # somebody's message closed it first, and took the announcement with it
        texts = [announcement]
        if answered:
            texts.append(_ask(db, rnd.chat_key, settings, _playing(db, rnd.chat_key)))
        else:
            _play(db, rnd.chat_key, None)  # nobody answered: the streak ends, and so does its category
        platform, _, chat_id = rnd.chat_key.partition(":")
        for text in texts:
            db.add(PendingAlert(platform=platform, chat_id=chat_id, text=text))
        queued += 1
    if overdue:
        db.commit()
    return queued


def leaderboard(db: Session, chat_key: str, limit: int = 10) -> list[Score]:
    return list(db.scalars(
        select(Score)
        .where(Score.chat_key == chat_key)
        .order_by(Score.points.desc(), Score.wins.desc(), Score.user_name)
        .limit(limit)
    ))
