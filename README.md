# NHL Score Ticker

Drives a physical 22-character WS2812 LED board with live NHL scores and goal
alerts, plays each team's goal horn, and ships a local React test interface with a
pixel-accurate board emulator so the whole thing can be developed with no hardware
attached.

A rebuild of the scraper half of
[NHL-Score-Scraper](https://github.com/ncnuech/NHL-Score-Scraper) (2016), which scraped
ESPN's HTML and has not run in years. The board is the same panel; its firmware has since
moved from the Arduino Yún's HTTP bridge to a WiFi sketch that takes a raw TCP socket. The
engine speaks the new one by default and still supports the old one.

Everything runs locally. The only outbound traffic is to the NHL public API, plus the LAN
call to the board.

## Getting started

Just want it running on Windows? Follow [KENT-SETUP.md](KENT-SETUP.md).

### Prerequisites

- **git**
- **[uv](https://docs.astral.sh/uv/)**, which manages Python and the engine's dependencies.
  It installs Python 3.12+ itself on first run, so you don't need Python already installed.
  - Windows (PowerShell): `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
  - macOS / Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **Node.js 22.18 or newer**, for the UI. The build runs its `.ts` check scripts directly
  with Node, which older versions can't do.

### 1. Clone

```sh
git clone https://github.com/noahnuechterlein/NHL-Score-Ticker.git
cd NHL-Score-Ticker
```

### 2. Set up the engine

```sh
cd engine
cp .env.example .env                   # local settings; gitignored
uv sync --extra dev --extra horn       # creates engine/.venv
uv run pytest                          # no hardware needed
```

Settings always come from `engine/.env`, wherever you launch the engine from. The commands
below assume you're in `engine/`.

### 3. Run it

**Development, with live-reloading UI** — two terminals:

```sh
# terminal 1
cd engine
uv run nhl-ticker                      # engine on http://127.0.0.1:8000

# terminal 2
cd ui
npm install
npm run dev                            # open http://localhost:5173 (proxies to the engine)
```

**Single process.** Build the UI once, and the engine serves it:

```sh
cd ui
npm install
npm run build                          # writes ui/dist

cd ../engine
uv run nhl-ticker                      # open http://127.0.0.1:8000
```

Open the UI and hit **Fake goal** to push a synthetic goal through the entire chain
(diff → queue → board payload → emulator → horn) without waiting for a live game.

To leave it running on a Raspberry Pi or other always-on box, see [Deployment](#deployment).

### 4. Connect the board

The board is off by default, so the engine starts emulator-only. Put the computer on the same
network as the board, then:

1. Check the board answers: `uv run nhl-ticker send "Hello"`. The default IP is in `.env`
   as `TICKER_BOARD_HOST`; the WiFi sketch prints its IP on the serial monitor.
2. In `engine/.env`, set `TICKER_BOARD_ENABLED=true` (and `TICKER_BOARD_HOST` if it differs).
3. Restart `uv run nhl-ticker`.

`send` works even while the board is disabled, so a working `send` doesn't mean the ticker is
wired up. To check, look for `board sink enabled -> tcp://...` in the engine's startup log
(a disabled board logs a warning instead), or the **board** indicator in the UI.

Optional: `TICKER_HORN_ENABLED=true` plays goal horns on the machine running the engine (the
`horn` extra from step 2 provides the audio). If the board is running the original Yún
firmware, set `TICKER_BOARD_PROTOCOL=http` and use its IP; see [Board protocol](#board-protocol).

### Send a one-off message

To put a message on the board without running the ticker:

```sh
uv run nhl-ticker send "Hello Kent"                  # board from .env
uv run nhl-ticker send "Go Wild" --color 154734 --host 192.168.68.82
uv run nhl-ticker send "~ff000030RED ~ffffe630white"  # hand-written colour markers pass through
```

It sends straight to the board, bypassing the queue, so a message sent while the ticker is
mid-scroll is dropped by the sketch.

## Layout

```
engine/nhl_ticker/
  nhl/          NHL API client and models
  core/         league table, diff engine, events, serialisation
  board/        protocol.py (wire format) + messages.py (what to say),
                timing, priority queue, transports
  sinks/        goal horn, WebSocket broadcast
  runner.py     the poll loop
  cadence.py    poll interval selection and failure backoff
  app.py        FastAPI: /api/*, /ws, serves the built UI
ui/src/
  font.ts       font bitmap extracted verbatim from the firmware
  payload.ts    payload parser, mirroring ledText::parseText
  components/LedBoard.tsx   the emulator
ui/scripts/
  check-font.ts guards glyph orientation; runs as part of `npm run build`
deploy/         systemd unit + install.sh for the board host
```

## What changed from the original

**Data.** ESPN HTML scraping is replaced by `api-web.nhle.com/v1/score`. One request returns
every game, score, state, clock, and an ordered `goals[]` array carrying the scorer,
assists, and strength — which used to require a second scrape of a boxscore table.

**Goal detection.** The original diffed the score and then guessed the scorer from a
boxscore; two goals inside one poll interval appeared as a single `+2` and one alert. The
engine now counts entries in `goals[]`, so both report with their real scorers.

**The board queue.** The original called `printToBoard` inline and then `time.sleep(15)`
*inside its poll loop*, so for fifteen seconds after every goal it was neither watching for
new goals nor able to report them. A single consumer task now owns the board and paces
writes against the sketch's `cmdDisplayed` flag; the poller enqueues and returns
immediately. Goals pre-empt the slate summary, which is idle content the board falls
back to rather than a queued item.

**Poll cadence** follows game state (live / pre-game / idle) instead of hardcoded
wall-clock rules that assumed evening games in one timezone. Failures back off separately,
so a blip never inherits the 30-minute idle interval.

**Resilience to the league changing things.** Unknown enum values degrade instead of
raising, and the slate is validated game by game, so one malformed game is dropped rather
than blanking the board. Board writes are retried; stale goal alerts are dropped rather
than announced minutes late.

**The slate summary** is paginated and cycles as the board's idle content, so a full
16-game night is shown in its entirety rather than truncated at the buffer limit.

**League table** rebuilt for 32 teams, keyed on the API's abbreviation. Fixes five
abbreviations that changed since 2016 (`CLS`→`CBJ`, `LA`→`LAK`, `NJ`→`NJD`, `SJ`→`SJS`,
`TB`→`TBL`) and adds Vegas, Seattle, and Utah.

**Dropped:** SMS via the retired Verizon email-to-text gateway, the `noahn.me` endpoints,
and the player-of-the-day feature.

## Board protocol

Two firmwares, chosen with `TICKER_BOARD_PROTOCOL`:

- **`tcp` (current, default).** The WiFi sketch listens on `<board-ip>:8080`. Open a socket,
  write the payload bytes as-is (no URL encoding), close. There is no clear command, so
  clearing writes a window of blanks.
- **`http` (original Yún sketch, kept for a revert).** `GET http://<board-ip>/arduino/text/<payload>`;
  also `text2/` and `clear`. Switch back with `TICKER_BOARD_PROTOCOL=http` and the Yún's IP.

The payload format is the same on both. The rest of this section was decoded from the original
`Arduino/LEDWebText/LEDWebText.ino`; see `engine/nhl_ticker/board/protocol.py` and
`timing.py` for the details. The WiFi sketch may have fixed some of these, but the engine keeps
working around them, since that's harmless if they are fixed.

- Payload is ASCII plus `~RRGGBBLL` colour markers — RGB and a brightness byte folded in as
  `(channel * br) >> 8`, so `#ff0000` at `0x30` reaches the LEDs as `rgb(47, 0, 0)`.
  The emulator brightens that for the screen, since `rgb(47, 0, 0)` is luma 10/255 and
  reads as off on a monitor; tick **true LED brightness** in the UI for the literal values.
  This is display only — the payload is identical either way
- 7 rows × 6 columns per cell (the 6th is letter spacing), 22 cells visible
- Longer text scrolls one column per frame for `(length + 3) × 6` frames, ≈33 ms each
- The sketch ignores writes while scrolling, and frees itself after only `delay(2000)` for
  short messages — hence both the queue and a configurable minimum dwell

### Three firmware constraints worked around

1. **Buffer overrun.** `ledText::addChar` guards with `numChar < sizeof text`, but `sizeof`
   on a `ledTextChar[150]` is 600 *bytes*, not 150 entries. It also writes to
   `text[numChar]` after incrementing, so index 150 is already past the end. The engine caps
   payloads at **149 visible characters**.
2. **Dropped writes.** Anything sent mid-scroll is silently discarded. Handled by the queue
   rather than by sleeping and hoping.
3. **ASCII only.** `ledTextDisplay` substitutes a space for anything outside 32-126, so
   accented names would lose letters. Board text is ASCII-folded first: `Stützle` is sent
   as `Stutzle` rather than `St tzle`.

These are in the sketch, which is out of scope here; the engine avoids tripping them.

Over HTTP, URLs deliberately leave RFC 3986 sub-delims (`!`, `'`, `(`, `)`, `,`, `/`) unescaped, which
is the byte pattern the 2016 code sent through `requests` and is known to have worked on
this board.

## Deployment

```sh
sudo ./deploy/install.sh                      # builds the venv, installs the unit
sudo -e /opt/nhl-score-ticker/engine/.env     # board IP, TICKER_TIMEZONE, enable sinks
sudo systemctl restart nhl-ticker
journalctl -u nhl-ticker -f
```

The unit runs the virtualenv's interpreter directly rather than `uv run`, because its
`ProtectHome=true` hides uv's cache; `install.sh` builds that venv ahead of time.

**Set `TICKER_TIMEZONE`.** It defaults to the host's local zone, which is wrong on a Pi
left at its UTC default — every start time would read hours out.

No credentials are needed anywhere: the NHL API takes no key, and all configuration lives
in `.env`, which is gitignored.
