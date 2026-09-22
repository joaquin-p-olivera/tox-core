import pytest

from tests.test_mention import raw

MEMBERS = [{"user_id": "u1", "user_name": "Alice"}, {"user_id": "u2", "user_name": "Bob"},
           {"user_id": "u3", "user_name": "Carla"}]


def tagged(client, n=60, **kwargs):
    """The set of user_ids !m tagged over n tries (as user u1 unless told otherwise)."""
    kwargs.setdefault("participants", MEMBERS)
    return {raw(client, "!m", **kwargs)[0]["mentions"][0]["user_id"] for _ in range(n)}


def test_mute_toggles(client):
    (muted,) = raw(client, "!mute", user="u2", name="Bob")
    assert muted["text"].startswith("🔇 Listo, Bob") and "!mute" in muted["text"]
    (again,) = raw(client, "!mute", user="u2", name="Bob")
    assert again["text"] == "🔔 Bob, volvés a estar en las etiquetas."
    (third,) = raw(client, "!mute", user="u2", name="Bob")
    assert third["text"].startswith("🔇 Listo, Bob")


def test_mute_message_uses_the_prefix_the_user_typed(client):
    (reply,) = raw(client, "/mute", user="u2", name="Bob", platform="telegram")
    assert "/mute" in reply["text"] and "!mute" not in reply["text"]


def test_a_muted_user_is_never_tagged(client):
    assert tagged(client) == {"u2", "u3"}
    raw(client, "!mute", user="u2", name="Bob")
    assert tagged(client) == {"u3"}


def test_unmuting_makes_the_user_taggable_again(client):
    raw(client, "!mute", user="u2")
    assert tagged(client) == {"u3"}
    raw(client, "!mute", user="u2")
    assert tagged(client) == {"u2", "u3"}


def test_mute_is_per_chat(client):
    raw(client, "!mute", user="u2", chat="g1")
    assert tagged(client, chat="g1") == {"u3"}
    assert tagged(client, chat="g2") == {"u2", "u3"}


def test_mute_is_per_platform(client):
    raw(client, "/mute", user="u2", platform="telegram")
    assert tagged(client, platform="whatsapp") == {"u2", "u3"}


def test_muted_user_can_still_use_the_bot_and_tag_others(client):
    raw(client, "!mute", user="u1", name="Alice")
    assert "ID de usuario: u1" in raw(client, "!id", user="u1")[0]["text"]
    assert tagged(client, user="u1") == {"u2", "u3"}  # muting only stops OTHERS from tagging you


def test_muted_user_is_matched_through_their_aliases(client):
    """WhatsApp: muted as an LID, but the group lists them by phone number (with the LID as an alias)."""
    raw(client, "!mute", user="111@lid", name="Bob")
    members = [{"user_id": "999@lid"},
               {"user_id": "59899111111@s.whatsapp.net", "aliases": ["59899111111@s.whatsapp.net", "111@lid"]},
               {"user_id": "59899222222@s.whatsapp.net", "aliases": ["59899222222@s.whatsapp.net", "222@lid"]}]
    assert tagged(client, user="999@lid", participants=members) == {"59899222222@s.whatsapp.net"}


def test_muted_users_are_skipped_in_the_seen_users_fallback_too(client):
    raw(client, "!id", user="u2", name="Bob")
    raw(client, "!id", user="u3", name="Carla")
    raw(client, "!mute", user="u2", name="Bob")
    assert tagged(client, participants=None) == {"u3"}


def test_everyone_else_muted(client):
    raw(client, "!mute", user="u2")
    raw(client, "!mute", user="u3")
    (reply,) = raw(client, "!m", participants=MEMBERS)
    assert reply["mentions"] == [] and reply["audio"] is None
    assert "todos en mute" in reply["text"]


def test_mute_in_a_private_chat(client):
    (reply,) = raw(client, "!mute", is_group=False)
    assert reply["text"] == "Este comando es para grupos."


def test_ayuda_lists_mute(client):
    assert "!mute" in raw(client, "!ayuda")[0]["text"]
