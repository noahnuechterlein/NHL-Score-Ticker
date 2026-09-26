"""The single writer that owns the board.

This is the piece the original got wrong. ``scraper.py`` called ``printToBoard`` inline and
then ``time.sleep(15)`` *inside the same loop that polled for scores*, so for fifteen
seconds after every goal it was neither watching for new goals nor able to report them. Any
goal scored in that window was folded into the next score diff and lost.

Here the poller never touches the board. It enqueues events and returns immediately; a
single consumer task drains the queue, respecting three rules:

1. **One message at a time.** The sketch only calls ``server.accept()`` when
   ``cmdDisplayed`` is true, so anything written mid-scroll is dropped on the floor. The
   consumer waits out ``timing.display_seconds`` before sending the next message.
2. **Priority.** A goal jumps ahead of a queued summary. Ties break by arrival order.
3. **Summaries coalesce.** A summary is a snapshot of the whole slate, so a newer one
   strictly supersedes an older one; only the freshest is ever shown.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
from dataclasses import dataclass, field

from ..config import Settings, settings as default_settings
from ..core.events import Event, Priority, SummaryTick
from . import timing
from .protocol import payloads_for, plain_text, visible_length
from .transport import BoardTransport

log = logging.getLogger(__name__)


@dataclass(order=True)
class _QueueItem:
    priority: int
    sequence: int
    event: Event = field(compare=False)
    payload: str = field(compare=False, default="")


@dataclass(frozen=True, slots=True)
class BoardMessage:
    """What the queue handed to the board, for the UI's benefit."""

    payload: str
    text: str
    priority: int
    kind: str
    hold_seconds: float
    sent_at: float


class BoardQueue:
    """Priority queue plus a consumer task that paces writes to the board."""

    def __init__(
        self,
        transport: BoardTransport,
        settings: Settings | None = None,
        on_message=None,
    ) -> None:
        self._transport = transport
        self._settings = settings or default_settings
        #: Optional async callback fired with each BoardMessage actually sent.
        self._on_message = on_message

        self._items: list[_QueueItem] = []
        self._counter = itertools.count()
        self._wakeup = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._busy_until: float = 0.0
        self._current: BoardMessage | None = None

    # ------------------------------------------------------------------ introspection

    @property
    def current(self) -> BoardMessage | None:
        return self._current

    @property
    def busy_seconds_remaining(self) -> float:
        return max(0.0, self._busy_until - time.monotonic())

    def pending(self) -> list[_QueueItem]:
        return sorted(self._items)

    def describe(self) -> dict:
        """Snapshot for the UI's queue inspector."""
        return {
            "busy": self.busy_seconds_remaining > 0,
            "busySecondsRemaining": round(self.busy_seconds_remaining, 2),
            "current": None
            if self._current is None
            else {
                "text": self._current.text,
                "kind": self._current.kind,
                "holdSeconds": round(self._current.hold_seconds, 2),
            },
            "pending": [
                {"kind": type(item.event).__name__, "text": plain_text(item.payload).strip()}
                for item in self.pending()
            ],
        }

    def set_message_callback(self, callback) -> None:
        """Register the async callback fired for each message that reaches the board."""
        self._on_message = callback

    # ------------------------------------------------------------------ producing

    def submit(self, event: Event) -> None:
        """Enqueue an event. Never blocks, never touches the network.

        A summary of a busy slate is too wide for one message, so it arrives as several
        pages; they are queued together and shown in order.
        """
        payloads = payloads_for(event, self._settings)

        if isinstance(event, SummaryTick):
            # A newer snapshot of the slate makes every page of the old one pointless.
            before = len(self._items)
            self._items = [i for i in self._items if not isinstance(i.event, SummaryTick)]
            if before != len(self._items):
                log.debug("coalesced %d stale summary page(s)", before - len(self._items))

        for payload in payloads:
            self._items.append(
                _QueueItem(
                    priority=int(event.priority),
                    sequence=next(self._counter),
                    event=event,
                    payload=payload,
                )
            )
        self._wakeup.set()

    # ------------------------------------------------------------------ consuming

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="board-queue")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def _pop(self) -> _QueueItem | None:
        if not self._items:
            return None
        best = min(self._items)
        self._items.remove(best)
        return best

    async def _run(self) -> None:
        while True:
            item = self._pop()
            if item is None:
                self._wakeup.clear()
                await self._wakeup.wait()
                continue
            await self.deliver(item)

    async def deliver(self, item: _QueueItem) -> BoardMessage:
        """Send one message and hold the board for as long as it will be busy."""
        hold = timing.display_seconds(visible_length(item.payload), self._settings)
        message = BoardMessage(
            payload=item.payload,
            text=plain_text(item.payload).strip(),
            priority=item.priority,
            kind=type(item.event).__name__,
            hold_seconds=hold,
            sent_at=time.time(),
        )

        await self._transport.send(item.payload)
        self._current = message
        self._busy_until = time.monotonic() + hold

        if self._on_message is not None:
            await self._on_message(message)

        await asyncio.sleep(hold)
        return message

    async def clear(self) -> None:
        await self._transport.clear()
        self._current = None
        self._busy_until = 0.0
