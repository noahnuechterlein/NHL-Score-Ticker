# NHL Score Ticker

Drives a physical 22-character WS2812 LED board (Arduino Yún) with live NHL scores and goal
alerts, plays each team's goal horn, and ships a local React test interface with a
pixel-accurate board emulator so the whole thing can be developed with no hardware
attached.

A rebuild of the scraper half of
[NHL-Score-Scraper](https://github.com/ncnuech/NHL-Score-Scraper) (2016), which scraped
ESPN's HTML and has not run in years. **The board and its firmware are unchanged** — this
replaces the software that feeds it.

Everything runs locally. The only outbound traffic is to the NHL public API, plus the LAN
call to the board.

## Quick start

```sh
# engine + tests, no hardware needed
cd engine
cp .env.example .env
uv run --extra dev --extra horn pytest
uv run python -m nhl_ticker            # http://127.0.0.1:8000

# UI, in a second terminal
cd ui
npm install
npm run dev                            # http://localhost:5173, proxies to the engine
```

`npm run build` writes `ui/dist`, which the engine serves directly — in production you only
need the one process.

Open the UI and hit **Fake goal** to push a synthetic goal through the entire chain
(diff → queue → board payload → emulator → horn) without waiting for a live game.

## Layout

```
engine/nhl_ticker/
  nhl/          NHL API client and models
  core/         league table, diff engine, events, serialisation
  board/        payload protocol, scroll timing, priority queue, transports
  sinks/        goal horn, WebSocket broadcast
  runner.py     the poll loop
  app.py        FastAPI: /api/*, /ws, serves the built UI
ui/src/
  font.ts       font bitmap extracted verbatim from the firmware
  payload.ts    payload parser, mirroring ledText::parseText
  components/LedBoard.tsx   the emulator
ui/scripts/
  check-font.ts guards glyph orientation; runs as part of `npm run build`
deploy/         systemd unit for the board host
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

**The slate summary** is paginated and cycles as the board's idle content, so a full
16-game night is shown in its entirety rather than truncated at the buffer limit.

**League table** rebuilt for 32 teams, keyed on the API's abbreviation. Fixes five
abbreviations that changed since 2016 (`CLS`→`CBJ`, `LA`→`LAK`, `NJ`→`NJD`, `SJ`→`SJS`,
`TB`→`TBL`) and adds Vegas, Seattle, and Utah.

**Dropped:** SMS via the retired Verizon email-to-text gateway, the `noahn.me` endpoints,
and the player-of-the-day feature.

## Board protocol

Decoded from `Arduino/LEDWebText/LEDWebText.ino`; see `engine/nhl_ticker/board/protocol.py`
and `timing.py` for the details.

- `GET http://<board-ip>/arduino/text/<payload>`; also `text2/` and `clear`
- Payload is ASCII plus `~RRGGBBLL` colour markers — RGB and a brightness byte folded in as
  `(channel * br) >> 8`, so `#ff0000` at `0x30` reaches the LEDs as `rgb(47, 0, 0)`
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

URLs deliberately leave RFC 3986 sub-delims (`!`, `'`, `(`, `)`, `,`, `/`) unescaped, which
is the byte pattern the 2016 code sent through `requests` and is known to have worked on
this board.

## Deployment

```sh
sudo cp deploy/nhl-ticker.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now nhl-ticker
journalctl -u nhl-ticker -f
```

## Security note

`Scraper1.0/Messenger.py` in the original repo contains a **plaintext Gmail password**
(`nhlscoreticker@gmail.com`), public since 2016. Nothing here uses it, but that account
should be rotated or deleted. No credentials belong in this repo; configuration lives in
`.env`, which is gitignored.
