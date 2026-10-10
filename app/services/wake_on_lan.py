"""Wake-on-LAN: sends a "magic packet" (6 bytes of 0xFF followed by the target MAC 16 times) as a UDP broadcast on
the local network. A device that supports it and has it enabled (BIOS/NIC settings; phones and most TVs don't) turns
on when it sees its own MAC in one. There is no answer to wait for, so "sent" is all this can ever confirm.

Only MACs listed in DEVICES_FILE are ever targeted (see commands/network.py), never one typed in a chat.
"""

import re
import socket

PORT = 9  # "discard": the conventional WoL port
GLOBAL_BROADCAST = "255.255.255.255"

_MAC = re.compile(r"^[0-9a-f]{2}([:-][0-9a-f]{2}){5}$", re.IGNORECASE)


def is_valid_mac(mac: str) -> bool:
    return bool(_MAC.match(mac))


def magic_packet(mac: str) -> bytes:
    if not is_valid_mac(mac):
        raise ValueError(f"Not a MAC address: {mac!r}")
    raw = bytes.fromhex(re.sub(r"[:-]", "", mac))
    return b"\xff" * 6 + raw * 16


def send(mac: str, broadcast_addresses: list[str]) -> None:
    """Broadcasts the packet to every given address (the subnet's own broadcast and/or the global one)."""
    packet = magic_packet(mac)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for address in dict.fromkeys(broadcast_addresses):  # same address twice is sent once
            sock.sendto(packet, (address, PORT))
