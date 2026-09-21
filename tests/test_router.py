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
