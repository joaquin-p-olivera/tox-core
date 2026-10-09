"""Finds this machine's address on the local network and the other devices connected to it (used by the admin-only !ipmaster).

How: the machine's own private IPv4 address(es) come from `ip addr`; every host of that subnet gets one ping so the
kernel learns who answers (it records them in its neighbour/ARP table), and `ip neigh` is then read back. Nothing is
installed and no root is needed. Only devices that answer (or have talked to this PC recently) appear: a phone in deep
sleep or a firewalled machine can be missing.

Names: a device only has one if it announces itself (mDNS/Bonjour, read with `avahi-browse` when it's installed: smart TVs,
PCs, printers, Apple devices... most phones and routers don't) or if you list its MAC in DEVICES_FILE, which wins. The home
router's DNS and NetBIOS were tried and give nothing here.

Safety: the subnet comes from the machine's own interface, never from what someone types in a chat, the programs run as
fixed argv lists with `shell=False` and a timeout, and the command is admin-only. A subnet wider than a /24 is cut down
to the /24 around this PC so a /16 can't make it fire 65,000 pings.
"""

import ipaddress
import json
import logging
import re
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

logger = logging.getLogger("tox.network")

OUI_FILE = "/usr/share/ieee-data/oui.txt"  # MAC prefix -> manufacturer, shipped with Debian/Ubuntu (package ieee-data)
PING_WORKERS = 128
PING_TIMEOUT_SECONDS = 3
# Container/VM bridges and loopback aren't "the home network"
SKIP_INTERFACE_PREFIXES = ("lo", "docker", "br-", "veth", "virbr")
# Neighbour entries that mean "nobody answered"
DEAD_STATES = {"FAILED", "INCOMPLETE"}
MAX_NAME_LENGTH = 40

_ADDR = re.compile(r"^\d+:\s+(\S+)\s+inet\s+(\d+\.\d+\.\d+\.\d+)/(\d+)")
_DEFAULT_ROUTE = re.compile(r"^default via (\d+\.\d+\.\d+\.\d+) dev (\S+)")
_NEIGHBOUR = re.compile(r"^(\d+\.\d+\.\d+\.\d+)(?: dev \S+)? lladdr ([0-9a-f:]{17}) (\S+)", re.IGNORECASE)


class Sources:
    """Everything that touches the machine. Tests pass a fake with the same methods."""

    def run(self, argv: list[str], timeout: float = 5.0) -> str:
        try:
            done = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=timeout,
                                  stdin=subprocess.DEVNULL, shell=False, check=False)
        except (OSError, subprocess.SubprocessError):
            return ""
        return done.stdout

    def read(self, path: str) -> str:
        try:
            with open(path, encoding="utf-8", errors="replace") as file:
                return file.read()
        except OSError:
            return ""


@dataclass(frozen=True)
class LocalNetwork:
    interface: str
    address: ipaddress.IPv4Address  # this machine
    subnet: ipaddress.IPv4Network  # what gets scanned
    gateway: ipaddress.IPv4Address | None  # the router, when the default route goes through this interface


@dataclass(frozen=True)
class Device:
    ip: ipaddress.IPv4Address
    mac: str
    vendor: str | None  # None when the manufacturer isn't known
    private_mac: bool  # a randomised MAC (what phones use per network for privacy): there is no manufacturer to look up
    is_gateway: bool
    name: str | None = None  # from DEVICES_FILE, else what the device announces over mDNS


@dataclass(frozen=True)
class NetworkReport:
    network: LocalNetwork
    devices: list[Device] = field(default_factory=list)


def parse_local_networks(addr_output: str, route_output: str) -> list[LocalNetwork]:
    """The machine's private IPv4 networks, home-style 192.168.x.x first."""
    gateways = {}
    for line in route_output.splitlines():
        if match := _DEFAULT_ROUTE.match(line.strip()):
            gateways[match.group(2)] = ipaddress.ip_address(match.group(1))

    networks = []
    for line in addr_output.splitlines():
        match = _ADDR.match(line.strip())
        if not match:
            continue
        interface, address, prefix = match.group(1), ipaddress.IPv4Address(match.group(2)), int(match.group(3))
        if not address.is_private or interface.startswith(SKIP_INTERFACE_PREFIXES):
            continue
        if prefix < 24:
            prefix = 24
        subnet = ipaddress.ip_network(f"{address}/{prefix}", strict=False)
        networks.append(LocalNetwork(interface, address, subnet, gateways.get(interface)))
    return sorted(networks, key=lambda n: (n.address.packed[:2] != bytes([192, 168]), n.address))


def parse_neighbours(output: str) -> dict[ipaddress.IPv4Address, str]:
    """ip -> MAC for every device the kernel has seen answer (entries nobody answered are dropped)."""
    found = {}
    for line in output.splitlines():
        match = _NEIGHBOUR.match(line.strip())
        if match and match.group(3).upper() not in DEAD_STATES:
            found[ipaddress.IPv4Address(match.group(1))] = match.group(2).lower()
    return found


def vendor_of(oui_text: str, mac: str) -> str | None:
    """The manufacturer of a MAC from the IEEE list ("00-11-22   (hex)   Acme Networks"), or None. Only the few
    prefixes actually found on the network are looked up, instead of parsing the whole ~5 MB file."""
    prefix = mac.replace(":", "")[:6].upper()
    match = re.search(rf"^{prefix[0:2]}-{prefix[2:4]}-{prefix[4:6]}\s+\(hex\)\s+(.+?)\s*$", oui_text, re.MULTILINE)
    return match.group(1) if match else None


def _clean_name(name: str) -> str | None:
    """Names come from the network (any device can announce any text): printable characters only, and short."""
    cleaned = "".join(ch for ch in name if ch.isprintable()).strip()
    return cleaned[:MAX_NAME_LENGTH] or None


def _unescape_avahi(text: str) -> str:
    """avahi-browse -p writes spaces and symbols as decimal escapes: "Smart\\032TV" -> "Smart TV"."""
    return re.sub(r"\\(\d{3})", lambda match: chr(int(match.group(1))), text)


# Services every PC/server announces: their instance name says nothing about what the device is
_UNINFORMATIVE_SERVICES = {"Web Site", "Workstation"}


def parse_mdns_names(output: str) -> dict[ipaddress.IPv4Address, str]:
    """ip -> what the device calls itself, from `avahi-browse -a -t -r -p`
    ("=;iface;IPv4;instance;service type;domain;host.local;address;port;txt"): its host name, plus the name of its first
    informative service when different (a smart TV can be host "Android" and "Smart TV (1234)" on AirPlay)."""
    hosts: dict[ipaddress.IPv4Address, str] = {}
    services: dict[ipaddress.IPv4Address, str] = {}
    for line in output.splitlines():
        fields = line.split(";")
        if len(fields) < 8 or fields[0] != "=" or fields[2] != "IPv4":
            continue
        try:
            address = ipaddress.IPv4Address(fields[7])
        except ValueError:
            continue
        if host := _clean_name(fields[6].removesuffix(".local")):
            hosts.setdefault(address, host)
        instance = _clean_name(_unescape_avahi(fields[3]))
        if instance and "@" not in instance and fields[4] not in _UNINFORMATIVE_SERVICES:
            services.setdefault(address, instance)
    names = {}
    for address in hosts.keys() | services.keys():
        parts = [part for part in (hosts.get(address), services.get(address)) if part]
        names[address] = " / ".join(dict.fromkeys(parts))  # same text twice shows once
    return names


def load_device_names(path: str) -> dict[str, str]:
    """MAC -> name from DEVICES_FILE ({"5a:11:22:33:44:66": "Celular de Ana"}). Missing or broken = no names."""
    file = Path(path)
    if not file.is_file():
        return {}
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        logger.warning("Could not read %s: %s", file.name, error)
        return {}
    if not isinstance(raw, dict):
        logger.warning("%s must be a JSON object {\"mac\": \"name\"}", file.name)
        return {}
    return {mac.lower().replace("-", ":"): name for mac, name in raw.items() if isinstance(mac, str) and isinstance(name, str)}


def is_private_mac(mac: str) -> bool:
    """A "locally administered" MAC (bit 1 of the first byte): made up by the device, not assigned to a manufacturer."""
    return bool(int(mac[:2], 16) & 0b10)


def _ping_sweep(sources: Sources, network: LocalNetwork) -> None:
    hosts = [host for host in network.subnet.hosts() if host != network.address]
    with ThreadPoolExecutor(max_workers=PING_WORKERS) as pool:
        # The result doesn't matter: the point is that the kernel learns who is there (see the module docstring).
        list(pool.map(lambda host: sources.run(["ping", "-c", "1", "-W", "1", "-n", str(host)], PING_TIMEOUT_SECONDS), hosts))


def primary_network(sources: Sources | None = None) -> LocalNetwork | None:
    """This machine's main local network (no scanning: just its own address), or None if it has no private one."""
    sources = sources or Sources()
    networks = parse_local_networks(sources.run(["ip", "-4", "-o", "addr", "show", "scope", "global"]),
                                    sources.run(["ip", "-4", "route", "show", "default"]))
    return networks[0] if networks else None


def scan(sources: Sources | None = None, device_names: dict[str, str] | None = None) -> list[NetworkReport]:
    """One report per local private network of this machine (normally just the home Wi-Fi/LAN)."""
    sources = sources or Sources()
    networks = parse_local_networks(sources.run(["ip", "-4", "-o", "addr", "show", "scope", "global"]),
                                    sources.run(["ip", "-4", "route", "show", "default"]))
    oui_text = sources.read(OUI_FILE)

    device_names = device_names or {}

    reports = []
    for network in networks:
        with ThreadPoolExecutor(max_workers=1) as background:  # the ~1 s mDNS listen overlaps with the pings
            mdns = background.submit(sources.run, ["avahi-browse", "-a", "-t", "-r", "-p"], PING_TIMEOUT_SECONDS + 2)
            _ping_sweep(sources, network)
            announced = parse_mdns_names(mdns.result())
        neighbours = parse_neighbours(sources.run(["ip", "-4", "neigh", "show", "dev", network.interface]))
        devices = [
            Device(ip=ip, mac=mac, vendor=vendor_of(oui_text, mac), private_mac=is_private_mac(mac),
                   is_gateway=ip == network.gateway,
                   name=_clean_name(device_names.get(mac, "")) or announced.get(ip))
            for ip, mac in sorted(neighbours.items())
            if ip in network.subnet and ip != network.address
        ]
        reports.append(NetworkReport(network, devices))
    return reports
