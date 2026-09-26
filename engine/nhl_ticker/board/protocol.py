"""The wire format: turning coloured text into the exact bytes the sketch expects.

This module knows about the Arduino's encoding and nothing about hockey. Composing a
message out of a game event lives in ``messages.py``.

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

import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from urllib.parse import quote

from ..config import Settings, settings as default_settings
from ..core.league import DEFAULT_TEAM

#: Longest visible string we will send. See the module docstring -- above this the sketch
#: writes past the end of its character buffer.
MAX_VISIBLE_CHARS = 149

#: Width of one colour marker in the payload: '~' plus eight hex digits.
MARKER_LEN = 9

#: Leading blanks so text scrolls in from an empty board instead of snapping into view.
#: The original used the same trick with a bare five-space prefix.
LEAD_IN = "     "

#: Characters left un-escaped in the board URL.
#:
#: Python's default safe set is just "/", and passing ``safe="~"`` *replaces* it rather
#: than adding to it -- which silently percent-encoded ``!``, ``'``, ``(``, ``)``, ``,``
#: and ``/``, so a goal alert went out as ``Goal%21`` and a final as ``F%2FOT``. The 2016
#: code reached the same board through ``requests``, which preserves RFC 3986 sub-delims,
#: so this restores the byte pattern that is known to have worked. Only space, ``%``,
#: ``#``, ``?`` and non-ASCII are escaped now.
#:
#: A literal ``/`` in the text is safe: ``cmdParse`` splits on the first one only.
URL_SAFE = "~!$&\'()*+,;=:@/-._"

#: The sketch replaces anything outside this range with a space (``if (ch<32 || ch>126)``).
PRINTABLE_MIN, PRINTABLE_MAX = 32, 126


@dataclass(frozen=True, slots=True)
class Segment:
    """A run of text in one colour. ``color`` is six hex digits, no sigil."""

    text: str
    color: str = DEFAULT_TEAM.color


def to_board_ascii(text: str) -> str:
    """Fold text down to characters the board can actually draw.

    The sketch substitutes a space for anything outside ASCII 32..126, so an unfolded
    "Stutzle" arrives as "St tzle" and loses a letter. Decomposing first and dropping the
    combining marks keeps the letter: "Stutzle". Real NHL data needs this -- our own
    fixtures contain Stutzle and Back.

    Anything with no ASCII equivalent at all (CJK, for instance) still degrades to a
    space, which is what the board would have shown anyway.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return "".join(
        c if PRINTABLE_MIN <= ord(c) <= PRINTABLE_MAX else " " for c in stripped
    )


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
        # Folded here rather than at each call site, so nothing unrenderable can reach
        # the board however a payload was assembled.
        out.append(to_board_ascii(segment.text))
    return "".join(out)


def walk(payload: str) -> Iterator[tuple[str, bool]]:
    """Yield ``(chunk, is_marker)`` across a payload, exactly as the sketch reads it.

    ``parseText`` consumes a marker whether or not it is complete, so a truncated one at
    the end swallows the remainder. Counting, stripping and truncating all used to
    re-implement this walk separately; they share it now so they cannot drift apart.
    """
    index = 0
    while index < len(payload):
        if payload[index] == "~":
            yield payload[index : index + MARKER_LEN], True
            index += MARKER_LEN
            continue
        yield payload[index], False
        index += 1


def visible_length(payload: str) -> int:
    """Count displayed characters, skipping colour markers the way the sketch does."""
    return sum(1 for _, is_marker in walk(payload) if not is_marker)


def plain_text(payload: str) -> str:
    """The payload with colour markers stripped -- what a person actually reads.

    Used for log lines and for the UI's human-readable echo of each board message.
    """
    return "".join(chunk for chunk, is_marker in walk(payload) if not is_marker)


def truncate(payload: str, limit: int = MAX_VISIBLE_CHARS) -> str:
    """Trim to ``limit`` *visible* characters, keeping colour markers intact."""
    out: list[str] = []
    shown = 0
    for chunk, is_marker in walk(payload):
        if is_marker:
            out.append(chunk)
            continue
        if shown >= limit:
            break
        out.append(chunk)
        shown += 1
    return "".join(out)


def encode_url(payload: str, settings: Settings | None = None) -> str:
    """Full board URL for a payload. See URL_SAFE for why the safe set is so wide."""
    cfg = settings or default_settings
    return f"{cfg.board_url_base}/text/{quote(payload, safe=URL_SAFE)}"
