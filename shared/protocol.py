"""Wire format between agent and dashboard.

Every message is a JSON object with a ``type`` discriminator. The agent
opens a WebSocket to the dashboard and sends:

  - Register   (once, on connect)
  - Heartbeat  (every ~10s)
  - Ack        (in response to a Command)

The dashboard sends:

  - Command    (shutdown / restart / restart-video)

Keeping both directions in one module so the agent and server can't
drift out of sync.
"""

from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# agent -> dashboard
# ---------------------------------------------------------------------------

class Register(BaseModel):
    type: Literal["register"] = "register"
    zone: int
    node: int
    mac: str           # primary NIC, lowercase, colon-separated
    hostname: str
    ip: str            # current IP the agent sees on its primary interface
    agent_version: str
    token: str         # shared secret from identity.conf


class Heartbeat(BaseModel):
    type: Literal["heartbeat"] = "heartbeat"
    uptime_seconds: int
    cpu_percent: float
    mem_percent: float
    disk_percent: float
    # Slice 2 will populate these; included now so the schema is stable.
    cpu_temp_c: float | None = None
    net_signal_dbm: float | None = None
    video_playing: bool | None = None
    last_error: str | None = None


class Ack(BaseModel):
    type: Literal["ack"] = "ack"
    command_id: str
    ok: bool
    detail: str = ""


AgentMessage = Union[Register, Heartbeat, Ack]


# ---------------------------------------------------------------------------
# dashboard -> agent
# ---------------------------------------------------------------------------

CommandKind = Literal["shutdown", "restart", "restart_video"]


class Command(BaseModel):
    type: Literal["command"] = "command"
    command_id: str          # uuid; agent echoes in Ack
    kind: CommandKind


ServerMessage = Command


# ---------------------------------------------------------------------------
# helpers used by both sides
# ---------------------------------------------------------------------------

def node_key(zone: int, node: int) -> str:
    """Stable identity string used as the primary key in the registry."""
    return f"zone-{zone}-node-{node}"
