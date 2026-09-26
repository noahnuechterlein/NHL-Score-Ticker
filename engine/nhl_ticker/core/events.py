"""Events the diff engine emits, and the priority the board queue ranks them by."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

from ..nhl.models import Game, Goal


class Priority(IntEnum):
    """Lower sorts first. A goal always pre-empts a pending summary."""

    GOAL = 0
    GAME_END = 1
    GAME_START = 2
    SUMMARY = 3


@dataclass(frozen=True, slots=True)
class GoalEvent:
    game: Game
    goal: Goal
    #: True when this goal was reconstructed on a catch-up tick rather than seen live,
    #: i.e. several goals landed between polls. The board still shows them all.
    backfilled: bool = False

    priority = Priority.GOAL

    @property
    def scoring_abbrev(self) -> str:
        return self.goal.team_abbrev

    @property
    def conceding_abbrev(self) -> str:
        home, away = self.game.home_team.abbrev, self.game.away_team.abbrev
        return away if self.scoring_abbrev == home else home


@dataclass(frozen=True, slots=True)
class GameStartEvent:
    game: Game

    priority = Priority.GAME_START


@dataclass(frozen=True, slots=True)
class GameEndEvent:
    game: Game

    priority = Priority.GAME_END

    @property
    def winner_abbrev(self) -> str:
        home, away = self.game.home_team, self.game.away_team
        return home.abbrev if home.score >= away.score else away.abbrev


@dataclass(frozen=True, slots=True)
class SummaryTick:
    """The whole slate, shown when the board has nothing more urgent to say."""

    games: list[Game] = field(default_factory=list)

    priority = Priority.SUMMARY


Event = GoalEvent | GameStartEvent | GameEndEvent | SummaryTick
