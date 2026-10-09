import logging

from tests.test_mention import raw

ADMIN = "admin1@lid"
MEMBERS = [{"user_id": "u2@lid", "user_name": "Bob"}, {"user_id": "u3@lid", "user_name": "Carla"}]
UNNAMED = [{"user_id": "u4@lid", "aliases": ["u4@lid", "59899123456@s.whatsapp.net"]}]


def test_uid_logs_every_user_and_only_reacts(client, caplog):
    with caplog.at_level(logging.INFO, logger="tox.uid"):
        (reply,) = raw(client, "!uid", user=ADMIN, name="Admin", participants=MEMBERS)
    assert reply["reaction"] == "👍" and reply["text"] == "" and reply["image"] is None
    lines = [r.getMessage() for r in caplog.records if r.name == "tox.uid"]
    assert "UID whatsapp:u2@lid — Bob" in lines and "UID whatsapp:u3@lid — Carla" in lines
    assert "UID whatsapp:admin1@lid — Admin" in lines, "the sender isn't in the platform's member list but must be printed"


def test_uid_for_a_non_admin_is_refused_with_a_reaction_and_prints_nothing(client, caplog):
    with caplog.at_level(logging.INFO, logger="tox.uid"):
        (reply,) = raw(client, "!uid", user="someone@lid", participants=MEMBERS)
    assert reply["reaction"] == "🚫" and reply["text"] == ""
    assert not [r for r in caplog.records if r.name == "tox.uid"]


def test_uid_is_not_listed_in_help_even_for_admins(send):
    (help_text,) = send("!ayuda", user=ADMIN)
    assert "!uid " not in help_text  # (!uuid contains "uid", so look for the exact command)
    assert send("!ayuda uid", user=ADMIN) == ["No conozco ese comando. Probá !ayuda"]


def test_uid_prints_the_phone_and_completes_a_missing_name_from_what_the_api_knows(client, caplog):
    raw(client, "!dado", user="u4@lid", name="Dani")  # u4 used the bot once: the API now knows their name
    with caplog.at_level(logging.INFO, logger="tox.uid"):
        raw(client, "!uid", user=ADMIN, name="Admin", participants=UNNAMED)
    lines = [r.getMessage() for r in caplog.records if r.name == "tox.uid"]
    assert "UID whatsapp:u4@lid — Dani | tel +59899123456" in lines
