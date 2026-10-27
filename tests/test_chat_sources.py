"""One chat reading another one's conversation (services/chat_sources.py), and only when it was asked about."""

from datetime import datetime, timedelta, timezone

import pytest

from app.models import ChatContextSource, HistoryChat
from app.services import chat_sources
from tests.test_claude_context import CHAT, HEADERS, answer, fake_claude, post, save  # noqa: F401

OTHER = "otro-grupo@g.us"
OWNER = {"user_id": "admin1@lid", "sender_is_group_admin": True}


@pytest.fixture
def reads_the_other(client, db_session, settings):
    """This chat may read OTHER's conversation, called up by "el otro grupo". Never the other way round."""
    post(client, "!h -on", **OWNER, admin=True)  # the history of this one
    db_session.add(HistoryChat(chat_key=f"whatsapp:{OTHER}", aliases="", enabled_by="test"))
    db_session.add(ChatContextSource(chat_key=f"whatsapp:{CHAT}", source_chat_key=f"whatsapp:{OTHER}",
                                     label="el otro grupo", triggers="el otro grupo,los pibes", added_by="test"))
    db_session.commit()
    # An hour ago, not a date written down: what is read is bounded by CLAUDE_CONTEXT_HOURS from now
    sent = datetime.now(timezone.utc) - timedelta(hours=1)
    response = client.post("/api/v1/history", headers=HEADERS, json={"platform": "whatsapp", "messages": [
        {"chat_id": OTHER, "message_id": "o1", "sender_id": "u9", "sender_name": "Dami",
         "text": "el sábado jugamos a las 9", "sent_at": sent.isoformat()}]})
    assert response.status_code == 200, response.text
    return settings


def test_the_other_chat_is_read_only_when_the_question_names_it(client, fake_claude, reads_the_other):
    answer(client, "!claude qué hora es?")
    assert "el sábado jugamos" not in fake_claude["prompts"][-1]  # nothing of the other chat was sent at all

    answer(client, "!claude qué están organizando en el otro grupo?")
    prompt = fake_claude["prompts"][-1]
    assert "Lo que se habló en el otro grupo" in prompt and "Dami: el sábado jugamos a las 9" in prompt


def test_any_of_the_trigger_words_calls_it_up_however_it_is_written(client, fake_claude, reads_the_other):
    answer(client, "!claude qué decían LOS PIBES?")  # another trigger, in caps
    assert "el sábado jugamos" in fake_claude["prompts"][-1]


def test_reading_is_one_way(db_session, reads_the_other):
    """The row says this chat reads the other one; the other one has no row and so knows nothing about this one."""
    assert chat_sources.asked_for(db_session, f"whatsapp:{CHAT}", "y el otro grupo?")
    assert chat_sources.asked_for(db_session, f"whatsapp:{OTHER}", "y el otro grupo?") == []


def test_a_remark_of_its_own_never_brings_another_chat_up(db_session, reads_the_other):
    """With no question there is nothing to ask for, which is what keeps it out of the interjections."""
    assert chat_sources.asked_for(db_session, f"whatsapp:{CHAT}", "") == []
    assert chat_sources.asked_for(db_session, f"whatsapp:{CHAT}", "   ") == []


def test_an_admin_asking_about_the_other_chat_is_answered_without_pc_access(client, fake_claude, reads_the_other):
    """The full-access mode never receives anybody else's text, so a question that needs it changes mode instead:
    that is the only way an admin gets this context at all, and the agent with permissions stays clean."""
    answer(client, "!claude qué organizaron en el otro grupo?", **OWNER, admin=True)
    # Any conversation in the prompt is itself the proof: the full-access mode is built without one
    assert "Dami: el sábado jugamos a las 9" in fake_claude["prompts"][-1]

    # anything else an admin asks is still the full-access mode, with no conversation of any chat in it
    answer(client, "!claude cómo está el disco?", **OWNER, admin=True)
    last = fake_claude["prompts"][-1]
    assert "el sábado jugamos" not in last and "Conversación reciente" not in last
