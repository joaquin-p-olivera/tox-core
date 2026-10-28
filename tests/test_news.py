"""Release notes announced on their own. What matters is that nothing is ever announced twice, that a fresh
database announces nothing at all, and that a half-written entry never reaches a chat."""

import json

import pytest

from app.models import NewsState, PendingAlert
from app.services import news

API_KEY = "test-key"
GROUPS = "1@g.us,2@g.us"


@pytest.fixture
def notes(monkeypatch, tmp_path):
    """A release-notes file of its own per test: the real one is the owner's and keeps changing."""
    path = tmp_path / "news.json"

    def write(entries: list[dict]) -> None:
        path.write_text(json.dumps(entries), encoding="utf-8")

    def fake_load(name):
        assert name == news.FILE
        return json.loads(path.read_text(encoding="utf-8"))

    monkeypatch.setattr(news, "load_json", fake_load)
    write([{"id": "a", "title": "lo primero", "text": "Texto A"},
           {"id": "b", "text": "Texto B", "versions": {"whatsapp-bot": "1.0.0", "api-core": "1.0.0"}}])
    return write


@pytest.fixture
def alerts(settings):
    object.__setattr__(settings, "ALERT_WHATSAPP_GROUP_JIDS", GROUPS)
    return settings


def queued(db) -> list[str]:
    return [row.text for row in db.query(PendingAlert).order_by(PendingAlert.id)]


def test_the_first_run_announces_nothing_and_remembers_where_the_file_stands(notes, alerts, db_session):
    assert news.queue_due(db_session, alerts) == 0
    assert queued(db_session) == []
    assert db_session.get(NewsState, news.KEY).last_id == "b"


def test_what_the_file_gains_is_announced_once_to_every_chat(notes, alerts, db_session):
    news.queue_due(db_session, alerts)  # the first run only marks where we are
    notes([{"id": "a", "text": "Texto A"}, {"id": "b", "text": "Texto B"},
           {"id": "c", "title": "lo nuevo", "text": "Texto C"}])

    assert news.queue_due(db_session, alerts) == 2  # one message per chat
    assert queued(db_session) == ["✨ Novedades de Tox\n\n*lo nuevo*\nTexto C"] * 2
    assert db_session.get(NewsState, news.KEY).last_id == "c"
    # and never again
    assert news.queue_due(db_session, alerts) == 0


def test_several_entries_at_once_are_capped(notes, alerts, db_session, monkeypatch):
    news.queue_due(db_session, alerts)
    monkeypatch.setattr(news, "MAX_AT_ONCE", 2)
    notes([{"id": "a", "text": "A"}, {"id": "b", "text": "B"},
           {"id": "c", "text": "C"}, {"id": "d", "text": "D"}, {"id": "e", "text": "E"}])

    news.queue_due(db_session, alerts)
    assert [text.splitlines()[-1] for text in queued(db_session)] == ["D", "D", "E", "E"]
    assert db_session.get(NewsState, news.KEY).last_id == "e"  # the ones skipped are not announced later either


def test_a_half_written_entry_is_skipped_instead_of_sent(notes, alerts, db_session):
    news.queue_due(db_session, alerts)
    notes([{"id": "a", "text": "A"}, {"id": "b", "text": "B"},
           {"id": "roto", "title": "sin texto"}, {"text": "sin id"}, {"id": "c", "text": "Texto C"}])

    news.queue_due(db_session, alerts)
    assert [text.splitlines()[-1] for text in queued(db_session)] == ["Texto C", "Texto C"]


def test_with_no_chat_configured_nothing_is_lost(notes, settings, db_session):
    object.__setattr__(settings, "ALERT_WHATSAPP_GROUP_JIDS", "")
    news.queue_due(db_session, settings)  # marks where we are
    notes([{"id": "a", "text": "A"}, {"id": "b", "text": "B"}, {"id": "c", "text": "Texto C"}])

    assert news.queue_due(db_session, settings) == 0
    assert db_session.get(NewsState, news.KEY).last_id == "b", "the marker stays put"
    # Turning the destinations on later still announces it
    object.__setattr__(settings, "ALERT_WHATSAPP_GROUP_JIDS", GROUPS)
    assert news.queue_due(db_session, settings) == 2


def test_a_marker_that_is_not_in_the_file_announces_from_the_start(notes, alerts, db_session):
    """Which is how a machine that has announced nothing yet is told to start from the beginning."""
    db_session.add(NewsState(key=news.KEY, last_id=""))
    db_session.commit()

    assert news.queue_due(db_session, alerts) == 4  # both entries, to both chats
    assert [text.splitlines()[-1] for text in queued(db_session)] == ["Texto A", "Texto A", "Texto B", "Texto B"]


def test_the_poll_is_what_sends_them(notes, alerts, db_session, client):
    news.queue_due(db_session, alerts)
    notes([{"id": "a", "text": "A"}, {"id": "b", "text": "B"}, {"id": "c", "text": "Texto C"}])

    body = client.get("/api/v1/alerts/pending", params={"platform": "whatsapp"},
                      headers={"X-API-Key": API_KEY}).json()
    assert [row["chat_id"] for row in body] == ["1@g.us", "2@g.us"]
    assert body[0]["text"].endswith("Texto C")


# --- !novedades ---

def test_novedades_shows_the_latest(notes, send):
    assert send("!novedades")[0] == ("✨ Novedades de Tox\n\nwhatsapp-bot 1.0.0\napi-core 1.0.0\n\nTexto B")


def test_the_versions_are_named_in_a_fixed_order_and_only_when_given(notes, send):
    assert news.versions({"versions": {"api-core": "1.2.0", "whatsapp-bot": "1.1.0"}}) == [
        "whatsapp-bot 1.1.0", "api-core 1.2.0"]
    assert news.versions({"versions": {"api-core": "1.2.0"}}) == ["api-core 1.2.0"]  # only the one that changed
    assert news.versions({}) == [] and news.versions({"versions": "1.0.0"}) == []


def test_novedades_with_nothing_to_tell(notes, send):
    notes([])
    assert send("!cambios")[0] == "Todavía no hay novedades para contar."


def test_probar_is_for_admins_and_shows_what_would_go_out(notes, alerts, send, db_session):
    assert send("!novedades -probar")[0] == "Eso es solo para los admins."

    news.queue_due(db_session, alerts)
    notes([{"id": "a", "text": "A"}, {"id": "b", "text": "B"}, {"id": "c", "text": "Texto C"}])
    reply = send("!novedades -probar", user="admin1@lid", sender_is_group_admin=True)[0]
    assert reply.startswith("Esto saldría a 2 chat(s):") and "Texto C" in reply


def test_probar_when_everything_is_already_out(notes, alerts, send, db_session):
    news.queue_due(db_session, alerts)
    reply = send("!novedades -probar", user="admin1@lid", sender_is_group_admin=True)[0]
    assert reply == "No hay novedades pendientes. Los 2 chats configurados ya están al día."


def test_the_real_file_is_valid_and_chat_ready():
    """The entries that ship with the repo: an id each, no duplicates, and nothing a chat should not read."""
    entries = news.entries()
    ids = [entry["id"] for entry in entries]
    assert ids and len(ids) == len(set(ids))
    for entry in entries:
        assert len(entry["text"]) <= 1500, entry["id"]  # a chat message, not a CHANGELOG
        assert set(entry.get("versions") or {}) <= set(news.APPS), entry["id"]
        assert news.message(entry).startswith(news.HEADER)
