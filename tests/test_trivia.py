from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models import Score, TriviaRound
from app.services import trivia_service
from app.utils import utcnow

LETTERS = "abcd"


def current_round(db, chat_key="whatsapp:g1"):
    return db.scalars(select(TriviaRound).where(TriviaRound.chat_key == chat_key)
                      .order_by(TriviaRound.id.desc())).first()


def correct_letter(db, chat_key="whatsapp:g1"):
    rnd = current_round(db, chat_key)
    return LETTERS[trivia_service._question_for_round(rnd).correct_index]


def wrong_letter(db, chat_key="whatsapp:g1"):
    right = correct_letter(db, chat_key)
    return next(letter for letter in LETTERS if letter != right)


def test_start_shows_question_with_four_choices(send):
    (text,) = send("!trivia")
    assert text.startswith("🧠 TRIVIA") and "Respondé con !a, !b, !c o !d" in text
    for label in ("A)", "B)", "C)", "D)"):
        assert label in text


def test_starting_again_reuses_the_open_question(send, db_session):
    send("!trivia")
    first_id = current_round(db_session).id
    replies = send("!trivia", user="u2", name="Bob")
    assert replies[0] == "¡Todavía hay una pregunta abierta!"
    assert current_round(db_session).id == first_id


def test_correct_answer_wins_points(send, db_session):
    send("!trivia")
    (reply,) = send(f"!{correct_letter(db_session)}")
    assert "¡Alice acertó!" in reply and "+1" in reply  # 10-15 points
    score = db_session.scalars(select(Score)).one()
    assert 10 <= score.points <= 15 and score.wins == 1
    assert current_round(db_session).status == "won"


def test_answer_command_variants(send, db_session):
    send("!trivia")
    letter = correct_letter(db_session)
    assert "acertó" in send(f"!responder {letter.upper()})")[0]


def test_wrong_answer_then_locked_out(send, db_session):
    send("!trivia")
    assert send(f"!{wrong_letter(db_session)}") == ["❌ No, Alice."]
    assert "Ya respondiste, Alice" in send(f"!{correct_letter(db_session)}")[0]
    assert current_round(db_session).status == "open"  # round continues for the others


def test_second_player_can_still_win_after_first_misses(send, db_session):
    send("!trivia")
    send(f"!{wrong_letter(db_session)}")
    (reply,) = send(f"!{correct_letter(db_session)}", user="u2", name="Bob")
    assert "¡Bob acertó!" in reply


def test_late_correct_answer_after_win(send, db_session):
    send("!trivia")
    letter = correct_letter(db_session)
    send(f"!{letter}")
    assert send(f"!{letter}", user="u2", name="Bob")[0].startswith("No hay ninguna pregunta abierta")


def test_concurrent_correct_answers_only_one_wins(db_session, settings):
    """Simulate the race: the round is claimed between the read and the claim."""
    from app.schemas import IncomingMessage

    def msg(uid, name):
        return IncomingMessage(platform="whatsapp", chat_id="g1", user_id=uid, user_name=name, text="")

    trivia_service.start_round(db_session, msg("u1", "Alice"), settings, "!")
    rnd = current_round(db_session)
    letter = LETTERS[trivia_service._question_for_round(rnd).correct_index]

    # Someone else wins first (directly in the DB), after our stale open-round read.
    original = trivia_service._open_round
    def stale_open_round(db, chat_key):
        found = original(db, chat_key)
        db.execute(TriviaRound.__table__.update().where(TriviaRound.id == rnd.id).values(status="won"))
        db.commit()
        return found
    trivia_service._open_round = stale_open_round
    try:
        (reply,) = trivia_service.submit_answer(db_session, msg("u2", "Bob"), letter, "!")
    finally:
        trivia_service._open_round = original
    assert reply.startswith("Tarde, Bob")
    assert db_session.scalars(select(Score)).first() is None  # no points awarded


def test_no_open_question(send):
    assert send("!a")[0] == "No hay ninguna pregunta abierta. Empezá una con !trivia"


def test_invalid_letter(send):
    send("!trivia")
    assert send("!responder z")[0].startswith("Elegí una de estas")
    assert send("!responder")[0].startswith("Uso:")


def test_expired_round_is_revealed(send, db_session):
    send("!trivia")
    rnd = current_round(db_session)
    rnd.expires_at = utcnow() - timedelta(seconds=1)
    db_session.commit()

    replies = send(f"!{correct_letter(db_session)}")
    assert replies[0].startswith("⏰ ¡Se acabó el tiempo! La respuesta era")
    assert current_round(db_session).status == "expired"


def test_trivia_after_expiry_reveals_and_starts_new(send, db_session):
    send("!trivia")
    rnd = current_round(db_session)
    rnd.expires_at = utcnow() - timedelta(seconds=1)
    db_session.commit()

    replies = send("!trivia")
    assert replies[0].startswith("⏰ ¡Se acabó el tiempo!")
    assert replies[1].startswith("🧠 TRIVIA")
    assert current_round(db_session).id != rnd.id


def test_category_filter(send, db_session):
    (text,) = send("!trivia juegos")
    assert "TRIVIA · Juegos" in text
    assert "TRIVIA · Geografía" in send("!trivia GEOGRAFÍA", chat="g2")[0]  # accents and case don't matter
    assert send("!trivia cocina", chat="other")[0].startswith("No conozco esa categoría. Disponibles:")


def test_rounds_are_isolated_per_chat(send, db_session):
    send("!trivia", chat="g1")
    assert send("!a", chat="g2")[0].startswith("No hay ninguna pregunta abierta")


def test_leaderboard(send, db_session):
    assert send("!ranking")[0].startswith("Todavía no hay puntajes")
    for user, name in (("u1", "Alice"), ("u2", "Bob")):
        send("!trivia")
        send(f"!{correct_letter(db_session)}", user=user, name=name)
    (board,) = send("!ranking")
    assert board.startswith("🏆 Ranking") and "🥇" in board and "🥈" in board
    assert "Alice" in board and "Bob" in board
    assert send("!top", chat="g-other")[0].startswith("Todavía no hay puntajes")  # !top is an alias


def test_question_data_is_valid():
    from app.services.content import load_json

    questions = load_json("trivia_questions.json")
    ids = [q["id"] for q in questions]
    assert len(ids) == len(set(ids))
    for q in questions:
        assert len(q["choices"]) == 4 and len(set(q["choices"])) == 4, q["id"]
        assert 0 <= q["answer"] < 4, q["id"]
    assert set(trivia_service.categories()) == set(trivia_service.CATEGORY_LABELS)
    assert "dev" not in trivia_service.categories()  # no programming questions


@pytest.mark.parametrize("round_id", range(1, 30))
def test_shuffle_keeps_the_correct_answer(round_id):
    from app.services.content import load_json

    for raw in load_json("trivia_questions.json")[:5]:
        q = trivia_service._build_question(raw, round_id)
        assert q.choices[q.correct_index] == raw["choices"][raw["answer"]]
        assert sorted(q.choices) == sorted(raw["choices"])
