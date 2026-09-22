import pytest

from app.commands import registry
from app.commands.registry import command
from app.config import Settings


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
    assert send("!secreto", user="someone@lid") == ["Este comando es solo para administradores."]
