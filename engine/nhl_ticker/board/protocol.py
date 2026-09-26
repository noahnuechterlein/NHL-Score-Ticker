"""Building the payload strings the Arduino sketch expects.

Wire format, from ``ledText::parseText`` in LEDWebText.ino: the text is plain ASCII, and a
tilde introduces a nine-character colour marker ``~RRGGBBLL`` -- six hex digits of colour
plus a brightness byte the sketch folds in as ``(channel * brightness) >> 8``. Markers are
consumed, not displayed, so they do not count toward the visible width.

Two hard limits come out of the firmware:

* ``CHARS`` (22) is the visible window. Anything longer scrolls.
* ``text[150]`` is the character buffer, and it is **not** safely bounded. ``addChar``
  guards with ``numChar < sizeof text``, but ``sizeof`` on a ``ledTextChar[150]`` is 600
  bytes, not 150 entries -- so the sketch will write up to 600 characters into a 150-slot
  array and corrupt memory past the end. It also writes to ``text[numChar]`` *after*
  incrementing, so it uses indices 1..N and index 150 is already out of bounds.
  We therefore cap visible text at 149 characters. This is a firmware bug we are working
  around rather than fixing, because the sketch is out of scope for this rebuild.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from ..config import Settings, settings as default_settings
from ..core.events import Event, GameEndEvent, GameStartEvent, GoalEvent, SummaryTick
from ..core.league import DEFAULT_TEAM, team
from ..nhl.models import Game, PeriodType

#: Longest visible string we will send. See the module docstring -- above this the sketch
#: writes past the end of its character buffer.
MAX_VISIBLE_CHARS = 149

#: Width of one colour marker in the payload: '~' plus eight hex digits.
MARKER_LEN = 9

#: Leading blanks so text scrolls in from an empty board instead of snapping into view.
#: The original used the same trick with a bare five-space prefix.
LEAD_IN = "     "


@dataclass(frozen=True, slots=True)
class Segment:
    """A run of text in one colour. ``color`` is six hex digits, no sigil."""

    text: str
    color: str = DEFAULT_TEAM.color


def marker(color: str, brightness: str) -> str:
    return f"~{color.lower()}{brightness.lower()}"


def render(segments: list[Segment], settings: Settings | None = None) -> str:
    """Flatten segments into a payload, emitting a colour marker only when it changes."""
    cfg = settings or default_settings
    out: list[str] = []
    current: str | None = None
    for segment in segments:
        if not segment.text:
            continue
        if segment.color != current:
            out.append(marker(segment.color, cfg.board_brightness))
            current = segment.color
        out.append(segment.text)
    return "".join(out)


def visible_length(payload: str) -> int:
    """Count displayed characters, skipping colour markers the way the sketch does."""
    count = 0
    index = 0
    while index < len(payload):
        if payload[index] == "~":
            # parseText consumes the marker whether or not it is complete.
            index += MARKER_LEN
            continue
        count += 1
        index += 1
    return count


def plain_text(payload: str) -> str:
    """The payload with colour markers stripped -- what a person actually reads.

    Used for log lines and for the UI's human-readable echo of each board message.
    """
    out: list[str] = []
    index = 0
    while index < len(payload):
        if payload[index] == "~":
            index += MARKER_LEN
            continue
        out.append(payload[index])
        index += 1
    return "".join(out)


def truncate(payload: str, limit: int = MAX_VISIBLE_CHARS) -> str:
    """Trim to ``limit`` *visible* characters, keeping colour markers intact."""
    if visible_length(payload) <= limit:
        return payload
    out: list[str] = []
    shown = 0
    index = 0
    while index < len(payload) and shown < limit:
        if payload[index] == "~":
            out.append(payload[index : index + MARKER_LEN])
            index += MARKER_LEN
            continue
        out.append(payload[index])
        shown += 1
        index += 1
    return "".join(out)


def encode_url(payload: str, settings: Settings | None = None) -> str:
    """Full board URL for a payload. Tilde is unreserved, so it survives quoting."""
    cfg = settings or default_settings
    return f"{cfg.board_url_base}/text/{quote(payload, safe='~')}"


# --------------------------------------------------------------------------- formatting


def _team_segment(abbrev: str) -> Segment:
    info = team(abbrev)
    return Segment(info.abbrev, info.color)


def _score_line(game: Game) -> list[Segment]:
    """``HOME 3-2 AWAY`` with each abbreviation in its team colour.

    Home first, matching the original's printableGameList.
    """
    return [
        _team_segment(game.home_team.abbrev),
        Segment(f" {game.home_team.score}-{game.away_team.score} "),
        _team_segment(game.away_team.abbrev),
    ]


def _status_suffix(game: Game) -> str:
    """Short status token: F, F/OT, F/SO, or the running clock."""
    if game.is_over:
        descriptor = game.game_outcome.last_period_type if game.game_outcome else None
        if descriptor is None and game.period_descriptor:
            descriptor = game.period_descriptor.period_type
        if descriptor == PeriodType.OT:
            return "F/OT"
        if descriptor == PeriodType.SO:
            return "F/SO"
        return "F"
    if game.clock and game.clock.in_intermission:
        return f"INT{game.period or ''}"
    if game.clock and game.clock.time_remaining:
        return f"P{game.period or 1} {game.clock.time_remaining}"
    return ""


def _strength_tag(goal) -> str:
    if goal.is_empty_net:
        return " EN"
    if goal.strength == "pp":
        return " PP"
    if goal.strength == "sh":
        return " SH"
    return ""


def goal_segments(event: GoalEvent) -> list[Segment]:
    """``WSH 3-2 BOS  WSH Goal! Ovechkin PP (Wilson, Dubois)``"""
    segments = [Segment(LEAD_IN), *_score_line(event.game), Segment("  ")]
    segments.append(_team_segment(event.scoring_abbrev))
    segments.append(Segment(" Goal! "))

    scorer = event.goal.scorer_last_name
    if scorer:
        segments.append(Segment(scorer + _strength_tag(event.goal)))
    assists = ", ".join(a.name for a in event.goal.assists if a.name)
    if assists:
        segments.append(Segment(f" ({assists})"))
    return segments


def game_start_segments(event: GameStartEvent) -> list[Segment]:
    game = event.game
    return [
        Segment(LEAD_IN),
        _team_segment(game.home_team.abbrev),
        Segment(" vs "),
        _team_segment(game.away_team.abbrev),
        Segment(" underway"),
    ]


def game_end_segments(event: GameEndEvent) -> list[Segment]:
    suffix = _status_suffix(event.game)
    return [Segment(LEAD_IN), *_score_line(event.game), Segment(f" {suffix}" if suffix else "")]


def summary_segments(games: list[Game]) -> list[Segment]:
    """Every game on the slate, in one scrolling line.

    Unstarted games show their start time rather than a 0-0 score, which is what the
    original's printableGameList did.
    """
    segments: list[Segment] = [Segment(LEAD_IN)]
    for index, game in enumerate(games):
        if index:
            segments.append(Segment("   "))
        if not game.has_started:
            segments.extend(
                [
                    _team_segment(game.home_team.abbrev),
                    Segment(" vs "),
                    _team_segment(game.away_team.abbrev),
                ]
            )
        else:
            segments.extend(_score_line(game))
            suffix = _status_suffix(game)
            if suffix:
                segments.append(Segment(f" {suffix}"))
    return segments


def segments_for(event: Event) -> list[Segment]:
    match event:
        case GoalEvent():
            return goal_segments(event)
        case GameEndEvent():
            return game_end_segments(event)
        case GameStartEvent():
            return game_start_segments(event)
        case SummaryTick():
            return summary_segments(event.games)
    raise TypeError(f"no board formatting for {type(event).__name__}")


def payload_for(event: Event, settings: Settings | None = None) -> str:
    """The exact string sent to the board -- and to the UI emulator."""
    return truncate(render(segments_for(event), settings))
