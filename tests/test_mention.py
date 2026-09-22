import pytest


def raw(client, text, *, user="u1", name="Alice", chat="g1", platform="whatsapp", is_group=True,
        participants=None):
    payload = {"platform": platform, "chat_id": chat, "user_id": user, "user_name": name,
               "text": text, "is_group": is_group}
    if participants is not None:
        payload["participants"] = participants
    response = client.post("/api/v1/messages", headers={"X-API-Key": "test-key"}, json=payload)
    assert response.status_code == 200, response.text
    return response.json()["replies"]


def test_m_mentions_a_participant_other_than_the_sender(client):
    members = [{"user_id": "u1", "user_name": "Alice"}, {"user_id": "u2", "user_name": "Bob"},
               {"user_id": "u3", "user_name": "Carla"}]
    seen = set()
    for _ in range(60):
        (reply,) = raw(client, "!m", participants=members)
        assert reply["text"] == "y tu mamá donde está? {@0}"
        (mention,) = reply["mentions"]
        assert mention["user_id"] != "u1"  # never the sender
        seen.add(mention["user_id"])
    assert seen == {"u2", "u3"}  # and it does pick different people


def test_m_without_participants_uses_users_seen_in_the_chat(client):
    raw(client, "!id", user="u2", name="Bob")
    raw(client, "!id", user="u3", name="Carla")
    (reply,) = raw(client, "!m", user="u1", name="Alice")
    assert reply["mentions"][0]["user_id"] in ("u2", "u3")


def test_seen_users_are_per_chat(client):
    raw(client, "!id", user="u2", name="Bob", chat="other-chat")
    (reply,) = raw(client, "!m", user="u1", chat="g1")
    assert reply["mentions"] == []
    assert reply["text"] == "No tengo a nadie para etiquetar todavía (o están todos en mute)."


def test_m_alone_in_the_chat(client):
    (reply,) = raw(client, "!m", participants=[{"user_id": "u1", "user_name": "Alice"}])
    assert reply["mentions"] == [] and "No tengo a nadie" in reply["text"]


def test_m_in_private_chat(client):
    (reply,) = raw(client, "!m", is_group=False)
    assert reply["text"] == "Este comando es para grupos."


def test_m_works_on_telegram_too(client):
    raw(client, "/id", user="10", name="Bob", platform="telegram")
    (reply,) = raw(client, "/m@tox_bot", user="11", name="Alice", platform="telegram")
    assert reply["mentions"] == [{"user_id": "10", "user_name": "Bob"}]


def test_plain_replies_have_no_mentions(client):
    (reply,) = raw(client, "!uuid")
    assert reply["mentions"] == [] and reply["audio"] is None and len(reply["text"]) == 36


@pytest.mark.parametrize("name_change", ["Alicia", None])
def test_seen_user_name_is_kept_up_to_date(client, name_change):
    raw(client, "!id", user="u2", name="Bob")
    raw(client, "!id", user="u2", name=name_change)
    (reply,) = raw(client, "!m", user="u1")
    assert reply["mentions"][0]["user_name"] == (name_change or "Bob")
