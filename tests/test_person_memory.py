"""What Tox remembers about the people of a chat from talking to them."""

import time

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.commands import claude as claude_command
from app.models import PersonNote
from app.services import claude_runner, person_memory
from app.utils import utcnow
from tests.test_mention import raw

HEADERS = {"X-API-Key": "test-key"}
CHAT = "whatsapp:g1"
PEOPLE = {"Alice": "whatsapp:u1", "Bob": "whatsapp:u2"}
LIMITS = {"max_notes": 20, "max_chars": 120}


# --- taking the lines off an answer ---

def test_the_lines_are_taken_off_and_read():
    clean, learned = person_memory.strip("Dale.\n[memoria] Alice: juega al fútbol los martes")
    assert clean == "Dale." and learned == [("Alice", "juega al fútbol los martes")]


def test_an_answer_with_no_lines_is_untouched():
    assert person_memory.strip("Todo bien, qué tal.") == ("Todo bien, qué tal.", [])


def test_a_line_that_cannot_be_read_is_still_taken_off():
    clean, learned = person_memory.strip("Ahí va.\n[memoria] esto no tiene dos puntos")
    assert clean == "Ahí va." and learned == []


def test_a_marker_in_the_middle_goes_too():
    clean, learned = person_memory.strip("Una.\n[MEMORIA]  Bob : toca la guitarra \nDos.")
    assert clean == "Una.\nDos." and learned == [("Bob", "toca la guitarra")]


# --- storing them ---

def test_a_note_is_stored_against_the_person_who_is_there(db_session):
    assert person_memory.remember(db_session, CHAT, [("alice", "es de Peñarol")], PEOPLE, **LIMITS) == 1
    (row,) = db_session.scalars(select(PersonNote))
    assert row.user_key == "whatsapp:u1" and row.text == "es de Peñarol" and row.chat_key == CHAT


def test_a_note_about_somebody_who_is_not_there_is_dropped(db_session):
    assert person_memory.remember(db_session, CHAT, [("Carlos", "no existe")], PEOPLE, **LIMITS) == 0
    assert db_session.scalars(select(PersonNote)).first() is None


def test_the_same_note_twice_is_counted_not_doubled(db_session):
    person_memory.remember(db_session, CHAT, [("Alice", "es de Peñarol")], PEOPLE, **LIMITS)
    assert person_memory.remember(db_session, CHAT, [("Alice", "Es  de  Peñarol")], PEOPLE, **LIMITS) == 0
    (row,) = db_session.scalars(select(PersonNote))
    assert row.seen == 2


def test_past_the_limit_the_notes_nobody_confirmed_go_first(db_session):
    for i in range(3):
        db_session.add(PersonNote(chat_key=CHAT, user_key="whatsapp:u1", text=f"vieja {i}",
                                  seen=2 if i == 0 else 1, learned_at=utcnow()))
    db_session.commit()
    person_memory.remember(db_session, CHAT, [("Alice", "nueva")], PEOPLE, max_notes=3, max_chars=120)
    kept = [row.text for row in person_memory.of_person(db_session, CHAT, "whatsapp:u1")]
    assert "nueva" in kept and "vieja 0" in kept and len(kept) == 3


def test_a_long_note_is_cut(db_session):
    person_memory.remember(db_session, CHAT, [("Alice", "x" * 500)], PEOPLE, max_notes=20, max_chars=30)
    assert len(person_memory.of_person(db_session, CHAT, "whatsapp:u1")[0].text) == 30


def test_notes_stay_in_the_chat_they_were_learned_in(db_session):
    person_memory.remember(db_session, CHAT, [("Alice", "algo")], PEOPLE, **LIMITS)
    assert person_memory.notes_for(db_session, "whatsapp:otro", ["whatsapp:u1"]) == {}
    assert person_memory.notes_for(db_session, CHAT, ["whatsapp:u1"]) == {"whatsapp:u1": ["algo"]}


# --- what the chat can do about it ---

def test_memoria_shows_yours_and_forgets_it(send, db_session):
    assert send("!memoria")[0] == "Todavía no me acuerdo de nada tuyo en este chat."
    person_memory.remember(db_session, CHAT, [("Alice", "es de Peñarol")], PEOPLE, **LIMITS)

    shown = send("!memoria")[0]
    assert "Lo que sé de Alice" in shown and "· es de Peñarol" in shown
    assert send("!memoria olvidame")[0].startswith("🧽 Listo, me olvidé de 1 cosa")
    assert send("!memoria")[0] == "Todavía no me acuerdo de nada tuyo en este chat."


def test_a_chat_can_turn_it_off(send, db_session):
    person_memory.remember(db_session, CHAT, [("Alice", "algo")], PEOPLE, **LIMITS)
    assert send("!config memoria no", user="admin1@lid", sender_is_group_admin=True)
    assert send("!memoria")[0].startswith("En este chat tengo la memoria apagada")


# --- end to end: what the chat sees and what is kept ---

def test_what_it_learns_never_reaches_the_chat(client, db_session, settings, monkeypatch):
    monkeypatch.setattr(claude_command, "session_factory",
                        sessionmaker(bind=db_session.get_bind(), autoflush=False, expire_on_commit=False))
    monkeypatch.setattr(claude_runner, "execute_public", lambda *a, **k: claude_runner.ClaudeResult(
        "Ahí va la respuesta.\n[memoria] Alice: es de Peñarol", None, None))

    (reply,) = raw(client, "!claude algo")
    deadline = time.time() + 5
    while time.time() < deadline:
        body = client.get(f"/api/v1/jobs/{reply['job']}", headers=HEADERS).json()
        if body["status"] == "done":
            break
        time.sleep(0.02)
    assert body["replies"][0]["text"] == "Ahí va la respuesta."  # the line is gone
    rows = person_memory.of_person(db_session, CHAT, "whatsapp:u1")
    assert [row.text for row in rows] == ["es de Peñarol"]  # ...and it was kept


def test_the_prompt_carries_what_is_known_and_asks_for_more(db_session, settings):
    from app.schemas import IncomingMessage
    from app.services import claude_context

    person_memory.remember(db_session, CHAT, [("Alice", "es de Peñarol")], PEOPLE, **LIMITS)
    message = IncomingMessage(platform="whatsapp", chat_id="g1", user_id="u1", user_name="Alice",
                              text="!claude hola", is_group=True,
                              participants=[{"user_id": "u1", "user_name": "Alice"}])
    context = claude_context.gather(db_session, settings, message, with_history=True)
    assert context.notes == {"Alice": ["es de Peñarol"]}
    assert "es de Peñarol" in context.prompt("hola")
    assert person_memory.ASK in context.prompt("hola", learn=True)
    assert person_memory.ASK not in context.prompt("hola")  # the full-access mode is never asked for notes


@pytest.fixture(autouse=True)
def no_real_cli(monkeypatch):
    monkeypatch.setattr(claude_runner, "execute",
                        lambda *a, **k: claude_runner.ClaudeResult("ok", None, None))
