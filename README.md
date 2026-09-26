# NHL Score Ticker

Drives a physical 22-character WS2812 LED board (Arduino Yun) with live NHL scores and goal
alerts, plays each team's goal horn, and ships a local React test UI with a pixel-accurate
board emulator so the whole thing can be developed without the hardware attached.

A rebuild of [NHL-Score-Scraper](https://github.com/ncnuech/NHL-Score-Scraper) (2016), which
scraped ESPN's HTML and has not run in years. The board and its firmware are unchanged; this
replaces the software that feeds it.

## Layout

    engine/   Python 3.12+ service: NHL API polling, diffing, board queue, horn, FastAPI
    ui/       Vite + React + TypeScript test interface

## Running

    cd engine
    cp .env.example .env        # board IP and toggles; no secrets belong here
    uv run --extra dev pytest   # tests, no hardware needed

Everything binds to localhost. The only outbound traffic is to the NHL public API, plus the
LAN call to the board when `TICKER_BOARD_ENABLED=true`.
