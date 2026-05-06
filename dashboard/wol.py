"""Wake-on-LAN magic packet sender.

A magic packet is six bytes of 0xFF followed by 16 repetitions of the
target MAC, broadcast as a UDP datagram on the LAN. We send to both the
limited broadcast (255.255.255.255) and the configured subnet broadcast,
which covers either case without the caller having to care.
"""

from __future__ import annotations

import socket
import struct


def _normalize_mac(mac: str) -> bytes:
    cleaned = mac.replace("-", "").replace(":", "").replace(".", "").strip()
    if len(cleaned) != 12:
        raise ValueError(f"invalid MAC address: {mac!r}")
    try:
        return bytes.fromhex(cleaned)
    except ValueError as e:
        raise ValueError(f"invalid MAC address: {mac!r}") from e


def build_magic_packet(mac: str) -> bytes:
    target = _normalize_mac(mac)
    return b"\xff" * 6 + target * 16


def send_wol(mac: str, broadcast_addrs: list[str] | None = None, port: int = 9) -> None:
    """Send the magic packet to one or more broadcast addresses.

    The default ``["255.255.255.255"]`` is the limited broadcast and works
    on a flat LAN. Pass the subnet broadcast (e.g. ``"192.168.1.255"``)
    explicitly if your network needs it.
    """
    packet = build_magic_packet(mac)
    addrs = broadcast_addrs or ["255.255.255.255"]
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for addr in addrs:
            sock.sendto(packet, (addr, port))
