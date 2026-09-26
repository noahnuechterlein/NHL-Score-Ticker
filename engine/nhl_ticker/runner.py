"""The service that ties everything together.

The poll loop's one job is: fetch a snapshot, hand it to the tracker, push whatever came
back onto the board queue, and go back to sleep. It never waits on the board, never plays
audio inline, and never sleeps on behalf of a display -- all of which the original did
inside this same loop.

Interval selection and failure backoff live in :mod:`nhl_ticker.cadence`; this module is
the loop and the fan-out to sinks.
"""

from __future__ import annotations

import asyncio
import logging
import random

from .board.queue import BoardMessage, BoardQueue
from .cadence import Cadence
from .config import Settings, settings as default_settings
from .core.events import Event, GoalEvent
from .core.serialize import event_to_dict, game_to_dict
from .core.state import ScoreboardTracker
from .nhl.client import NHLClient
from .nhl.models import Game, Goal, Scoreboard
from .sinks.broadcast import BroadcastHub

log = logging.getLogger(__name__)


class TickerService:
    def __init__(
        self,
        client: NHLClient,
        queue: BoardQueue,
        hub: BroadcastHub,
        settings: Settings | None = None,
        horn=None,
    ) -> None:
        self._settings = settings or default_settings
        self._client = client
        self._queue = queue
        self._hub = hub
        self._horn = horn

        self.tracker = ScoreboardTracker()
        self.scoreboard: Scoreboard | None = None
        self.last_error: str | None = None

        self._task: asyncio.Task | None = None
        self._poll_now = asyncio.Event()
        self._cadence = Cadence(self._settings)

    @property
    def hub(self) -> BroadcastHub:
        return self._hub

    @property
    def queue(self) -> BoardQueue:
        return self._queue

    async def clear_board(self) -> None:
        await self._queue.clear()
        await self.publish_queue()

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        await self._queue.start()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="poller")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self._queue.stop(clear=self._settings.board_clear_on_exit)

    def request_poll(self) -> None:
        """Ask the loop to come round immediately instead of waiting out its interval."""
        self._poll_now.set()

    # ------------------------------------------------------------------ cadence

    def poll_interval(self, scoreboard: Scoreboard | None) -> float:
        """The healthy poll interval for a slate, ignoring any failure backoff."""
        return self._cadence.healthy_interval(scoreboard)

    def note_failure(self) -> None:
        self._cadence.note_failure()

    def note_success(self) -> None:
        self._cadence.note_success()

    def next_interval(self) -> float:
        """How long to wait before the next poll, given the slate and any failures."""
        return self._cadence.next_interval(self.scoreboard)

    # ------------------------------------------------------------------ the loop

    async def _run(self) -> None:
        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # One bad response must not kill the ticker for the night.
                self.note_failure()
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.warning(
                    "poll failed (%d in a row), retrying in %.0fs: %s",
                    self._cadence.failures,
                    self.next_interval(),
                    self.last_error,
                )
                await self._hub.broadcast(
                    {"type": "error", "message": self.last_error}, remember=False
                )

            interval = self.next_interval()
            # A little jitter so a restart loop does not synchronise onto the API.
            interval += random.uniform(0, min(2.0, interval * 0.1))
            try:
                await asyncio.wait_for(self._poll_now.wait(), timeout=interval)
            except (asyncio.TimeoutError, TimeoutError):
                pass
            finally:
                self._poll_now.clear()

    async def poll_once(self) -> Scoreboard:
        scoreboard = await self._client.fetch_scoreboard()
        self.note_success()
        self.last_error = None
        self.scoreboard = scoreboard

        events = self.tracker.ingest(scoreboard)
        await self.dispatch(events)

        # Refresh what the board falls back to when idle. This swaps the page content
        # without resetting the rotation, so the cycle keeps advancing across polls.
        self._queue.set_summary(list(scoreboard.games))

        await self._hub.broadcast(
            {
                "type": "scoreboard",
                "date": scoreboard.current_date,
                "games": [game_to_dict(g) for g in scoreboard.games],
                "pollSeconds": self.poll_interval(scoreboard),
            }
        )
        await self.publish_queue()
        return scoreboard

    async def dispatch(self, events: list[Event]) -> None:
        """Push events to every sink. Audio is fired and forgotten so it cannot stall us."""
        for event in events:
            log.info("event: %s", event_to_dict(event)["text"])
            self._queue.submit(event)
            await self._hub.broadcast({"type": "event", **event_to_dict(event)}, remember=False)

            if self._horn is not None and isinstance(event, GoalEvent):
                self._horn.play(event.scoring_abbrev)

    # ------------------------------------------------------------------ UI plumbing

    async def on_board_message(self, message: BoardMessage) -> None:
        """Called by the queue for every message that actually reaches the board."""
        await self._hub.broadcast(
            {
                "type": "boardMessage",
                "payload": message.payload,
                "text": message.text,
                "kind": message.kind,
                "holdSeconds": round(message.hold_seconds, 2),
                "sentAt": message.sent_at,
            },
            remember=False,
        )
        await self.publish_queue()

    async def publish_queue(self) -> None:
        await self._hub.broadcast({"type": "queue", **self._queue.describe()})

    def snapshot(self) -> dict:
        return {
            "date": self.scoreboard.current_date if self.scoreboard else None,
            "games": [game_to_dict(g) for g in (self.scoreboard.games if self.scoreboard else [])],
            "queue": self._queue.describe(),
            "pollSeconds": self.poll_interval(self.scoreboard),
            "boardEnabled": self._settings.board_enabled,
            # The emulator must scroll at the board's rate, not its own guess, or the two
            # drift apart as soon as board_frame_ms is calibrated against real hardware.
            "boardFrameMs": self._settings.board_frame_ms,
            "boardOnline": self._queue.hardware_online,
            # The engine host's own speaker. The website plays horns independently.
            "hornEnabled": self._settings.horn_enabled,
            "hornMaxSeconds": self._settings.horn_max_seconds,
            "lastError": self.last_error,
        }

    # ------------------------------------------------------------------ test injection

    async def fake_goal(
        self, game_id: int | None = None, team_abbrev: str | None = None
    ) -> dict:
        """Synthesise a goal so the full chain can be exercised off-season.

        Uses a real game off the current slate, so the resulting board message is shaped
        exactly like a live alert. Goes through ``dispatch`` rather than straight to the
        queue: the point of the control is to exercise *every* sink, and submitting
        directly skipped both the horn and the UI event feed.
        """
        game = self._pick_game(game_id, team_abbrev)
        if game is None:
            if team_abbrev:
                raise LookupError(f"no game on the slate features {team_abbrev}")
            raise LookupError("no games on the slate to attach a fake goal to")

        scoring = team_abbrev or random.choice([game.home_team.abbrev, game.away_team.abbrev])
        is_home = scoring == game.home_team.abbrev
        home_score = game.home_team.score + (1 if is_home else 0)
        away_score = game.away_team.score + (0 if is_home else 1)

        goal = Goal(
            period=game.period or 1,
            timeInPeriod="12:34",
            name={"default": "T. Test"},
            lastName={"default": "Testerson"},
            teamAbbrev={"default": scoring},
            strength=random.choice(["ev", "ev", "pp", "sh"]),
            assists=[],
            awayScore=away_score,
            homeScore=home_score,
        )
        # Reflect the new score so the board message reads consistently.
        bumped = game.model_copy(deep=True)
        bumped.home_team.score = home_score
        bumped.away_team.score = away_score

        event = GoalEvent(game=bumped, goal=goal)
        await self.dispatch([event])
        return event_to_dict(event)

    def _pick_game(self, game_id: int | None, team_abbrev: str | None = None) -> Game | None:
        """Choose a game to hang a synthetic goal on.

        An explicit id wins. Otherwise, if a team was named, the game must be one that team
        is actually playing in -- scoring a Boston goal in a Colorado-Winnipeg game would
        produce a board message that could never occur.
        """
        games = self.scoreboard.games if self.scoreboard else []
        if not games:
            return None
        if game_id is not None:
            game = next((g for g in games if g.id == game_id), None)
            if game is None or team_abbrev is None:
                return game
            return game if game.team_for(team_abbrev.upper()) else None
        if team_abbrev:
            games = [g for g in games if g.team_for(team_abbrev.upper())]
            if not games:
                return None
        live = [g for g in games if g.is_live]
        return random.choice(live or games)
