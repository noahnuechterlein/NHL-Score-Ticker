"""Runtime configuration, read from the environment or a local .env file.

Everything here binds to localhost except the board (LAN) and the NHL API. Nothing is
published to an external service. See .env.example for the tunables that matter.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TICKER_",
        # Anchored to engine/ rather than the working directory, so launching from the repo
        # root still picks up TICKER_BOARD_ENABLED and friends instead of silently using
        # defaults.
        env_file=_REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- NHL API ---
    nhl_api_base: str = "https://api-web.nhle.com/v1"
    http_timeout_seconds: float = 15.0
    http_max_retries: int = 3

    # --- polling cadence, chosen from game state rather than wall-clock time ---
    poll_live_seconds: float = 8.0
    poll_pregame_seconds: float = 300.0
    poll_idle_seconds: float = 1800.0
    #: First retry delay after a failed poll. Failures must not inherit the idle interval:
    #: a blip at startup leaves us with no scoreboard at all, which reads as "idle".
    poll_error_seconds: float = 5.0
    #: Ceiling on the exponential retry backoff.
    poll_error_max_seconds: float = 60.0

    # --- Arduino LED board ---
    board_enabled: bool = False
    #: How payloads reach the board. ``tcp`` is the current WiFi firmware: a raw socket that
    #: takes the payload bytes and closes. ``http`` is the original Yun sketch,
    #: LEDWebText.ino, kept so a firmware revert is a one-line .env change.
    board_protocol: Literal["tcp", "http"] = "tcp"
    #: Printed by the WiFi sketch on its serial monitor. The Yun used 10.177.105.137.
    board_host: str = "192.168.68.82"
    #: TCP listener port. Ignored over HTTP, which always uses port 80.
    board_port: int = 8080
    #: Visible character cells. CHARS in LEDWebText.ino.
    board_chars: int = 22
    #: Pixel columns per character cell, the last of which is always blank. COLS in the
    #: sketch. The row count is fixed by the panel and lives in the emulator's font module;
    #: only the column count feeds a calculation here.
    board_cols: int = 6
    #: Milliseconds the sketch takes to push one frame (one column of scroll). Measured on
    #: hardware during bring-up; ~924 pixels x 24 bits x 1.4us plus delay(1).
    board_frame_ms: float = 33.0
    #: Short messages do not scroll and the sketch frees itself immediately, so we impose
    #: our own dwell time to stop them being overwritten instantly.
    board_min_dwell_seconds: float = 5.0
    #: The sketch's own hold after a non-scrolling message: delay(2000) in ledTextDisplay.
    #: Configurable because it is a property of the flashed firmware, not a law of physics.
    board_static_hold_seconds: float = 2.0
    #: Multiplier on every computed hold. board_frame_ms is derived from the sketch, not
    #: measured, and if it is even slightly low we write while the board is still
    #: scrolling -- which the sketch discards silently. Erring long costs a moment of
    #: dwell; erring short loses the message entirely.
    board_hold_margin: float = 1.1
    #: Attempts per board write. The NHL client retries but the board used to get one try,
    #: so a single LAN blip dropped a goal alert outright.
    board_write_attempts: int = 3
    board_write_backoff_seconds: float = 0.25
    #: Clear the board when the service shuts down, rather than leaving the last message
    #: frozen there indefinitely.
    board_clear_on_exit: bool = True

    #: Drop goal alerts older than this. A burst of goals each holding 8-16s can otherwise
    #: push the board minutes behind live play, announcing goals long after the fact.
    goal_max_age_seconds: float = 300.0
    #: Hard cap on queued messages, so a stalled board cannot grow the backlog without end.
    queue_max_items: int = 64

    #: Brightness byte appended to every ~RRGGBB colour code.
    board_brightness: str = "30"
    #: Target visible width of one page of the slate summary. A full 16-game night does not
    #: fit in the 149-character buffer, and a single maximum-length message owns the board
    #: for ~20s, which is how long a goal would then wait. Paginating bounds both. The
    #: original did the same thing, splitting printableGameList at 132 characters.
    #: Lower means a goal waits less time behind a summary but the slate takes more pages
    #: to cycle through; 60 chars is about 12s a page.
    board_summary_max_chars: int = 60

    # --- goal horns ---
    horn_enabled: bool = False
    horn_dir: Path = _REPO_ROOT / "assets" / "horns"
    horn_max_seconds: float = 10.0

    #: IANA zone for board-facing times. Empty means the host's own local zone, which is
    #: fine on a desktop but wrong on a Raspberry Pi left at its UTC default -- every game
    #: would read four or five hours out. Pin it in .env on the board host.
    timezone: str = ""

    # --- local web server ---
    host: str = "127.0.0.1"
    port: int = 8000
    ui_dist: Path = _REPO_ROOT.parent / "ui" / "dist"

    @property
    def board_url_base(self) -> str:
        return f"http://{self.board_host}/arduino"


settings = Settings()
