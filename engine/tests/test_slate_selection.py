"""Which games the board's slate summary should carry.

Once the night is under way, a wall of "7:00 / 7:00 / 8:00" listings is noise -- what you
want on the board is the games actually happening. So the rule is:

* once anything has started, show only started and finished games
* show upcoming games only while nothing has started yet

Start times are still useful, just not once there is real hockey to report.
"""

from __future__ import annotations

from conftest import MATCHUPS, make_game, make_scoreboard

from nhl_ticker.board.messages import slate_for_summary, summary_pages
from nhl_ticker.board.protocol import plain_text


def games(*specs: tuple[int, str]) -> list:
    """Build a slate from (id, state) pairs, each game with a distinct matchup."""
    return make_scoreboard(
        *[
            make_game(game_id=i, away=MATCHUPS[i][0], home=MATCHUPS[i][1], state=state)
            for i, state in specs
        ]
    ).games


def abbrevs(selected) -> set[str]:
    return {g.home_team.abbrev for g in selected}


# ------------------------------------------------------------------ the rule


def test_before_anything_starts_the_whole_slate_is_shown():
    slate = games((0, "FUT"), (1, "FUT"), (2, "PRE"))
    assert len(slate_for_summary(slate)) == 3


def test_once_a_game_is_live_the_upcoming_ones_drop_off():
    slate = games((0, "LIVE"), (1, "FUT"), (2, "FUT"))
    assert abbrevs(slate_for_summary(slate)) == {"WSH"}


def test_finished_games_also_count_as_started():
    """An afternoon final is worth showing even if nothing else has begun."""
    slate = games((0, "FINAL"), (1, "FUT"), (2, "FUT"))
    assert abbrevs(slate_for_summary(slate)) == {"WSH"}


def test_live_and_finished_games_are_shown_together():
    slate = games((0, "FINAL"), (1, "LIVE"), (2, "OFF"), (3, "FUT"))
    assert abbrevs(slate_for_summary(slate)) == {"WSH", "NYI", "MIN"}


def test_pregame_counts_as_not_started():
    """PRE is warmups, not hockey, so it drops off once a real game is running."""
    slate = games((0, "LIVE"), (1, "PRE"))
    assert abbrevs(slate_for_summary(slate)) == {"WSH"}


def test_an_all_finished_slate_is_shown_in_full():
    slate = games((0, "FINAL"), (1, "OFF"), (2, "FINAL"))
    assert len(slate_for_summary(slate)) == 3


def test_an_empty_slate_stays_empty():
    assert slate_for_summary([]) == []


def test_a_game_in_an_unknown_state_is_treated_as_not_started():
    """An UNKNOWN state must not be mistaken for live and suppress the real slate."""
    slate = games((0, "FUT"), (1, "PPD"))
    assert len(slate_for_summary(slate)) == 2

    started = games((0, "LIVE"), (1, "PPD"))
    assert abbrevs(slate_for_summary(started)) == {"WSH"}


def test_ordering_is_preserved():
    slate = games((0, "LIVE"), (1, "FUT"), (2, "FINAL"), (3, "LIVE"))
    assert [g.id for g in slate_for_summary(slate)] == [0, 2, 3]


# ------------------------------------------------------------------ through the board


def test_the_board_shows_start_times_only_before_puck_drop():
    pages = summary_pages(games((0, "FUT"), (1, "FUT")))
    text = " ".join(plain_text(p) for p in pages)
    assert " vs " in text


def test_the_board_drops_start_times_once_play_begins():
    slate = games((0, "LIVE"), (1, "FUT"), (2, "FUT"), (3, "FUT"))
    text = " ".join(plain_text(p) for p in summary_pages(slate))

    assert " vs " not in text, "upcoming matchups should be gone"
    assert "WSH" in text


def test_dropping_upcoming_games_shrinks_the_page_count():
    """The practical payoff: one live game mid-afternoon is one short page, not five."""
    busy = games(*[(i, "FUT") for i in range(8)])
    assert len(summary_pages(busy)) > 1

    one_live = games((0, "LIVE"), *[(i, "FUT") for i in range(1, 8)])
    assert len(summary_pages(one_live)) == 1


def test_the_board_shows_nothing_rather_than_a_blank_page_when_filtered_to_empty():
    """Filtering can never empty the slate -- if nothing started, everything shows."""
    assert summary_pages(games((0, "FUT"))) != []
