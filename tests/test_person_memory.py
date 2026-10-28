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

def note(name, text, everywhere=False):
    return person_memory.Learned(name=name, text=text, everywhere=everywhere)


def test_the_lines_are_taken_off_and_read():
    clean, learned = person_memory.strip("Dale.\n[memoria] Alice: juega al fútbol los martes")
    assert clean == "Dale." and learned == [note("Alice", "juega al fútbol los martes")]


def test_a_line_can_say_the_note_is_about_the_person_anywhere():
    clean, learned = person_memory.strip("Ahí va.\n[memoria global] Alice: es veterinaria\n[memoria] Alice: le dicen Ali")
    assert clean == "Ahí va."
    assert learned == [note("Alice", "es veterinaria", everywhere=True), note("Alice", "le dicen Ali")]


def test_an_answer_with_no_lines_is_untouched():
    assert person_memory.strip("Todo bien, qué tal.") == ("Todo bien, qué tal.", [])


def test_a_line_that_cannot_be_read_is_still_taken_off():
    clean, learned = person_memory.strip("Ahí va.\n[memoria] esto no tiene dos puntos")
    assert clean == "Ahí va." and learned == []


def test_a_marker_in_the_middle_goes_too():
    clean, learned = person_memory.strip("Una.\n[MEMORIA]  Bob : toca la guitarra \nDos.")
    assert clean == "Una.\nDos." and learned == [note("Bob", "toca la guitarra")]


def test_the_line_that_says_nothing_was_learned_leaves_no_note():
    clean, learned = person_memory.strip("Todo bien.\n[memoria] -")
    assert clean == "Todo bien." and learned == []


def test_saying_nothing_was_learned_is_not_an_unreadable_line(caplog):
    with caplog.at_level("INFO", logger="tox.memory"):
        person_memory.strip("Dale.\n[memoria] -")
    assert "unreadable" not in caplog.text


def test_an_answer_that_carried_the_block_can_be_told_from_one_that_did_not():
    assert person_memory.carried_block("Dale.\n[memoria] -")
    assert person_memory.carried_block("Dale.\n[memoria] Alice: es veterinaria")
    assert not person_memory.carried_block("Dale, ahí va la respuesta y nada más.")


# --- storing them ---

def test_a_note_is_stored_against_the_person_who_is_there(db_session):
    assert person_memory.remember(db_session, CHAT, [note("alice", "es de Peñarol")], PEOPLE, **LIMITS) == 1
    (row,) = db_session.scalars(select(PersonNote))
    assert row.user_key == "whatsapp:u1" and row.text == "es de Peñarol" and row.chat_key == CHAT


def test_a_note_about_somebody_who_is_not_there_is_dropped(db_session):
    assert person_memory.remember(db_session, CHAT, [note("Carlos", "no existe")], PEOPLE, **LIMITS) == 0
    assert db_session.scalars(select(PersonNote)).first() is None


def test_a_display_name_is_not_what_somebody_is_called(db_session):
    """The model reads "Leandro" in the conversation; WhatsApp says "Leandro 1899". Real groups are full of this,
    and matching the whole string would drop almost every note."""
    people = {"Leandro 1899": "whatsapp:u1", "Joaquín.": "whatsapp:u2", "line🧃🐛": "whatsapp:u3"}
    stored = person_memory.remember(db_session, CHAT, [note("Leandro", "es veterinario"),
                                                       note("Joaquín", "vive en Salto"),
                                                       note("line", "toca la guitarra")], people, **LIMITS)
    assert stored == 3
    assert {row.user_key for row in db_session.scalars(select(PersonNote))} == {
        "whatsapp:u1", "whatsapp:u2", "whatsapp:u3"}


def test_a_name_two_people_could_answer_to_is_dropped(db_session):
    """A fact filed under the wrong person is worse than one that was dropped."""
    people = {"Ana María": "whatsapp:u1", "Ana Laura": "whatsapp:u2"}
    assert person_memory.remember(db_session, CHAT, [note("Ana", "es de Salto")], people, **LIMITS) == 0
    # ...but her whole name is still her
    assert person_memory.remember(db_session, CHAT, [note("Ana Laura", "es de Salto")], people, **LIMITS) == 1


def test_a_number_two_people_share_is_not_a_name(db_session):
    """"Leandro 1899" and "Pedro 1899" are not the same person, and "1899" is nobody."""
    people = {"Leandro 1899": "whatsapp:u1", "Pedro 1899": "whatsapp:u2"}
    assert person_memory.remember(db_session, CHAT, [note("1899", "algo")], people, **LIMITS) == 0
    assert person_memory.remember(db_session, CHAT, [note("Pedro", "juega al fútbol")], people, **LIMITS) == 1
    (row,) = db_session.scalars(select(PersonNote))
    assert row.user_key == "whatsapp:u2"


def test_the_same_note_twice_is_counted_not_doubled(db_session):
    person_memory.remember(db_session, CHAT, [note("Alice", "es de Peñarol")], PEOPLE, **LIMITS)
    assert person_memory.remember(db_session, CHAT, [note("Alice", "Es  de  Peñarol")], PEOPLE, **LIMITS) == 0
    (row,) = db_session.scalars(select(PersonNote))
    assert row.seen == 2


def test_past_the_limit_the_notes_nobody_confirmed_go_first(db_session):
    for i in range(3):
        db_session.add(PersonNote(chat_key=CHAT, user_key="whatsapp:u1", text=f"vieja {i}",
                                  seen=2 if i == 0 else 1, learned_at=utcnow()))
    db_session.commit()
    person_memory.remember(db_session, CHAT, [note("Alice", "nueva")], PEOPLE, max_notes=3, max_chars=120)
    _, kept_rows = person_memory.of_person(db_session, CHAT, "whatsapp:u1")
    kept = [row.text for row in kept_rows]
    assert "nueva" in kept and "vieja 0" in kept and len(kept) == 3


def test_a_long_note_is_cut(db_session):
    person_memory.remember(db_session, CHAT, [note("Alice", "x" * 500)], PEOPLE, max_notes=20, max_chars=30)
    assert len(person_memory.of_person(db_session, CHAT, "whatsapp:u1")[1][0].text) == 30


def test_a_note_about_the_group_stays_in_the_group(db_session):
    person_memory.remember(db_session, CHAT, [note("Alice", "le dicen Ali acá")], PEOPLE, **LIMITS)
    assert person_memory.notes_for(db_session, "whatsapp:otro", ["whatsapp:u1"]) == {}
    assert person_memory.notes_for(db_session, CHAT, ["whatsapp:u1"]) == {"whatsapp:u1": ["le dicen Ali acá"]}


def test_a_note_about_the_person_is_read_in_every_chat(db_session):
    person_memory.remember(db_session, CHAT, [note("Alice", "es veterinaria", everywhere=True)], PEOPLE, **LIMITS)
    for chat in (CHAT, "whatsapp:otro", "whatsapp:un-tercero"):
        assert person_memory.notes_for(db_session, chat, ["whatsapp:u1"]) == {"whatsapp:u1": ["es veterinaria"]}


def test_what_the_person_is_comes_before_what_the_group_made_of_it(db_session):
    person_memory.remember(db_session, CHAT, [note("Alice", "le dicen Ali")], PEOPLE, **LIMITS)
    person_memory.remember(db_session, CHAT, [note("Alice", "es veterinaria", everywhere=True)], PEOPLE, **LIMITS)
    assert person_memory.notes_for(db_session, CHAT, ["whatsapp:u1"]) == {
        "whatsapp:u1": ["es veterinaria", "le dicen Ali"]}


def test_each_kind_has_its_own_ceiling(db_session):
    """What a group knows about somebody must not crowd out what is true about them."""
    for i in range(3):
        person_memory.remember(db_session, CHAT, [note("Alice", f"del grupo {i}")], PEOPLE, max_notes=3, max_chars=120)
    person_memory.remember(db_session, CHAT, [note("Alice", "es veterinaria", everywhere=True)], PEOPLE,
                           max_notes=3, max_chars=120)
    everywhere, here = person_memory.of_person(db_session, CHAT, "whatsapp:u1")
    assert [row.text for row in everywhere] == ["es veterinaria"] and len(here) == 3


# --- what the chat can do about it ---

def test_memory_shows_both_kinds_apart(send, db_session):
    assert send("!memory")[0] == "Todavía no me acuerdo de nada tuyo."
    person_memory.remember(db_session, CHAT, [note("Alice", "le dicen Ali")], PEOPLE, **LIMITS)
    person_memory.remember(db_session, CHAT, [note("Alice", "es de Peñarol", everywhere=True)], PEOPLE, **LIMITS)
    person_memory.remember(db_session, "whatsapp:otro", [note("Alice", "en el otro grupo")],
                           PEOPLE, **LIMITS)

    shown = send("!memory")[0]
    assert "De vos, en cualquier chat:" in shown and "· es de Peñarol" in shown
    assert "Solo en este chat:" in shown and "· le dicen Ali" in shown
    assert "en el otro grupo" not in shown  # another group's notes are not yours to read here


def test_anybody_can_ask_about_anybody_in_the_chat(send, db_session):
    """These are notes about the people of a chat, taken from what was said in front of all of them."""
    person_memory.remember(db_session, CHAT, [note("Bob", "toca la guitarra")], PEOPLE, **LIMITS)
    person_memory.remember(db_session, CHAT, [note("Bob", "es de Durazno", everywhere=True)], PEOPLE, **LIMITS)

    shown = send("!memory Bob", participants=[{"user_id": "u1", "user_name": "Alice"},
                                              {"user_id": "u2", "user_name": "Bob"}])[0]
    assert shown.startswith("🧠 Lo que sé de Bob:")
    assert "*De Bob, en cualquier chat:*" in shown and "· es de Durazno" in shown
    assert "Solo en este chat:" in shown and "· toca la guitarra" in shown
    assert "memory forget" not in shown  # it is not Alice's memory to wipe


def test_a_name_is_read_generously_but_asks_when_it_is_not_sure(send, db_session):
    """"Lean" is "Leandro 1899": somebody asking about a person in the room is not a fact being filed."""
    members = [{"user_id": "u1", "user_name": "Alice"}, {"user_id": "u2", "user_name": "Leandro 1899"},
               {"user_id": "u3", "user_name": "Leticia"}]
    person_memory.remember(db_session, CHAT, [person_memory.Learned(name="Leandro 1899", text="tiene una tienda")],
                           {"Leandro 1899": "whatsapp:u2"}, **LIMITS)

    for written in ("Leandro", "lean", "LEANDRO 1899"):
        assert "· tiene una tienda" in send(f"!memory {written}", participants=members)[0], written
    assert send("!memory le", participants=members)[0] == "¿Cuál de todos? Puede ser Leandro 1899, Leticia."
    assert "No conozco a nadie así" in send("!memory Pedro", participants=members)[0]


def test_asking_about_somebody_the_bot_knows_nothing_of(send, db_session):
    members = [{"user_id": "u1", "user_name": "Alice"}, {"user_id": "u2", "user_name": "Bob"}]
    assert send("!memory Bob", participants=members)[0] == "Todavía no me acuerdo de nada de Bob."
    assert send("!memory")[0] == "Todavía no me acuerdo de nada tuyo."


def test_a_mention_is_who_is_being_asked_about(send, db_session):
    person_memory.remember(db_session, CHAT, [note("Bob", "toca la guitarra")], PEOPLE, **LIMITS)
    shown = send("!memory @u2", mentioned=[{"user_id": "u2", "user_name": "Bob"}])[0]
    assert shown.startswith("🧠 Lo que sé de Bob:") and "· toca la guitarra" in shown


def test_forgetting_is_held_with_the_admins_for_now(send, db_session):
    """Reading your own notes is the point of the command; wiping them is held back while the feature is new."""
    person_memory.remember(db_session, CHAT, [note("Alice", "le dicen Ali")], PEOPLE, **LIMITS)

    turned_down = send("!memory forget")[0]
    assert "solo para los admins" in turned_down
    assert db_session.scalars(select(PersonNote)).first() is not None  # nothing was deleted
    assert "memory forget" not in send("!memory")[0]  # nor is it offered to somebody who would be turned down

    # An admin forgets their own, and is the only one offered it
    admin = {"user": "admin1@lid", "name": "Joaquín", "sender_is_group_admin": True}
    person_memory.remember(db_session, CHAT, [note("Joaquín", "algo suyo")],
                           {"Joaquín": "whatsapp:admin1@lid"}, **LIMITS)
    assert "memory forget" in send("!memory", **admin)[0]
    assert send("!memory forget", **admin)[0] == "🧽 Listo, me olvidé de 1 cosa tuya, en todos los chats."
    # ...and only their own: Alice's note is still there
    assert [row.user_key for row in db_session.scalars(select(PersonNote))] == ["whatsapp:u1"]


def test_a_chat_can_turn_it_off(send, db_session):
    person_memory.remember(db_session, CHAT, [note("Alice", "algo")], PEOPLE, **LIMITS)
    assert send("!config memory off", user="admin1@lid", sender_is_group_admin=True)
    assert send("!memory")[0].startswith("En este chat tengo la memoria apagada")


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
    everywhere, here = person_memory.of_person(db_session, CHAT, "whatsapp:u1")
    assert [row.text for row in everywhere + here] == ["es de Peñarol"]  # ...and it was kept


def test_an_answer_that_learned_nothing_says_so_too(client, db_session, settings, monkeypatch, caplog):
    """The common case, and it has to be readable: a silence would look exactly like learning being off."""
    monkeypatch.setattr(claude_command, "session_factory",
                        sessionmaker(bind=db_session.get_bind(), autoflush=False, expire_on_commit=False))
    monkeypatch.setattr(claude_runner, "execute_public", lambda *a, **k: claude_runner.ClaudeResult(
        "Ahí va la respuesta.\n[memoria] -", None, None))

    with caplog.at_level("INFO", logger="tox.claude"):
        (reply,) = raw(client, "!claude algo")
        deadline = time.time() + 5
        while time.time() < deadline:
            if client.get(f"/api/v1/jobs/{reply['job']}", headers=HEADERS).json()["status"] == "done":
                break
            time.sleep(0.02)
    assert "nothing worth keeping came up" in caplog.text
    assert "without the note block" not in caplog.text  # it did follow the request


def test_an_answer_that_ignored_the_request_says_so_in_the_log(client, db_session, settings, monkeypatch, caplog):
    """The block is asked for on every answer, so one without it is the model not complying -- not a quiet chat."""
    monkeypatch.setattr(claude_command, "session_factory",
                        sessionmaker(bind=db_session.get_bind(), autoflush=False, expire_on_commit=False))
    monkeypatch.setattr(claude_runner, "execute_public", lambda *a, **k: claude_runner.ClaudeResult(
        "Ahí va la respuesta, y nada más.", None, None))

    with caplog.at_level("INFO", logger="tox.claude"):
        (reply,) = raw(client, "!claude algo")
        deadline = time.time() + 5
        while time.time() < deadline:
            if client.get(f"/api/v1/jobs/{reply['job']}", headers=HEADERS).json()["status"] == "done":
                break
            time.sleep(0.02)
    assert "without the note block" in caplog.text


def test_the_prompt_carries_what_is_known(db_session, settings):
    from app.schemas import IncomingMessage
    from app.services import claude_context

    person_memory.remember(db_session, CHAT, [note("Alice", "es de Peñarol")], PEOPLE, **LIMITS)
    message = IncomingMessage(platform="whatsapp", chat_id="g1", user_id="u1", user_name="Alice",
                              text="!claude hola", is_group=True,
                              participants=[{"user_id": "u1", "user_name": "Alice"}])
    context = claude_context.gather(db_session, settings, message, with_history=True)
    assert context.notes == {"Alice": ["es de Peñarol"]}
    assert "es de Peñarol" in context.prompt("hola")
    # What is asked OF the model never travels with the question: the public prompt tells it to obey nothing that
    # comes with the question, so the request for notes lives in the system prompt.
    assert person_memory.ASK not in context.prompt("hola")


def test_the_notes_are_asked_for_in_the_system_prompt_or_not_at_all(settings):
    asked = claude_runner.PUBLIC_SYSTEM_PROMPT.format(limit=800)
    assert person_memory.ASK not in asked, "nothing is asked for by default"

    seen = {}
    monkey = lambda question, prompt, budget, timeout, s: seen.setdefault("prompt", prompt) or claude_runner.ClaudeResult("ok", None, None)
    original = claude_runner._chat_only
    claude_runner._chat_only = monkey
    try:
        claude_runner.execute_public("hola", settings, None, learn=True)
        assert person_memory.ASK in seen["prompt"]
        seen.clear()
        claude_runner.execute_public("hola", settings, None, learn=False)
        assert person_memory.ASK not in seen["prompt"]
    finally:
        claude_runner._chat_only = original


def test_the_notes_are_read_before_the_answer_is_cut(client, db_session, settings, monkeypatch):
    """They are at the end of the answer, and the answer is cut to CLAUDE_MAX_ANSWER_CHARS before it is sent."""
    monkeypatch.setattr(claude_command, "session_factory",
                        sessionmaker(bind=db_session.get_bind(), autoflush=False, expire_on_commit=False))
    object.__setattr__(settings, "CLAUDE_MAX_ANSWER_CHARS", 60)
    long_answer = "x" * 200 + "\n[memoria global] Alice: es veterinaria"
    monkeypatch.setattr(claude_runner, "execute_public",
                        lambda *a, **k: claude_runner.ClaudeResult(long_answer, None, None))

    (reply,) = raw(client, "!claude algo")
    deadline = time.time() + 5
    while time.time() < deadline:
        job = client.get(f"/api/v1/jobs/{reply['job']}", headers=HEADERS).json()
        if job["status"] == "done":
            break
        time.sleep(0.02)
    assert len(job["replies"][0]["text"]) == 60  # the answer was cut...
    everywhere, _ = person_memory.of_person(db_session, CHAT, "whatsapp:u1")
    assert [row.text for row in everywhere] == ["es veterinaria"]  # ...and the note survived it


@pytest.fixture(autouse=True)
def no_real_cli(monkeypatch):
    monkeypatch.setattr(claude_runner, "execute",
                        lambda *a, **k: claude_runner.ClaudeResult("ok", None, None))


def test_the_switch_takes_off_and_on_as_well_as_si_and_no(send, db_session):
    """`!config memory off` is the way it is written; sí/no still work, like every other yes/no setting."""
    admin = {"user": "admin1@lid", "sender_is_group_admin": True}
    assert "no" in send("!config memory off", **admin)[0].lower()
    assert send("!memory")[0].startswith("En este chat tengo la memoria apagada")
    send("!config memory on", **admin)
    assert send("!memory")[0] == "Todavía no me acuerdo de nada tuyo."


def test_the_admins_own_questions_are_learned_from_in_a_group(client, db_session, settings, monkeypatch):
    """A group is people talking, and an admin is one of them: their questions are how Tox gets to know THEM. What
    they may do to the PC has nothing to do with it."""
    monkeypatch.setattr(claude_command, "session_factory",
                        sessionmaker(bind=db_session.get_bind(), autoflush=False, expire_on_commit=False))
    seen = {}

    def fake_execute(question, session_id, settings_, tone=None, learn=False):
        seen["learn"] = learn
        return claude_runner.ClaudeResult("Ahí va.\n[memoria global] Joaquín: vive en Salto", None, None)

    monkeypatch.setattr(claude_runner, "execute", fake_execute)
    members = [{"user_id": "admin1@lid", "user_name": "Joaquín"}]
    (reply,) = raw(client, "!claude algo", user="admin1@lid", name="Joaquín", participants=members,
                   sender_is_group_admin=True)
    deadline = time.time() + 5
    while time.time() < deadline:
        if client.get(f"/api/v1/jobs/{reply['job']}", headers=HEADERS).json()["status"] == "done":
            break
        time.sleep(0.02)
    assert seen["learn"] is True  # the full-access prompt was asked for the notes too
    everywhere, _ = person_memory.of_person(db_session, CHAT, "whatsapp:admin1@lid")
    assert [row.text for row in everywhere] == ["vive en Salto"]


def test_a_private_chat_is_not_a_group_and_keeps_nothing(client, db_session, settings, monkeypatch):
    """One person driving the machine, with no group to know: there is nobody there to remember things about."""
    monkeypatch.setattr(claude_command, "session_factory",
                        sessionmaker(bind=db_session.get_bind(), autoflush=False, expire_on_commit=False))
    monkeypatch.setattr(claude_runner, "execute", lambda *a, **k: claude_runner.ClaudeResult(
        "Ahí va.\n[memoria global] Joaquín: vive en Salto", None, None))

    (reply,) = raw(client, "!claude algo", user="admin1@lid", name="Joaquín", chat="admin1@lid", is_group=False,
                   sender_is_group_admin=True)
    deadline = time.time() + 5
    while time.time() < deadline:
        if client.get(f"/api/v1/jobs/{reply['job']}", headers=HEADERS).json()["status"] == "done":
            break
        time.sleep(0.02)
    assert db_session.scalars(select(PersonNote)).all() == []


def test_the_personal_mode_is_never_learned_from(client, db_session, settings, monkeypatch):
    """There the bot writes as the owner, and the other side is one person who never asked for any of this."""
    monkeypatch.setattr(claude_command, "session_factory",
                        sessionmaker(bind=db_session.get_bind(), autoflush=False, expire_on_commit=False))
    monkeypatch.setattr(claude_runner, "execute_public", lambda *a, **k: claude_runner.ClaudeResult(
        "Ahí va.\n[memoria] Ana: vive en Salto", None, None))

    body = {"platform": "whatsapp", "chat_id": "un-contacto", "user_id": "u9", "user_name": "Ana",
            "text": ".b c algo", "is_group": False, "personal": True}
    reply = client.post("/api/v1/messages", headers=HEADERS, json=body).json()["replies"][0]
    deadline = time.time() + 5
    while time.time() < deadline:
        job = client.get(f"/api/v1/jobs/{reply['job']}", headers=HEADERS).json()
        if job["status"] == "done":
            break
        time.sleep(0.02)
    assert job["replies"][0]["text"] == "Ahí va."  # the line is still taken off
    assert db_session.scalars(select(PersonNote)).first() is None  # ...and nothing was kept
