import threading
import time

import pytest

from app.config import Settings
from app.services import claude_runner, jobs, redaction
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
    # no placeholder message: the bot reacts with ⏳ to the command and sends the answer when the job is done
    assert reply["text"] == "" and reply["reaction"] == "⏳" and reply["job"]
    done = wait_done(client, reply["job"])
    assert [r["text"] for r in done] == ["Todo bien.", ""] and done[1]["reaction"] == "✅"  # ✅ replaces the ⏳
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
    answer = wait_done(client, reply["job"])[0]
    assert len(answer["text"]) == 400 and answer["text"].endswith("…")

    monkeypatch.setattr(claude_runner, "execute",
                        lambda *a: claude_runner.ClaudeResult("la clave es supersecretvalue123, ok", None, None))
    (reply,) = raw(client, "!claude de nuevo", **ADMIN)
    assert wait_done(client, reply["job"])[0]["text"] == "la clave es ***, ok"


def test_admin_questions_go_one_by_one_and_the_next_waits_its_turn(client, monkeypatch):
    release = threading.Event()
    order = []

    def slow(question, session_id, settings_):
        order.append(question)
        release.wait(5)
        return claude_runner.ClaudeResult("ok", None, None)

    monkeypatch.setattr(claude_runner, "execute", slow)
    (first,) = raw(client, "!claude lenta", **ADMIN)
    (second,) = raw(client, "!claude otra", **ADMIN)
    assert second["job"]  # not refused: it waits
    time.sleep(0.2)
    assert order == ["lenta"]  # and does not start while the first one runs
    release.set()
    wait_done(client, first["job"])
    wait_done(client, second["job"])
    assert order == ["lenta", "otra"]


def test_somebody_is_told_so_only_when_the_waiting_line_is_full_too(client, settings, monkeypatch):
    object.__setattr__(settings, "CLAUDE_QUEUE_MAX", 0)
    release = threading.Event()
    monkeypatch.setattr(claude_runner, "execute", lambda *a: release.wait(5) and claude_runner.ClaudeResult("ok", None, None))
    (first,) = raw(client, "!claude lenta", **ADMIN)
    assert "Hay muchas consultas" in raw(client, "!claude otra", **ADMIN)[0]["text"]
    release.set()
    wait_done(client, first["job"])
    time.sleep(0.1)  # the place is freed right after the job is filled in
    assert raw(client, "!claude ahora sí", **ADMIN)[0]["job"]


def test_the_no_access_questions_run_side_by_side(client, monkeypatch):
    both_running = threading.Barrier(2, timeout=5)  # only passes if two questions are inside at the same time

    def together(question, settings_):
        both_running.wait()
        return claude_runner.ClaudeResult("ok", None, None)

    monkeypatch.setattr(claude_runner, "execute_public", together)
    jobs_ = [raw(client, f"!claude pregunta {n}", user=f"friend{n}@lid", sender_is_group_admin=False)[0]["job"]
             for n in (1, 2)]
    assert [wait_done(client, job)[0]["text"] for job in jobs_] == ["ok", "ok"]


def test_questions_per_hour_are_limited(client, settings):
    object.__setattr__(settings, "CLAUDE_QUESTIONS_PER_HOUR", 2)
    for _ in range(2):
        (reply,) = raw(client, "!claude hola", **ADMIN)
        wait_done(client, reply["job"])
        time.sleep(0.05)
    assert "límite de 2 preguntas por hora" in raw(client, "!claude hola", **ADMIN)[0]["text"]


def test_it_needs_a_question(client, isolated):
    assert raw(client, "!claude", **ADMIN)[0]["text"].startswith("Uso:")
    assert isolated == []


def test_an_error_becomes_a_message_for_the_chat(client, monkeypatch):
    def failing(*a):
        raise claude_runner.ClaudeError("Claude tardó más de 10 min y lo corté.")
    monkeypatch.setattr(claude_runner, "execute", failing)
    (reply,) = raw(client, "!claude algo", **ADMIN)
    done = wait_done(client, reply["job"])
    assert done[0]["text"] == "⚠️ Claude tardó más de 10 min y lo corté." and done[1]["reaction"] == "⚠️"


# --- everybody else: a chat with no access to the PC ---

PERSON = {"user": "friend@lid", "sender_is_group_admin": False}


@pytest.fixture
def public(monkeypatch):
    calls = []

    def fake_public(question, settings_):
        calls.append(question)
        return claude_runner.ClaudeResult("Escribile a alguien@ejemplo.com", "never-kept", 0.01)

    monkeypatch.setattr(claude_runner, "execute_public", fake_public)
    return calls


def test_anybody_can_ask_but_only_gets_the_no_access_mode(client, isolated, public):
    (reply,) = raw(client, "!claude capital de Francia?", **PERSON)
    assert reply["job"]
    # no PC access: the full-access runner is never used, and the account's e-mail is masked in the answer
    assert wait_done(client, reply["job"])[0]["text"] == "Escribile a ***"
    assert public == ["capital de Francia?"] and isolated == []
    assert raw(client, "!claude", **PERSON)[0]["text"] == "Uso: !claude <pregunta>"  # no admin extras


def test_a_listed_admin_who_is_not_admin_of_the_group_gets_the_no_access_mode(client, isolated, public):
    (reply,) = raw(client, "!claude hola", user="admin1@lid", sender_is_group_admin=False)
    wait_done(client, reply["job"])
    assert public == ["hola"] and isolated == []


def test_the_admins_conversation_is_never_seen_by_the_others(client, isolated, public):
    wait_done(client, raw(client, "!claude uno", **ADMIN)[0]["job"])
    wait_done(client, raw(client, "!claude dos", **PERSON)[0]["job"])
    time.sleep(0.05)
    wait_done(client, raw(client, "!claude tres", **ADMIN)[0]["job"])
    # (the two people share a display name, so the group context is added to the question: only its end is compared)
    assert [session for _, session in isolated] == [None, "session-1"]
    assert isolated[0][0] == "uno" and isolated[1][0].endswith("tres") and len(public) == 1 and public[0].endswith("dos")


def test_the_others_have_their_own_limits_and_do_not_use_up_the_admins(client, settings, isolated, public):
    object.__setattr__(settings, "CLAUDE_PUBLIC_QUESTIONS_PER_PERSON_PER_HOUR", 1)
    wait_done(client, raw(client, "!claude una", **PERSON)[0]["job"])
    time.sleep(0.05)
    assert "límite de 1 preguntas por hora por persona" in raw(client, "!claude otra", **PERSON)[0]["text"]
    other = raw(client, "!claude de otra persona", user="friend2@lid", sender_is_group_admin=False)[0]
    assert other["job"]  # the limit is per person
    wait_done(client, other["job"])
    time.sleep(0.05)

    object.__setattr__(settings, "CLAUDE_PUBLIC_QUESTIONS_PER_HOUR", 2)  # the others' pool: 2 used, so it is full
    assert "límite de 2 preguntas por hora." in raw(client, "!claude tercera", user="friend3@lid",
                                                    sender_is_group_admin=False)[0]["text"]
    object.__setattr__(settings, "CLAUDE_QUESTIONS_PER_HOUR", 1)  # the admins' pool has not been touched by them
    assert raw(client, "!claude admin", **ADMIN)[0]["job"]


def test_the_no_access_mode_really_runs_without_tools_or_the_projects_folder(settings, monkeypatch):
    seen = {}

    def fake_run(argv, question, cwd, timeout):
        seen.update(argv=argv, cwd=cwd, timeout=timeout, exists_while_running=cwd.is_dir())
        return type("Done", (), {"stdout": '{"result": "ok", "total_cost_usd": 0.01}', "stderr": "", "returncode": 0})()

    monkeypatch.setattr(claude_runner, "_run", fake_run)
    monkeypatch.setattr(claude_runner, "_binary", lambda s: "claude")
    assert claude_runner.execute_public("hola", settings).text == "ok"

    argv = seen["argv"]
    assert argv[argv.index("--tools") + 1] == ""  # an empty list: no built-in tool at all
    assert "--strict-mcp-config" in argv and "--no-session-persistence" in argv
    assert "bypassPermissions" not in argv and "--resume" not in argv
    assert str(seen["cwd"]) != str(claude_runner.workdir(settings)) and seen["exists_while_running"]
    assert not seen["cwd"].exists()  # the empty temporary folder is removed afterwards


def test_unknown_jobs_are_404(client):
    assert client.get("/api/v1/jobs/nope", headers=HEADERS).status_code == 404


def test_known_secret_shapes_are_masked_without_any_env_file(tmp_path):
    text = "token rnd_AbCdEfGhIjKlMnOpQrSt y 59899123456@s.whatsapp.net en 120363000000000001@g.us"
    assert redaction.redact(text, set()) == "token *** y *** en ***"


def test_the_no_access_prompt_follows_the_tone_and_always_keeps_its_limits(settings, monkeypatch):
    prompts = {}

    def fake_run(argv, question, cwd, timeout):
        prompts[settings.CLAUDE_TONE] = argv[argv.index("--append-system-prompt") + 1]
        return type("Done", (), {"stdout": '{"result": "ok"}', "stderr": "", "returncode": 0})()

    monkeypatch.setattr(claude_runner, "_run", fake_run)
    monkeypatch.setattr(claude_runner, "_binary", lambda s: "claude")
    for tone in ("normal", "picante", "salado"):
        object.__setattr__(settings, "CLAUDE_TONE", tone)
        claude_runner.execute_public("hola", settings)
    assert "Tono:" not in prompts["normal"]
    assert "SALADO" in prompts["salado"] and "SALADO" not in prompts["picante"] and "picante" in prompts["picante"]
    assert "Lo único que no hacés" in prompts["salado"] and "Límites que no cruzás" in prompts["picante"]


def test_the_admins_mode_gets_the_tone_too_without_losing_its_rules(settings):
    spicy = claude_runner._admin_prompt(settings)
    object.__setattr__(settings, "CLAUDE_TONE", "normal")
    plain = claude_runner._admin_prompt(settings)
    assert "SALADO" in spicy and "NUNCA incluyas" in spicy and "correcta y completa" in spicy
    assert "SALADO" not in plain and "NUNCA incluyas" in plain
