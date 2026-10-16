from tests.conftest import API_KEY

ADMIN = "admin1@lid"


def personal(client, text, *, user=ADMIN):
    response = client.post(
        "/api/v1/messages",
        headers={"X-API-Key": API_KEY},
        json={"platform": "whatsapp", "chat_id": "5981@s.whatsapp.net", "user_id": user, "user_name": "Yo",
              "text": text, "is_group": False, "personal": True},
    )
    assert response.status_code == 200, response.text
    return response.json()["replies"]


def test_personal_mode_uses_the_dot_b_prefix_and_not_the_usual_ones(client):
    (reply,) = personal(client, ".b dado d6")
    assert reply["text"].startswith("🎲")
    assert personal(client, "!dado d6") == []
    assert personal(client, ".bdado d6") == []  # the command has to be separated: ".b dado"


def test_replies_show_the_personal_prefix(client):
    (reply,) = personal(client, ".b sticker")
    assert ".b sticker" in reply["text"] and "!sticker" not in reply["text"]


def test_an_admin_is_a_regular_user_from_the_personal_number(client, send):
    assert send("!fred", user=ADMIN)[0] != "🚫"  # the same person, in a group: an admin
    assert personal(client, ".b fred") == [{**personal(client, ".b fred")[0], "reaction": "🚫", "text": ""}]
    (help_reply,) = personal(client, ".b ayuda")
    assert ".b fred" not in help_reply["text"] and ".b dado" in help_reply["text"]


def test_reminders_are_refused(client):
    (reply,) = personal(client, ".b recordar 18:30 sacar la basura")
    assert "no están disponibles" in reply["text"]
