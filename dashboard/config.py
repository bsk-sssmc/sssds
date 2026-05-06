"""Runtime configuration for the dashboard.

All knobs come from environment variables so the same code runs in dev
and on the deployed box without edits. The systemd unit pins these.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _required(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(
            f"environment variable {name} is required (see deploy/dashboard.service)"
        )
    return val


@dataclass(frozen=True)
class Config:
    bind_host: str
    bind_port: int

    # Path to the SQLite file. Created on first run.
    db_path: str

    # bcrypt hash of the single admin password.
    admin_password_hash: str

    # Used to sign the session cookie. Anything random and >=32 chars.
    session_secret: str

    # Shared token agents must present on connect.
    agent_token: str

    # How long an event row stays in the audit log.
    event_retention_days: int

    # Optional explicit subnet broadcast (e.g. "192.168.1.255"). When
    # blank we fall back to the limited broadcast 255.255.255.255.
    wol_broadcast: str

    # An offline node is one we haven't heard from in this many seconds.
    offline_after_seconds: int


def load() -> Config:
    return Config(
        bind_host=os.environ.get("SSSDS_BIND_HOST", "0.0.0.0"),
        bind_port=int(os.environ.get("SSSDS_BIND_PORT", "8080")),
        db_path=os.environ.get("SSSDS_DB_PATH", "/var/lib/sssds/dashboard.db"),
        admin_password_hash=_required("SSSDS_ADMIN_PASSWORD_HASH"),
        session_secret=_required("SSSDS_SESSION_SECRET"),
        agent_token=_required("SSSDS_AGENT_TOKEN"),
        event_retention_days=int(os.environ.get("SSSDS_EVENT_RETENTION_DAYS", "180")),
        wol_broadcast=os.environ.get("SSSDS_WOL_BROADCAST", ""),
        offline_after_seconds=int(os.environ.get("SSSDS_OFFLINE_AFTER", "30")),
    )
