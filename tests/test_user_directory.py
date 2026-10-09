import json

from app.services import user_directory as ud


def test_loads_names_and_finds_a_person_by_any_of_their_ids(tmp_path):
    path = tmp_path / "users.json"
    path.write_text(json.dumps({"Ana": ["whatsapp:1@lid", "whatsapp:598@s.whatsapp.net"], "Beto": ["telegram:7"]}))
    users = ud.load_users(str(path))
    assert ud.name_of(users, "whatsapp", "598@s.whatsapp.net") == "Ana"
    assert ud.name_of(users, "whatsapp", "nobody", "1@lid") == "Ana"
    assert ud.name_of(users, "whatsapp", "7") is None, "the platform is part of the id"
    assert ud.name_of(users, "telegram", "7") == "Beto"


def test_a_missing_or_broken_file_means_nobody_is_listed(tmp_path):
    assert ud.load_users(str(tmp_path / "nope.json")) == {}
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    assert ud.load_users(str(broken)) == {}
    broken.write_text(json.dumps(["not", "an", "object"]))
    assert ud.load_users(str(broken)) == {}
