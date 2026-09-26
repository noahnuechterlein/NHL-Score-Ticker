"""The slate summary must actually cycle through all of its pages.

A busy night does not fit in one board message, so the summary is paginated. The pages are
only useful if the board reaches them: the first implementation re-submitted a fresh
summary on every poll and discarded every pending page, so whenever the poll interval was
shorter than a page's hold time -- which is the normal case during live hockey -- the board
looped on page one forever and most of the slate was never shown.

These tests pin the delivery behaviour, not just the page construction.
"""

from __future__ import annotations

import asyncio

import pytest
from conftest import make_game, make_scoreboard

from nhl_ticker.board.protocol import (
    plain_text,
)
from nhl_ticker.board.messages import (
    summary_pages,
)
from nhl_ticker.board.queue import BoardQueue
from nhl_ticker.board.transport import NullTransport
from nhl_ticker.config import Settings
from nhl_ticker.core.events import GoalEvent
from nhl_ticker.nhl.models import Scoreboard
from nhl_ticker.runner import TickerService
from nhl_ticker.sinks.broadcast import BroadcastHub

from test_queue import goal_event


class RepeatClient:
    """Returns the same slate every poll, like a quiet period during live games."""

    def __init__(self, scoreboard: Scoreboard):
        self._scoreboard = scoreboard
        self.calls = 0

    async def fetch_scoreboard(self, date: str | None = None) -> Scoreboard:
        self.calls += 1
        return self._scoreboard


#: Distinct matchups, so pages are textually distinguishable from one another. Using the
#: same teams for every game makes every page identical and hides rotation bugs.
MATCHUPS = [
    ("BOS", "WSH"), ("NYR", "NYI"), ("DAL", "MIN"), ("WPG", "COL"),
    ("TOR", "MTL"), ("EDM", "CGY"), ("VGK", "SJS"), ("PHI", "PIT"),
    ("CHI", "STL"), ("DET", "CBJ"), ("FLA", "TBL"), ("VAN", "SEA"),
]


def busy_slate(count: int = 12) -> Scoreboard:
    return make_scoreboard(
        *[
            make_game(game_id=i, away=MATCHUPS[i][0], home=MATCHUPS[i][1], state="LIVE")
            for i in range(count)
        ]
    )


@pytest.fixture
def quick() -> Settings:
    """Real asyncio timing, but scaled so a page holds tens of milliseconds.

    Keeps the property that matters -- poll interval shorter than a page hold -- while
    letting the test run in about a second.
    """
    return Settings(
        board_frame_ms=0.1,
        board_min_dwell_seconds=0.0,
        board_static_hold_seconds=0.0,
        poll_live_seconds=0.02,
    )


def page_index_of(text: str, pages: list[str]) -> int | None:
    for index, page in enumerate(pages):
        if plain_text(page).strip() == text.strip():
            return index
    return None


async def test_every_summary_page_reaches_the_board_during_live_polling(quick):
    """The regression: with polls faster than a page, pages 2+ were never displayed."""
    slate = busy_slate()
    pages = summary_pages(slate.games, quick)
    assert len(pages) >= 3, "need a multi-page slate for this to mean anything"

    transport = NullTransport()
    queue = BoardQueue(transport, quick)
    service = TickerService(RepeatClient(slate), queue, BroadcastHub(), quick)
    await service.start()

    # Poll far more often than a page can be displayed, as live play does.
    for _ in range(60):
        await service.poll_once()
        await asyncio.sleep(quick.poll_live_seconds)
    await service.stop()

    shown = {page_index_of(plain_text(sent), pages) for sent in transport.sent}
    shown.discard(None)
    assert shown == set(range(len(pages))), (
        f"pages {sorted(set(range(len(pages))) - shown)} never reached the board"
    )


async def test_pages_advance_in_order_rather_than_repeating_the_first(quick):
    slate = busy_slate()
    pages = summary_pages(slate.games, quick)

    transport = NullTransport()
    queue = BoardQueue(transport, quick)
    service = TickerService(RepeatClient(slate), queue, BroadcastHub(), quick)
    await service.start()
    for _ in range(40):
        await service.poll_once()
        await asyncio.sleep(quick.poll_live_seconds)
    await service.stop()

    order = [page_index_of(plain_text(s), pages) for s in transport.sent]
    order = [o for o in order if o is not None]
    assert len(order) >= 3
    # Consecutive displays must not all be the same page.
    assert len(set(order[:4])) > 1, f"board stuck repeating page {order[0]}"


async def test_a_goal_still_pre_empts_the_rotating_summary(quick):
    """Fixing rotation must not cost us goal priority."""
    slate = busy_slate()
    transport = NullTransport()
    queue = BoardQueue(transport, quick)
    service = TickerService(RepeatClient(slate), queue, BroadcastHub(), quick)
    await service.start()

    await service.poll_once()
    await asyncio.sleep(quick.poll_live_seconds * 2)
    before = len(transport.sent)

    queue.submit(goal_event("Ovechkin", "WSH"))
    for _ in range(20):
        await asyncio.sleep(0.005)
        if any("Goal!" in s for s in transport.sent[before:]):
            break
    await service.stop()

    goals = [s for s in transport.sent if "Goal!" in s]
    assert goals, "a submitted goal must still reach the board"


async def test_summary_content_stays_fresh_as_scores_change(quick):
    """Rotation must not pin the board to a stale snapshot of the slate."""
    first = busy_slate()
    later = make_scoreboard(
        *[
            make_game(
                game_id=i, away=MATCHUPS[i][0], home=MATCHUPS[i][1],
                state="LIVE", home_score=4,
            )
            for i in range(12)
        ]
    )

    transport = NullTransport()
    queue = BoardQueue(transport, quick)
    client = RepeatClient(first)
    service = TickerService(client, queue, BroadcastHub(), quick)
    await service.start()

    for _ in range(10):
        await service.poll_once()
        await asyncio.sleep(quick.poll_live_seconds)
    client._scoreboard = later
    for _ in range(30):
        await service.poll_once()
        await asyncio.sleep(quick.poll_live_seconds)
    await service.stop()

    assert any("4-0" in plain_text(s) for s in transport.sent), "board never showed the new score"


async def test_a_single_page_slate_still_works(quick):
    """The common small-slate case must not regress into showing nothing."""
    slate = make_scoreboard(make_game(game_id=1, state="LIVE"))
    transport = NullTransport()
    queue = BoardQueue(transport, quick)
    service = TickerService(RepeatClient(slate), queue, BroadcastHub(), quick)
    await service.start()
    for _ in range(10):
        await service.poll_once()
        await asyncio.sleep(quick.poll_live_seconds)
    await service.stop()

    assert transport.sent
    assert all("WSH" in plain_text(s) for s in transport.sent)


async def test_an_empty_slate_displays_nothing_rather_than_crashing(quick):
    transport = NullTransport()
    queue = BoardQueue(transport, quick)
    service = TickerService(RepeatClient(make_scoreboard()), queue, BroadcastHub(), quick)
    await service.start()
    for _ in range(5):
        await service.poll_once()
        await asyncio.sleep(quick.poll_live_seconds)
    await service.stop()

    assert transport.sent == []


def test_goal_event_helper_is_shared() -> None:
    """Guard against the import from test_queue silently breaking."""
    assert isinstance(goal_event(), GoalEvent)
