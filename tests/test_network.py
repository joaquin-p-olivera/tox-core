import ipaddress

import pytest

from app.services import network_scan as ns
from tests.conftest import REFUSED
from tests.test_mention import raw

ADDR = (
    "1: lo    inet 127.0.0.1/8 scope host lo\\\n"
    "2: wlo1    inet 192.168.1.21/24 brd 192.168.1.255 scope global dynamic noprefixroute wlo1\\\n"
    "3: docker0    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0\\\n"
)
ROUTE = "default via 192.168.1.1 dev wlo1 proto dhcp src 192.168.1.21 metric 600\n"
NEIGH = (
    "192.168.1.1 dev wlo1 lladdr 00:11:22:33:44:55 REACHABLE\n"
    "192.168.1.6 dev wlo1 lladdr 5a:11:22:33:44:66 STALE\n"
    "192.168.1.16 lladdr bc:11:22:33:44:99 DELAY\n"
    "192.168.1.99 dev wlo1  INCOMPLETE\n"
    "192.168.1.98 dev wlo1 lladdr aa:bb:cc:dd:ee:ff FAILED\n"
)
OUI = "00-11-22   (hex)\t\tAcme Networks\n001122     (base 16)\t\tAcme Networks\n"


class FakeSources:
    def __init__(self):
        self.pinged = []

    def run(self, argv, timeout=5.0):
        if argv[0] == "ping":
            self.pinged.append(argv[-1])
            return ""
        return ADDR if "addr" in argv else ROUTE if "route" in argv else NEIGH

    def read(self, path):
        return OUI


def test_only_the_home_network_is_picked_and_the_router_is_found():
    (network,) = ns.parse_local_networks(ADDR, ROUTE)  # not lo, not docker0
    assert (network.interface, str(network.address), str(network.subnet)) == ("wlo1", "192.168.1.21", "192.168.1.0/24")
    assert network.gateway == ipaddress.ip_address("192.168.1.1")


def test_a_subnet_wider_than_a_24_is_cut_down_to_the_24_around_this_pc():
    (network,) = ns.parse_local_networks("2: eth0    inet 10.1.2.3/16 scope global eth0\\\n", "")
    assert str(network.subnet) == "10.1.2.0/24"


def test_neighbours_ignore_entries_nobody_answered():
    assert [str(ip) for ip in ns.parse_neighbours(NEIGH)] == ["192.168.1.1", "192.168.1.6", "192.168.1.16"]


def test_neighbours_are_read_with_and_without_the_dev_part():
    # `ip neigh show dev wlo1` (what the scan runs) leaves "dev wlo1" out of each line; plain `ip neigh show` has it
    without_dev = "192.168.1.11 lladdr 8a:11:22:33:44:77 REACHABLE \n192.168.1.12  INCOMPLETE\n"
    assert [str(ip) for ip in ns.parse_neighbours(without_dev)] == ["192.168.1.11"]


def test_vendor_lookup_only_needs_the_matching_line():
    assert ns.vendor_of(OUI, "00:11:22:33:44:55") == "Acme Networks"
    assert ns.vendor_of(OUI, "bc:11:22:33:44:99") is None


@pytest.mark.parametrize("mac, private", [("00:11:22:33:44:55", False), ("5a:11:22:33:44:66", True),
                                          ("8a:11:22:33:44:77", True), ("bc:11:22:33:44:99", False)])
def test_randomised_macs_are_recognised(mac, private):
    assert ns.is_private_mac(mac) is private


def test_scan_pings_the_whole_subnet_but_never_this_pc_and_describes_each_device():
    sources = FakeSources()
    (report,) = ns.scan(sources)
    assert len(sources.pinged) == 253 and "192.168.1.21" not in sources.pinged  # 254 hosts minus this PC
    router, phone, other = report.devices
    assert (str(router.ip), router.vendor, router.is_gateway) == ("192.168.1.1", "Acme Networks", True)
    assert phone.private_mac and phone.vendor is None
    assert other.vendor is None and not other.private_mac


def test_ipmaster_shows_this_pc_and_the_devices_and_ipm_is_the_same_command(client, monkeypatch):
    real_scan = ns.scan
    monkeypatch.setattr(ns, "scan", lambda **kw: real_scan(FakeSources(), **kw))
    (reply,) = raw(client, "!ipmaster", user="admin1@lid")
    text = reply["text"]
    assert "Red local 192.168.1.0/24 (wlo1)" in text and "Esta PC: 192.168.1.21" in text
    assert "• 192.168.1.1 — 00:11:22:33:44:55 · Acme Networks · router" in text
    assert "MAC privada" in text and "Otros dispositivos (3)" in text
    assert raw(client, "!ipm", user="admin1@lid")[0]["text"] == text


def test_ipmaster_is_admin_only_and_scans_nothing_for_others(send, monkeypatch):
    monkeypatch.setattr(ns, "scan", lambda: pytest.fail("a non-admin must never trigger a scan"))
    for name in ("!ipmaster", "!ipm"):
        assert send(name, user="someone@lid") == REFUSED


MDNS = (
    "=;wlo1;IPv4;Smart\\032TV\\0401234\\041;AirPlay Remote Video;local;Android.local;192.168.1.16;7000;\"model=AppleTV3,2\"\n"
    "=;wlo1;IPv4;A1B2C3D4E5F6\\064Smart\\032TV\\0401234\\041;AirTunes Remote Audio;local;Android.local;192.168.1.16;7000;\n"
    "=;wlo1;IPv4;PCDEANA;Workstation;local;PCDEANA.local;192.168.1.13;9;\n"
    "=;wlo1;IPv4;PCDEANA;Web Site;local;PCDEANA.local;192.168.1.13;80;\n"
    "=;wlo1;IPv6;Algo;Workstation;local;Algo.local;fe80::1;9;\n"
)


def test_mdns_names_combine_the_host_with_an_informative_service_and_ignore_the_rest():
    names = {str(ip): name for ip, name in ns.parse_mdns_names(MDNS).items()}
    assert names == {"192.168.1.16": "Android / Smart TV(1234)", "192.168.1.13": "PCDEANA"}


def test_names_from_the_network_are_cleaned():
    assert ns._clean_name("hola\x00\x1b[31m mundo ") == "hola[31m mundo"
    assert len(ns._clean_name("x" * 200)) == ns.MAX_NAME_LENGTH
    assert ns._clean_name("   ") is None


def test_your_names_win_over_what_the_device_announces(tmp_path):
    class Sources(FakeSources):
        def run(self, argv, timeout=5.0):
            return MDNS if argv[0] == "avahi-browse" else super().run(argv, timeout)

    path = tmp_path / "devices.json"
    path.write_text('{"BC-11-22-33-44-99": "Living TV"}')
    devices = {str(d.ip): d.name for d in ns.scan(Sources(), ns.load_device_names(str(path)))[0].devices}
    assert devices["192.168.1.16"] == "Living TV"  # listed by MAC (any case, - or :) beats the announced one
    assert devices["192.168.1.1"] is None and devices["192.168.1.6"] is None


def test_a_missing_or_broken_devices_file_means_no_names(tmp_path):
    assert ns.load_device_names(str(tmp_path / "nope.json")) == {}
    broken = tmp_path / "broken.json"
    broken.write_text("{nope")
    assert ns.load_device_names(str(broken)) == {}
