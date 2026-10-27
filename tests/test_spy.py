"""`!spy`: naming chats and deciding which one may read which (commands/spy.py)."""

from app.models import ChatAlias, ChatContextSource
from app.services import chat_aliases, chat_sources
from tests.conftest import REFUSED

ADMIN = "admin1@lid"


def other(send, text, **kwargs):
    """The same command, sent from another chat."""
    return send(text, user=ADMIN, chat="g2", **kwargs)[0]


def test_a_chat_is_named_once_and_the_name_is_what_others_type(send, db_session):
    assert "*festejen*" in send("!spy -alias Festejen", user=ADMIN)[0]  # written in g1, upper case is fine
    assert chat_aliases.key_of(db_session, "festejen") == "whatsapp:g1"
    assert "*festejen*" in other(send, "!spy -list")

    # one name per chat: naming it again replaces the old one instead of leaving two
    send("!spy -alias gamers", user=ADMIN)
    assert chat_aliases.key_of(db_session, "festejen") is None
    assert chat_aliases.alias_of(db_session, "whatsapp:g1") == "gamers"


def test_a_name_that_is_not_one_word_is_refused(send, db_session):
    assert "una sola palabra" in send("!spy -alias Festejen Uruguayos", user=ADMIN)[0]
    assert db_session.query(ChatAlias).count() == 0


def test_one_chat_reads_another_and_never_the_other_way_round(send, db_session):
    send("!spy -alias festejen", user=ADMIN)  # g1 is "festejen"
    answer = other(send, "!spy festejen risas,asado")  # g2 asks to read it
    assert "*festejen*" in answer and "risas, asado" in answer

    assert [r.source_chat_key for r in chat_sources.of_chat(db_session, "whatsapp:g2")] == ["whatsapp:g1"]
    assert chat_sources.of_chat(db_session, "whatsapp:g1") == []  # the row for the other direction is never written
    assert chat_sources.asked_for(db_session, "whatsapp:g2", "qué onda el asado?")
    assert chat_sources.asked_for(db_session, "whatsapp:g1", "qué onda el asado?") == []


def test_without_words_the_name_itself_is_the_word(send, db_session):
    send("!spy -alias librecourse", user=ADMIN)
    other(send, "!spy librecourse")
    (row,) = chat_sources.of_chat(db_session, "whatsapp:g2")
    assert chat_sources.words_of(row) == ["librecourse"]


def test_off_stops_it_and_says_so_when_there_was_nothing(send, db_session):
    send("!spy -alias festejen", user=ADMIN)
    other(send, "!spy festejen")
    assert "ya no lee" in other(send, "!spy -off festejen")
    assert db_session.query(ChatContextSource).count() == 0
    assert "no estaba leyendo" in other(send, "!spy -off festejen")


def test_a_name_nobody_gave_is_not_a_chat(send, db_session):
    assert "No conozco" in send("!spy loquesea", user=ADMIN)[0]
    assert db_session.query(ChatContextSource).count() == 0


def test_a_chat_does_not_read_itself(send):
    send("!spy -alias gamers", user=ADMIN)
    assert "no se lee a sí mismo" in send("!spy gamers", user=ADMIN)[0]


def test_the_listing_says_what_this_chat_reads_and_that_nobody_reads_it(send):
    send("!spy -alias festejen", user=ADMIN)
    other(send, "!spy festejen risas")
    answer = other(send, "!spy")
    assert "*festejen*" in answer and "risas" in answer and "Nadie lo lee a él" in answer


def test_only_an_admin_decides_who_reads_whom(send):
    assert send("!spy -alias loquesea") == REFUSED
    assert send("!spy") == REFUSED
