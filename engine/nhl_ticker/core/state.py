"""Pure diffing of consecutive scoreboard snapshots into events.

No I/O and no sleeping -- everything here is synchronous and deterministic, which makes it
the main unit-test surface. The original mixed this logic into the same loop that made HTTP
calls and slept for 15 seconds at a time, which is why it dropped events.

Goal detection is by **position in the API's ordered ``goals`` array**, not by score delta.
The original compared scores and then went and re-scraped a boxscore to guess who had just
scored; if two goals landed inside one poll interval it saw a single +2 and reported one
goal. Counting array entries reports both, each with its real scorer attached.
"""

from __future__ import annotations

import logging

from ..nhl.models import Game, GameState, Scoreboard
from .events import Event, GameEndEvent, GameStartEvent, GoalEvent

log = logging.getLogger(__name__)


class ScoreboardTracker:
    """Remembers what it has already reported, so it can report only what is new."""

    def __init__(self) -> None:
        #: game id -> number of entries in that game's `goals` array we have emitted for
        self._goals_seen: dict[int, int] = {}
        #: game id -> the state we last saw it in
        self._states: dict[int, GameState] = {}

    @property
    def tracked_ids(self) -> set[int]:
        return set(self._states)

    def ingest(self, scoreboard: Scoreboard) -> list[Event]:
        """Fold a new snapshot in and return everything that changed since the last one.

        A game seen for the first time is recorded silently. That covers both a cold start
        mid-game -- the original would have fired every goal already scored that night at
        boot, which its ``hasFinishedBoot`` flag existed to suppress -- and the daily
        rollover onto a fresh slate.
        """
        events: list[Event] = []
        current_ids: set[int] = set()

        for game in scoreboard.games:
            current_ids.add(game.id)
            if game.id not in self._states:
                self._prime(game)
                continue
            events.extend(self._diff_game(game))

        for stale_id in self.tracked_ids - current_ids:
            log.debug("game %s dropped off the slate", stale_id)
            self._states.pop(stale_id, None)
            self._goals_seen.pop(stale_id, None)

        return events

    def _prime(self, game: Game) -> None:
        """Adopt a game's current state as the baseline without emitting anything."""
        self._states[game.id] = game.game_state
        self._goals_seen[game.id] = len(game.goals)
        log.debug(
            "priming %s %s@%s state=%s goals=%d",
            game.id,
            game.away_team.abbrev,
            game.home_team.abbrev,
            game.game_state,
            len(game.goals),
        )

    def _diff_game(self, game: Game) -> list[Event]:
        events: list[Event] = []
        previous_state = self._states[game.id]
        seen = self._goals_seen.get(game.id, 0)

        if not previous_state.has_started and game.has_started:
            events.append(GameStartEvent(game=game))

        # A shrinking array means the league took a goal off the board (disallowed on
        # review). Re-baseline quietly rather than emitting anything.
        if len(game.goals) < seen:
            log.info(
                "game %s goal count fell %d -> %d; re-baselining",
                game.id,
                seen,
                len(game.goals),
            )
            seen = len(game.goals)

        new_goals = game.goals[seen:]
        for goal in new_goals:
            events.append(GoalEvent(game=game, goal=goal, backfilled=len(new_goals) > 1))

        if not previous_state.is_over and game.is_over:
            events.append(GameEndEvent(game=game))

        self._states[game.id] = game.game_state
        self._goals_seen[game.id] = len(game.goals)
        return events
