"""Payload encoding and scroll timing, checked against the firmware's own behaviour."""

from __future__ import annotations

from conftest import make_game, make_goal, make_scoreboard

from nhl_ticker.board import timing
from nhl_ticker.board.protocol import (
    MARKER_LEN,
    MAX_VISIBLE_CHARS,
    Segment,
    encode_url,
    marker,
    payload_for,
    payloads_for,
    plain_text,
    render,
    summary_pages,
    truncate,
    visible_length,
)
from nhl_ticker.config import Settings, settings
from nhl_ticker.core.events import GameEndEvent, GameStartEvent, GoalEvent, SummaryTick
from nhl_ticker.core.league import team


def _goal_event(**kwargs):
    game = make_scoreboard(make_game(**kwargs)).games[0]
    return GoalEvent(game=game, goal=game.goals[-1])


# ------------------------------------------------------------------ marker encoding


def test_marker_matches_the_originals_format():
    """League.py emitted '~480000' and appended brightness '30'."""
    assert marker("480000", "30") == "~480000" + "30"
    assert len(marker("ffffff", "30")) == MARKER_LEN


def test_render_emits_a_marker_only_when_the_colour_changes():
    payload = render([Segment("AB", "ff0000"), Segment("CD", "ff0000"), Segment("EF", "00ff00")])
    assert payload.count("~") == 2
    assert payload == "~ff000030ABCD~00ff0030EF"


def test_render_skips_empty_segments():
    assert render([Segment("", "ff0000"), Segment("X", "00ff00")]) == "~00ff0030X"


def test_visible_length_ignores_markers():
    assert visible_length("~ff000030BOS") == 3
    assert visible_length("BOS") == 3
    assert visible_length("~ff000030BOS~00ff0030 2-1") == 7


def test_visible_length_handles_a_truncated_trailing_marker():
    """parseText consumes the marker whether or not it is complete."""
    assert visible_length("AB~ff00") == 2


# ------------------------------------------------------------------ buffer safety


def test_truncate_caps_visible_characters_not_payload_bytes():
    long_payload = render([Segment("x" * 400, "ff0000")])
    trimmed = truncate(long_payload)
    assert visible_length(trimmed) == MAX_VISIBLE_CHARS
    assert trimmed.startswith("~ff000030")


def test_truncate_preserves_colour_markers_it_passes():
    payload = render([Segment("a" * 100, "ff0000"), Segment("b" * 100, "00ff00")])
    trimmed = truncate(payload, limit=150)
    assert trimmed.count("~") == 2


def test_truncate_is_a_noop_below_the_cap():
    payload = render([Segment("short")])
    assert truncate(payload) == payload


def test_every_formatted_event_respects_the_firmware_buffer():
    """The sketch corrupts memory past 149 characters; nothing may exceed that."""
    games = [
        make_scoreboard(make_game(game_id=i, away="BOS", home="WSH", state="LIVE")).games[0]
        for i in range(16)
    ]
    payload = payload_for(SummaryTick(games=games))
    assert visible_length(payload) <= MAX_VISIBLE_CHARS


# ------------------------------------------------------------------ message shapes


def test_goal_payload_names_the_scorer_and_colours_both_teams():
    event = _goal_event(
        home_score=1, goals=[make_goal("WSH", "Ovechkin", 0, 1, strength="pp", assists=[
            {"playerId": 1, "name": {"default": "T. Wilson"}}
        ])]
    )
    payload = payload_for(event)

    assert plain_text(payload).strip() == "WSH 1-0 BOS  WSH Goal! Ovechkin PP (T. Wilson)"
    assert marker(team("WSH").color, settings.board_brightness) in payload
    assert marker(team("BOS").color, settings.board_brightness) in payload


def test_goal_payload_marks_shorthanded_and_empty_net():
    sh = payload_for(_goal_event(home_score=1, goals=[make_goal("WSH", "Eller", 0, 1, strength="sh")]))
    assert "Eller SH" in plain_text(sh)

    en = payload_for(
        _goal_event(home_score=1, goals=[make_goal("WSH", "Eller", 0, 1, modifier="empty-net")])
    )
    assert "Eller EN" in plain_text(en)


def test_goal_payload_omits_the_assist_clause_when_unassisted():
    payload = payload_for(_goal_event(home_score=1, goals=[make_goal("WSH", "Ovechkin", 0, 1)]))
    assert "(" not in plain_text(payload)


def test_game_end_payload_reports_overtime():
    game = make_scoreboard(
        make_game(state="FINAL", home_score=3, away_score=2, period=4, period_type="OT")
    ).games[0]
    assert plain_text(payload_for(GameEndEvent(game=game))).endswith("F/OT")


def test_game_end_payload_reports_shootout_and_regulation():
    so = make_scoreboard(make_game(state="FINAL", period=5, period_type="SO")).games[0]
    assert plain_text(payload_for(GameEndEvent(game=so))).endswith("F/SO")

    reg = make_scoreboard(make_game(state="FINAL", period=3)).games[0]
    assert plain_text(payload_for(GameEndEvent(game=reg))).endswith("F")


def test_game_start_payload():
    game = make_scoreboard(make_game(state="LIVE")).games[0]
    assert "WSH vs BOS underway" in plain_text(payload_for(GameStartEvent(game=game)))


def test_summary_shows_matchups_for_unstarted_games_and_scores_for_live_ones():
    games = make_scoreboard(
        make_game(game_id=1, state="FUT"),
        make_game(game_id=2, away="NYR", home="NYI", away_score=2, home_score=1, state="LIVE"),
    ).games
    text = plain_text(payload_for(SummaryTick(games=games)))

    assert "WSH vs BOS" in text
    assert "NYI 1-2 NYR" in text


def test_summary_of_a_real_recorded_slate(live_fixture):
    payload = payload_for(SummaryTick(games=live_fixture.games))
    assert plain_text(payload).strip() == (
        "WSH 3-2 BOS F/OT   NYI 1-6 NYR F   MIN 1-2 DAL F   COL 5-2 WPG F"
    )
    assert visible_length(payload) <= MAX_VISIBLE_CHARS


# ------------------------------------------------------------------ url encoding


def test_encode_url_escapes_spaces_but_keeps_the_tilde():
    url = encode_url("~ff000030BOS 2-1")
    assert url.startswith(f"{settings.board_url_base}/text/")
    assert "~ff000030BOS%202-1" in url


# ------------------------------------------------------------------ timing


def test_scroll_frame_count_matches_the_sketch_formula():
    """cmdIteration wraps at (text.length() + 3) * COLS."""
    assert timing.scroll_frames(22) == (22 + 3) * settings.board_cols
    assert timing.scroll_frames(80) == (80 + 3) * settings.board_cols


def test_messages_within_the_window_do_not_scroll():
    assert timing.scrolls(settings.board_chars) is False
    assert timing.scrolls(settings.board_chars + 1) is True


def test_long_message_duration_matches_the_originals_fifteen_second_sleep():
    """An 80-char goal alert takes ~16 s on the board, which is why it slept 15."""
    assert 15.0 < timing.scroll_seconds(80) < 18.0


def test_static_messages_get_the_minimum_dwell():
    """The firmware frees itself after delay(2000), so we hold the board ourselves."""
    dwell = timing.display_seconds(10)
    assert dwell == max(settings.board_min_dwell_seconds, timing.FIRMWARE_STATIC_HOLD_SECONDS)
    assert dwell >= timing.FIRMWARE_STATIC_HOLD_SECONDS


def test_display_time_never_undercuts_the_firmware_hold():
    for length in range(1, 200):
        assert timing.display_seconds(length) >= timing.FIRMWARE_STATIC_HOLD_SECONDS


# ------------------------------------------------------------------ summary pagination


def test_a_busy_slate_is_split_across_pages_instead_of_being_truncated():
    """16 games do not fit the 149-char buffer; the tail must not simply vanish.

    The original paginated too, splitting printableGameList at 132 characters.
    """
    games = make_scoreboard(
        *[make_game(game_id=i, state="FUT") for i in range(16)]
    ).games
    pages = summary_pages(games)

    assert len(pages) > 1
    assert all(visible_length(p) <= MAX_VISIBLE_CHARS for p in pages)
    # Every game appears somewhere across the pages.
    assert sum(plain_text(p).count("WSH vs BOS") for p in pages) == 16


def test_pages_respect_the_configured_width():
    games = make_scoreboard(*[make_game(game_id=i, state="FUT") for i in range(12)]).games
    narrow = Settings(board_summary_max_chars=40)

    pages = summary_pages(games, narrow)
    # Only a page forced to hold a single oversized game may exceed the target.
    assert all(visible_length(p) <= 40 or plain_text(p).count("vs") == 1 for p in pages)


def test_a_short_slate_still_fits_on_one_page():
    games = make_scoreboard(
        make_game(game_id=1, state="FINAL"),
        make_game(game_id=2, away="NYR", home="NYI", state="FINAL"),
    ).games
    assert len(summary_pages(games)) == 1


def test_pages_never_split_a_single_game_across_two(live_fixture):
    """Every page must be a whole number of matchups, however the slate divides."""
    for page in summary_pages(live_fixture.games):
        text = plain_text(page).strip()
        assert text
        # A page that ended mid-matchup would leave a dangling separator.
        assert not text.endswith("-")


def test_pagination_bounds_how_long_a_goal_waits_behind_a_summary():
    """The board cannot be interrupted mid-scroll, so page length is the worst-case wait."""
    games = make_scoreboard(*[make_game(game_id=i, state="FUT") for i in range(16)]).games
    worst = max(timing.display_seconds(visible_length(p)) for p in summary_pages(games))
    assert worst < 14.0


def test_an_empty_slate_produces_no_pages():
    assert summary_pages([]) == []


def test_payloads_for_expands_summaries_but_not_goals():
    games = make_scoreboard(*[make_game(game_id=i, state="FUT") for i in range(16)]).games
    assert len(payloads_for(SummaryTick(games=games))) > 1

    event = _goal_event(home_score=1, goals=[make_goal("WSH", "Ovechkin", 0, 1)])
    assert len(payloads_for(event)) == 1
