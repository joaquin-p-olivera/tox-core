from tests.test_mention import raw

ADMIN = "admin1@lid"  # in ADMIN_USER_IDS (see the settings fixture)


def test_a_listed_admin_who_is_not_admin_of_this_group_is_refused(client, send):
    for command in ("!service", "!uid"):
        (reply,) = raw(client, command, user=ADMIN, sender_is_group_admin=False)
        assert reply["reaction"] == "🚫" and reply["text"] == ""


def test_a_listed_admin_who_also_administers_the_group_is_allowed(client):
    (reply,) = raw(client, "!uid", user=ADMIN, sender_is_group_admin=True)
    assert reply["reaction"] == "👍"


def test_being_admin_of_the_group_is_not_enough_without_being_listed(client):
    (reply,) = raw(client, "!service", user="someone@lid", sender_is_group_admin=True)
    assert reply["reaction"] == "🚫" and reply["text"] == ""


def test_a_platform_that_does_not_report_it_keeps_using_the_list_only(client):
    (reply,) = raw(client, "!uid", user=ADMIN)  # sender_is_group_admin omitted
    assert reply["reaction"] == "👍"


def test_private_chats_only_look_at_the_list(client):
    (reply,) = raw(client, "!uid", user=ADMIN, is_group=False, sender_is_group_admin=False)
    assert reply["reaction"] == "👍"


def test_id_explains_why_a_listed_admin_is_a_plain_user_here(send, client):
    (reply,) = raw(client, "!id", user=ADMIN, sender_is_group_admin=False)
    assert "usuario (estás en la lista de admins, pero no sos admin de este grupo)" in reply["text"]
    assert "Rol: admin" in raw(client, "!id", user=ADMIN, sender_is_group_admin=True)[0]["text"]
