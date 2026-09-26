"""The 32-team table: display colour and goal horn per franchise.

Port of the original ``League.py``, with three changes:

1. **Keyed by the API's ``abbrev``**, not by scraped display name. The original had to carry
   a 60-line block aliasing "Bruins" to "Boston Bruins" because it read team names out of
   ESPN's HTML; the API gives us a stable abbreviation, so that whole mechanism is gone.
2. **Abbreviations corrected.** The original's ``CLS``/``LA``/``NJ``/``SJ``/``TB`` are
   ``CBJ``/``LAK``/``NJD``/``SJS``/``TBL`` in the NHL API. Horn files are unchanged.
3. **Three franchises added** that did not exist in 2016: Vegas (2017), Seattle (2021), and
   Utah (2024, relocated from Arizona). Utah inherits the Arizona horn; Vegas and Seattle
   have no horn in the original asset set and fall back to the generic one until real
   files are dropped in.

Colours are the original's, minus the leading ``~``; ``board.protocol`` adds the sigil and
the brightness byte. A couple of them are very dark and barely register on the LEDs (LAK's
near-black in particular) -- they are kept as-is for fidelity and are easy to tune here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Team:
    abbrev: str
    name: str
    #: Six hex digits, no leading '~'.
    color: str
    horn: str

    @property
    def has_real_horn(self) -> bool:
        return self.horn != DEFAULT_HORN


DEFAULT_HORN = "gameOver.mp3"

#: Used for punctuation, scores, and any team we do not recognise.
DEFAULT_TEAM = Team("DEF", "Default", "ffffe6", DEFAULT_HORN)

TEAMS: dict[str, Team] = {
    t.abbrev: t
    for t in (
        Team("ANA", "Ducks", "c2672c", "anaheim.mp3"),
        Team("BOS", "Bruins", "fcb930", "boston.mp3"),
        Team("BUF", "Sabres", "9e4e00", "buffalo.mp3"),
        Team("CAR", "Hurricanes", "ff0000", "carolina.mp3"),
        Team("CBJ", "Blue Jackets", "002e62", "columbus.mp3"),
        Team("CGY", "Flames", "c90000", "calgary.mp3"),
        Team("CHI", "Blackhawks", "480000", "chicago.mp3"),
        Team("COL", "Avalanche", "830018", "colorado.mp3"),
        Team("DAL", "Stars", "084c00", "dallas.mp3"),
        Team("DET", "Red Wings", "ff0000", "detroit.mp3"),
        Team("EDM", "Oilers", "eb6e1e", "edmonton.mp3"),
        Team("FLA", "Panthers", "c49818", "florida.mp3"),
        Team("LAK", "Kings", "231f20", "losangeles.mp3"),
        Team("MIN", "Wild", "103d17", "minnesota.mp3"),
        Team("MTL", "Canadiens", "ff0000", "montreal.mp3"),
        Team("NJD", "Devils", "ff0000", "newjersey.mp3"),
        Team("NSH", "Predators", "ffb71a", "nashville.mp3"),
        Team("NYI", "Islanders", "ff5000", "newyorkislanders.mp3"),
        Team("NYR", "Rangers", "005dab", "newyorkrangers.mp3"),
        Team("OTT", "Senators", "d47e00", "ottawa.mp3"),
        Team("PHI", "Flyers", "f32b00", "philadelphia.mp3"),
        Team("PIT", "Penguins", "7ed5fa", "pittsburgh.mp3"),
        # Added 2021. No horn in the original asset set.
        Team("SEA", "Kraken", "99d9d9", DEFAULT_HORN),
        Team("SJS", "Sharks", "00765d", "sanjose.mp3"),
        Team("STL", "Blues", "083377", "stlouis.mp3"),
        Team("TBL", "Lightning", "ffffff", "tampabay.mp3"),
        Team("TOR", "Maple Leafs", "013e7f", "toronto.mp3"),
        # Relocated from Arizona in 2024; inherits the Coyotes horn.
        Team("UTA", "Mammoth", "71afe5", "arizona.mp3"),
        Team("VAN", "Canucks", "013e7f", "vancouver.mp3"),
        # Added 2017. No horn in the original asset set.
        Team("VGK", "Golden Knights", "b4975a", DEFAULT_HORN),
        Team("WPG", "Jets", "002e62", "winnepeg.mp3"),
        Team("WSH", "Capitals", "ff0000", "washington.mp3"),
    )
}


def team(abbrev: str) -> Team:
    """Look up a team, falling back to the neutral default rather than raising.

    The original indexed ``teamDict`` directly and threw a KeyError onto a bare ``except``
    whenever the league changed; an unknown abbreviation should degrade to white text, not
    silently swallow the event.
    """
    return TEAMS.get(abbrev.upper(), DEFAULT_TEAM)


def horn_path(abbrev: str, horn_dir: Path) -> Path | None:
    """Absolute path to a team's horn, or None if the file is not on disk."""
    candidate = horn_dir / team(abbrev).horn
    if candidate.is_file():
        return candidate
    fallback = horn_dir / DEFAULT_HORN
    return fallback if fallback.is_file() else None
