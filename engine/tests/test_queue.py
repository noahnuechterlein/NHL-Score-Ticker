"""Board queue behaviour: priority, coalescing, and never blocking the poller."""

from __future__ import annotations

import asyncio

import pytest
from conftest import make_game, make_goal, make_scoreboard

from nhl_ticker.board import timing
from nhl_ticker.board.protocol import plain_text
from nhl_ticker.board.queue import BoardQueue
from nhl_ticker.board.transport import FanOutTransport, HttpBoardTransport, NullTransport
from nhl_ticker.config import Settings
from nhl_ticker.core.events import GameEndEvent, GameStartEvent, GoalEvent, SummaryTick


@pytest.fixture
def fast_settings() -> Settings:
    """Collapse all hold times so tests run instantly."""
    return Settings(
        board_frame_ms=0.0, board_min_dwell_seconds=0.0, board_static_hold_seconds=0.0
    )


def goal_event(scorer: str = "Ovechkin", team: str = "WSH") -> GoalEvent:
    game = make_scoreboard(
        make_game(home_score=1, goals=[make_goal(team, scorer, 0, 1)])
    ).games[0]
    return GoalEvent(game=game, goal=game.goals[-1])


def slate(*, score: int = 0):
    """Games for the idle summary. Summaries are set, not submitted."""
    return make_scoreboard(make_game(home_score=score)).games


async def drain(queue: BoardQueue, transport: NullTransport, expected: int) -> None:
    """Run the consumer until ``expected`` messages have actually reached the transport.

    Waiting on ``pending()`` alone is not enough: the last item is popped off the queue
    before it is delivered, so an empty queue does not mean the work is done.
    """
    await queue.start()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 2.0
    while loop.time() < deadline and len(transport.sent) < expected:
        await asyncio.sleep(0.005)
    await queue.stop()


# ------------------------------------------------------------------ priority


async def test_a_goal_pre_empts_the_idle_summary(fast_settings):
    """Previously asserted over two queued items; the summary is now idle content.

    The property under test is unchanged: a goal reaches the board before the slate does.
    """
    transport = NullTransport()
    queue = BoardQueue(transport, fast_settings)

    queue.set_summary(slate())
    queue.submit(goal_event())
    await drain(queue, transport, 1)

    assert "Goal!" in transport.sent[0]


async def test_priority_order_across_all_event_kinds(fast_settings):
    transport = NullTransport()
    queue = BoardQueue(transport, fast_settings)
    game = make_scoreboard(make_game(state="FINAL")).games[0]

    queue.submit(GameStartEvent(game=game))
    queue.submit(GameEndEvent(game=game))
    queue.submit(goal_event())
    await drain(queue, transport, 3)

    # Goal first, then game end, then game start. Unchanged by the summary rework.
    assert "Goal!" in transport.sent[0]
    assert transport.sent[1].endswith("F")
    assert "underway" in transport.sent[2]


async def test_equal_priority_keeps_arrival_order(fast_settings):
    transport = NullTransport()
    queue = BoardQueue(transport, fast_settings)

    queue.submit(goal_event("Pastrnak", "BOS"))
    queue.submit(goal_event("Ovechkin", "WSH"))
    await drain(queue, transport, 2)

    assert "Pastrnak" in transport.sent[0]
    assert "Ovechkin" in transport.sent[1]


# ------------------------------------------------------------------ coalescing


async def test_refreshing_the_summary_replaces_its_content(fast_settings):
    """Replaced test_stale_summaries_are_dropped.

    That test asserted that re-submitting a summary discarded the pending pages of the
    previous one -- the very mechanism that starved every page after the first. Setting
    the summary now swaps the content outright, so only the newest slate can be shown.
    """
    transport = NullTransport()
    queue = BoardQueue(transport, fast_settings)

    queue.set_summary(slate(score=0))
    queue.set_summary(slate(score=1))
    queue.set_summary(slate(score=2))

    await drain(queue, transport, 1)
    assert "2-0" in transport.sent[0]


async def test_setting_a_summary_never_disturbs_queued_goals(fast_settings):
    """Replaced test_coalescing_does_not_touch_goals; same guarantee, new mechanism."""
    transport = NullTransport()
    queue = BoardQueue(transport, fast_settings)

    queue.submit(goal_event("Pastrnak", "BOS"))
    queue.set_summary(slate())
    queue.submit(goal_event("Ovechkin", "WSH"))
    queue.set_summary(slate(score=1))

    assert len(queue.pending()) == 2
    await drain(queue, transport, 2)
    assert "Pastrnak" in transport.sent[0]
    assert "Ovechkin" in transport.sent[1]


async def test_submitting_a_summary_is_rejected(fast_settings):
    """There must be exactly one way to set the slate, or the old bug can creep back."""
    queue = BoardQueue(NullTransport(), fast_settings)
    with pytest.raises(TypeError, match="set_summary"):
        queue.submit(SummaryTick(games=slate()))


# ------------------------------------------------------------------ pacing


async def test_submit_never_blocks_even_while_the_board_is_busy():
    """The poller must be able to keep enqueueing while a long message scrolls.

    This is precisely what the original could not do: it slept inside the poll loop.
    """
    slow = Settings(board_frame_ms=33.0, board_min_dwell_seconds=5.0)
    queue = BoardQueue(NullTransport(), slow)
    await queue.start()

    queue.submit(goal_event())
    await asyncio.sleep(0)

    # Board is now mid-message; enqueueing more must return immediately.
    start = asyncio.get_running_loop().time()
    for index in range(50):
        queue.submit(goal_event(f"Player{index}"))
    elapsed = asyncio.get_running_loop().time() - start

    assert elapsed < 0.5
    assert len(queue.pending()) == 50
    await queue.stop()


async def test_messages_sent_while_busy_are_queued_not_dropped():
    slow = Settings(board_frame_ms=33.0, board_min_dwell_seconds=5.0)
    transport = NullTransport()
    queue = BoardQueue(transport, slow)
    await queue.start()

    queue.submit(goal_event("First"))
    await asyncio.sleep(0.05)
    queue.submit(goal_event("Second"))
    await asyncio.sleep(0.05)

    # Only the first has gone out; the second is waiting its turn, not lost.
    assert len(transport.sent) == 1
    assert "First" in transport.sent[0]
    assert len(queue.pending()) == 1
    assert queue.busy_seconds_remaining > 0

    await queue.stop()


async def test_hold_time_reflects_message_length(fast_settings):
    """A long scrolling message must own the board longer than a short one."""
    normal = Settings()
    assert timing.display_seconds(120, normal) > timing.display_seconds(10, normal)


# ------------------------------------------------------------------ introspection & fan-out


async def test_describe_reports_pending_work_and_summary_rotation(fast_settings):
    queue = BoardQueue(NullTransport(), fast_settings)
    queue.submit(goal_event())
    queue.set_summary(slate())

    snapshot = queue.describe()
    assert [p["kind"] for p in snapshot["pending"]] == ["GoalEvent"]
    assert "Goal!" in snapshot["pending"][0]["text"]
    # The UI needs to see where in the slate cycle the board is.
    assert snapshot["summaryPages"] == 1
    assert snapshot["summaryPage"] == 0


async def test_fan_out_sends_identical_payloads_to_every_transport(fast_settings):
    """The emulator must receive exactly what the hardware receives."""
    board = NullTransport()
    emulator = NullTransport()
    queue = BoardQueue(FanOutTransport(board, emulator), fast_settings)

    queue.submit(goal_event())
    await drain(queue, board, 1)

    assert board.sent == emulator.sent
    assert len(board.sent) == 1


async def test_on_message_callback_fires_with_the_sent_payload(fast_settings):
    seen = []

    async def record(message):
        seen.append(message)

    transport = NullTransport()
    queue = BoardQueue(transport, fast_settings, on_message=record)
    queue.submit(goal_event())
    await drain(queue, transport, 1)

    assert len(seen) == 1
    assert seen[0].kind == "GoalEvent"
    assert "Goal!" in seen[0].text


# ------------------------------------------------------------------ board health


class FailingTransport(NullTransport):
    """A board that is plugged in but not answering."""

    is_hardware = True

    def __init__(self) -> None:
        super().__init__()
        self.online = False

    async def send(self, payload: str) -> bool:
        return False


async def test_an_unreachable_board_is_not_masked_by_the_emulator(fast_settings):
    """FanOut used to return 'any succeeded', so the always-succeeding emulator made a
    dead board look like a healthy write."""
    fan = FanOutTransport(FailingTransport(), NullTransport())
    assert await fan.send("~ffffe630hi") is False


async def test_a_healthy_board_reports_success(fast_settings):
    fan = FanOutTransport(NullTransport(), NullTransport())
    assert await fan.send("~ffffe630hi") is True


async def test_queue_exposes_hardware_reachability(fast_settings):
    queue = BoardQueue(FanOutTransport(FailingTransport(), NullTransport()), fast_settings)
    assert queue.hardware_online is False

    # With no real board attached there is nothing to report.
    emulator_only = BoardQueue(FanOutTransport(NullTransport()), fast_settings)
    assert emulator_only.hardware_online is None


def test_a_fresh_http_transport_has_no_opinion_yet():
    assert HttpBoardTransport().online is None


# ------------------------------------------------------------------ resilience & bounds


class FlakyTransport(NullTransport):
    """Fails the first `failures` writes, then succeeds. Models a LAN blip."""

    is_hardware = True

    def __init__(self, failures: int = 1) -> None:
        super().__init__()
        self.failures = failures
        self.attempts = 0

    async def send(self, payload: str) -> bool:
        self.attempts += 1
        if self.attempts <= self.failures:
            return False
        return await super().send(payload)


async def test_a_transient_board_failure_is_retried():
    """One LAN blip used to drop a goal alert outright; the NHL client retried, the
    board got no attempts at all."""
    from nhl_ticker.board.transport import RetryingTransport

    flaky = FlakyTransport(failures=1)
    transport = RetryingTransport(flaky, attempts=3, backoff_seconds=0.0)

    assert await transport.send("~ffffe630hi") is True
    assert flaky.attempts == 2
    assert flaky.sent == ["~ffffe630hi"]


async def test_retries_give_up_rather_than_looping_forever():
    from nhl_ticker.board.transport import RetryingTransport

    flaky = FlakyTransport(failures=99)
    transport = RetryingTransport(flaky, attempts=3, backoff_seconds=0.0)

    assert await transport.send("~ffffe630hi") is False
    assert flaky.attempts == 3


async def test_a_healthy_board_is_written_once():
    from nhl_ticker.board.transport import RetryingTransport

    good = FlakyTransport(failures=0)
    transport = RetryingTransport(good, attempts=3, backoff_seconds=0.0)

    await transport.send("~ffffe630hi")
    assert good.attempts == 1, "no retry when the first write lands"


async def test_stale_goal_alerts_are_dropped(fast_settings):
    """A burst of goals must not still be scrolling minutes later."""
    cfg = fast_settings.model_copy(update={"goal_max_age_seconds": 0.05})
    transport = NullTransport()
    queue = BoardQueue(transport, cfg)

    queue.submit(goal_event("Stale", "BOS"))
    await asyncio.sleep(0.1)
    queue.submit(goal_event("Fresh", "WSH"))
    await drain(queue, transport, 1)

    joined = " ".join(transport.sent)
    assert "Fresh" in joined
    assert "Stale" not in joined


async def test_a_fresh_goal_is_never_dropped(fast_settings):
    transport = NullTransport()
    queue = BoardQueue(transport, fast_settings)
    queue.submit(goal_event("Ovechkin", "WSH"))
    await drain(queue, transport, 1)
    assert "Ovechkin" in transport.sent[0]


async def test_the_queue_is_bounded(fast_settings):
    """An unbounded backlog would grow without limit if the board stalled."""
    cfg = fast_settings.model_copy(update={"queue_max_items": 5})
    queue = BoardQueue(NullTransport(), cfg)

    for index in range(50):
        queue.submit(goal_event(f"Player{index}"))

    assert len(queue.pending()) <= 5


async def test_bounding_the_queue_keeps_the_newest_goals(fast_settings):
    """If we must shed load, the most recent goals are the ones worth showing."""
    cfg = fast_settings.model_copy(update={"queue_max_items": 3})
    queue = BoardQueue(NullTransport(), cfg)

    for index in range(10):
        queue.submit(goal_event(f"Player{index}"))

    kept = " ".join(plain_text(i.payload) for i in queue.pending())
    assert "Player9" in kept
    assert "Player0" not in kept


async def test_clear_is_sent_when_the_queue_stops(fast_settings):
    """Stopping the service left the last message frozen on the board indefinitely."""
    transport = NullTransport()
    queue = BoardQueue(transport, fast_settings)
    await queue.start()
    queue.submit(goal_event())
    await asyncio.sleep(0.02)
    await queue.stop(clear=True)

    assert transport.cleared == 1


async def test_stopping_without_clear_leaves_the_board_alone(fast_settings):
    transport = NullTransport()
    queue = BoardQueue(transport, fast_settings)
    await queue.start()
    await queue.stop()
    assert transport.cleared == 0
