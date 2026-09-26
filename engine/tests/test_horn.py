"""Horn sink: file resolution, replacement, and failure tolerance."""

from __future__ import annotations

from pathlib import Path

from nhl_ticker.config import Settings
from nhl_ticker.core.league import TEAMS
from nhl_ticker.sinks.horn import HornSink, NullPlayer

HORN_DIR = Path(__file__).resolve().parent.parent / "assets" / "horns"


def build(**overrides) -> tuple[HornSink, NullPlayer]:
    player = NullPlayer()
    options = {"horn_dir": HORN_DIR, "horn_max_seconds": 60.0} | overrides
    return HornSink(Settings(**options), player), player


def test_plays_the_scoring_teams_own_horn():
    sink, player = build()
    assert sink.play("BOS") is True
    assert player.started[-1].name == "boston.mp3"
    sink.stop()


def test_abbreviations_that_changed_since_2016_still_resolve():
    """CLS/LA/NJ/SJ/TB became CBJ/LAK/NJD/SJS/TBL; the horn files did not change."""
    sink, player = build()
    expected = {
        "CBJ": "columbus.mp3",
        "LAK": "losangeles.mp3",
        "NJD": "newjersey.mp3",
        "SJS": "sanjose.mp3",
        "TBL": "tampabay.mp3",
    }
    for abbrev, filename in expected.items():
        assert sink.play(abbrev) is True
        assert player.started[-1].name == filename
    sink.stop()


def test_utah_inherits_the_arizona_horn():
    sink, player = build()
    assert sink.play("UTA") is True
    assert player.started[-1].name == "arizona.mp3"
    sink.stop()


def test_teams_with_no_horn_fall_back_instead_of_failing():
    """Vegas and Seattle postdate the original asset set."""
    sink, player = build()
    for abbrev in ("VGK", "SEA"):
        assert sink.play(abbrev) is True
        assert player.started[-1].name == "gameOver.mp3"
    sink.stop()


def test_every_team_in_the_league_can_play_something():
    sink, player = build()
    for abbrev in TEAMS:
        assert sink.play(abbrev) is True, abbrev
    assert len(player.started) == 32
    sink.stop()


def test_unknown_team_falls_back_to_the_default_horn():
    sink, player = build()
    assert sink.play("XXX") is True
    assert player.started[-1].name == "gameOver.mp3"
    sink.stop()


def test_a_new_goal_replaces_the_horn_in_progress():
    sink, player = build()
    sink.play("BOS")
    sink.play("WSH")
    assert [p.name for p in player.started] == ["boston.mp3", "washington.mp3"]
    sink.stop()


def test_missing_horn_directory_is_reported_not_raised():
    sink, player = build(horn_dir=Path("does-not-exist"))
    assert sink.play("BOS") is False
    assert player.started == []


def test_a_broken_player_does_not_propagate():
    """A dead sound device must not take the ticker down mid-game."""

    class Broken(NullPlayer):
        def start(self, path):
            raise OSError("no audio device")

    sink = HornSink(Settings(horn_dir=HORN_DIR), Broken())
    assert sink.play("BOS") is False


def test_stop_is_safe_before_anything_has_played():
    sink, player = build()
    sink.stop()
    assert player.stops == 1


def test_an_expired_timer_cannot_stop_a_later_horn():
    """The race: timer N fires, blocks on the lock while play() N+1 runs, then stops the
    *new* horn once the lock frees. cancel() is a no-op on an already-fired timer."""
    sink, player = build()

    sink.play("BOS")
    stale_stop = sink._stop_generation_callback()  # what timer 1 would run
    sink.play("WSH")
    assert player.started[-1].name == "washington.mp3"

    stale_stop()  # timer 1 finally gets the lock
    assert player.stops == 0, "a stale timer must not cut off the current horn"

    sink.stop()
    assert player.stops == 1


def test_the_current_timer_still_stops_its_own_horn():
    sink, player = build()
    sink.play("BOS")
    sink._stop_generation_callback()()
    assert player.stops == 1
