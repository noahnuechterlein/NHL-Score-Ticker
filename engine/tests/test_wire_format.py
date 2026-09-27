"""Golden snapshot of every byte the board can receive.

The payload string is the contract with the hardware, and it is the one thing no
refactor may change. These tests render every event type from the recorded fixtures under
pinned settings and compare against a committed snapshot, so a restructuring that alters
the wire format fails loudly instead of quietly shipping to the board.

Regenerate deliberately, never casually:  UPDATE_WIRE_SNAPSHOT=1 pytest tests/test_wire_format.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from conftest import make_game, make_goal, make_scoreboard

from nhl_ticker.board.protocol import (
    encode_url,
    plain_text,
)
from nhl_ticker.board.messages import (
    payload_for,
    summary_pages,
)
from nhl_ticker.config import Settings
from nhl_ticker.core.events import GameEndEvent, GameStartEvent, GoalEvent, SummaryTick

SNAPSHOT = Path(__file__).parent / "fixtures" / "wire_format.json"

#: Pinned so the snapshot is reproducible anywhere: start times depend on the zone, and
#: brightness and page width feed straight into the bytes.
PINNED = Settings(
    timezone="America/Chicago",
    board_brightness="30",
    board_summary_max_chars=60,
)


def build_cases(live_fixture, future_fixture) -> dict[str, object]:
    game = live_fixture.games[0]
    accented = make_scoreboard(
        make_game(
            state="LIVE",
            home_score=1,
            goals=[
                make_goal(
                    "OTT",
                    "Stützle",
                    0,
                    1,
                    strength="pp",
                    assists=[{"playerId": 1, "name": {"default": "O. Bäck"}}],
                )
            ],
        )
    ).games[0]

    cases: dict[str, object] = {
        "goal": payload_for(GoalEvent(game=game, goal=game.goals[-1]), PINNED),
        "goal_accented": payload_for(
            GoalEvent(game=accented, goal=accented.goals[-1]), PINNED
        ),
        "game_end_ot": payload_for(GameEndEvent(game=game), PINNED),
        "game_start": payload_for(GameStartEvent(game=game), PINNED),
        "summary_finished": payload_for(SummaryTick(games=live_fixture.games), PINNED),
        "summary_pages_scheduled": summary_pages(future_fixture.games, PINNED),
    }
    # A mixed slate: upcoming games drop off once anything has started.
    mixed = make_scoreboard(
        make_game(game_id=1, away="BOS", home="WSH", home_score=2, state="LIVE"),
        make_game(game_id=2, away="NYR", home="NYI", state="FINAL"),
        make_game(game_id=3, away="DAL", home="MIN", state="FUT"),
        make_game(game_id=4, away="WPG", home="COL", state="FUT"),
    ).games
    cases["summary_mixed_slate"] = summary_pages(mixed, PINNED)

    cases["goal_url"] = encode_url(cases["goal"], PINNED)
    cases["game_end_url"] = encode_url(cases["game_end_ot"], PINNED)

    # The engine's own reading of each payload. Without this the TypeScript parity check
    # can only compare its parser against itself -- and since both its functions share one
    # marker walk, that cannot detect a disagreement with this side. Proven: breaking
    # MARKER_LEN in the emulator left the check passing until these were added.
    cases["plain"] = {
        name: plain_text(value) if isinstance(value, str) else [plain_text(v) for v in value]
        for name, value in cases.items()
        if not name.endswith("_url")
    }
    return cases


@pytest.fixture
def cases(live_fixture, future_fixture):
    return build_cases(live_fixture, future_fixture)


def test_wire_format_is_unchanged(cases):
    if os.environ.get("UPDATE_WIRE_SNAPSHOT"):
        SNAPSHOT.write_text(json.dumps(cases, indent=2, ensure_ascii=False), encoding="utf-8")
        pytest.skip("snapshot regenerated")

    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    for name, value in cases.items():
        assert value == expected[name], f"wire format changed for {name!r}"
    assert set(cases) == set(expected), "a case was added or removed without regenerating"


def test_every_snapshotted_payload_is_pure_ascii(cases):
    for name, value in cases.items():
        if name == "plain":
            continue
        payloads = value if isinstance(value, list) else [value]
        for payload in payloads:
            assert all(32 <= ord(c) <= 126 for c in payload), name


def test_the_snapshot_records_the_engines_reading_of_every_payload():
    """The TypeScript parity check needs a cross-language reference, not a self-comparison."""
    import json as _json

    recorded = _json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert "plain" in recorded
    for name, value in recorded.items():
        if name.endswith("_url") or name == "plain":
            continue
        assert name in recorded["plain"], f"no expected text recorded for {name}"


def test_the_snapshot_actually_covers_the_interesting_cases(cases):
    """Guard against the snapshot silently becoming trivial."""
    assert "Goal!" in cases["goal"]
    assert "Stutzle" in cases["goal_accented"], "accent folding must be covered"
    assert "F/OT" in cases["game_end_ot"]
    assert len(cases["summary_pages_scheduled"]) > 1, "pagination must be covered"
    mixed = " ".join(cases["summary_mixed_slate"])
    assert " vs " not in mixed, "upcoming games must be dropped once play has started"
    assert "WSH" in mixed and "NYI" in mixed
    assert "%20" in cases["goal_url"], "url encoding must be covered"
