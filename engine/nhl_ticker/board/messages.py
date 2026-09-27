"""Composing board messages out of game events.

The hockey half of the board output: what a goal, a final, or the night's slate should
say. The byte-level encoding lives in ``protocol.py``; this module only ever produces
:class:`~nhl_ticker.board.protocol.Segment` lists and hands them over to be rendered.
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..config import Settings, settings as default_settings
from ..core.events import Event, GameEndEvent, GameStartEvent, GoalEvent, SummaryTick
from ..core.league import team
from ..nhl.models import Game, PeriodType
from .protocol import (
    LEAD_IN,
    MAX_VISIBLE_CHARS,
    Segment,
    render,
    truncate,
    visible_length,
)

log = logging.getLogger(__name__)

def _display_zone(settings: Settings | None = None) -> ZoneInfo | None:
    """The configured zone, or None to mean "whatever the host thinks local is"."""
    cfg = settings or default_settings
    if not cfg.timezone:
        return None
    try:
        return ZoneInfo(cfg.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("unknown timezone %r; falling back to host local", cfg.timezone)
        return None


def start_time_label(game: Game, settings: Settings | None = None) -> str:
    """Start time for a game that has not begun, e.g. "7:00".

    Rendered without a meridiem to stay narrow, as the original did; the NHL does not
    schedule games at hours where 12-hour time is ambiguous.
    """
    if not game.start_time_utc:
        return ""
    try:
        moment = datetime.fromisoformat(game.start_time_utc.replace("Z", "+00:00"))
    except ValueError:
        return ""
    local = moment.astimezone(_display_zone(settings))
    return f"{(local.hour % 12) or 12}:{local.minute:02d}"


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

    # Name the period rather than numbering it. Overtime is period 4 and a shootout
    # period 5, so numbering them read "P4" and "P5" on the board -- least useful at
    # exactly the moment the board matters most.
    descriptor = game.period_descriptor.period_type if game.period_descriptor else None
    if descriptor == PeriodType.SO:
        return "SO"  # a shootout has no meaningful clock
    label = "OT" if descriptor == PeriodType.OT else f"P{game.period or 1}"

    if game.clock and game.clock.time_remaining:
        return f"{label} {game.clock.time_remaining}"
    return label


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


def slate_for_summary(games: list[Game]) -> list[Game]:
    """The games worth putting on the board right now.

    Once anything has started, upcoming games are dropped: a wall of "7:00  7:00  8:00"
    is noise next to hockey that is actually happening, and on a busy night those
    listings are most of the slate, so they push the live games onto later pages. Before
    puck drop they are the whole point, so then everything shows.

    A game in an UNKNOWN state counts as not started, so an unrecognised state can never
    suppress the rest of the slate by looking like live hockey.
    """
    started = [game for game in games if game.has_started]
    return started or list(games)


def summary_segments(games: list[Game], settings: Settings | None = None) -> list[Segment]:
    """Render the given games as one scrolling line.

    A pure renderer: it shows exactly what it is handed. Which games belong on the board
    is :func:`slate_for_summary`'s decision, applied by the callers below.

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
            start = start_time_label(game, settings)
            if start:
                segments.append(Segment(f" {start}"))
        else:
            segments.extend(_score_line(game))
            suffix = _status_suffix(game)
            if suffix:
                segments.append(Segment(f" {suffix}"))
    return segments


def summary_pages(games: list[Game], settings: Settings | None = None) -> list[str]:
    """Split the slate into board-sized pages, packed greedily.

    A 16-game night overflows the 149-character buffer, so without this the tail of the
    slate would simply never be shown. Pages also bound how long a goal alert can sit
    behind a summary, since the board cannot be interrupted mid-scroll.
    """
    cfg = settings or default_settings
    limit = min(cfg.board_summary_max_chars, MAX_VISIBLE_CHARS)

    pages: list[str] = []
    current: list[Game] = []
    for game in slate_for_summary(games):
        candidate = current + [game]
        if current and visible_length(render(summary_segments(candidate, cfg), cfg)) > limit:
            pages.append(truncate(render(summary_segments(current, cfg), cfg)))
            current = [game]
        else:
            current = candidate
    if current:
        pages.append(truncate(render(summary_segments(current, cfg), cfg)))
    return pages


def payloads_for(event: Event, settings: Settings | None = None) -> list[str]:
    """Every board message this event produces.

    One per event today. Summaries are the only multi-message case and they no longer
    come through here -- the queue treats the slate as idle content and calls
    :func:`summary_pages` directly.
    """
    return [payload_for(event, settings)]


def segments_for(event: Event, settings: Settings | None = None) -> list[Segment]:
    match event:
        case GoalEvent():
            return goal_segments(event)
        case GameEndEvent():
            return game_end_segments(event)
        case GameStartEvent():
            return game_start_segments(event)
        case SummaryTick():
            return summary_segments(slate_for_summary(event.games), settings)
    raise TypeError(f"no board formatting for {type(event).__name__}")


def payload_for(event: Event, settings: Settings | None = None) -> str:
    """The exact string sent to the board -- and to the UI emulator."""
    return truncate(render(segments_for(event, settings), settings))

