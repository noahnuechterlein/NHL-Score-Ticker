"""Diff-engine behaviour, including the failure modes of the 2016 original."""

from __future__ import annotations

from conftest import make_game, make_goal, make_scoreboard

from nhl_ticker.core.events import GameEndEvent, GameStartEvent, GoalEvent
from nhl_ticker.core.state import ScoreboardTracker


def test_cold_start_is_silent_even_mid_game():
    """Booting into a game already in progress must not replay goals already scored.

    The original fired every alert it found at boot; hasFinishedBoot existed to mask it.
    """
    tracker = ScoreboardTracker()
    goals = [make_goal("BOS", "Pastrnak", 1, 0), make_goal("WSH", "Ovechkin", 1, 1)]
    events = tracker.ingest(make_scoreboard(make_game(away_score=1, home_score=1, goals=goals)))
    assert events == []


def test_cold_start_on_finished_slate_is_silent(live_fixture):
    tracker = ScoreboardTracker()
    assert tracker.ingest(live_fixture) == []


def test_idempotent_when_nothing_changes(live_fixture):
    tracker = ScoreboardTracker()
    tracker.ingest(live_fixture)
    assert tracker.ingest(live_fixture) == []


def test_single_goal_emits_one_event_with_the_real_scorer():
    tracker = ScoreboardTracker()
    tracker.ingest(make_scoreboard(make_game()))

    events = tracker.ingest(
        make_scoreboard(
            make_game(home_score=1, goals=[make_goal("WSH", "Ovechkin", 0, 1, strength="pp")])
        )
    )

    assert len(events) == 1
    goal_event = events[0]
    assert isinstance(goal_event, GoalEvent)
    assert goal_event.scoring_abbrev == "WSH"
    assert goal_event.conceding_abbrev == "BOS"
    assert goal_event.goal.scorer_last_name == "Ovechkin"
    assert goal_event.goal.strength == "pp"
    assert goal_event.backfilled is False


def test_two_goals_in_one_interval_both_emit():
    """The case the original silently collapsed into a single alert.

    It diffed the score, saw +2, and reported one goal scraped from a boxscore table.
    """
    tracker = ScoreboardTracker()
    tracker.ingest(make_scoreboard(make_game()))

    events = tracker.ingest(
        make_scoreboard(
            make_game(
                away_score=1,
                home_score=1,
                goals=[
                    make_goal("BOS", "Pastrnak", 1, 0),
                    make_goal("WSH", "Ovechkin", 1, 1),
                ],
            )
        )
    )

    assert [e.goal.scorer_last_name for e in events] == ["Pastrnak", "Ovechkin"]
    assert [e.scoring_abbrev for e in events] == ["BOS", "WSH"]
    assert all(e.backfilled for e in events)


def test_game_start_emits_once():
    tracker = ScoreboardTracker()
    tracker.ingest(make_scoreboard(make_game(state="FUT")))

    events = tracker.ingest(make_scoreboard(make_game(state="LIVE")))
    assert len(events) == 1 and isinstance(events[0], GameStartEvent)

    assert tracker.ingest(make_scoreboard(make_game(state="LIVE"))) == []


def test_pre_game_state_does_not_count_as_started():
    tracker = ScoreboardTracker()
    tracker.ingest(make_scoreboard(make_game(state="FUT")))
    assert tracker.ingest(make_scoreboard(make_game(state="PRE"))) == []


def test_game_end_in_regulation():
    tracker = ScoreboardTracker()
    tracker.ingest(make_scoreboard(make_game(state="LIVE", home_score=3, away_score=2)))

    events = tracker.ingest(make_scoreboard(make_game(state="FINAL", home_score=3, away_score=2)))
    assert len(events) == 1
    assert isinstance(events[0], GameEndEvent)
    assert events[0].winner_abbrev == "WSH"


def test_game_end_in_overtime_reports_the_winning_goal_then_the_end():
    tracker = ScoreboardTracker()
    tracker.ingest(make_scoreboard(make_game(state="LIVE", home_score=2, away_score=2, period=3)))

    events = tracker.ingest(
        make_scoreboard(
            make_game(
                state="FINAL",
                home_score=3,
                away_score=2,
                period=4,
                period_type="OT",
                goals=[make_goal("WSH", "Strome", 2, 3, period=4)],
            )
        )
    )

    assert [type(e) for e in events] == [GoalEvent, GameEndEvent]
    assert events[0].goal.period == 4


def test_shootout_end_is_reported_once():
    tracker = ScoreboardTracker()
    tracker.ingest(make_scoreboard(make_game(state="LIVE", period=3)))

    events = tracker.ingest(
        make_scoreboard(make_game(state="FINAL", period=5, period_type="SO", home_score=1))
    )
    assert any(isinstance(e, GameEndEvent) for e in events)

    settled = make_scoreboard(make_game(state="OFF", period=5, period_type="SO", home_score=1))
    assert tracker.ingest(settled) == []


def test_final_to_off_transition_does_not_re_emit():
    tracker = ScoreboardTracker()
    tracker.ingest(make_scoreboard(make_game(state="LIVE")))
    tracker.ingest(make_scoreboard(make_game(state="FINAL")))
    assert tracker.ingest(make_scoreboard(make_game(state="OFF"))) == []


def test_disallowed_goal_rebaselines_without_emitting():
    """A goal wiped out on review shrinks the array; that must not fire anything."""
    tracker = ScoreboardTracker()
    tracker.ingest(make_scoreboard(make_game()))
    tracker.ingest(
        make_scoreboard(make_game(home_score=1, goals=[make_goal("WSH", "Ovechkin", 0, 1)]))
    )

    assert tracker.ingest(make_scoreboard(make_game(home_score=0, goals=[]))) == []

    # The next real goal must still report normally.
    events = tracker.ingest(
        make_scoreboard(make_game(home_score=1, goals=[make_goal("WSH", "Wilson", 0, 1)]))
    )
    assert len(events) == 1
    assert events[0].goal.scorer_last_name == "Wilson"


def test_game_leaving_the_slate_is_forgotten():
    tracker = ScoreboardTracker()
    tracker.ingest(make_scoreboard(make_game(game_id=1), make_game(game_id=2, away="NYR")))
    assert tracker.tracked_ids == {1, 2}

    tracker.ingest(make_scoreboard(make_game(game_id=1)))
    assert tracker.tracked_ids == {1}


def test_multiple_games_diff_independently():
    tracker = ScoreboardTracker()
    tracker.ingest(
        make_scoreboard(make_game(game_id=1), make_game(game_id=2, away="NYR", home="NYI"))
    )

    events = tracker.ingest(
        make_scoreboard(
            make_game(game_id=1, home_score=1, goals=[make_goal("WSH", "Ovechkin", 0, 1)]),
            make_game(
                game_id=2,
                away="NYR",
                home="NYI",
                away_score=1,
                goals=[make_goal("NYR", "Panarin", 1, 0)],
            ),
        )
    )
    assert {e.scoring_abbrev for e in events} == {"WSH", "NYR"}


def test_whole_slate_starting_from_scheduled(future_fixture):
    """Priming on a real all-FUT slate tracks every game and stays quiet."""
    tracker = ScoreboardTracker()
    assert tracker.ingest(future_fixture) == []
    assert len(tracker.tracked_ids) == len(future_fixture.games)
