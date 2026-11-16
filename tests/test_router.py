import pytest

from app.services.command_router import parse_command

PREFIXES = ["!", "/"]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("!ping", ("!", "ping", "")),
        ("/ping", ("/", "ping", "")),
        ("  !roll 2d6  ", ("!", "roll", "2d6")),
        ("!PING", ("!", "ping", "")),
        ("/ping@tox_bot", ("/", "ping", "")),
        ("!choose a | b", ("!", "choose", "a | b")),
    ],
)
def test_parses_commands(text, expected):
    assert parse_command(text, PREFIXES) == expected


@pytest.mark.parametrize("text", ["ping", "hello !ping", "!", "! ping", "!!!", "/", "/ ", "!!wow", ""])
def test_ignores_non_commands(text):
    assert parse_command(text, PREFIXES) is None


GREETING_REPLY = "Tu nariz contra mis bolas"


@pytest.mark.parametrize("text", ["hola", "Hola", "HOLA", "¡Hola!", "holaaa", "hooola", "holas", "hola ! ",
                                  "holi", "Holis", "holiis"])
def test_a_greeting_in_a_group_gets_the_answer_it_deserves(send, text):
    assert send(text) == [GREETING_REPLY]


@pytest.mark.parametrize("text", ["hola a todos", "qué hora es, hola", "holanda", "ho la", "chau", "h0la",
                                  "ola", "buenas", "holaris"])
def test_only_the_greeting_on_its_own_is_answered(send, text):
    assert send(text) == []


def test_a_greeting_is_answered_in_groups_only(send):
    assert send("hola", is_group=False) == []  # a one-to-one chat with the bot: somebody starting a conversation
    assert send("hola", is_group=False, personal=True) == []  # the personal mode writes as the owner
