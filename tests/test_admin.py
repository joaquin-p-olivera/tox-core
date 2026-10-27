import pytest

from app.commands import registry
from app.commands.registry import command
from app.config import Settings


from tests.conftest import REFUSED
@pytest.fixture
def admin_only_command():
    """Registers a throwaway admin-only command and cleans the global registry afterwards."""

    @command("secreto", description="solo admins", admin_only=True)
    def secret(ctx):
        return "top secret"

    yield
    for token in [t for t, cmd in registry._COMMANDS.items() if cmd.name == "secreto"]:
        del registry._COMMANDS[token]


def test_settings_parse_admin_ids():
    # _env_file=None: never let the developer's real .env leak into the test.
    parsed = Settings(_env_file=None, API_KEY="k", ADMIN_USER_IDS=" whatsapp:a@lid , telegram:1 ,")
    assert parsed.admin_user_keys == {"whatsapp:a@lid", "telegram:1"}
    assert Settings(_env_file=None, API_KEY="k").admin_user_keys == frozenset()


def test_id_shows_the_role(send):
    assert "Rol: admin" in send("!id", user="admin1@lid")[0]
    assert "Rol: usuario" in send("!id", user="someone@lid")[0]


def test_admin_ids_are_namespaced_by_platform(send):
    # "99" is an admin on Telegram only; the same id on WhatsApp is a regular user.
    assert "Rol: admin" in send("/id", user="99", platform="telegram")[0]
    assert "Rol: usuario" in send("!id", user="99", platform="whatsapp")[0]
    assert "Rol: usuario" in send("!id", user="admin1@lid", platform="telegram")[0]


def test_admin_only_command(send, admin_only_command):
    assert send("!secreto", user="admin1@lid") == ["top secret"]
    assert send("!secreto", user="someone@lid") == REFUSED


# --- the owner: an admin in every chat, including groups they do not run (OWNER_USER_IDS) ---

def test_a_listed_admin_is_not_one_in_a_group_they_do_not_administer(send, admin_only_command):
    """What was already true, and the reason OWNER_USER_IDS exists."""
    assert send("!secreto", user="admin1@lid", sender_is_group_admin=False) == REFUSED


def test_the_owner_is_an_admin_even_where_they_administer_nothing(send, settings, admin_only_command):
    object.__setattr__(settings, "OWNER_USER_IDS", "whatsapp:dueño@lid")
    assert send("!secreto", user="dueño@lid", sender_is_group_admin=False) == ["top secret"]
    # ... and being the owner is enough: no need to be on ADMIN_USER_IDS too
    assert "whatsapp:dueño@lid" not in settings.admin_user_keys


def test_the_owner_is_still_not_an_admin_from_their_own_number(send, settings, admin_only_command):
    """The personal mode writes as the owner into somebody else's chat: an admin answer there is not theirs to see."""
    object.__setattr__(settings, "OWNER_USER_IDS", "whatsapp:dueño@lid")
    assert send(".b secreto", user="dueño@lid", chat="un-contacto", is_group=False, personal=True) == REFUSED


def test_nobody_else_becomes_an_admin_because_an_owner_exists(send, settings, admin_only_command):
    object.__setattr__(settings, "OWNER_USER_IDS", "whatsapp:dueño@lid")
    assert send("!secreto", user="cualquiera@lid", sender_is_group_admin=True) == REFUSED
