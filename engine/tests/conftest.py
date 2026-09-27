"""Shared builders and fakes for the test suite.

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
        # Mirrors the live endpoint's mixed encoding: name/lastName come back as
        # localisation objects but teamAbbrev is a bare string. Both shapes are pinned in
        # tests/test_model_resilience.py.
        "name": {"default": scorer},
        "lastName": {"default": scorer},
        "teamAbbrev": team,
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


#: Distinct matchups for multi-game slates.
#:
#: Worth spelling out why: reusing one matchup makes every summary page render identical
#: text, which silently hides page-rotation and slate-selection bugs. It did exactly that
#: once -- a rotation test passed against a board looping page one forever, because all the
#: pages looked the same.
MATCHUPS: list[tuple[str, str]] = [
    ("BOS", "WSH"), ("NYR", "NYI"), ("DAL", "MIN"), ("WPG", "COL"),
    ("TOR", "MTL"), ("EDM", "CGY"), ("VGK", "SJS"), ("PHI", "PIT"),
    ("CHI", "STL"), ("DET", "CBJ"), ("FLA", "TBL"), ("VAN", "SEA"),
]


class FakeClient:
    """Stands in for NHLClient, serving a scripted sequence of scoreboards.

    Walks the list one snapshot per poll and then repeats the last one forever, which
    covers both "play out this sequence of events" and "hold this slate steady".
    """

    def __init__(self, *scoreboards: Scoreboard):
        assert scoreboards, "a FakeClient with nothing to serve is a silent no-op"
        self.scoreboards = list(scoreboards)
        self.calls = 0

    async def fetch_scoreboard(self, date: str | None = None) -> Scoreboard:
        board = self.scoreboards[min(self.calls, len(self.scoreboards) - 1)]
        self.calls += 1
        return board


class RecordingSocket:
    """Stands in for a connected UI client, keeping everything it was sent."""

    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, message: dict) -> None:
        self.messages.append(message)

    def of_type(self, kind: str) -> list[dict]:
        return [m for m in self.messages if m.get("type") == kind]
