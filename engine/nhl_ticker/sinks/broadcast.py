"""Fan-out of engine activity to connected UI clients over WebSocket.

The hub is deliberately dumb: it holds a set of sockets and pushes JSON at them. A client
that has gone away is dropped rather than retried, and a slow client can never block the
engine, because every send is wrapped and failures are discarded.

Crucially the UI receives the *exact* board payload string, not a re-derived one, so the
emulator is rendering the same bytes the hardware would.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)


class BroadcastHub:
    def __init__(self) -> None:
        self._clients: set[Any] = set()
        self._lock = asyncio.Lock()
        #: Last message of each type, replayed to a client on connect so a freshly opened
        #: tab is not staring at an empty screen until the next poll.
        self._latest: dict[str, dict] = {}

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def connect(self, websocket: Any) -> None:
        async with self._lock:
            self._clients.add(websocket)
        log.info("ui client connected (%d total)", len(self._clients))
        for message in self._latest.values():
            await self._send_one(websocket, message)

    async def disconnect(self, websocket: Any) -> None:
        async with self._lock:
            self._clients.discard(websocket)
        log.info("ui client disconnected (%d left)", len(self._clients))

    async def _send_one(self, websocket: Any, message: dict) -> bool:
        try:
            await websocket.send_json(message)
            return True
        except Exception as exc:  # a dead socket must not propagate into the engine
            log.debug("dropping ui client: %s", exc)
            return False

    async def broadcast(self, message: dict, *, remember: bool = True) -> None:
        if remember:
            self._latest[message.get("type", "?")] = message
        if not self._clients:
            return
        dead = [c for c in list(self._clients) if not await self._send_one(c, message)]
        if dead:
            async with self._lock:
                self._clients.difference_update(dead)


class BroadcastTransport:
    """A BoardTransport that publishes to the UI instead of to hardware.

    Slotted into FanOutTransport alongside the real board so both get the same payload.
    """

    def __init__(self, hub: BroadcastHub) -> None:
        self._hub = hub

    async def send(self, payload: str) -> bool:
        await self._hub.broadcast({"type": "board", "payload": payload}, remember=False)
        return True

    async def clear(self) -> bool:
        await self._hub.broadcast({"type": "boardClear"}, remember=False)
        return True

    async def aclose(self) -> None:
        return None
