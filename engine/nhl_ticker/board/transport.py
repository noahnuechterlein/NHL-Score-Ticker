"""Getting a payload onto the physical board -- or pretending to.

``TcpBoardTransport`` is the current firmware: open a socket, write the payload, close.
``HttpBoardTransport`` is the original Yun sketch: a bare GET, exactly as the original's
``Printer.printToBoard`` did, kept in case the board goes back to that firmware.
``NullTransport`` records what would have been sent, which is what lets the whole pipeline
be developed and tested with no hardware attached.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

import httpx

from ..config import Settings, settings as default_settings
from .protocol import (
    encode_url,
    plain_text,
)

log = logging.getLogger(__name__)


class BoardTransport(Protocol):
    async def send(self, payload: str) -> bool:
        """Deliver a payload. Returns False if it did not land."""
        ...

    async def clear(self) -> bool: ...

    async def aclose(self) -> None: ...


class NullTransport:
    """Discards writes but keeps a log of them, for tests and emulator-only runs."""

    #: Not real hardware, so its success says nothing about the board.
    is_hardware = False

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.cleared = 0

    async def send(self, payload: str) -> bool:
        self.sent.append(payload)
        log.info("board (null): %s", plain_text(payload))
        return True

    async def clear(self) -> bool:
        self.cleared += 1
        return True

    async def aclose(self) -> None:
        return None


class HttpBoardTransport:
    """Talks to the Arduino Yun over HTTP.

    A failed write is logged and swallowed rather than raised: a board that is unplugged,
    asleep, or mid-scroll must not take the ticker down with it. The original recursed into
    itself on failure (``self.clearBoard()`` calling itself after a sleep), which could
    stack overflow if the board stayed unreachable.
    """

    is_hardware = True

    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None):
        self._settings = settings or default_settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=self._settings.http_timeout_seconds)
        #: Outcome of the most recent write. None until something has been tried.
        self.online: bool | None = None

    async def _get(self, url: str) -> bool:
        try:
            response = await self._client.get(url)
            response.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            log.warning("board write failed (%s): %s", type(exc).__name__, exc)
            return False

    async def send(self, payload: str) -> bool:
        ok = await self._get(encode_url(payload, self._settings))
        if ok:
            log.info("board: %s", plain_text(payload))
        elif self.online:
            log.error("board at %s stopped responding", self._settings.board_host)
        self.online = ok
        return ok

    async def clear(self) -> bool:
        return await self._get(f"{self._settings.board_url_base}/clear")

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class TcpBoardTransport:
    """Talks to the WiFi firmware over a raw TCP socket.

    One connection per message: the sketch reads until the client hangs up and treats
    everything it got as the payload, so there is no framing and no URL encoding -- the
    marker string goes out byte for byte. Failures are logged and swallowed, as over HTTP.
    """

    is_hardware = True

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or default_settings
        #: Outcome of the most recent write. None until something has been tried.
        self.online: bool | None = None

    async def _write(self, data: bytes) -> bool:
        cfg = self._settings
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(cfg.board_host, cfg.board_port),
                cfg.http_timeout_seconds,
            )
            try:
                writer.write(data)
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
            return True
        except (OSError, TimeoutError) as exc:
            log.warning("board write failed (%s): %s", type(exc).__name__, exc)
            return False

    async def send(self, payload: str) -> bool:
        # render() has already folded to ASCII; "replace" is only a backstop.
        ok = await self._write(payload.encode("ascii", "replace"))
        if ok:
            log.info("board: %s", plain_text(payload))
        elif self.online:
            log.error("board at %s stopped responding", describe(self._settings))
        self.online = ok
        return ok

    async def clear(self) -> bool:
        # The socket has no clear command, so overwrite the window with blanks.
        return await self._write(b" " * self._settings.board_chars)

    async def aclose(self) -> None:
        return None


class RetryingTransport:
    """Retries a wrapped transport a few times before giving up.

    The board sits on the LAN and the Yun is not especially robust, so a single dropped
    write is routine. Previously that meant a goal alert was simply lost -- the NHL client
    retried three times while the board got exactly one attempt. Unlike the original,
    which recursed into itself on failure, this gives up after a bounded number of tries.
    """

    def __init__(
        self,
        inner: BoardTransport,
        attempts: int = 3,
        backoff_seconds: float = 0.25,
    ) -> None:
        self._inner = inner
        self._attempts = max(1, attempts)
        self._backoff = backoff_seconds

    @property
    def is_hardware(self) -> bool:
        return getattr(self._inner, "is_hardware", False)

    @property
    def online(self) -> bool | None:
        return getattr(self._inner, "online", None)

    async def _attempt(self, action, what: str) -> bool:
        for attempt in range(1, self._attempts + 1):
            if await action():
                if attempt > 1:
                    log.info("board %s succeeded on attempt %d", what, attempt)
                return True
            if attempt < self._attempts:
                await asyncio.sleep(self._backoff * attempt)
        log.warning("board %s failed after %d attempts", what, self._attempts)
        return False

    async def send(self, payload: str) -> bool:
        return await self._attempt(lambda: self._inner.send(payload), "write")

    async def clear(self) -> bool:
        return await self._attempt(self._inner.clear, "clear")

    async def aclose(self) -> None:
        await self._inner.aclose()


class FanOutTransport:
    """Sends to several transports at once.

    Used to drive the real board and the UI emulator from the same queue, guaranteeing
    they see byte-identical payloads.

    Reports success only when *every* leg succeeded. Returning "any succeeded" hid the
    case that actually matters: with the emulator always succeeding, an unreachable board
    looked like a healthy write.
    """

    is_hardware = True

    def __init__(self, *transports: BoardTransport) -> None:
        self._transports = [t for t in transports if t is not None]

    @property
    def hardware_online(self) -> bool | None:
        """Whether the real board answered last time, or None if there is no real board."""
        for transport in self._transports:
            if getattr(transport, "is_hardware", False):
                online = getattr(transport, "online", None)
                if online is not None:
                    return online
        return None

    async def send(self, payload: str) -> bool:
        results = [await t.send(payload) for t in self._transports]
        return all(results) if results else True

    async def clear(self) -> bool:
        results = [await t.clear() for t in self._transports]
        return all(results) if results else True

    async def aclose(self) -> None:
        for transport in self._transports:
            await transport.aclose()


def describe(settings: Settings) -> str:
    """Where board writes go, for log lines."""
    if settings.board_protocol == "http":
        return settings.board_url_base
    return f"tcp://{settings.board_host}:{settings.board_port}"


def board_transport(settings: Settings) -> RetryingTransport:
    """The real board for the configured protocol, wrapped in retries."""
    inner = HttpBoardTransport(settings) if settings.board_protocol == "http" else TcpBoardTransport(settings)
    return RetryingTransport(
        inner,
        attempts=settings.board_write_attempts,
        backoff_seconds=settings.board_write_backoff_seconds,
    )

