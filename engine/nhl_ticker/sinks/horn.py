"""Goal horns.

A port of ``Buzzer.py``, which shelled out to a hardcoded VLC path
(``C:/Program Files (x86)/VideoLAN/VLC/vlc.exe`` or ``/usr/bin/cvlc``), then killed it with
``os.kill(pid, SIGINT)``. That had three problems worth fixing rather than carrying over:

* ``startBuzzer`` opened with an unconditional ``time.sleep(10)``, on the calling thread,
  which is part of why the original's poll loop stalled after every goal.
* ``SIGINT`` does not exist on Windows, so the stop path only ever worked on Linux.
* The VLC path was hardcoded, so the whole thing silently did nothing without that exact
  install.

Playback now goes through miniaudio (``just_playback``), which is a pure wheel on Windows,
macOS, and Linux/ARM, needs no external binary, and stops cleanly. If the package is not
installed the sink degrades to logging rather than raising -- a missing speaker should
never take the ticker down.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Protocol

from ..config import Settings, settings as default_settings
from ..core.league import horn_path, team

log = logging.getLogger(__name__)


class Player(Protocol):
    def start(self, path: Path) -> None: ...

    def stop(self) -> None: ...


class NullPlayer:
    """Records what would have played. Used in tests and when audio is unavailable."""

    def __init__(self) -> None:
        self.started: list[Path] = []
        self.stops = 0

    def start(self, path: Path) -> None:
        self.started.append(path)

    def stop(self) -> None:
        self.stops += 1


class MiniaudioPlayer:
    """One playback at a time, backed by just_playback."""

    def __init__(self) -> None:
        from just_playback import Playback  # imported lazily so the dep stays optional

        self._playback_cls = Playback
        self._playback = None

    def start(self, path: Path) -> None:
        self.stop()
        playback = self._playback_cls()
        playback.load_file(str(path))
        playback.play()
        self._playback = playback

    def stop(self) -> None:
        if self._playback is None:
            return
        try:
            if self._playback.playing:
                self._playback.stop()
        except Exception as exc:
            log.debug("horn stop failed: %s", exc)
        finally:
            self._playback = None


def default_player() -> Player:
    try:
        return MiniaudioPlayer()
    except Exception as exc:
        log.warning("audio unavailable (%s); horns will be logged only", exc)
        return NullPlayer()


class HornSink:
    """Plays the scoring team's horn, capped at ``horn_max_seconds``.

    A new goal cuts off the previous horn rather than layering on top of it, matching the
    board's one-message-at-a-time behaviour. Everything is non-blocking: ``play`` returns
    immediately and a timer handles the stop.
    """

    def __init__(self, settings: Settings | None = None, player: Player | None = None) -> None:
        self._settings = settings or default_settings
        self._player = player if player is not None else default_player()
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()
        #: Bumped on every play/stop. A timer only acts on the horn it was created for:
        #: an already-fired timer cannot be cancelled, so without this it would block on
        #: the lock and then cut off whichever horn started next.
        self._generation = 0

    def play(self, abbrev: str) -> bool:
        """Start the horn for a team. Returns False if there was nothing to play."""
        path = horn_path(abbrev, self._settings.horn_dir)
        if path is None:
            log.warning("no horn file for %s in %s", abbrev, self._settings.horn_dir)
            return False

        if not team(abbrev).has_real_horn:
            log.info("%s has no dedicated horn; using the generic one", abbrev)

        with self._lock:
            self._cancel_timer()
            self._generation += 1
            generation = self._generation
            try:
                self._player.start(path)
            except Exception as exc:
                log.warning("horn playback failed for %s: %s", abbrev, exc)
                return False

            self._timer = threading.Timer(
                self._settings.horn_max_seconds, self._stop_generation, args=(generation,)
            )
            self._timer.daemon = True
            self._timer.start()

        log.info("horn: %s (%s)", abbrev, path.name)
        return True

    def stop(self) -> None:
        """Stop whatever is playing now."""
        with self._lock:
            self._cancel_timer()
            self._generation += 1
            self._player.stop()

    def _stop_generation(self, generation: int) -> None:
        """Stop the horn, but only if it is still the one this timer was set for."""
        with self._lock:
            if generation != self._generation:
                log.debug("ignoring expired horn timer for generation %d", generation)
                return
            self._cancel_timer()
            self._generation += 1
            self._player.stop()

    def _stop_generation_callback(self):
        """The callback the currently-armed timer will run. Exposed for tests."""
        generation = self._generation
        return lambda: self._stop_generation(generation)

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
