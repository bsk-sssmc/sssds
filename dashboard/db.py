"""SQLite layer for the dashboard.

Two tables:

  nodes   — one row per registered client node, keyed by zone+node.
            Acts as the authoritative registry. New nodes are inserted
            on first heartbeat (auto-registration).

  events  — append-only audit log of commands and connection state
            changes. Rotated by date with ``prune_events()``.
"""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    key            TEXT PRIMARY KEY,        -- 'zone-1-node-3'
    zone           INTEGER NOT NULL,
    node           INTEGER NOT NULL,
    mac            TEXT,
    hostname       TEXT,
    last_ip        TEXT,
    last_seen      INTEGER,                 -- unix seconds
    first_seen     INTEGER NOT NULL,
    last_heartbeat TEXT                     -- raw JSON of most recent heartbeat
);
CREATE INDEX IF NOT EXISTS idx_nodes_last_seen ON nodes(last_seen);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      INTEGER NOT NULL,               -- unix seconds
    node    TEXT,                           -- node key, or NULL for system events
    kind    TEXT NOT NULL,                  -- 'command', 'connect', 'disconnect', 'wake', 'error'
    detail  TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_node ON events(node);
"""


def init(db_path: str) -> None:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    with _connect(db_path) as cx:
        cx.executescript(SCHEMA)


@contextmanager
def _connect(db_path: str) -> Iterator[sqlite3.Connection]:
    cx = sqlite3.connect(db_path, isolation_level=None)  # autocommit
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA journal_mode=WAL")
    cx.execute("PRAGMA foreign_keys=ON")
    try:
        yield cx
    finally:
        cx.close()


# ---------------------------------------------------------------------------
# nodes
# ---------------------------------------------------------------------------

def upsert_node(
    db_path: str,
    *,
    key: str,
    zone: int,
    node: int,
    mac: str,
    hostname: str,
    ip: str,
) -> None:
    now = int(time.time())
    with _connect(db_path) as cx:
        cx.execute(
            """
            INSERT INTO nodes (key, zone, node, mac, hostname, last_ip, last_seen, first_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                mac=excluded.mac,
                hostname=excluded.hostname,
                last_ip=excluded.last_ip,
                last_seen=excluded.last_seen
            """,
            (key, zone, node, mac, hostname, ip, now, now),
        )


def update_heartbeat(db_path: str, key: str, payload_json: str) -> None:
    now = int(time.time())
    with _connect(db_path) as cx:
        cx.execute(
            "UPDATE nodes SET last_seen=?, last_heartbeat=? WHERE key=?",
            (now, payload_json, key),
        )


def list_nodes(db_path: str) -> list[dict]:
    with _connect(db_path) as cx:
        rows = cx.execute(
            "SELECT * FROM nodes ORDER BY zone, node"
        ).fetchall()
        return [dict(r) for r in rows]


def get_node(db_path: str, key: str) -> dict | None:
    with _connect(db_path) as cx:
        row = cx.execute("SELECT * FROM nodes WHERE key=?", (key,)).fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# events
# ---------------------------------------------------------------------------

def log_event(db_path: str, *, node: str | None, kind: str, detail: str = "") -> None:
    with _connect(db_path) as cx:
        cx.execute(
            "INSERT INTO events (ts, node, kind, detail) VALUES (?, ?, ?, ?)",
            (int(time.time()), node, kind, detail),
        )


def prune_events(db_path: str, retention_days: int) -> int:
    cutoff = int(time.time()) - retention_days * 86400
    with _connect(db_path) as cx:
        cur = cx.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
        return cur.rowcount or 0
