"""End-to-end: a scripted game driven through the real service, queue, and sinks.

No hardware and no network. This is the test that would have caught the original's
signature failure -- goals scored while the board was busy going missing entirely.
"""

from __future__ import annotations

import asyncio

import pytest
from conftest import make_game, make_goal, make_scoreboard

from nhl_ticker.board.queue import BoardQueue
from nhl_ticker.board.transport import FanOutTransport, NullTransport
from nhl_ticker.config import Settings
from nhl_ticker.nhl.models import Scoreboard
from nhl_ticker.runner import TickerService
from nhl_ticker.sinks.broadcast import BroadcastHub
from nhl_ticker.sinks.horn import HornSink, NullPlayer


class ScriptedClient:
    """Walks through a scripted sequence, one snapshot per poll."""

    def __init__(self, *scoreboards: Scoreboard):
        self._scoreboards = list(scoreboards)
        self.index = 0

    async def fetch_scoreboard(self, date: str | None = None) -> Scoreboard:
        board = self._scoreboards[min(self.index, len(self._scoreboards) - 1)]
        self.index += 1
        return board


class RecordingSocket:
    """Stands in for a connected UI client."""

    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, message: dict) -> None:
        self.messages.append(message)

    def of_type(self, kind: str) -> list[dict]:
        return [m for m in self.messages if m.get("type") == kind]


@pytest.fixture
def instant() -> Settings:
    """Zero hold times so a whole game plays out inside a test."""
    return Settings(
        board_frame_ms=0.0,
        board_min_dwell_seconds=0.0,
        board_static_hold_seconds=0.0,
        horn_max_seconds=0.01,
    )


def a_game_in_three_periods() -> list[Scoreboard]:
    """Scheduled, under way, two quick goals, then a final in overtime."""
    first = make_goal("BOS", "Pastrnak", 1, 0)
    second = make_goal("WSH", "Ovechkin", 1, 1, strength="pp")
    winner = make_goal("WSH", "Strome", 1, 2, period=4)

    return [
        make_scoreboard(make_game(state="FUT")),
        make_scoreboard(make_game(state="LIVE")),
        # Both goals land inside one poll interval.
        make_scoreboard(
            make_game(state="LIVE", away_score=1, home_score=1, goals=[first, second])
        ),
        make_scoreboard(
            make_game(
                state="FINAL",
                away_score=1,
                home_score=2,
                period=4,
                period_type="OT",
                goals=[first, second, winner],
            )
        ),
    ]


async def run_polls(service: TickerService, transport: NullTransport, count: int) -> None:
    await service.queue.start()
    for _ in range(count):
        await service.poll_once()
        # Let the consumer drain what that poll produced.
        for _ in range(50):
            await asyncio.sleep(0)
    await asyncio.sleep(0.05)
    await service.stop()


def build(instant: Settings):
    board = NullTransport()
    socket = RecordingSocket()
    hub = BroadcastHub()
    queue = BoardQueue(FanOutTransport(board), instant)
    player = NullPlayer()
    service = TickerService(
        ScriptedClient(*a_game_in_three_periods()),
        queue,
        hub,
        instant,
        horn=HornSink(instant, player),
    )
    queue.set_message_callback(service.on_board_message)
    return service, board, hub, socket, player


async def test_a_whole_game_produces_the_right_board_messages(instant):
    service, board, hub, socket, _ = build(instant)
    await hub.connect(socket)

    await run_polls(service, board, 4)

    texts = [t for t in board.sent]
    joined = " | ".join(texts)

    # Both goals from the same interval reached the board, in order.
    assert "Pastrnak" in joined
    assert "Ovechkin" in joined
    assert joined.index("Pastrnak") < joined.index("Ovechkin")
    # And the overtime winner and the final.
    assert "Strome" in joined
    assert "F/OT" in joined


async def test_priming_poll_emits_no_alerts(instant):
    service, board, _, _, _ = build(instant)
    await run_polls(service, board, 1)

    # The first poll only ever shows the slate summary, never a goal alert.
    assert all("Goal!" not in message for message in board.sent)


async def test_the_horn_fires_once_per_goal_for_the_scoring_team(instant):
    service, board, _, _, player = build(instant)
    await run_polls(service, board, 4)

    assert [p.name for p in player.started] == [
        "boston.mp3",
        "washington.mp3",
        "washington.mp3",
    ]


async def test_the_ui_sees_every_goal_and_the_payloads(instant):
    service, board, hub, socket, _ = build(instant)
    await hub.connect(socket)

    await run_polls(service, board, 4)

    goals = [m for m in socket.of_type("event") if m["kind"] == "GoalEvent"]
    assert [g["scorer"] for g in goals] == ["Pastrnak", "Ovechkin", "Strome"]
    assert goals[1]["strength"] == "pp"
    # Goals arriving together are flagged so the UI can say so.
    assert goals[0]["backfilled"] is True

    board_messages = socket.of_type("boardMessage")
    assert board_messages, "the UI must be told what reached the board"
    assert all(m["payload"].startswith("~") for m in board_messages)


async def test_the_emulator_and_the_board_receive_identical_bytes(instant):
    """Whatever the UI renders must be exactly what the hardware was sent."""
    hardware = NullTransport()
    emulator = NullTransport()
    hub = BroadcastHub()
    queue = BoardQueue(FanOutTransport(hardware, emulator), instant)
    service = TickerService(
        ScriptedClient(*a_game_in_three_periods()), queue, hub, instant
    )
    queue.set_message_callback(service.on_board_message)

    await run_polls(service, hardware, 4)

    assert hardware.sent == emulator.sent
    assert len(hardware.sent) > 0


async def test_poll_rate_tracks_the_game_through_its_lifecycle(instant):
    service, board, _, _, _ = build(instant)
    await service.queue.start()

    await service.poll_once()  # FUT
    assert service.poll_interval(service.scoreboard) == instant.poll_pregame_seconds

    await service.poll_once()  # LIVE
    assert service.poll_interval(service.scoreboard) == instant.poll_live_seconds

    await service.poll_once()  # still LIVE
    await service.poll_once()  # FINAL
    assert service.poll_interval(service.scoreboard) == instant.poll_idle_seconds

    await service.stop()
