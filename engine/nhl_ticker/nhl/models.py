"""Pydantic models for the subset of api-web.nhle.com/v1/score that the ticker uses.

The API wraps most human-readable strings in a localisation object (``{"default": "Bruins",
"fr": "Bruins"}``); ``Localized`` flattens those to the default string at parse time so the
rest of the engine never has to think about it.

Fields that only exist once a game is under way (score, clock, period, goals) are optional:
a ``FUT`` game carries none of them.

Only what the ticker actually reads is modelled. Pydantic ignores keys it has no field for,
and the recorded fixtures in ``tests/fixtures`` document the full payload better than
half-modelled fields would -- so there is nothing to gain from carrying the rest.

Everything here is deliberately lenient. The scoreboard used to validate as a single unit,
so one unexpected value on one game failed the whole parse -- and because the poll loop
then retried the same payload forever, a single new enum value from the league would
silently freeze the board for the rest of the season. Unknown enum members degrade,
nullable numbers coerce, and ``Scoreboard`` drops individual games it cannot read rather
than losing the slate.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, Field, ValidationError, model_validator

log = logging.getLogger(__name__)


def _flatten_localized(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("default", "")
    return value


Localized = Annotated[str, BeforeValidator(_flatten_localized)]


def _default_if_none(value: Any) -> Any:
    """Let a null fall through to the field default.

    The API omits score and sog before puck drop, but has been known to send them as null
    instead, which a plain ``int`` field rejects.
    """
    return 0 if value is None else value


NullableInt = Annotated[int, BeforeValidator(_default_if_none)]


class GameState(StrEnum):
    """Lifecycle states the API reports for a game.

    ``CRIT`` is "critical" -- a close game late in the third or in overtime. It behaves like
    ``LIVE`` for our purposes. ``OFF`` means final and fully settled; ``FINAL`` means the
    game just ended and stats may still be moving.

    ``UNKNOWN`` is ours, not the league's: anything we do not recognise lands here and is
    treated as neither live nor finished, so a new state cannot be mistaken for either.
    """

    FUT = "FUT"
    PRE = "PRE"
    LIVE = "LIVE"
    CRIT = "CRIT"
    FINAL = "FINAL"
    OFF = "OFF"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def _missing_(cls, value: object) -> "GameState":
        log.warning("unrecognised gameState %r; treating as UNKNOWN", value)
        return cls.UNKNOWN

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
    #: Ours, for anything the league adds later (a second overtime, say).
    UNKNOWN = "UNKNOWN"

    @classmethod
    def _missing_(cls, value: object) -> "PeriodType":
        log.warning("unrecognised periodType %r; treating as UNKNOWN", value)
        return cls.UNKNOWN


class TeamSide(BaseModel):
    id: int = 0
    name: Localized = ""
    #: Falls back to the neutral default in the league table, which renders white.
    abbrev: str = "DEF"
    score: NullableInt = 0
    sog: NullableInt = 0


class Assist(BaseModel):
    name: Localized = ""


class Goal(BaseModel):
    """A single scoring play.

    The API returns these in chronological order, which is what lets the diff engine detect
    new goals by index rather than by guessing from a score delta.
    """

    period: NullableInt = 1
    time_in_period: str = Field(default="", alias="timeInPeriod")
    player_id: int | None = Field(default=None, alias="playerId")
    name: Localized = ""
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
    in_intermission: bool = Field(default=False, alias="inIntermission")

    model_config = {"populate_by_name": True}


class PeriodDescriptor(BaseModel):
    period_type: PeriodType = Field(default=PeriodType.REG, alias="periodType")

    model_config = {"populate_by_name": True}


class GameOutcome(BaseModel):
    last_period_type: PeriodType = Field(default=PeriodType.REG, alias="lastPeriodType")

    model_config = {"populate_by_name": True}


class Game(BaseModel):
    #: Required: without an id we cannot track the game across polls, so such a record is
    #: dropped by Scoreboard rather than given a meaningless placeholder.
    id: int
    game_state: GameState = Field(default=GameState.UNKNOWN, alias="gameState")
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

    @model_validator(mode="before")
    @classmethod
    def _drop_unreadable_games(cls, data: Any) -> Any:
        """Validate games one at a time so a bad one cannot take the slate with it.

        This is the defence that holds whatever the league changes next: the per-field
        leniency above covers what we can anticipate, this covers what we cannot. A
        dropped game is logged, because silent data loss would be worse than the crash it
        replaces.
        """
        if not isinstance(data, dict):
            return data
        raw_games = data.get("games")
        if not isinstance(raw_games, list):
            return data

        kept: list[Any] = []
        for index, raw in enumerate(raw_games):
            if isinstance(raw, Game):
                kept.append(raw)
                continue
            try:
                kept.append(Game.model_validate(raw))
            except ValidationError as exc:
                identifier = raw.get("id") if isinstance(raw, dict) else "?"
                log.warning(
                    "skipping unreadable game %s at index %d: %s",
                    identifier,
                    index,
                    exc.errors()[0].get("msg", exc) if exc.errors() else exc,
                )

        return {**data, "games": kept}
