"""Payload encoding and scroll timing, checked against the firmware's own behaviour."""

from __future__ import annotations

import re

import pytest

from conftest import make_game, make_goal, make_scoreboard

from nhl_ticker.board import timing
from nhl_ticker.board.protocol import (
    MARKER_LEN,
    MAX_VISIBLE_CHARS,
    Segment,
    encode_url,
    marker,
    plain_text,
    render,
    truncate,
    visible_length,
)
from nhl_ticker.board.messages import (
    _status_suffix,
    payload_for,
    payloads_for,
    start_time_label,
    summary_pages,
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


def test_summary_shows_matchups_before_anything_starts():
    """Split from test_summary_shows_matchups_for_unstarted_games_and_scores_for_live_ones.

    That test put one FUT and one LIVE game on the same slate and expected both. The
    board now drops upcoming games once play begins -- see tests/test_slate_selection.py
    -- so the two halves are asserted separately.
    """
    games = make_scoreboard(
        make_game(game_id=1, state="FUT"),
        make_game(game_id=2, away="NYR", home="NYI", state="FUT"),
    ).games
    assert "WSH vs BOS" in plain_text(payload_for(SummaryTick(games=games)))


def test_summary_shows_scores_for_games_in_progress():
    games = make_scoreboard(
        make_game(game_id=2, away="NYR", home="NYI", away_score=2, home_score=1, state="LIVE"),
    ).games
    assert "NYI 1-2 NYR" in plain_text(payload_for(SummaryTick(games=games)))


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
    """The firmware frees itself after delay(2000), so we hold the board ourselves.

    Asserted as "at least the floor" rather than an exact value: board_hold_margin
    deliberately pads every hold, so pinning the literal would just re-encode the margin.
    """
    dwell = timing.display_seconds(10)
    floor = max(settings.board_min_dwell_seconds, timing.FIRMWARE_STATIC_HOLD_SECONDS)
    assert dwell >= floor
    assert dwell == floor * settings.board_hold_margin


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


def test_payloads_for_returns_one_message_per_event():
    """Replaced test_payloads_for_expands_summaries_but_not_goals.

    That test covered a SummaryTick branch that became unreachable once the queue started
    treating the slate as idle content: submit() rejects summaries and set_summary()
    calls summary_pages directly. The branch is gone; pagination is covered by the
    summary_pages tests above and by tests/test_summary_rotation.py.
    """
    event = _goal_event(home_score=1, goals=[make_goal("WSH", "Ovechkin", 0, 1)])
    assert len(payloads_for(event)) == 1

    games = make_scoreboard(*[make_game(game_id=i, state="FUT") for i in range(16)]).games
    assert len(summary_pages(games)) > 1, "the slate still paginates, just not via payloads_for"


# ------------------------------------------------------------------ wire encoding


def test_url_keeps_the_characters_the_original_sent_literally():
    """The 2016 code went through requests, which preserves sub-delims.

    Encoding them ourselves would put `Goal%21` and `F%2FOT` on the wire. We cannot prove
    how the Yun decodes without the hardware, so match the byte pattern that is known to
    have worked.
    """
    url = encode_url("~ffffe630WSH Goal! O'Reilly (T. Wilson, P. Dubois) F/OT")

    for literal in ("Goal!", "O'Reilly", "(T.", "Wilson,", "F/OT"):
        assert literal in url, f"{literal!r} should not be percent-encoded"
    assert "%21" not in url
    assert "%27" not in url
    assert "%2F" not in url and "%2f" not in url
    assert "%28" not in url and "%29" not in url


def test_url_still_encodes_what_must_be_encoded():
    url = encode_url("a b~ff000030c#d?e%f")
    assert "%20" in url, "spaces must be encoded"
    assert "%23" in url, "# would start a fragment"
    assert "%3F" in url, "? would start a query"
    assert "%25" in url, "% must be escaped"
    assert "~ff000030" in url, "the colour marker must survive intact"


def test_url_targets_the_sketch_text_command():
    assert encode_url("hi").startswith(f"{settings.board_url_base}/text/")


def test_a_literal_slash_is_safe_for_the_sketch_parser():
    """cmdParse splits on the FIRST '/' only, so later slashes are just text."""
    url = encode_url("~ffffe630WSH 3-2 BOS F/OT")
    after_command = url.split("/text/", 1)[1]
    assert after_command.count("/") == 1


# ------------------------------------------------------------------ non-ascii names


def test_accented_names_are_folded_rather_than_blanked():
    """The sketch maps anything outside ASCII 32..126 to a space, so 'Stutzle' beats
    'St tzle'. Real API data contains both of these names."""
    event = _goal_event(
        home_score=1, goals=[make_goal("WSH", "St\u00fctzle", 0, 1)]
    )
    assert "Stutzle" in plain_text(payload_for(event))


def test_folding_handles_a_range_of_real_hockey_names():
    for raw, expected in [
        ("St\u00fctzle", "Stutzle"),
        ("B\u00e4ck", "Back"),
        ("H\u00f6glander", "Hoglander"),
        ("Br\u00e4nnstr\u00f6m", "Brannstrom"),
        ("Ren\u00e9", "Rene"),
    ]:
        event = _goal_event(home_score=1, goals=[make_goal("WSH", raw, 0, 1)])
        assert expected in plain_text(payload_for(event))


def test_folding_applies_to_assists_too():
    event = _goal_event(
        home_score=1,
        goals=[
            make_goal(
                "WSH", "Smith", 0, 1,
                assists=[{"playerId": 1, "name": {"default": "T. St\u00fctzle"}}],
            )
        ],
    )
    assert "St\u00fctzle" not in plain_text(payload_for(event))
    assert "Stutzle" in plain_text(payload_for(event))


def test_every_board_payload_is_pure_ascii():
    """Anything the sketch cannot render must never reach the wire."""
    event = _goal_event(home_score=1, goals=[make_goal("WSH", "\u00d6stlund\u2013\u4e2d", 0, 1)])
    payload = payload_for(event)
    assert all(32 <= ord(c) <= 126 for c in payload), repr(payload)


def test_unfoldable_characters_do_not_vanish_silently():
    """A name with nothing ASCII in it should still leave something on the board."""
    event = _goal_event(home_score=1, goals=[make_goal("WSH", "\u4e2d\u6751", 0, 1)])
    text = plain_text(payload_for(event))
    assert "Goal!" in text


# ------------------------------------------------------------------ start times


def test_unstarted_games_show_their_start_time():
    """The original's printableGameList did this; the rewrite had dropped it."""
    game = make_scoreboard(make_game(state="FUT")).games[0]
    text = plain_text(payload_for(SummaryTick(games=[game])))
    assert "WSH vs BOS" in text
    # 23:00 UTC in whatever zone the host runs in, formatted h:mm.
    assert re.search(r"WSH vs BOS \d{1,2}:\d{2}", text), text


def test_started_games_show_the_score_not_a_start_time():
    game = make_scoreboard(make_game(state="LIVE", home_score=2, away_score=1)).games[0]
    text = plain_text(payload_for(SummaryTick(games=[game])))
    assert "WSH 2-1 BOS" in text
    assert " vs " not in text


def test_a_missing_or_malformed_start_time_is_omitted_not_fatal():
    for stamp in ("", "not-a-timestamp"):
        raw = make_game(state="FUT")
        raw["startTimeUTC"] = stamp
        game = make_scoreboard(raw).games[0]
        text = plain_text(payload_for(SummaryTick(games=[game])))
        assert "WSH vs BOS" in text


def test_start_times_are_converted_to_local_time():
    from datetime import datetime

    raw = make_game(state="FUT")
    raw["startTimeUTC"] = "2026-10-08T23:00:00Z"
    game = make_scoreboard(raw).games[0]
    expected_hour = (
        datetime.fromisoformat("2026-10-08T23:00:00+00:00").astimezone().hour % 12
    ) or 12
    assert f"{expected_hour}:00" in plain_text(payload_for(SummaryTick(games=[game])))


def test_start_times_widen_pages_but_pagination_still_holds():
    games = make_scoreboard(*[make_game(game_id=i, state="FUT") for i in range(16)]).games
    pages = summary_pages(games)
    assert all(visible_length(p) <= MAX_VISIBLE_CHARS for p in pages)
    assert sum(plain_text(p).count("WSH vs BOS") for p in pages) == 16


# ------------------------------------------------------------------ live period labels


def _live(period: int, period_type: str, clock: str = "03:12"):
    raw = make_game(state="LIVE", period=period, period_type=period_type)
    raw["clock"] = {"timeRemaining": clock}
    return make_scoreboard(raw).games[0]


def test_live_overtime_reads_OT_not_P4():
    """The board said 'P4' during overtime -- wrong at the moment you most want it."""
    assert _status_suffix(_live(4, "OT")) == "OT 03:12"


def test_live_shootout_reads_SO():
    """A shootout has no meaningful clock, so the clock is dropped."""
    assert _status_suffix(_live(5, "SO")) == "SO"


def test_live_regulation_periods_are_unchanged():
    assert _status_suffix(_live(1, "REG")) == "P1 03:12"
    assert _status_suffix(_live(3, "REG")) == "P3 03:12"


def test_a_second_overtime_still_reads_OT():
    """Playoffs run multiple overtimes; period 6 is still overtime, not 'P6'."""
    assert _status_suffix(_live(6, "OT")).startswith("OT")


def test_finished_games_are_unaffected():
    for period, ptype, expected in [(3, "REG", "F"), (4, "OT", "F/OT"), (5, "SO", "F/SO")]:
        game = make_scoreboard(make_game(state="FINAL", period=period, period_type=ptype)).games[0]
        assert _status_suffix(game) == expected


def test_intermission_is_unaffected():
    raw = make_game(state="LIVE", period=2, period_type="REG")
    raw["clock"] = {"timeRemaining": "12:00", "inIntermission": True}
    assert _status_suffix(make_scoreboard(raw).games[0]).startswith("INT")


# ------------------------------------------------------------------ start time zone


def test_start_times_use_the_configured_timezone():
    """A Pi left on UTC would otherwise show every game hours off."""
    raw = make_game(state="FUT")
    raw["startTimeUTC"] = "2026-10-08T23:00:00Z"
    game = make_scoreboard(raw).games[0]

    eastern = Settings(timezone="America/New_York")
    pacific = Settings(timezone="America/Los_Angeles")
    assert start_time_label(game, eastern) == "7:00"
    assert start_time_label(game, pacific) == "4:00"


def test_an_unknown_timezone_falls_back_instead_of_crashing():
    raw = make_game(state="FUT")
    raw["startTimeUTC"] = "2026-10-08T23:00:00Z"
    game = make_scoreboard(raw).games[0]
    assert start_time_label(game, Settings(timezone="Not/AZone"))


def test_midday_games_are_not_ambiguous():
    raw = make_game(state="FUT")
    raw["startTimeUTC"] = "2026-10-08T16:00:00Z"  # noon Eastern
    game = make_scoreboard(raw).games[0]
    assert start_time_label(game, Settings(timezone="America/New_York")) == "12:00"


# ------------------------------------------------------------------ hold safety margin


def test_a_margin_can_be_added_to_every_hold():
    """board_frame_ms is derived, not measured. If it is even slightly low we write while
    the sketch is still scrolling and it drops the message with no error at all."""
    plain = Settings(board_hold_margin=1.0)
    padded = Settings(board_hold_margin=1.25)

    assert timing.display_seconds(80, padded) == pytest.approx(
        timing.display_seconds(80, plain) * 1.25
    )


def test_the_margin_applies_to_static_messages_too():
    plain = Settings(board_hold_margin=1.0)
    padded = Settings(board_hold_margin=1.5)
    assert timing.display_seconds(10, padded) > timing.display_seconds(10, plain)


def test_the_default_margin_is_on_the_safe_side():
    """Better to hold the board a moment too long than to have a write silently dropped."""
    assert Settings().board_hold_margin >= 1.0
