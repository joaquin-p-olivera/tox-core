import time
from datetime import datetime, timedelta, timezone

import pytest

from app.commands import history as history_command
from app.services import chat_history, claude_runner, jobs
from tests.conftest import API_KEY

HEADERS = {"X-API-Key": API_KEY}
CHAT = "5981@s.whatsapp.net"


# 15:00 UTC is 12:00 in Montevideo: "today" starts at 03:00 UTC, whatever time the tests really run at
NOW = datetime(2026, 10, 13, 15, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    jobs.clear()
    monkeypatch.setattr(history_command, "utcnow", lambda: NOW.replace(tzinfo=None))
    calls = []

    def fake_summary(transcript, settings):
        calls.append(transcript)
        return claude_runner.ClaudeResult("Hablaron de la cena.", None, 0.02)

    monkeypatch.setattr(claude_runner, "execute_summary", fake_summary)
    return calls


def personal(client, text, *, chat=CHAT, aliases=(), is_group=False, is_personal=True):
    response = client.post(
        "/api/v1/messages", headers=HEADERS,
        json={"platform": "whatsapp", "chat_id": chat, "chat_aliases": list(aliases), "user_id": "me@lid",
              "user_name": "Yo", "text": text, "is_group": is_group, "personal": is_personal})
    assert response.status_code == 200, response.text
    return response.json()["replies"]


def message(chat, number, text, *, minutes_ago=1, sender="amigo@lid"):
    sent = NOW - timedelta(minutes=minutes_ago)
    return {"chat_id": chat, "message_id": f"m{number}", "sender_id": sender, "sender_name": "Ana", "text": text,
            "sent_at": sent.isoformat()}


def save(client, *messages):
    response = client.post("/api/v1/history", headers=HEADERS, json={"platform": "whatsapp", "messages": list(messages)})
    assert response.status_code == 200, response.text
    return response.json()["stored"]


def chats(client):
    return client.get("/api/v1/history/chats", headers=HEADERS, params={"platform": "whatsapp"}).json()["chats"]


def wait_done(client, job_id, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/v1/jobs/{job_id}", headers=HEADERS).json()
        if body["status"] == "done":
            return body["replies"]
        time.sleep(0.02)
    raise AssertionError("the job never finished")


def test_it_only_saves_the_chats_that_were_turned_on_and_says_nothing_in_the_chat(client):
    assert save(client, message(CHAT, 1, "hola")) == 0  # not on yet: ignored

    (reply,) = personal(client, ".b h -on")
    assert reply["text"] == "" and reply["reaction"] == "✅"  # no announcement in the chat
    assert chats(client) == [CHAT]

    assert save(client, message(CHAT, 1, "hola"), message("otro@s.whatsapp.net", 2, "no")) == 1
    assert save(client, message(CHAT, 1, "hola")) == 0  # the same message id is kept once

    (status,) = personal(client, ".b h -status")
    assert "activado" in status["text"] and "Mensajes guardados de este chat: 1" in status["text"]

    personal(client, ".b h -off")
    assert chats(client) == [] and save(client, message(CHAT, 3, "chau")) == 0
    assert "desactivado" in personal(client, ".b h -status")[0]["text"]
    assert "Mensajes guardados de este chat: 1" in personal(client, ".b h -status")[0]["text"]  # kept

    personal(client, ".b h -delete")
    assert "Mensajes guardados de este chat: 0" in personal(client, ".b h -status")[0]["text"]


def test_the_flags_are_the_only_form_and_the_old_name_still_works(client):
    assert personal(client, ".b h on")[0]["text"].startswith("Uso: .b h -on")  # a bare word is not a flag
    assert chats(client) == []
    personal(client, ".b h –on")  # the en dash a phone keyboard makes
    assert chats(client) == [CHAT]
    personal(client, ".b historial --off")
    assert chats(client) == []


def test_the_help_lists_them_only_for_the_personal_bot_and_with_their_flags(client, send):
    (help_text,) = [r["text"] for r in personal(client, ".b ayuda")]
    assert ".b h — " in help_text and ".b res — " in help_text
    assert "-today" in help_text and "-all" in help_text and "-delete" in help_text
    assert "hoy" not in personal(client, ".b ayuda res")[0]["text"].split("\n")[0]  # the usage line is English flags
    assert personal(client, ".b ayuda res")[0]["text"].startswith(".b res [-today | -all | 6h | 2d | 150]")
    assert personal(client, ".b ayuda h")[0]["text"].startswith(".b h -on | -off | -status | -delete")

    assert "!h —" not in send("!ayuda")[0] and "!res —" not in send("!ayuda")[0]  # not in the groups bot
    assert send("!ayuda res")[0].startswith("No conozco ese comando")


def test_an_admin_can_use_them_through_the_groups_bot_and_nobody_else(client, send):
    group = "120363000000000001@g.us"
    assert send("!h -on", user="admin1@lid", chat=group) == ["✅"]
    assert chats(client) == [group]
    assert save(client, message(group, 1, "hola", sender="otro@lid")) == 1

    assert send("!h -status", user="admin1@lid", chat=group)[0].startswith("📝 Historial de este chat: activado")
    assert "!h — " in send("!ayuda", user="admin1@lid", chat=group)[0]  # an admin sees them in the help
    assert "!h — " not in send("!ayuda", user="u1", chat=group)[0]

    assert send("!h -off", user="u1", chat=group) == ["🚫"]  # a member who is not an admin
    assert send("!res", user="u1", chat=group) == ["🚫"]
    assert chats(client) == [group]  # untouched


def test_a_listed_admin_who_is_not_an_admin_of_the_group_is_refused_like_for_every_admin_command(client):
    response = client.post("/api/v1/messages", headers=HEADERS, json={
        "platform": "whatsapp", "chat_id": "120363000000000001@g.us", "user_id": "admin1@lid", "user_name": "A",
        "text": "!h -on", "is_group": True, "sender_is_group_admin": False})
    assert response.json()["replies"][0]["reaction"] == "🚫"
    assert chats(client) == []


def test_it_is_only_for_the_owners_personal_bot(client):
    (reply,) = personal(client, "!h -on", is_personal=False)
    assert reply["reaction"] == "🚫" and chats(client) == []
    assert personal(client, "!resumen", is_personal=False)[0]["reaction"] == "🚫"


def test_the_two_forms_of_a_private_chat_are_one_chat(client, settings):
    alias = "9999@lid"
    personal(client, ".b h -on", aliases=[alias])
    assert sorted(chats(client)) == sorted([CHAT, alias])

    assert save(client, message(CHAT, 1, "por número"), message(alias, 2, "por lid")) == 2
    # asked under the other form, it is still the same conversation
    (reply,) = personal(client, ".b res", chat=alias, aliases=[CHAT])
    assert reply["reaction"] == "⏳"
    done = wait_done(client, reply["job"])
    assert done[0]["text"] == "Hablaron de la cena."


def test_the_summary_gets_who_said_what_and_when(client, isolated):
    personal(client, ".b h -on")
    save(client, message(CHAT, 1, "¿cenamos el viernes?", minutes_ago=30), message(CHAT, 2, "dale,\na las 21", minutes_ago=20),
         message(CHAT, 3, "de ayer", minutes_ago=60 * 13))  # 02:00 local: yesterday

    (reply,) = personal(client, ".b res")  # today is the default
    assert reply["text"] == "" and reply["reaction"] == "⏳" and reply["job"]
    text, reaction = wait_done(client, reply["job"])
    assert text["text"] == "Hablaron de la cena." and reaction["reaction"] == ""  # the ⏳ is taken off, not replaced

    (transcript,) = isolated
    assert "[13/10 11:30] Ana: ¿cenamos el viernes?" in transcript  # local time
    assert "Ana: dale, a las 21" in transcript  # one line per message
    assert "de ayer" not in transcript


def test_it_can_summarise_the_whole_history_or_a_span_or_a_count(client, isolated):
    personal(client, ".b h -on")
    save(client, message(CHAT, 1, "de ayer", minutes_ago=60 * 13), message(CHAT, 2, "de la semana", minutes_ago=60 * 24 * 5),
         message(CHAT, 3, "de hoy", minutes_ago=10))

    for text, expected, absent in ((".b res -today", ["de hoy"], ["de ayer", "de la semana"]),
                                   (".b res -all", ["de hoy", "de ayer", "de la semana"], []),
                                   (".b res 2d", ["de hoy", "de ayer"], ["de la semana"]),
                                   (".b res 2", ["de hoy", "de ayer"], ["de la semana"]),
                                   (".b resumen --ALL", ["de la semana"], [])):  # the old name, any dash, any case
        (reply,) = personal(client, text)
        wait_done(client, reply["job"])
        transcript = isolated[-1]
        assert all(w in transcript for w in expected), text
        assert not any(w in transcript for w in absent), text


def test_nothing_saved_and_a_bad_period(client):
    assert "No tengo mensajes guardados" in personal(client, ".b res")[0]["text"]
    assert personal(client, ".b res cualquier cosa")[0]["text"].startswith("Uso: .b res")
    assert personal(client, ".b res -yesterday")[0]["text"].startswith("Uso: .b res")


def test_past_the_limit_the_oldest_messages_are_deleted_first(client, settings):
    object.__setattr__(settings, "HISTORY_MAX_MB", 1)
    personal(client, ".b h -on")
    filler = "x" * 900
    batch = [message(CHAT, i, f"{i} {filler}", minutes_ago=2000 - i) for i in range(2000)]  # ~1.8 MB
    chat_history.store(settings, f"whatsapp:{CHAT}", [
        chat_history.Incoming(m["message_id"], "a@lid", "Ana", m["text"],
                              datetime.fromisoformat(m["sent_at"]).replace(tzinfo=None)) for m in batch])

    assert chat_history.total_bytes(settings) > 1024 * 1024
    assert chat_history.trim(settings) > 0
    assert chat_history.total_bytes(settings) <= 1024 * 1024

    kept = chat_history.recent(settings, f"whatsapp:{CHAT}", since=None, limit=5000)
    assert kept and kept[-1].text.startswith("1999 ")  # the newest survive
    assert not kept[0].text.startswith("0 ")  # the oldest went
