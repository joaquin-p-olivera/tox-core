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


def up(*ports, localhost_only=()):
    """A fake `_reachable`: these ports answer on the LAN address; localhost_only ones only on 127.0.0.1."""
    return lambda host, port: port in ports or (port in localhost_only and host == "127.0.0.1")


def test_fred_sends_the_states_in_one_message_and_then_one_url_per_message(client, lan, monkeypatch):
    monkeypatch.setattr(fred_command, "_reachable", up(5200, localhost_only=(5203,)))  # 5201: nothing anywhere
    replies = raw(client, "!fred", user="admin1@lid")
    assert [r["text"] for r in replies] == [
        "🟢 Frontend\n🔴 Admin module\n🟢 Studio (solo desde esta PC)",
        "http://192.168.1.21:5200",
        "http://192.168.1.21:5201",
        "http://localhost:5203",  # local-only by design: the LAN address would not work, not even from this PC
    ]


def test_an_app_that_only_listens_on_localhost_is_not_reported_as_down(client, lan, monkeypatch):
    # what a Vite dev server without --host does: it answers on 127.0.0.1 but the LAN address refuses
    monkeypatch.setattr(fred_command, "_reachable", up(5200, localhost_only=(5201, 5203)))
    status, *urls = [r["text"] for r in raw(client, "!fred", user="admin1@lid")]
    assert status == "🟢 Frontend\n🟡 Admin module (solo desde esta PC)\n🟢 Studio (solo desde esta PC)"
    assert urls == ["http://192.168.1.21:5200", "http://localhost:5201", "http://localhost:5203"]


def test_fred_with_everything_down_marks_all_red_and_still_gives_the_urls(client, lan, monkeypatch):
    monkeypatch.setattr(fred_command, "_reachable", lambda host, port: False)
    status, *urls = [r["text"] for r in raw(client, "!fred", user="admin1@lid")]
    assert status == "🔴 Frontend\n🔴 Admin module\n🔴 Studio"
    assert urls == ["http://192.168.1.21:5200", "http://192.168.1.21:5201", "http://localhost:5203"]


def test_fred_is_admin_only(send, monkeypatch):
    monkeypatch.setattr(ns, "primary_network", lambda: pytest.fail("a non-admin must not trigger anything"))
    assert send("!fred", user="someone@lid") == REFUSED
