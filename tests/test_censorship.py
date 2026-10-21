from tests.conftest import REFUSED
from tests.test_mention import raw
from tests.test_moderation import ADMIN, HEADERS, person


def censored(client, platform="whatsapp"):
    response = client.get(f"/api/v1/censored?platform={platform}", headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def test_censura_puts_the_tagged_member_on_the_list_of_this_chat(client):
    (reply,) = raw(client, "!censura @bob", mentioned=[person("bob@lid", "Bob", aliases=["bob@lid", "5989@s.whatsapp.net"])], **ADMIN)
    assert "Censura activada" in reply["text"] and reply["mentions"][0]["user_id"] == "bob@lid"
    # Nothing is deleted by the API and nobody leaves the group: the bot reads the list and deletes on its own
    assert reply["remove"] == []
    assert censored(client) == [{"chat_id": "g1", "user_ids": ["bob@lid", "5989@s.whatsapp.net"]}]


def test_the_same_command_again_lifts_it(client):
    tagged = [person("bob@lid", "Bob")]
    raw(client, "!censura @bob", mentioned=tagged, **ADMIN)
    (reply,) = raw(client, "!censura @bob", mentioned=tagged, **ADMIN)
    assert "Censura levantada" in reply["text"] and censored(client) == []


def test_censorship_is_per_chat(client):
    raw(client, "!censura @bob", mentioned=[person("bob@lid", "Bob")], **ADMIN)
    assert [entry["chat_id"] for entry in censored(client)] == ["g1"]
    # The same person writing in another group is not censored there
    raw(client, "!censura @bob", mentioned=[person("bob@lid", "Bob")], chat="g2", **ADMIN)
    assert sorted(entry["chat_id"] for entry in censored(client)) == ["g1", "g2"]
    # ...and lifting it in one leaves the other alone
    raw(client, "!censura @bob", mentioned=[person("bob@lid", "Bob")], chat="g2", **ADMIN)
    assert [entry["chat_id"] for entry in censored(client)] == ["g1"]


def test_censura_is_admin_only_and_needs_a_tag_and_a_group(client, send):
    assert send("!censura @bob", user="someone@lid") == REFUSED
    assert "Uso:" in raw(client, "!censura", **ADMIN)[0]["text"]
    assert "grupos" in raw(client, "!censura @bob", mentioned=[person("bob@lid")], is_group=False,
                           user="admin1@lid")[0]["text"]
    assert censored(client) == []


def test_censura_refuses_only_the_bot_and_the_sender(client):
    for target, expected in [(person("bot@lid", is_bot=True), "No me puedo censurar a mí mismo."),
                             (person("admin1@lid"), "No te podés censurar a vos mismo."),
                             (person("admin2@lid", aliases=["admin1@lid"]), "vos mismo")]:  # the sender under another id
        (reply,) = raw(client, "!censura @x", mentioned=[target], **ADMIN)
        assert expected in reply["text"]
    assert censored(client) == []


def test_an_admin_can_be_censored_unlike_banned(client):
    """A ban refuses an admin and anybody whose admin status is unknown; a censorship does not: nobody leaves the
    group and the same command undoes it."""
    for target in (person("groupadmin@lid", "Ana", admin=True),  # administers the group
                   person("99", "Bot admin"),                    # telegram:99 is in ADMIN_USER_IDS
                   {"user_id": "unknown@lid", "user_name": "Sin roster"}):  # admin status unknown: no fail-closed
        (reply,) = raw(client, "!censura @x", mentioned=[target], **ADMIN)
        assert "Censura activada" in reply["text"], reply["text"]
    assert censored(client) == [{"chat_id": "g1", "user_ids": ["groupadmin@lid", "99", "unknown@lid"]}]


def test_several_tagged_at_once_are_toggled_one_by_one(client):
    bob, carla = person("bob@lid", "Bob"), person("carla@lid", "Carla")
    raw(client, "!censura @bob", mentioned=[bob], **ADMIN)
    # Bob is already censored, Carla is not: the same command turns one off and the other on
    (reply,) = raw(client, "!censura @bob @carla", mentioned=[bob, carla], **ADMIN)
    assert "Censura activada" in reply["text"] and "Censura levantada" in reply["text"]
    assert censored(client) == [{"chat_id": "g1", "user_ids": ["carla@lid"]}]


def test_the_list_is_per_platform_and_needs_the_api_key(client):
    raw(client, "!censura @bob", mentioned=[person("bob@lid", "Bob")], **ADMIN)
    assert censored(client, "telegram") == []
    assert client.get("/api/v1/censored?platform=whatsapp").status_code == 401
