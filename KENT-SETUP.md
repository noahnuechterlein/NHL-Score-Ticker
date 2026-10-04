# Running the ticker on Windows

Step-by-step, from nothing to live scores on the board. Every command goes in **PowerShell**
(Start menu → type "PowerShell" → open it). Copy each grey box, paste it in, press Enter.

## 0. Install three tools (one time only)

```powershell
winget install --id Git.Git -e
winget install --id OpenJS.NodeJS.LTS -e
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Then **close PowerShell and open a new one**, so it can find what you just installed.

## 1. Download the ticker

```powershell
cd $HOME
git clone https://github.com/noahnuechterlein/NHL-Score-Ticker.git
cd NHL-Score-Ticker
```

Already downloaded it before? Get the latest instead:

```powershell
cd $HOME\NHL-Score-Ticker
git pull
```

## 2. Build the web page

Do this once now, and again after every update.

```powershell
cd $HOME\NHL-Score-Ticker\ui
npm install
npm run build
```

## 3. Turn the board on

The ticker's settings live in a file called `.env` in the `engine` folder. It doesn't come
with the download; this makes it from the example and opens it in Notepad:

```powershell
cd $HOME\NHL-Score-Ticker\engine
if (!(Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

In Notepad:

1. Change `TICKER_BOARD_ENABLED=false` to `TICKER_BOARD_ENABLED=true`
2. Check `TICKER_BOARD_HOST` is the board's IP address (the board prints it on the Arduino
   serial monitor when it starts). It's set to `192.168.68.82` already.
3. Save (Ctrl+S) and close Notepad.

## 4. Test the board

```powershell
cd $HOME\NHL-Score-Ticker\engine
uv run nhl-ticker send "Hello"
```

"Hello" should appear on the board. The first run takes a minute while it sets itself up.

## 5. Start the ticker

```powershell
cd $HOME\NHL-Score-Ticker\engine
uv run nhl-ticker
```

Near the top you should see **`board sink enabled -> tcp://192.168.68.82:8080`**. Scores
start on the board within a few seconds whenever games are on.

- Open **http://127.0.0.1:8000** in a browser to see an on-screen copy of the board.
- Leave the PowerShell window open while it runs. **Ctrl+C** stops it.

## Every time after

```powershell
cd $HOME\NHL-Score-Ticker\engine
uv run nhl-ticker
```

## Getting updates

```powershell
cd $HOME\NHL-Score-Ticker
git pull
```

Then do step 2 again. Your `.env` settings are kept.

## If something's wrong

| You see | What it means |
|---|---|
| `board disabled: set TICKER_BOARD_ENABLED=true ...` | Step 3 didn't stick. Reopen `.env`, make sure it says `true`, and save. |
| `board write failed` | The ticker can't reach the board: the IP in `.env` is wrong, the board is off, or this computer isn't on the same WiFi. |
| `git` / `npm` / `uv` "is not recognized" | Close PowerShell and open a new one after step 0. |
| No errors, but nothing on the board | There may be no games right now. On the web page, press **Fake goal** to test. |

**Goal horns (optional):** in `.env`, set `TICKER_HORN_ENABLED=true`, then start the ticker
with `uv run --extra horn nhl-ticker` instead. Each team's horn plays through the computer's
speakers.
