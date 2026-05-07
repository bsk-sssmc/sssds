"""Client-node agent for the museum dashboard.

Reads /etc/sssds/identity.conf, opens a WebSocket to the dashboard, and
keeps it alive forever — heartbeats every 10s, executes commands as
they arrive. On disconnect we sleep with capped exponential backoff and
try again. Designed to run as a systemd service, so any unhandled error
just kills the process and lets systemd restart it.

config file format (key=value, # comments allowed):

    zone=1
    node=3
    dashboard_url=ws://192.168.1.10:8080/ws/agent
    token=shared-secret-from-the-server
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import sys
import time
from pathlib import Path

import psutil
import websockets
from websockets.exceptions import ConnectionClosed

# Make ``shared.protocol`` importable when the agent is run from /opt/sssds
# (the provisioner installs ``shared/`` next to ``agent/``).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.protocol import Ack, Heartbeat, Register  # noqa: E402

from agent import platform_linux as plat  # noqa: E402


AGENT_VERSION = "0.1.0"
DEFAULT_CONFIG_PATH = "/etc/sssds/identity.conf"
HEARTBEAT_EVERY = 10.0       # seconds
RECONNECT_MIN = 1.0
RECONNECT_MAX = 30.0

log = logging.getLogger("sssds.agent")
logging.basicConfig(
    level=os.environ.get("SSSDS_AGENT_LOG", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

REQUIRED_KEYS = {"zone", "node", "dashboard_url", "token"}


def load_config(path: str) -> dict:
    if not os.path.isfile(path):
        raise SystemExit(f"missing config: {path}")
    out: dict[str, str] = {}
    with open(path) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            key = k.strip().lower()
            # The same file is consumed by systemd as an EnvironmentFile,
            # so its keys are SSSDS_ZONE / SSSDS_NODE / .... Strip that
            # prefix so the agent's view stays plain (zone, node, ...).
            if key.startswith("sssds_"):
                key = key[len("sssds_"):]
            out[key] = v.strip().strip('"').strip("'")
    missing = REQUIRED_KEYS - out.keys()
    if missing:
        raise SystemExit(f"{path}: missing keys: {sorted(missing)}")
    return out


# ---------------------------------------------------------------------------
# heartbeat payload
# ---------------------------------------------------------------------------

def _uptime_seconds() -> int:
    try:
        return int(time.time() - psutil.boot_time())
    except Exception:
        return 0


def collect_heartbeat() -> Heartbeat:
    return Heartbeat(
        uptime_seconds=_uptime_seconds(),
        cpu_percent=psutil.cpu_percent(interval=None),
        mem_percent=psutil.virtual_memory().percent,
        disk_percent=psutil.disk_usage("/").percent,
        # Slice 2 will populate cpu_temp_c, net_signal_dbm, video_playing.
    )


# ---------------------------------------------------------------------------
# command dispatch
# ---------------------------------------------------------------------------

async def dispatch(kind: str) -> tuple[bool, str]:
    if kind == "shutdown":
        return await plat.shutdown()
    if kind == "restart":
        return await plat.restart()
    if kind == "restart_video":
        return await plat.restart_video()
    return False, f"unknown command: {kind}"


# ---------------------------------------------------------------------------
# main session
# ---------------------------------------------------------------------------

async def session(cfg: dict) -> None:
    """One connection lifetime: register, then loop on heartbeat + command."""
    url = cfg["dashboard_url"]
    register = Register(
        zone=int(cfg["zone"]),
        node=int(cfg["node"]),
        mac=plat.primary_mac(),
        hostname=socket.gethostname(),
        ip=plat.primary_ip(),
        agent_version=AGENT_VERSION,
        token=cfg["token"],
    )

    log.info("connecting to %s as zone-%s-node-%s", url, cfg["zone"], cfg["node"])
    async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
        await ws.send(register.model_dump_json())
        log.info("registered")

        async def heartbeat_loop() -> None:
            while True:
                hb = collect_heartbeat()
                await ws.send(hb.model_dump_json())
                await asyncio.sleep(HEARTBEAT_EVERY)

        async def receive_loop() -> None:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    log.warning("malformed frame from server")
                    continue
                if msg.get("type") != "command":
                    continue
                cid = msg.get("command_id", "")
                kind = msg.get("kind", "")
                log.info("command id=%s kind=%s", cid, kind)
                ok, detail = await dispatch(kind)
                ack = Ack(command_id=cid, ok=ok, detail=detail)
                try:
                    await ws.send(ack.model_dump_json())
                except ConnectionClosed:
                    return

        # Both tasks must run until the WS dies; whichever ends first
        # cancels the other.
        hb = asyncio.create_task(heartbeat_loop())
        rx = asyncio.create_task(receive_loop())
        done, pending = await asyncio.wait(
            {hb, rx}, return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
        for t in done:
            exc = t.exception()
            if exc and not isinstance(exc, ConnectionClosed):
                raise exc


async def run(config_path: str) -> None:
    cfg = load_config(config_path)
    backoff = RECONNECT_MIN
    while True:
        try:
            await session(cfg)
            backoff = RECONNECT_MIN  # clean disconnect — reset
        except (OSError, ConnectionClosed) as e:
            log.warning("connection lost: %s — reconnecting in %.1fs", e, backoff)
        except Exception:
            log.exception("session failed — reconnecting in %.1fs", backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, RECONNECT_MAX)


def main() -> None:
    path = os.environ.get("SSSDS_AGENT_CONFIG", DEFAULT_CONFIG_PATH)
    try:
        asyncio.run(run(path))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
