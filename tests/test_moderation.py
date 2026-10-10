from datetime import datetime, timedelta

import pytest

from app.models import PendingBan
from tests.conftest import REFUSED
from tests.test_mention import raw

ADMIN = {"user": "admin1@lid", "sender_is_group_admin": True}
HEADERS = {"X-API-Key": "test-key"}


def person(user_id, name=None, *, admin=False, aliases=(), is_bot=False):
    return {"user_id": user_id, "user_name": name, "aliases": list(aliases), "is_group_admin": admin, "is_bot": is_bot}


# --- !ban ---

def test_ban_removes_the_tagged_member(client):
    (reply,) = raw(client, "!ban @bob", mentioned=[person("bob@lid", "Bob")], **ADMIN)
    assert reply["remove"] == ["bob@lid"] and reply["sweep_blacklist"] is False
    assert reply["mentions"][0]["user_id"] == "bob@lid"


def test_ban_is_admin_only(send):
    assert send("!ban @bob", user="someone@lid") == REFUSED


@pytest.mark.parametrize("target, expected", [
    (person("bot@lid", is_bot=True), "mí mismo"),
    (person("admin1@lid"), "vos mismo"),
    (person("x@lid", admin=True), "admin"),
    (person("admin2@lid", aliases=["admin1@lid"]), "vos mismo"),  # the sender under another of their ids
    ({"user_id": "y@lid", "user_name": "Yo"}, "comprobar"),  # group-admin status unknown: refused (fail closed)
])
def test_ban_refuses_the_bot_the_sender_admins_and_the_unknown(client, target, expected):
    (reply,) = raw(client, "!ban @x", mentioned=[target], **ADMIN)
    assert reply["remove"] == [] and expected in reply["text"]


def test_ban_never_touches_a_listed_admin_even_if_not_group_admin(client):
    (reply,) = raw(client, "!ban @x", mentioned=[person("99", aliases=["99"])], **ADMIN)
    assert reply["remove"] == ["99"]  # control: not listed
    # telegram:99 is in ADMIN_USER_IDS
    (reply,) = raw(client, "!ban @x", mentioned=[person("99")], platform="telegram", user="99", chat="c1",
                   sender_is_group_admin=True)
    assert reply["remove"] == []


def test_ban_needs_a_tag_and_a_group(client):
    assert "Uso:" in raw(client, "!ban", **ADMIN)[0]["text"]
    assert "grupos" in raw(client, "!ban @bob", mentioned=[person("bob@lid", admin=False)], is_group=False,
                           user="admin1@lid")[0]["text"]


# --- !permaban / !unpermaban ---

def test_permaban_blacklists_removes_and_asks_for_a_sweep(client):
    (reply,) = raw(client, "!permaban @bob", mentioned=[person("bob@lid", "Bob", aliases=["bob@lid", "59891@s.whatsapp.net"])], **ADMIN)
    assert reply["remove"] == ["bob@lid"] and reply["sweep_blacklist"] is True
    listed = client.get("/api/v1/blacklist?platform=whatsapp", headers=HEADERS).json()
    assert listed == [{"user_ids": ["bob@lid", "59891@s.whatsapp.net"], "name": "Bob"}]
    assert client.get("/api/v1/blacklist?platform=telegram", headers=HEADERS).json() == []


def test_unpermaban_lists_and_removes_by_number_or_name(client):
    raw(client, "!permaban @a", mentioned=[person("a@lid", "Ana")], **ADMIN)
    raw(client, "!permaban @b", mentioned=[person("b@lid", "Beto")], **ADMIN)
    listing = raw(client, "!unpermaban", **ADMIN)[0]["text"]
    assert "1. Ana" in listing and "2. Beto" in listing
    assert "Saqué a Ana" in raw(client, "!unpermaban 1", **ADMIN)[0]["text"]
    assert "Saqué a Beto" in raw(client, "!despermaban beto", **ADMIN)[0]["text"]
    assert raw(client, "!unpermaban", **ADMIN)[0]["text"] == "La lista negra está vacía."
    assert client.get("/api/v1/blacklist?platform=whatsapp", headers=HEADERS).json() == []


def test_unpermaban_is_admin_only(send):
    assert send("!unpermaban", user="someone@lid") == REFUSED


# --- !ruleta-ban ---

ROSTER = [person("m1@lid", "Uno"), person("m2@lid", "Dos"), person("g@lid", "Gerente", admin=True),
          person("bot@lid", is_bot=True), person("admin1@lid", "Yo (admin listado)")]


def test_the_roulette_only_picks_regular_members_and_waits_for_confirmation(client):
    picked = set()
    for _ in range(30):
        (reply,) = raw(client, "!ruleta-ban", participants=ROSTER, **ADMIN)
        assert reply["remove"] == []  # nothing happens until an admin confirms
        picked.add(reply["mentions"][0]["user_id"])
    assert picked <= {"m1@lid", "m2@lid"}


def test_si_bans_the_picked_one_and_only_once(client):
    raw(client, "!ruleta-ban", participants=[person("m1@lid", "Uno")], **ADMIN)
    (reply,) = raw(client, "!si", **ADMIN)
    assert reply["remove"] == ["m1@lid"]
    assert "No hay ninguna ruleta" in raw(client, "!si", **ADMIN)[0]["text"]


def test_no_saves_them_and_an_old_roulette_expires(client, db_session):
    raw(client, "!ruleta-ban", participants=[person("m1@lid", "Uno")], **ADMIN)
    (reply,) = raw(client, "!no", **ADMIN)
    assert reply["remove"] == [] and "Se salvó" in reply["text"]

    raw(client, "!ruleta-ban", participants=[person("m1@lid", "Uno")], **ADMIN)
    db_session.query(PendingBan).update({"created_at": datetime(2020, 1, 1)})
    db_session.commit()
    assert "No hay ninguna ruleta" in raw(client, "!si", **ADMIN)[0]["text"]


def test_the_roulette_needs_the_roster_and_someone_to_pick(client):
    assert "lista de integrantes" in raw(client, "!ruleta-ban", **ADMIN)[0]["text"]
    only_admins = [person("g@lid", admin=True)]
    assert "No hay a quién" in raw(client, "!ruleta-ban", participants=only_admins, **ADMIN)[0]["text"]


def test_confirming_is_admin_only(send):
    assert send("!si", user="someone@lid") == REFUSED
    assert send("!no", user="someone@lid") == REFUSED


# --- !cumple ---

def test_anyone_registers_their_own_birthday_and_it_is_overwritten(client):
    (reply,) = raw(client, "!cumple 15/03/1995", user="u5", name="Eva")
    assert "Registré" in reply["text"] and "15/03/1995" in reply["text"]
    (reply,) = raw(client, "!cumple 16/03/1995", user="u5", name="Eva")
    assert "Actualicé" in reply["text"] and "antes: 15/03/1995" in reply["text"]
    assert "16/03/1995" in raw(client, "!cumple", user="u5")[0]["text"]


def test_the_year_is_optional(client):
    assert "15/03." in raw(client, "!cumple 15/03", user="u5")[0]["text"]
    assert "1904" not in raw(client, "!cumple", user="u5")[0]["text"]
    (reply,) = raw(client, "!cumple 29/02", user="u5")  # a leap day is fine without a year
    assert "Actualicé" in reply["text"] and "29/02 (antes: 15/03)" in reply["text"]
    (reply,) = raw(client, "!cumple 15/03/1990", user="u5")
    assert "(antes: 29/02)" in reply["text"] and "15/03/1990" in reply["text"]


def test_invalid_dates_are_refused(client):
    for text in ("!cumple", "!cumple mañana", "!cumple 31/02/1990", "!cumple 15/03/2999", "!cumple 15/03/1800", "!cumple 31/04", "!cumple 15/13"):
        assert "Uso:" in raw(client, text, user="u5")[0]["text"]


def test_only_admins_can_set_someone_elses_date(client):
    tag = [person("u8@lid", "Beto")]
    assert "Solo los admins" in raw(client, "!cumple @Beto 01/01/1990", user="u5", mentioned=tag)[0]["text"]
    (reply,) = raw(client, "!cumple @Beto 01/01/1990", mentioned=tag, **ADMIN)
    assert reply["mentions"][0]["user_id"] == "u8@lid" and "Registré" in reply["text"]
    (reply,) = raw(client, "!cumple @Beto 02/01/1990", mentioned=tag, **ADMIN)
    assert "antes: 01/01/1990" in reply["text"]


def test_an_admin_without_a_tag_sets_their_own(client):
    (reply,) = raw(client, "!cumple 10/10/1980", **ADMIN)
    assert reply["mentions"][0]["user_id"] == "admin1@lid"


def test_hyphenated_command_names_parse():
    from app.services.command_router import parse_command
    assert parse_command("!ruleta-ban", ["!"]) == ("!", "ruleta-ban", "")
    assert parse_command("!-x", ["!"]) is None
