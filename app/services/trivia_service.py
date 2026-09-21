"""Multiplayer trivia: one open question per chat, everyone competes, first correct answer wins."""

import random
import unicodedata
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import Score, TriviaAttempt, TriviaRound
from ..models.trivia_round import STATUS_EXPIRED, STATUS_OPEN, STATUS_WON
from ..schemas import IncomingMessage
from ..utils import utcnow
from .content import load_json

LETTERS = "ABCD"
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
    "geografia": "Geografía",
    "ciencia": "Ciencia",
    "historia": "Historia",
    "cultura": "Cultura",
    "deportes": "Deportes",
    "uruguay": "Uruguay",
    "juegos": "Juegos",
}


def categories() -> list[str]:
    return sorted({q["category"] for q in load_json("trivia_questions.json")})


def normalize_category(value: str) -> str:
    """"Geografía" -> "geografia": accents and case don't matter when typing."""
    decomposed = unicodedata.normalize("NFD", value.strip().lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def _build_question(raw: dict, round_id: int) -> Question:
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
    raw = next(q for q in load_json("trivia_questions.json") if q["id"] == rnd.question_id)
    return _build_question(raw, rnd.id)


def _format_question(q: Question, seconds_left: int, prefix: str) -> str:
    lines = [f"🧠 TRIVIA · {CATEGORY_LABELS.get(q.category, q.category)}", q.text, ""]
    lines += [f"{LETTERS[i]}) {choice}" for i, choice in enumerate(q.choices)]
    lines += ["", f"Respondé con {prefix}a, {prefix}b, {prefix}c o {prefix}d — ¡gana el primero que acierte! ({seconds_left}s)"]
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
            return ["¡Todavía hay una pregunta abierta!", _format_question(_question_for_round(current), left, prefix)]
        announcement = _expire(db, current)
        if announcement:
            replies.append(announcement)

    recent = set(db.scalars(
        select(TriviaRound.question_id)
        .where(TriviaRound.chat_key == message.chat_key)
        .order_by(TriviaRound.id.desc())
        .limit(RECENT_QUESTIONS_TO_AVOID)
    ))
    pool = [q for q in load_json("trivia_questions.json") if not category or q["category"] == category]
    fresh = [q for q in pool if q["id"] not in recent] or pool  # tiny pool: allow repeats
    raw = random.choice(fresh)

    timeout = settings.TRIVIA_TIMEOUT_SECONDS
    rnd = TriviaRound(chat_key=message.chat_key, question_id=raw["id"],
                      started_at=now, expires_at=now + timedelta(seconds=timeout))
    db.add(rnd)
    db.commit()
    replies.append(_format_question(_build_question(raw, rnd.id), timeout, prefix))
    return replies


def submit_answer(db: Session, message: IncomingMessage, letter: str, prefix: str) -> list[str]:
    name = message.display_name
    rnd = _open_round(db, message.chat_key)
    if rnd is None:
        return [f"No hay ninguna pregunta abierta. Empezá una con {prefix}trivia"]

    if rnd.expires_at <= utcnow():
        announcement = _expire(db, rnd)
        return [announcement or "¡Llegaste tarde!", f"Empezá otra con {prefix}trivia"]

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

    return [f"🎉 ¡{name} acertó! {_reveal(question)}\n+{points} pts (total: {score.points})"]


def leaderboard(db: Session, chat_key: str, limit: int = 10) -> list[Score]:
    return list(db.scalars(
        select(Score)
        .where(Score.chat_key == chat_key)
        .order_by(Score.points.desc(), Score.wins.desc(), Score.user_name)
        .limit(limit)
    ))
