"""Builders for synthetic scoreboards.

Hand-built snapshots keep the diff tests readable and let us construct sequences the live
API would take a whole season to produce (two goals inside one poll, a disallowed goal, a
game vanishing off the slate).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nhl_ticker.nhl.models import Scoreboard

FIXTURES = Path(__file__).parent / "fixtures"


def make_goal(team: str, scorer: str, away_score: int, home_score: int, **kwargs) -> dict:
    return {
        "period": kwargs.get("period", 1),
        "timeInPeriod": kwargs.get("time", "10:00"),
        "playerId": kwargs.get("player_id", 8400000),
        "name": {"default": scorer},
        "lastName": {"default": scorer},
        "teamAbbrev": {"default": team},
        "strength": kwargs.get("strength", "ev"),
        "goalModifier": kwargs.get("modifier", "none"),
        "assists": kwargs.get("assists", []),
        "awayScore": away_score,
        "homeScore": home_score,
    }


def make_game(
    game_id: int = 1,
    away: str = "BOS",
    home: str = "WSH",
    away_score: int = 0,
    home_score: int = 0,
    state: str = "LIVE",
    goals: list[dict] | None = None,
    period: int = 1,
    period_type: str = "REG",
) -> dict:
    return {
        "id": game_id,
        "gameState": state,
        "gameDate": "2026-10-08",
        "startTimeUTC": "2026-10-08T23:00:00Z",
        "awayTeam": {"id": 6, "name": {"default": away}, "abbrev": away, "score": away_score},
        "homeTeam": {"id": 15, "name": {"default": home}, "abbrev": home, "score": home_score},
        "period": period,
        "periodDescriptor": {"number": period, "periodType": period_type},
        "goals": goals or [],
    }


def make_scoreboard(*games: dict, date: str = "2026-10-08") -> Scoreboard:
    return Scoreboard.model_validate({"currentDate": date, "games": list(games)})


@pytest.fixture
def live_fixture() -> Scoreboard:
    """A real recorded response: four completed games."""
    raw = json.loads((FIXTURES / "score_sample.json").read_text(encoding="utf-8"))
    return Scoreboard.model_validate(raw)


@pytest.fixture
def future_fixture() -> Scoreboard:
    """A real recorded response: ten scheduled games, none started."""
    raw = json.loads((FIXTURES / "score_future.json").read_text(encoding="utf-8"))
    return Scoreboard.model_validate(raw)
