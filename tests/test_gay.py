from tests.test_mention import raw


def test_gay_answers_with_a_percentage_and_tags_the_target(client):
    seen = set()
    for _ in range(200):
        (reply,) = raw(client, "!gay @Bob", mentioned=[{"user_id": "u2", "user_name": "Bob"}])
        assert reply["mentions"] == [{"user_id": "u2", "user_name": "Bob"}]
        assert reply["text"].startswith("{@0} es ") and reply["text"].count("%") == 1
        percent = int(reply["text"].split(" es ")[1].split("%")[0])
        assert 0 <= percent <= 100
        seen.add(percent)
    assert len(seen) > 20, "it must actually be random"


def test_gay_without_a_tagged_user_says_how_to_use_it(client):
    (reply,) = raw(client, "!gay")
    assert reply["mentions"] == [] and "Etiquetá a alguien" in reply["text"]


def write_users(settings, users):
    import json
    from pathlib import Path

    Path(settings.USERS_FILE).write_text(json.dumps(users))


def test_gay_for_a_listed_person_with_a_fixed_percentage_is_always_that_number(client, settings):
    write_users(settings, {"Leandro 1899": ["whatsapp:l1@lid", "whatsapp:59899000111@s.whatsapp.net"]})
    for target in ({"user_id": "l1@lid"}, {"user_id": "59899000111@s.whatsapp.net"}):  # either form of their id
        for _ in range(5):
            (reply,) = raw(client, "!gay @Leandro", mentioned=[target])
            assert reply["text"] == "{@0} es 1899% gay 🏳️‍🌈"


def test_gay_for_anyone_else_stays_random_even_if_listed(client, settings):
    write_users(settings, {"Leandro 1899": ["whatsapp:l1@lid"], "Beto": ["whatsapp:a1@lid"]})
    percents = {raw(client, "!gay @Beto", mentioned=[{"user_id": "a1@lid"}])[0]["text"] for _ in range(60)}
    assert len(percents) > 10 and not any("1899" in text for text in percents)


def test_gay_tagging_the_bot_gets_a_cheeky_answer_and_no_percentage(client):
    for _ in range(10):
        (reply,) = raw(client, "!gay @bot", mentioned=[{"user_id": "bot@lid", "is_bot": True}])
        assert reply["text"].startswith("Yo no soy gay como vos ") and "%" not in reply["text"]
        assert reply["mentions"] == []
