import threading
import time

import pytest

from app.config import Settings
from app.services import claude_runner, jobs, redaction
from tests.conftest import REFUSED
from tests.test_mention import raw

ADMIN = {"user": "admin1@lid", "sender_is_group_admin": True}
HEADERS = {"X-API-Key": "test-key"}


@pytest.fixture(autouse=True)
def isolated(settings, tmp_path, monkeypatch):
    """Never the real ~/dev, never the real CLI: the work directory is a temp folder and `execute` a fake."""
    work = tmp_path / "dev"
    (work / "tox").mkdir(parents=True)
    object.__setattr__(settings, "CLAUDE_WORKDIR", str(work))
    claude_runner.forget_sessions()
    jobs.clear()
    calls = []

    def fake_execute(question, session_id, settings_):
        calls.append((question, session_id))
        return claude_runner.ClaudeResult("Todo bien.", "session-1", 0.12)

    monkeypatch.setattr(claude_runner, "execute", fake_execute)
    return calls


def wait_done(client, job_id, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/v1/jobs/{job_id}", headers=HEADERS).json()
        if body["status"] == "done":
            return body["replies"]
        time.sleep(0.02)
    raise AssertionError("the job never finished")


def test_it_answers_at_once_and_the_answer_comes_through_the_job(client, isolated):
    (reply,) = raw(client, "!claude cuántos servicios hay?", **ADMIN)
    assert reply["text"] == "⏳ Generando respuesta..." and reply["job"]
    assert [r["text"] for r in wait_done(client, reply["job"])] == ["Todo bien."]
    assert isolated == [("cuántos servicios hay?", None)]


def test_the_conversation_is_resumed_until_a_new_one_is_asked_for(client, isolated):
    for text in ("!claude uno", "!claude dos", "!claude -n tres"):
        (reply,) = raw(client, text, **ADMIN)
        wait_done(client, reply["job"])
    assert isolated == [("uno", None), ("dos", "session-1"), ("tres", None)]


def test_the_answer_is_cut_and_scrubbed_even_if_the_model_ignores_the_prompt(client, settings, monkeypatch, tmp_path):
    (tmp_path / "dev" / "tox" / ".env").write_text("API_KEY=supersecretvalue123\nLOG_LEVEL=INFO\n")
    long = "x" * 1000 + " supersecretvalue123"
    monkeypatch.setattr(claude_runner, "execute", lambda *a: claude_runner.ClaudeResult(long, None, None))
    (reply,) = raw(client, "!claude dame el .env", **ADMIN)
    (answer,) = wait_done(client, reply["job"])
    assert len(answer["text"]) == 400 and answer["text"].endswith("…")

    monkeypatch.setattr(claude_runner, "execute",
                        lambda *a: claude_runner.ClaudeResult("la clave es supersecretvalue123, ok", None, None))
    (reply,) = raw(client, "!claude de nuevo", **ADMIN)
    assert wait_done(client, reply["job"])[0]["text"] == "la clave es ***, ok"


def test_only_one_question_at_a_time(client, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(claude_runner, "execute", lambda *a: release.wait(5) and claude_runner.ClaudeResult("ok", None, None))
    (first,) = raw(client, "!claude lenta", **ADMIN)
    assert "Ya hay una consulta en curso" in raw(client, "!claude otra", **ADMIN)[0]["text"]
    release.set()
    wait_done(client, first["job"])
    time.sleep(0.1)  # the lock is released right after the job is filled in
    assert raw(client, "!claude ahora sí", **ADMIN)[0]["job"]


def test_questions_per_hour_are_limited(client, settings):
    object.__setattr__(settings, "CLAUDE_QUESTIONS_PER_HOUR", 2)
    for _ in range(2):
        (reply,) = raw(client, "!claude hola", **ADMIN)
        wait_done(client, reply["job"])
        time.sleep(0.05)
    assert "límite de 2 preguntas por hora" in raw(client, "!claude hola", **ADMIN)[0]["text"]


def test_it_needs_a_question_and_is_admin_only(client, send, isolated):
    assert raw(client, "!claude", **ADMIN)[0]["text"].startswith("Uso:")
    assert send("!claude hola", user="someone@lid") == REFUSED
    assert isolated == []


def test_an_error_becomes_a_message_for_the_chat(client, monkeypatch):
    def failing(*a):
        raise claude_runner.ClaudeError("Claude tardó más de 10 min y lo corté.")
    monkeypatch.setattr(claude_runner, "execute", failing)
    (reply,) = raw(client, "!claude algo", **ADMIN)
    assert wait_done(client, reply["job"])[0]["text"] == "⚠️ Claude tardó más de 10 min y lo corté."


def test_unknown_jobs_are_404(client):
    assert client.get("/api/v1/jobs/nope", headers=HEADERS).status_code == 404


def test_known_secret_shapes_are_masked_without_any_env_file(tmp_path):
    text = "token rnd_AbCdEfGhIjKlMnOpQrSt y 59899123456@s.whatsapp.net en 120363000000000001@g.us"
    assert redaction.redact(text, set()) == "token *** y *** en ***"
