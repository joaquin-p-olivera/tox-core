import socket

import pytest

from app.commands import fred as fred_command
from app.services import network_scan as ns
from tests.conftest import REFUSED
from tests.test_mention import raw
from tests.test_network import ADDR, ROUTE, FakeSources


@pytest.fixture
def lan(monkeypatch):
    """This PC at 192.168.1.21 (what FakeSources says), whatever the machine running the tests has."""
    real = ns.primary_network
    monkeypatch.setattr(ns, "primary_network", lambda: real(FakeSources()))


def test_primary_network_is_the_pc_s_own_and_needs_no_scan():
    sources = FakeSources()
    network = ns.primary_network(sources)
    assert str(network.address) == "192.168.1.21" and sources.pinged == []


def test_reachable_tells_an_open_port_from_a_closed_one():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        assert fred_command._reachable("127.0.0.1", port) is True
    assert fred_command._reachable("127.0.0.1", port) is False  # closed again


TITLE = "🌐 Fred, para abrir desde cualquier dispositivo de la red local:"


def test_fred_is_one_message_with_a_blank_line_between_the_title_and_each_app(client, lan, monkeypatch):
    monkeypatch.setattr(fred_command, "_reachable", lambda host, port: port == 5200)  # 5201: nothing anywhere
    (reply,) = raw(client, "!fred", user="admin1@lid")
    assert reply["text"] == f"{TITLE}\n\n🟢 Frontend: http://192.168.1.21:5200\n\n🔴 Admin module: http://192.168.1.21:5201"


def test_an_app_that_only_listens_on_localhost_is_not_reported_as_down(client, lan, monkeypatch):
    # what a Vite dev server without --host does: it answers on 127.0.0.1 but the LAN address refuses
    monkeypatch.setattr(fred_command, "_reachable", lambda host, port: port == 5200 or host == "127.0.0.1")
    (reply,) = raw(client, "!fred", user="admin1@lid")
    assert "🟢 Frontend: http://192.168.1.21:5200\n" in reply["text"] + "\n"
    assert reply["text"].endswith("🟡 Admin module: http://192.168.1.21:5201 (solo desde esta PC)")
    assert "🔴" not in reply["text"]


def test_fred_with_everything_up_marks_both_as_up(client, lan, monkeypatch):
    monkeypatch.setattr(fred_command, "_reachable", lambda host, port: True)
    (reply,) = raw(client, "!fred", user="admin1@lid")
    assert "🟢 Frontend: http://192.168.1.21:5200" in reply["text"] and "🟢 Admin module: http://192.168.1.21:5201" in reply["text"]
    assert "🔴" not in reply["text"]


def test_fred_is_admin_only(send, monkeypatch):
    monkeypatch.setattr(ns, "primary_network", lambda: pytest.fail("a non-admin must not trigger anything"))
    assert send("!fred", user="someone@lid") == REFUSED
