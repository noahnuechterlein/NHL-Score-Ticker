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
2. **Priority.** A goal jumps ahead of anything less urgent. Ties break by arrival order.
3. **The summary is idle content, not a queued item.** A busy slate does not fit in one
   message, so it is paginated; the consumer cycles those pages whenever the queue has
   nothing more urgent to say, and a poll refreshes the page *content* without resetting
   the rotation.

That third rule exists because the obvious alternative is broken. Queueing the pages and
coalescing older ones on each poll means that whenever the poll interval is shorter than a
page hold time -- the normal case during live hockey -- the unshown pages are discarded
before the consumer ever reaches them, and the board loops on page one forever.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
from dataclasses import dataclass, field

from ..config import Settings, settings as default_settings
from ..core.events import Event, GoalEvent, Priority, SummaryTick
from ..nhl.models import Game
from . import timing
from .protocol import (
    plain_text,
    visible_length,
)
from .messages import (
    payloads_for,
    summary_pages,
)
from .transport import BoardTransport

log = logging.getLogger(__name__)

#: Floor on how long any message owns the board, so a zeroed hold cannot spin the
#: consumer through the summary rotation. Only reachable from test settings.
MIN_HOLD_SECONDS = 0.001


@dataclass(order=True)
class _QueueItem:
    priority: int
    sequence: int
    event: Event = field(compare=False)
    payload: str = field(compare=False, default="")
    #: Monotonic timestamp of submission, for staleness checks.
    queued_at: float = field(compare=False, default=0.0)


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
        #: Paginated slate summary, shown when nothing more urgent is queued.
        self._summary: list[str] = []
        #: Which page comes next. Deliberately survives a summary refresh.
        self._summary_index = 0
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
            "summaryPages": len(self._summary),
            "summaryPage": (self._summary_index % len(self._summary)) if self._summary else 0,
        }

    @property
    def hardware_online(self) -> bool | None:
        """Whether the physical board answered its last write, if there is one."""
        return getattr(self._transport, "hardware_online", None)

    def set_message_callback(self, callback) -> None:
        """Register the async callback fired for each message that reaches the board."""
        self._on_message = callback

    # ------------------------------------------------------------------ producing

    def submit(self, event: Event) -> None:
        """Enqueue an event. Never blocks, never touches the network."""
        if isinstance(event, SummaryTick):
            raise TypeError(
                "the slate summary is idle content, not a queued event -- use set_summary()"
            )

        now = time.monotonic()
        for payload in payloads_for(event, self._settings):
            self._items.append(
                _QueueItem(
                    priority=int(event.priority),
                    sequence=next(self._counter),
                    event=event,
                    payload=payload,
                    queued_at=now,
                )
            )

        self._enforce_bound()
        self._wakeup.set()

    def _enforce_bound(self) -> None:
        """Cap the backlog, shedding the oldest first.

        If the board stalls, an unbounded queue grows without limit. When something has to
        go, the oldest goals are the least worth showing -- by the time we caught up they
        would be minutes stale anyway.
        """
        limit = self._settings.queue_max_items
        if limit <= 0 or len(self._items) <= limit:
            return
        dropped = len(self._items) - limit
        self._items.sort(key=lambda item: item.sequence)
        self._items = self._items[dropped:]
        log.warning("board queue over %d items; dropped %d oldest", limit, dropped)

    def _is_stale(self, item: _QueueItem) -> bool:
        """Goal alerts expire; everything else stays queued.

        A summary is regenerated each poll so it cannot go stale, and start/end events are
        rare enough to be worth showing late.
        """
        max_age = self._settings.goal_max_age_seconds
        if max_age <= 0 or not isinstance(item.event, GoalEvent):
            return False
        return (time.monotonic() - item.queued_at) > max_age

    def set_summary(self, games: list[Game]) -> None:
        """Replace the slate summary the board falls back to when it is otherwise idle.

        The rotation index is preserved across refreshes, so a poll arriving mid-cycle does
        not send the board back to page one -- doing exactly that is what starved every
        page after the first. The index is only clamped, never reset, which keeps the
        content current while the cycle keeps advancing.

        An empty slate clears the summary entirely rather than leaving a blank message to
        write over and over.
        """
        self._summary = summary_pages(games, self._settings)
        self._summary_index = (
            self._summary_index % len(self._summary) if self._summary else 0
        )
        self._wakeup.set()

    # ------------------------------------------------------------------ consuming

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="board-queue")

    async def stop(self, clear: bool = False) -> None:
        """Stop the consumer, optionally blanking the board on the way out.

        Without the clear the board keeps displaying whatever it last received, forever.
        """
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if clear:
            await self.clear()

    def _pop(self) -> _QueueItem | None:
        """Highest-priority item that is still worth showing."""
        while self._items:
            best = min(self._items)
            self._items.remove(best)
            if not self._is_stale(best):
                return best
            log.info("dropping stale alert: %s", plain_text(best.payload).strip())
        return None

    def _next_summary_page(self) -> str | None:
        """The next page of the slate summary, advancing the rotation."""
        if not self._summary:
            return None
        page = self._summary[self._summary_index % len(self._summary)]
        self._summary_index = (self._summary_index + 1) % len(self._summary)
        return page

    async def _run(self) -> None:
        while True:
            item = self._pop()
            if item is not None:
                await self.deliver(item)
                continue

            page = self._next_summary_page()
            if page is not None:
                await self._show(page, "SummaryTick", int(Priority.SUMMARY))
                continue

            # Nothing queued and no slate to show: wait until something arrives.
            self._wakeup.clear()
            await self._wakeup.wait()

    async def deliver(self, item: _QueueItem) -> BoardMessage:
        """Send one queued event and hold the board for as long as it will be busy."""
        return await self._show(item.payload, type(item.event).__name__, item.priority)

    async def _show(self, payload: str, kind: str, priority: int) -> BoardMessage:
        """Write one message and own the board until it has finished displaying."""
        hold = max(
            timing.display_seconds(visible_length(payload), self._settings),
            MIN_HOLD_SECONDS,
        )
        message = BoardMessage(
            payload=payload,
            text=plain_text(payload).strip(),
            priority=priority,
            kind=kind,
            hold_seconds=hold,
            sent_at=time.time(),
        )

        await self._transport.send(payload)
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
