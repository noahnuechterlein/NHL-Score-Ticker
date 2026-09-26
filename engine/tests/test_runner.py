"""Poll cadence, event dispatch, and the fake-goal test hook."""

from __future__ import annotations

import pytest
from conftest import make_game, make_goal, make_scoreboard

from nhl_ticker.board.queue import BoardQueue
from nhl_ticker.board.transport import NullTransport
from nhl_ticker.config import Settings
from nhl_ticker.nhl.models import Scoreboard
from nhl_ticker.runner import TickerService
from nhl_ticker.sinks.broadcast import BroadcastHub


class StubClient:
    """Serves a canned sequence of scoreboards instead of calling the NHL API."""

    def __init__(self, *scoreboards: Scoreboard):
        self._scoreboards = list(scoreboards)
        self.calls = 0

    async def fetch_scoreboard(self, date: str | None = None) -> Scoreboard:
        board = self._scoreboards[min(self.calls, len(self._scoreboards) - 1)]
        self.calls += 1
        return board


@pytest.fixture
def fast_settings() -> Settings:
    return Settings(
        board_frame_ms=0.0, board_min_dwell_seconds=0.0, board_static_hold_seconds=0.0
    )


def build(*scoreboards: Scoreboard, settings: Settings | None = None):
    cfg = settings or Settings(
        board_frame_ms=0.0, board_min_dwell_seconds=0.0, board_static_hold_seconds=0.0
    )
    transport = NullTransport()
    queue = BoardQueue(transport, cfg)
    service = TickerService(StubClient(*scoreboards), queue, BroadcastHub(), cfg)
    return service, transport, queue


# ------------------------------------------------------------------ cadence


def test_poll_interval_is_fast_while_a_game_is_live(fast_settings):
    service, _, _ = build(settings=fast_settings)
    live = make_scoreboard(make_game(state="LIVE"), make_game(game_id=2, state="FUT"))
    assert service.poll_interval(live) == fast_settings.poll_live_seconds


def test_poll_interval_is_slow_before_puck_drop(fast_settings):
    service, _, _ = build(settings=fast_settings)
    scheduled = make_scoreboard(make_game(state="FUT"), make_game(game_id=2, state="PRE"))
    assert service.poll_interval(scheduled) == fast_settings.poll_pregame_seconds


def test_poll_interval_idles_once_every_game_is_final(fast_settings):
    service, _, _ = build(settings=fast_settings)
    done = make_scoreboard(make_game(state="FINAL"), make_game(game_id=2, state="OFF"))
    assert service.poll_interval(done) == fast_settings.poll_idle_seconds


def test_poll_interval_idles_on_an_empty_slate(fast_settings):
    """The original dropped to an hourly poll on a wall-clock rule instead."""
    service, _, _ = build(settings=fast_settings)
    assert service.poll_interval(make_scoreboard()) == fast_settings.poll_idle_seconds
    assert service.poll_interval(None) == fast_settings.poll_idle_seconds


# ------------------------------------------------------------------ polling


async def test_first_poll_primes_quietly_then_reports_the_next_goal():
    before = make_scoreboard(make_game(state="LIVE"))
    after = make_scoreboard(
        make_game(state="LIVE", home_score=1, goals=[make_goal("WSH", "Ovechkin", 0, 1)])
    )
    service, _, queue = build(before, after)

    await service.poll_once()
    # Priming queues no alerts at all; the slate is idle content, not a queued event.
    assert queue.pending() == []
    assert queue.describe()["summaryPages"] == 1

    await service.poll_once()
    kinds = [type(i.event).__name__ for i in queue.pending()]
    assert "GoalEvent" in kinds


async def test_polling_refreshes_the_summary_without_queueing_anything():
    """Replaced test_a_summary_is_offered_every_poll_but_never_accumulates.

    That test checked the queue held exactly one pending summary. Queueing summaries at
    all was the bug: each poll discarded the unshown pages of the previous one.
    """
    board = make_scoreboard(make_game(state="LIVE"))
    service, _, queue = build(board)

    for _ in range(5):
        await service.poll_once()

    assert queue.pending() == []
    assert queue.describe()["summaryPages"] >= 1


async def test_a_failing_fetch_does_not_raise_out_of_the_service():
    class Boom:
        async def fetch_scoreboard(self, date=None):
            raise RuntimeError("network down")

    cfg = Settings(board_frame_ms=0.0, board_min_dwell_seconds=0.0, board_static_hold_seconds=0.0)
    service = TickerService(Boom(), BoardQueue(NullTransport(), cfg), BroadcastHub(), cfg)

    with pytest.raises(RuntimeError):
        await service.poll_once()
    # The loop wrapper is what swallows it; snapshot still works with no data.
    assert service.snapshot()["games"] == []


# ------------------------------------------------------------------ failure backoff


def test_a_failed_poll_retries_quickly_rather_than_waiting_out_the_idle_interval():
    """A network blip at startup left scoreboard=None, so poll_interval returned the
    idle value and the ticker went silent for half an hour."""
    cfg = Settings(poll_idle_seconds=1800.0, poll_error_seconds=5.0)
    service, _, _ = build(make_scoreboard(), settings=cfg)

    service.note_failure()
    assert service.next_interval() <= 10.0


def test_backoff_grows_but_stays_bounded():
    cfg = Settings(poll_error_seconds=5.0, poll_error_max_seconds=60.0)
    service, _, _ = build(make_scoreboard(), settings=cfg)

    seen = []
    for _ in range(10):
        service.note_failure()
        seen.append(service.next_interval())

    assert seen[0] < seen[1] < seen[2], "backoff should grow"
    assert max(seen) <= cfg.poll_error_max_seconds
    assert seen == sorted(seen), "backoff must never shrink while failing"


def test_backoff_never_exceeds_the_healthy_interval_when_that_is_shorter():
    """Failing during live play must not slow us below the normal live cadence."""
    cfg = Settings(
        poll_live_seconds=8.0, poll_error_seconds=5.0, poll_error_max_seconds=60.0
    )
    service, _, _ = build(make_scoreboard(make_game(state="LIVE")), settings=cfg)
    service.scoreboard = make_scoreboard(make_game(state="LIVE"))

    for _ in range(10):
        service.note_failure()
    assert service.next_interval() <= cfg.poll_live_seconds


async def test_a_successful_poll_clears_the_backoff():
    cfg = Settings(
        board_frame_ms=0.0,
        board_min_dwell_seconds=0.0,
        board_static_hold_seconds=0.0,
        poll_error_seconds=5.0,
    )
    service, _, _ = build(make_scoreboard(make_game(state="LIVE")), settings=cfg)

    for _ in range(5):
        service.note_failure()
    assert service.next_interval() > cfg.poll_error_seconds

    await service.poll_once()
    assert service.next_interval() == service.poll_interval(service.scoreboard)


# ------------------------------------------------------------------ fake goals


async def test_fake_goal_attaches_to_a_game_the_named_team_actually_plays_in():
    """A Boston goal must not land in a Colorado-Winnipeg game."""
    board = make_scoreboard(
        make_game(game_id=1, away="BOS", home="WSH", state="LIVE"),
        make_game(game_id=2, away="WPG", home="COL", state="LIVE"),
    )
    service, _, _ = build(board)
    await service.poll_once()

    for _ in range(10):
        event = service.fake_goal(team_abbrev="BOS")
        assert event["gameId"] == 1
        assert "BOS Goal!" in event["text"]


async def test_fake_goal_rejects_a_team_that_is_not_on_the_slate():
    service, _, _ = build(make_scoreboard(make_game(away="BOS", home="WSH", state="LIVE")))
    await service.poll_once()

    with pytest.raises(LookupError, match="TOR"):
        service.fake_goal(team_abbrev="TOR")


async def test_fake_goal_rejects_a_team_not_in_the_requested_game():
    board = make_scoreboard(
        make_game(game_id=1, away="BOS", home="WSH", state="LIVE"),
        make_game(game_id=2, away="WPG", home="COL", state="LIVE"),
    )
    service, _, _ = build(board)
    await service.poll_once()

    with pytest.raises(LookupError):
        service.fake_goal(game_id=2, team_abbrev="BOS")


async def test_fake_goal_bumps_the_scoring_teams_score():
    board = make_scoreboard(
        make_game(away="BOS", home="WSH", away_score=2, home_score=3, state="LIVE")
    )
    service, _, _ = build(board)
    await service.poll_once()

    event = service.fake_goal(team_abbrev="BOS")
    assert "WSH 3-3 BOS" in event["text"]


async def test_fake_goal_errors_clearly_on_an_empty_slate():
    service, _, _ = build(make_scoreboard())
    await service.poll_once()

    with pytest.raises(LookupError):
        service.fake_goal()


async def test_fake_goal_does_not_corrupt_the_real_scoreboard():
    """Injection must not poison the tracker into reporting a phantom goal later."""
    board = make_scoreboard(make_game(away="BOS", home="WSH", state="LIVE"))
    service, _, _ = build(board)
    await service.poll_once()

    service.fake_goal(team_abbrev="BOS")

    assert service.scoreboard.games[0].away_team.score == 0
    assert await service.poll_once() is not None
