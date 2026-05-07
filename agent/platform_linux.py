"""Linux-specific actions invoked by the agent.

All shutdown/restart paths shell out to /sbin/shutdown via passwordless
sudo. The sudoers rule installed by provision-node.sh whitelists exactly
those two argument forms — nothing else.

restart_video bounces the systemd unit that runs the VLC tool. The unit
name matches what provision-node.sh installs.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import socket
import subprocess

log = logging.getLogger("sssds.agent.linux")

VLC_UNIT = "sssds-vlc.service"


# ---------------------------------------------------------------------------
# system identity
# ---------------------------------------------------------------------------

def primary_interface() -> str | None:
    """Pick the interface holding the default route."""
    try:
        out = subprocess.check_output(
            ["ip", "route", "show", "default"], text=True, timeout=2
        )
    except Exception:
        return None
    m = re.search(r"\bdev\s+(\S+)", out)
    return m.group(1) if m else None


def primary_mac() -> str:
    iface = primary_interface()
    if iface:
        path = f"/sys/class/net/{iface}/address"
        try:
            with open(path) as f:
                return f.read().strip().lower()
        except OSError:
            pass
    # Fall back to scanning all real interfaces.
    for name in sorted(os.listdir("/sys/class/net")):
        if name == "lo":
            continue
        try:
            with open(f"/sys/class/net/{name}/address") as f:
                mac = f.read().strip().lower()
            if mac and mac != "00:00:00:00:00:00":
                return mac
        except OSError:
            continue
    return "00:00:00:00:00:00"


def primary_ip() -> str:
    """Best-effort current IP. Uses a UDP connect trick — no packet sent."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "0.0.0.0"


# ---------------------------------------------------------------------------
# control actions
# ---------------------------------------------------------------------------

async def _run(*cmd: str) -> tuple[bool, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    text = (out or b"").decode("utf-8", errors="replace").strip()
    return proc.returncode == 0, text


async def shutdown() -> tuple[bool, str]:
    return await _run("sudo", "-n", "/sbin/shutdown", "-h", "now")


async def restart() -> tuple[bool, str]:
    return await _run("sudo", "-n", "/sbin/shutdown", "-r", "now")


async def restart_video() -> tuple[bool, str]:
    # /usr/bin/systemctl is the real binary on every modern Ubuntu/Lubuntu;
    # sudoers also accepts /bin/systemctl for older layouts.
    return await _run("sudo", "-n", "/usr/bin/systemctl", "restart", VLC_UNIT)
