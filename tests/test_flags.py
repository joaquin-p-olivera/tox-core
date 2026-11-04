"""The flags round: the questions nobody wrote down, made out of flags.json."""

import pytest

from app.services import flags, trivia_service


def test_a_code_is_its_own_flag():
    assert flags.emoji("UY") == "🇺🇾" and flags.emoji("jp") == "🇯🇵"


def test_the_question_asks_for_the_flag_and_only_one_answer_is_right():
    q = trivia_service._build_question({"id": "flag-UY"}, round_id=7)
    assert flags.emoji("UY") in q.text and q.category == flags.CATEGORY
    assert len(q.choices) == 4 and len(set(q.choices)) == 4
    assert q.choices[q.correct_index] == "Uruguay"


def test_the_wrong_answers_come_from_the_same_continent():
    names = {row["name"]: row["continent"] for row in flags.countries()}
    for round_id in range(1, 15):
        q = trivia_service._build_question({"id": "flag-UY"}, round_id)
        assert {names[choice] for choice in q.choices} == {"Sudamérica"}, q.choices


def test_the_same_round_always_shows_the_same_question():
    first = trivia_service._build_question({"id": "flag-KE"}, 3)
    assert first == trivia_service._build_question({"id": "flag-KE"}, 3)
    assert first != trivia_service._build_question({"id": "flag-KE"}, 4)


def test_a_country_that_is_not_there():
    with pytest.raises(KeyError):
        flags.question("flag-XX", 1)


def test_the_country_list_is_valid():
    rows = flags.countries()
    codes = [row["code"] for row in rows]
    assert len(codes) == len(set(codes)) and len(rows) > 50
    names = [row["name"] for row in rows]
    assert len(names) == len(set(names))
    for row in rows:
        assert len(row["code"]) == 2 and row["code"].isupper(), row
        assert row["name"] and row["continent"]


def test_a_continent_with_too_few_countries_borrows_the_rest():
    """Norteamérica has three countries in all, so its wrong answers cannot all come from there."""
    names = {row["name"]: row["continent"] for row in flags.countries()}
    q = trivia_service._build_question({"id": "flag-MX"}, 11)
    assert len(q.choices) == 4 and len(set(q.choices)) == 4
    assert q.choices[q.correct_index] == "México"
    assert {names[choice] for choice in q.choices} != {"Norteamérica"}


# --- in the chat ---

def test_banderas_starts_a_round_that_is_answered_like_any_other(send, db_session):
    (text,) = send("!banderas")
    assert "TRIVIA · Banderas" in text and "¿De qué país es esta bandera?" in text

    from tests.test_trivia import correct_letter
    assert "acertó" in send(correct_letter(db_session))[0]  # the bare letter answers it


def test_the_category_can_also_be_asked_for_by_name(send):
    assert "TRIVIA · Banderas" in send("!trivia banderas")[0]
    assert "TRIVIA · Banderas" in send("!trivia BANDERAS", chat="g2")[0]
