"""Tracks the live WebSocket per node.

A node is "online" when there's an active socket in the registry. The
dashboard sends commands by looking up the node's key here and writing
JSON over the socket.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable

from fastapi import WebSocket

from shared.protocol import Command, CommandKind


@dataclass
class Connection:
    ws: WebSocket
    key: str
    ip: str


class ConnectionManager:
    def __init__(self) -> None:
        self._by_key: dict[str, Connection] = {}
        self._lock = asyncio.Lock()
        # command_id -> future resolved when the agent acks
        self._pending: dict[str, asyncio.Future[tuple[bool, str]]] = {}
        self.on_change: Callable[[], Awaitable[None]] | None = None

    async def attach(self, conn: Connection) -> None:
        async with self._lock:
            old = self._by_key.get(conn.key)
            if old is not None:
                # A reconnect — drop the stale socket.
                try:
                    await old.ws.close()
                except Exception:
                    pass
            self._by_key[conn.key] = conn
        await self._fire_change()

    async def detach(self, key: str, ws: WebSocket) -> None:
        async with self._lock:
            current = self._by_key.get(key)
            # Only forget if it's still the same socket — a fast reconnect
            # may already have replaced it.
            if current is not None and current.ws is ws:
                self._by_key.pop(key, None)
        await self._fire_change()

    def online_keys(self) -> set[str]:
        return set(self._by_key.keys())

    def is_online(self, key: str) -> bool:
        return key in self._by_key

    async def send_command(
        self, key: str, kind: CommandKind, timeout: float = 5.0
    ) -> tuple[bool, str]:
        conn = self._by_key.get(key)
        if conn is None:
            return False, "node is not connected"

        cmd = Command(command_id=str(uuid.uuid4()), kind=kind)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[tuple[bool, str]] = loop.create_future()
        self._pending[cmd.command_id] = fut
        try:
            await conn.ws.send_text(cmd.model_dump_json())
        except Exception as e:
            self._pending.pop(cmd.command_id, None)
            return False, f"send failed: {e}"

        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            return False, "agent did not ack in time"
        finally:
            self._pending.pop(cmd.command_id, None)

    def resolve_ack(self, command_id: str, ok: bool, detail: str) -> None:
        fut = self._pending.get(command_id)
        if fut is not None and not fut.done():
            fut.set_result((ok, detail))

    async def _fire_change(self) -> None:
        if self.on_change is not None:
            try:
                await self.on_change()
            except Exception:
                pass


def parse_agent_message(raw: str) -> dict:
    """Parse + minimally validate a JSON envelope from the agent side."""
    msg = json.loads(raw)
    if not isinstance(msg, dict) or "type" not in msg:
        raise ValueError("malformed message")
    return msg
