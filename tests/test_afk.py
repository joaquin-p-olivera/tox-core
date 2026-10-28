"""!afk: the bot answers for somebody who said they are away, and the away ends when they write again."""

from datetime import timedelta

from app.models import AfkUser
from app.services import afk
from app.utils import utcnow

TAGGED = {"mentioned": [{"user_id": "u2", "user_name": "Bob"}]}


def test_afk_marks_you_away_with_a_reason(send, db_session):
    assert send("!afk comiendo")[0] == "🌙 Listo, Alice: quedás afk (comiendo). Vuelvo a contar cuando escribas."
    row = db_session.get(AfkUser, "whatsapp:u1")
    assert row.reason == "comiendo" and row.user_name == "Alice"


def test_tagging_somebody_away_is_answered_for_them(send, db_session):
    send("!afk comiendo", user="u2", name="Bob")
    (notice,) = send("hola {@0}", user="u1", name="Alice", **TAGGED)
    assert notice == "🌙 Bob está afk hace un rato: comiendo"


def test_replying_to_somebody_away_counts_as_looking_for_them(send, db_session):
    send("!afk", user="u2", name="Bob")
    (notice,) = send("qué decís?", quoted={"text": "lo de ayer", "user_id": "u2", "user_name": "Bob"})
    assert notice == "🌙 Bob está afk hace un rato"  # no reason given, so none is made up


def test_writing_anything_is_being_back(send, db_session):
    send("!afk", user="u2", name="Bob")
    db_session.get(AfkUser, "whatsapp:u2").since = utcnow() - timedelta(hours=3, minutes=5)
    db_session.commit()

    (back,) = send("ya volví", user="u2", name="Bob")
    assert back == "👋 Bob volvió (hace 3 h 5 min que se fue)"
    assert db_session.get(AfkUser, "whatsapp:u2") is None


def test_being_back_comes_before_what_the_command_answers(send, db_session):
    send("!afk", user="u2", name="Bob")
    replies = send("!uuid", user="u2", name="Bob")
    assert replies[0].startswith("👋 Bob volvió") and len(replies[1]) == 36


def test_saying_you_are_away_is_not_being_back(send, db_session):
    send("!afk primera", user="u2", name="Bob")
    replies = send("!afk segunda", user="u2", name="Bob")
    assert len(replies) == 1 and "segunda" in replies[0]
    assert db_session.get(AfkUser, "whatsapp:u2").reason == "segunda"


def test_nobody_is_told_they_tagged_themselves(send):
    send("!afk", user="u2", name="Bob")
    assert send("me voy {@0}", user="u2", name="Bob", **TAGGED) == ["👋 Bob volvió (hace un rato que se fue)"]


def test_the_personal_mode_never_touches_the_away_list(send, db_session):
    db_session.add(AfkUser(user_key="whatsapp:u2", user_name="Bob", reason="comiendo"))
    db_session.commit()
    assert send("hola", personal=True, **TAGGED) == []
    assert db_session.get(AfkUser, "whatsapp:u2") is not None


def test_how_long_ago_is_told_in_something_readable(db_session):
    row = AfkUser(user_key="k", user_name="Ana", since=utcnow())
    for delta, expected in ((timedelta(seconds=10), "hace un rato"), (timedelta(minutes=5), "hace 5 min"),
                            (timedelta(hours=2), "hace 2 h"), (timedelta(hours=2, minutes=30), "hace 2 h 30 min"),
                            (timedelta(days=2, hours=3), "hace 2 días 3 h")):
        row.since = utcnow() - delta
        assert afk.since_text(row) == expected
