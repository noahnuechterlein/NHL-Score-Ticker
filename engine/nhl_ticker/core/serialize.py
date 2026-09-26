"""Shaping engine objects into the JSON the UI consumes.

Kept separate from the Pydantic models so the wire format for the test interface can
change without disturbing how we parse the NHL API. Keys are camelCase to match the
TypeScript side.
"""

from __future__ import annotations

from ..board.protocol import payload_for, plain_text
from .events import Event, GameEndEvent, GameStartEvent, GoalEvent, SummaryTick
from .league import team
from .state import ScoreboardTracker
from ..nhl.models import Game


def game_to_dict(game: Game) -> dict:
    home, away = game.home_team, game.away_team
    return {
        "id": game.id,
        "state": str(game.game_state),
        "hasStarted": game.has_started,
        "isLive": game.is_live,
        "isOver": game.is_over,
        "period": game.period,
        "periodType": (
            str(game.period_descriptor.period_type) if game.period_descriptor else None
        ),
        "clock": None if game.clock is None else game.clock.time_remaining,
        "inIntermission": bool(game.clock and game.clock.in_intermission),
        "startTimeUTC": game.start_time_utc,
        "home": {
            "abbrev": home.abbrev,
            "name": home.name,
            "score": home.score,
            "sog": home.sog,
            "color": f"#{team(home.abbrev).color}",
        },
        "away": {
            "abbrev": away.abbrev,
            "name": away.name,
            "score": away.score,
            "sog": away.sog,
            "color": f"#{team(away.abbrev).color}",
        },
        "goalCount": len(game.goals),
    }


def event_to_dict(event: Event) -> dict:
    """An event plus the board payload it will produce, so the UI can preview both."""
    payload = payload_for(event)
    base = {
        "kind": type(event).__name__,
        "priority": int(event.priority),
        "payload": payload,
        "text": plain_text(payload).strip(),
    }

    match event:
        case GoalEvent():
            base |= {
                "gameId": event.game.id,
                "scorer": event.goal.scorer_last_name,
                "team": event.scoring_abbrev,
                "teamColor": f"#{team(event.scoring_abbrev).color}",
                "strength": event.goal.strength,
                "period": event.goal.period,
                "timeInPeriod": event.goal.time_in_period,
                "assists": [a.name for a in event.goal.assists],
                "backfilled": event.backfilled,
                "highlightUrl": event.goal.highlight_clip_url,
            }
        case GameStartEvent() | GameEndEvent():
            base |= {"gameId": event.game.id}
        case SummaryTick():
            base |= {"gameCount": len(event.games)}
    return base


def tracker_to_dict(tracker: ScoreboardTracker) -> dict:
    return {"trackedGames": sorted(tracker.tracked_ids)}
