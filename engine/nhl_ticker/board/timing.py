"""How long the board stays busy with a message.

Derived from ``ledTextDisplay()`` in LEDWebText.ino. Each call to that function renders one
frame and advances the scroll by a single pixel column:

    offset = cmdIteration++;
    if (cmdIteration >= (text.length()+3)*COLS) { cmdIteration = 0; cmdDisplayed = true; }

so a message of N characters takes ``(N + 3) * COLS`` frames to cycle. A frame is the time
to clock out every pixel -- 22 chars x 6 cols x 7 rows = 924 pixels at 24 bits and roughly
1.4 us per bit, about 31 ms -- plus the trailing ``delay(1)``. Call it ~33 ms, and
calibrate ``board_frame_ms`` against real hardware during bring-up.

Short messages are the awkward case. When ``text.length() <= CHARS`` the sketch sets
``cmdDisplayed = true`` on the very first frame, so the board declares itself free after a
single ``delay(2000)`` and will accept a replacement immediately. Left alone, a short goal
alert could be wiped by the next summary two seconds later. The engine therefore imposes
its own minimum dwell.

This is what the original was approximating with ``time.sleep(15)`` scattered through its
scrape loop -- a constant tuned for the longest message it expected to send.
"""

from __future__ import annotations

from ..config import Settings, settings as default_settings

#: The stock sketch's hold after rendering a non-scrolling message: delay(2000). This is
#: the default for ``board_static_hold_seconds``; read that setting rather than this
#: constant so a reflashed board (or a test) can say otherwise.
FIRMWARE_STATIC_HOLD_SECONDS = 2.0

#: Blank character cells the sketch appends before wrapping the scroll.
SCROLL_GAP_CHARS = 3


def scroll_frames(visible_chars: int, settings: Settings | None = None) -> int:
    """Frames for one full scroll cycle: ``(N + 3) * COLS``."""
    cfg = settings or default_settings
    return (visible_chars + SCROLL_GAP_CHARS) * cfg.board_cols


def scrolls(visible_chars: int, settings: Settings | None = None) -> bool:
    cfg = settings or default_settings
    return visible_chars > cfg.board_chars


def scroll_seconds(visible_chars: int, settings: Settings | None = None) -> float:
    """Wall-clock time for one full scroll cycle of a message this long."""
    cfg = settings or default_settings
    return scroll_frames(visible_chars, cfg) * cfg.board_frame_ms / 1000.0


def display_seconds(visible_chars: int, settings: Settings | None = None) -> float:
    """How long to own the board before sending the next message.

    Scrolling messages need a full cycle so the tail is actually read. Static ones get the
    configured minimum dwell, which must clear the firmware's own 2 s hold.
    """
    cfg = settings or default_settings
    floor = max(cfg.board_min_dwell_seconds, cfg.board_static_hold_seconds)
    base = floor if not scrolls(visible_chars, cfg) else max(
        scroll_seconds(visible_chars, cfg), floor
    )
    # board_frame_ms is derived rather than measured, so hold a little longer than the
    # estimate. A write that lands mid-scroll is discarded by the sketch without any
    # error, which is far worse than a moment of extra dwell.
    return base * cfg.board_hold_margin
