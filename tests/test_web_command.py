import json
import logging
from pathlib import Path

import pytest

from app.services import remote_browser as rb
from tests.conftest import REFUSED
from tests.test_mention import raw

ADMIN = "admin1@lid"


class FakeBrowser:
    """Stands in for the real one: records what the command asked for and answers with a canned screenshot."""

    def __init__(self):
        self.calls = []
        self.app = None
        self.error = None

    def _answer(self, *call):
        self.calls.append(call)
        if self.error:
            raise self.error
        return rb.Snapshot(image=b"\xff\xd8jpeg", title="Panel", url="http://x/", marks=["Email (campo)", "Entrar (botón)"])

    def open(self, app):
        self.app = app
        return self._answer("open", app.name)

    def click(self, number):
        return self._answer("click", number)

    def type(self, number, text):
        return self._answer("type", number, text)

    def press(self, key):
        return self._answer("press", key)

    def scroll(self, down):
        return self._answer("scroll", down)

    def back(self):
        return self._answer("back")

    def login(self):
        return self._answer("login")

    def refresh(self):
        return self._answer("refresh")

    def close(self):
        self.calls.append(("close",))
        self.app = None


@pytest.fixture
def fake(monkeypatch, settings):
    Path(settings.WEB_APPS_FILE).write_text(json.dumps(
        {"trip-trace": {"url": "http://localhost:5173", "description": "Dashboard", "login": {"user": "a@b.c", "password": "pw"}}}))
    browser = FakeBrowser()
    monkeypatch.setattr(rb, "get_browser", lambda idle: browser)
    return browser


def ask(client, text):
    (reply,) = raw(client, text, user=ADMIN)
    return reply


def test_without_arguments_it_explains_itself_and_lists_the_apps(client, fake):
    text = ask(client, "!web")["text"]
    assert "trip-trace — Dashboard" in text and "!web <número>" in text and "!web t <número> <texto>" in text
    assert fake.calls == []


def test_opening_an_app_returns_a_screenshot_and_the_numbered_elements(client, fake):
    reply = ask(client, "!web trip-trace")
    assert fake.calls == [("open", "trip-trace")]
    assert reply["image"] and reply["text"].splitlines() == ["🌐 trip-trace — Panel", "1 Email (campo)", "2 Entrar (botón)"]
    image = client.get(f"/api/v1/images/{reply['image']}", headers={"X-API-Key": "test-key"})
    assert image.headers["content-type"] == "image/jpeg" and image.content == b"\xff\xd8jpeg"


def test_every_action_reaches_the_browser(client, fake):
    ask(client, "!web trip-trace")
    fake.calls.clear()
    for text in ("!web 2", "!w t 1 hola  mundo", "!web escribir 1 x", "!web login", "!web enter", "!web Esc", "!web bajar",
                 "!web subir", "!web atrás", "!web ver"):
        ask(client, text)
    assert fake.calls == [("click", 2), ("type", 1, "hola  mundo"), ("type", 1, "x"), ("login",), ("press", "enter"),
                          ("press", "esc"), ("scroll", True), ("scroll", False), ("back",), ("refresh",)]


def test_closing_says_so_and_unknown_words_show_the_help(client, fake):
    assert ask(client, "!web cerrar")["text"] == "No había nada abierto."
    ask(client, "!web trip-trace")
    assert ask(client, "!web cerrar")["text"] == "🔒 Navegador cerrado."
    assert ask(client, "!web algo-raro")["text"].startswith('No conozco "algo-raro".')


def test_typing_needs_a_number_and_a_text(client, fake):
    for text in ("!web t", "!web t 2", "!web t dos hola"):
        assert ask(client, text)["text"].startswith("Uso: !web t <número> <texto>")
    assert fake.calls == []


def test_a_browser_problem_is_shown_as_a_warning(client, fake):
    fake.error = rb.WebError("No hay un elemento 9 en la captura actual.")
    reply = ask(client, "!web 9")
    assert reply["text"] == "⚠️ No hay un elemento 9 en la captura actual." and reply["image"] is None


def test_what_is_typed_is_never_logged(client, fake, caplog):
    with caplog.at_level(logging.INFO, logger="tox.web"):
        ask(client, "!web t 1 contraseña-secreta-123")
    assert "contraseña-secreta-123" not in caplog.text and "type into 1 (22 characters)" in caplog.text


def test_non_admins_are_refused_and_the_browser_is_never_touched(client, monkeypatch):
    monkeypatch.setattr(rb, "get_browser", lambda idle: pytest.fail("a non-admin must not reach the browser"))
    assert raw(client, "!web trip-trace", user="someone@lid")[0]["reaction"] == "🚫"


def test_the_app_file_is_validated(tmp_path):
    path = tmp_path / "apps.json"
    path.write_text(json.dumps({"ok": {"url": "http://localhost:1"}, "login": {"url": "http://x"}, "sinurl": {},
                                "mala": {"url": "ftp://x"}, "Mayus": {"url": "http://x"}}))
    apps, problems = rb.load_apps(str(path))
    assert list(apps) == ["ok"]
    assert len(problems) == 4  # a reserved word, no url, a non-http url, an invalid name
    assert rb.load_apps(str(tmp_path / "nope.json")) == ({}, [])
