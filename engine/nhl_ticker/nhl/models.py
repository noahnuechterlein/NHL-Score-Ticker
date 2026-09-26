"""Pydantic models for the subset of api-web.nhle.com/v1/score that the ticker uses.

The API wraps most human-readable strings in a localisation object (``{"default": "Bruins",
"fr": "Bruins"}``); ``Localized`` flattens those to the default string at parse time so the
rest of the engine never has to think about it.

Fields that only exist once a game is under way (score, clock, period, goals) are optional:
a ``FUT`` game carries none of them.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, Field


def _flatten_localized(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("default", "")
    return value


Localized = Annotated[str, BeforeValidator(_flatten_localized)]


class GameState(StrEnum):
    """Lifecycle states the API reports for a game.

    ``CRIT`` is "critical" -- a close game late in the third or in overtime. It behaves like
    ``LIVE`` for our purposes. ``OFF`` means final and fully settled; ``FINAL`` means the
    game just ended and stats may still be moving.
    """

    FUT = "FUT"
    PRE = "PRE"
    LIVE = "LIVE"
    CRIT = "CRIT"
    FINAL = "FINAL"
    OFF = "OFF"

    @property
    def is_live(self) -> bool:
        return self in (GameState.LIVE, GameState.CRIT)

    @property
    def is_over(self) -> bool:
        return self in (GameState.FINAL, GameState.OFF)

    @property
    def has_started(self) -> bool:
        return self.is_live or self.is_over


class PeriodType(StrEnum):
    REG = "REG"
    OT = "OT"
    SO = "SO"


class TeamSide(BaseModel):
    id: int
    name: Localized = ""
    abbrev: str
    score: int = 0
    sog: int = 0
    logo: str | None = None


class Assist(BaseModel):
    player_id: int = Field(alias="playerId")
    name: Localized = ""


class Goal(BaseModel):
    """A single scoring play.

    The API returns these in chronological order, which is what lets the diff engine detect
    new goals by index rather than by guessing from a score delta.
    """

    period: int = 1
    time_in_period: str = Field(default="", alias="timeInPeriod")
    player_id: int | None = Field(default=None, alias="playerId")
    name: Localized = ""
    first_name: Localized = Field(default="", alias="firstName")
    last_name: Localized = Field(default="", alias="lastName")
    team_abbrev: Localized = Field(default="", alias="teamAbbrev")
    # "ev" | "pp" | "sh" | "en" -- absent on some older//preseason feeds.
    strength: str = "ev"
    goal_modifier: str = Field(default="none", alias="goalModifier")
    assists: list[Assist] = Field(default_factory=list)
    away_score: int = Field(default=0, alias="awayScore")
    home_score: int = Field(default=0, alias="homeScore")
    highlight_clip_url: str | None = Field(default=None, alias="highlightClipSharingUrl")

    model_config = {"populate_by_name": True}

    @property
    def is_empty_net(self) -> bool:
        return self.goal_modifier == "empty-net" or self.strength == "en"

    @property
    def scorer_last_name(self) -> str:
        return self.last_name or self.name


class Clock(BaseModel):
    time_remaining: str = Field(default="", alias="timeRemaining")
    seconds_remaining: int = Field(default=0, alias="secondsRemaining")
    running: bool = False
    in_intermission: bool = Field(default=False, alias="inIntermission")

    model_config = {"populate_by_name": True}


class PeriodDescriptor(BaseModel):
    number: int = 1
    period_type: PeriodType = Field(default=PeriodType.REG, alias="periodType")
    max_regulation_periods: int = Field(default=3, alias="maxRegulationPeriods")

    model_config = {"populate_by_name": True}


class GameOutcome(BaseModel):
    last_period_type: PeriodType = Field(default=PeriodType.REG, alias="lastPeriodType")
    ot_periods: int = Field(default=0, alias="otPeriods")

    model_config = {"populate_by_name": True}


class Game(BaseModel):
    id: int
    game_state: GameState = Field(alias="gameState")
    game_date: str = Field(default="", alias="gameDate")
    start_time_utc: str = Field(default="", alias="startTimeUTC")
    away_team: TeamSide = Field(alias="awayTeam")
    home_team: TeamSide = Field(alias="homeTeam")
    clock: Clock | None = None
    period: int | None = None
    period_descriptor: PeriodDescriptor | None = Field(default=None, alias="periodDescriptor")
    game_outcome: GameOutcome | None = Field(default=None, alias="gameOutcome")
    goals: list[Goal] = Field(default_factory=list)

    model_config = {"populate_by_name": True}

    @property
    def is_live(self) -> bool:
        return self.game_state.is_live

    @property
    def is_over(self) -> bool:
        return self.game_state.is_over

    @property
    def has_started(self) -> bool:
        return self.game_state.has_started

    def team_for(self, abbrev: str) -> TeamSide | None:
        if self.away_team.abbrev == abbrev:
            return self.away_team
        if self.home_team.abbrev == abbrev:
            return self.home_team
        return None


class Scoreboard(BaseModel):
    current_date: str = Field(default="", alias="currentDate")
    games: list[Game] = Field(default_factory=list)

    model_config = {"populate_by_name": True}

    def by_id(self) -> dict[int, Game]:
        return {g.id: g for g in self.games}
