"""The scoreboard must survive the NHL changing something.

`Scoreboard` used to validate as a single unit, so one unexpected value on one game failed
the entire parse. That is worse than a dropped poll: `poll_once` raises, the tracker never
advances, the board freezes on its last summary, and the retry backoff re-fetches the same
poisoned payload forever. A new `gameState` value -- an ordinary thing for the league to
add mid-season -- was enough to silently kill the ticker.

These tests pin the two defences: individual fields degrade rather than raise, and a game
that is unparseable anyway is dropped without taking the rest of the slate with it.
"""

from __future__ import annotations

import json

import pytest

from nhl_ticker.nhl.models import GameState, PeriodType, Scoreboard


def slate(*games: dict, date: str = "2026-10-08") -> dict:
    return {"currentDate": date, "games": list(games)}


def a_game(game_id: int = 1, **overrides) -> dict:
    game = {
        "id": game_id,
        "gameState": "LIVE",
        "awayTeam": {"id": 6, "abbrev": "BOS", "score": 1},
        "homeTeam": {"id": 15, "abbrev": "WSH", "score": 2},
        "goals": [],
    }
    game.update(overrides)
    return game


# ------------------------------------------------------------------ lenient fields


@pytest.mark.parametrize(
    "label, overrides",
    [
        ("null score", {"awayTeam": {"id": 6, "abbrev": "BOS", "score": None}}),
        ("null sog", {"awayTeam": {"id": 6, "abbrev": "BOS", "sog": None}}),
        ("null period", {"period": None}),
        ("missing abbrev", {"awayTeam": {"id": 6, "score": 1}}),
        ("unknown periodType", {"periodDescriptor": {"number": 5, "periodType": "OT2"}}),
        ("goal with null period", {"goals": [{"teamAbbrev": "BOS", "period": None}]}),
        ("assist without playerId", {"goals": [{"teamAbbrev": "BOS", "assists": [{"name": "X"}]}]}),
        ("null clock", {"clock": None}),
        ("unknown future field", {"someNewFieldTheLeagueAdded": {"x": 1}}),
    ],
)
def test_odd_but_plausible_fields_do_not_fail_the_parse(label, overrides):
    board = Scoreboard.model_validate(slate(a_game(**overrides)))
    assert len(board.games) == 1, label


@pytest.mark.parametrize("state", ["PPD", "MATCHUP", "SOMETHING_NEW", ""])
def test_unknown_game_states_degrade_instead_of_raising(state):
    """A new state must not be fatal, and must not be mistaken for live or finished."""
    board = Scoreboard.model_validate(slate(a_game(gameState=state)))
    game = board.games[0]
    assert game.game_state is GameState.UNKNOWN
    assert not game.is_live
    assert not game.is_over
    assert not game.has_started


def test_known_game_states_still_parse_exactly():
    for state in ("FUT", "PRE", "LIVE", "CRIT", "FINAL", "OFF"):
        board = Scoreboard.model_validate(slate(a_game(gameState=state)))
        assert board.games[0].game_state == state


def test_unknown_period_type_degrades_but_known_ones_do_not():
    board = Scoreboard.model_validate(
        slate(a_game(periodDescriptor={"number": 6, "periodType": "OT2"}))
    )
    assert board.games[0].period_descriptor.period_type is PeriodType.UNKNOWN

    for known in ("REG", "OT", "SO"):
        board = Scoreboard.model_validate(
            slate(a_game(periodDescriptor={"number": 1, "periodType": known}))
        )
        assert board.games[0].period_descriptor.period_type == known


# ------------------------------------------------------------------ per-game isolation


def test_one_unparseable_game_does_not_blank_the_slate():
    """The structural guarantee: whatever breaks next, the other games still show."""
    good = [a_game(game_id=i) for i in range(13)]
    broken = {"this": "is not a game at all"}

    board = Scoreboard.model_validate(slate(*good, broken))

    assert len(board.games) == 13
    assert {g.id for g in board.games} == set(range(13))


def test_a_game_missing_its_id_is_skipped_not_fatal():
    board = Scoreboard.model_validate(slate(a_game(game_id=1), {"gameState": "LIVE"}))
    assert [g.id for g in board.games] == [1]


def test_a_game_missing_a_team_is_skipped():
    board = Scoreboard.model_validate(
        slate(a_game(game_id=1), {"id": 2, "gameState": "LIVE", "awayTeam": {"id": 1}})
    )
    assert [g.id for g in board.games] == [1]


def test_an_entirely_broken_slate_yields_no_games_rather_than_raising():
    board = Scoreboard.model_validate(slate({"junk": 1}, {"more": 2}))
    assert board.games == []
    assert board.current_date == "2026-10-08"


def test_skipped_games_are_logged(caplog):
    """Silent data loss would be worse than the crash it replaces."""
    with caplog.at_level("WARNING"):
        Scoreboard.model_validate(slate(a_game(game_id=1), {"junk": True}))
    assert any("skip" in r.message.lower() or "game" in r.message.lower() for r in caplog.records)


# ------------------------------------------------------------------ real-world shapes


def test_both_localised_shapes_parse():
    """The score endpoint sends teamAbbrev as a bare string but lastName as an object.

    Real observed mixed encoding, so both forms are pinned here deliberately.
    """
    board = Scoreboard.model_validate(
        slate(
            a_game(
                goals=[
                    {
                        "teamAbbrev": "BOS",
                        "lastName": {"default": "Pastrnak"},
                        "name": {"default": "D. Pastrnak"},
                    }
                ]
            )
        )
    )
    goal = board.games[0].goals[0]
    assert goal.team_abbrev == "BOS"
    assert goal.scorer_last_name == "Pastrnak"


def test_recorded_fixtures_still_parse_fully(live_fixture, future_fixture):
    """Leniency must not quietly start dropping games that used to work."""
    assert len(live_fixture.games) == 4
    assert len(future_fixture.games) == 10


def test_a_real_payload_with_one_game_corrupted_keeps_the_rest():
    raw = json.loads(
        (__import__("pathlib").Path(__file__).parent / "fixtures" / "score_sample.json")
        .read_text(encoding="utf-8")
    )
    raw["games"][2] = {"totally": "broken"}
    board = Scoreboard.model_validate(raw)
    assert len(board.games) == 3
