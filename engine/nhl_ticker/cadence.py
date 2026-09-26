"""How often to poll, and how to back off when polling fails.

Pulled out of the poll loop because it is pure, self-contained decision logic and the
most test-dense part of the runner -- it reads better with the I/O out of the way.

Two rules, and the interaction between them is the subtle bit:

* While healthy, the interval follows *game state* rather than the wall clock. The
  original hardcoded ``if hour == 3: clear`` / ``if hour == 7: startDay`` and flipped
  between 10s and an hour on fixed boundaries, which broke for afternoon games and for
  anyone outside the author's timezone.
* While failing, it backs off exponentially instead. The healthy interval is the wrong
  answer after a failure, because a failure usually means there is no scoreboard to read
  state from -- and an absent scoreboard looks "idle", i.e. up to half an hour of silence
  after one blip at startup.
"""

from __future__ import annotations

from .config import Settings, settings as default_settings
from .nhl.models import Scoreboard


class Cadence:
    """Tracks consecutive failures and answers "how long until the next poll?"."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or default_settings
        self._failures = 0

    @property
    def failures(self) -> int:
        return self._failures

    def note_failure(self) -> None:
        self._failures += 1

    def note_success(self) -> None:
        self._failures = 0

    def healthy_interval(self, scoreboard: Scoreboard | None) -> float:
        """Fast while anything is live, slow before puck drop, idle once the slate is done."""
        cfg = self._settings
        if scoreboard is None or not scoreboard.games:
            return cfg.poll_idle_seconds
        if any(game.is_live for game in scoreboard.games):
            return cfg.poll_live_seconds
        if any(not game.has_started for game in scoreboard.games):
            return cfg.poll_pregame_seconds
        return cfg.poll_idle_seconds

    def next_interval(self, scoreboard: Scoreboard | None) -> float:
        """The wait before the next poll, accounting for any run of failures.

        The backoff is capped both absolutely and by the healthy interval: failing during
        live play must never make us poll more slowly than live play already demands.
        """
        healthy = self.healthy_interval(scoreboard)
        if not self._failures:
            return healthy

        cfg = self._settings
        backoff = cfg.poll_error_seconds * (2 ** (self._failures - 1))
        return min(backoff, cfg.poll_error_max_seconds, healthy)
