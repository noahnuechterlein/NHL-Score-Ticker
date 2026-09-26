"""Serving goal horns to the browser.

The engine already owns the team-to-file mapping, including the fallbacks for franchises
that postdate the 2016 asset set, so the browser asks for a horn by team abbreviation
rather than duplicating that table in TypeScript.

TestClient is used without its context manager on purpose: entering it runs the app's
lifespan, which would start the real poll loop and hit the NHL API. These routes do not
touch the service.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from nhl_ticker.app import app

client = TestClient(app)


def test_a_team_horn_is_served_as_audio():
    response = client.get("/api/horn/BOS")
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    assert len(response.content) > 10_000


def test_abbreviations_are_case_insensitive():
    assert client.get("/api/horn/bos").status_code == 200


def test_teams_without_their_own_horn_fall_back():
    """Vegas and Seattle postdate the original asset set."""
    for abbrev in ("VGK", "SEA"):
        response = client.get(f"/api/horn/{abbrev}")
        assert response.status_code == 200, abbrev
    # Same bytes as the generic horn they fall back to.
    assert client.get("/api/horn/VGK").content == client.get("/api/horn/DEF").content


def test_utah_inherits_the_arizona_horn():
    """Compared against the file, not against "ARI" -- that abbreviation no longer exists
    in the league table, so it would itself fall back to the generic horn."""
    from nhl_ticker.config import settings

    expected = (settings.horn_dir / "arizona.mp3").read_bytes()
    assert client.get("/api/horn/UTA").content == expected


def test_every_team_in_the_league_resolves():
    from nhl_ticker.core.league import TEAMS

    for abbrev in TEAMS:
        assert client.get(f"/api/horn/{abbrev}").status_code == 200, abbrev


def test_an_unknown_team_falls_back_rather_than_404ing():
    """An abbreviation we do not know still gets the generic horn, as the board does."""
    assert client.get("/api/horn/XXX").status_code == 200


def test_the_horn_is_cacheable():
    """These are static megabytes; the browser should not refetch them every goal."""
    assert "max-age" in client.get("/api/horn/BOS").headers.get("cache-control", "")


def test_a_path_traversal_attempt_is_rejected():
    """The abbreviation reaches the filesystem, so it must not be able to escape."""
    for nasty in ("../../etc/passwd", "..%2F..%2Fsecrets", "BOS/../../../pyproject.toml"):
        response = client.get(f"/api/horn/{nasty}")
        assert response.status_code in (200, 404), nasty
        if response.status_code == 200:
            assert response.headers["content-type"] == "audio/mpeg", nasty


def test_browser_horns_do_not_depend_on_the_server_speaker():
    """TICKER_HORN_ENABLED controls the engine's own audio device, not the website."""
    from nhl_ticker.config import settings

    assert settings.horn_enabled is False
    assert client.get("/api/horn/BOS").status_code == 200
