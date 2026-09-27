import { useEffect, useState } from "react";
import { Eraser, RefreshCw, Repeat, Volume2, VolumeX, Zap } from "lucide-react";
import EventLog from "./components/EventLog";
import GameList from "./components/GameList";
import LedBoard from "./components/LedBoard";
import QueueInspector from "./components/QueueInspector";
import { markersIn } from "./payload";
import { useHorn } from "./useHorn";
import { useTicker } from "./useTicker";

function Pill({ on, label }: { on: boolean; label: string }) {
  return (
    <span
      className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${
        on
          ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
          : "border-zinc-700 bg-zinc-800/50 text-zinc-500"
      }`}
    >
      {label}
    </span>
  );
}

function Button({
  onClick,
  icon: Icon,
  children,
  disabled,
}: {
  onClick: () => void;
  icon: typeof Zap;
  children: React.ReactNode;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="inline-flex items-center gap-1.5 rounded-md border border-zinc-700 bg-zinc-800/60 px-2.5 py-1.5 text-xs text-zinc-300 transition-colors hover:border-zinc-600 hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-40"
    >
      <Icon size={13} />
      {children}
    </button>
  );
}

export default function App() {
  const ticker = useTicker();
  const horn = useHorn(ticker.hornMaxSeconds);
  const [team, setTeam] = useState("");
  const [trueBrightness, setTrueBrightness] = useState(false);

  // Fires on every goal the engine reports, including injected test goals. Timed with the
  // event rather than the board message, matching when the engine's own horn sounds.
  useEffect(() => {
    if (ticker.lastGoal) horn.play(ticker.lastGoal.team);
    // horn.play is stable; depending on it would re-fire on every volume change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticker.lastGoal]);

  const payload = ticker.board?.payload ?? "";
  const teamsPlaying = Array.from(
    new Set(ticker.games.flatMap((g) => [g.away.abbrev, g.home.abbrev])),
  ).sort();

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6">
      <header className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold tracking-tight">NHL Score Ticker</h1>
        <div className="flex items-center gap-1.5">
          <Pill on={ticker.connected} label={ticker.connected ? "connected" : "offline"} />
          <Pill
            on={ticker.boardEnabled && ticker.boardOnline !== false}
            label={
              ticker.boardEnabled && ticker.boardOnline === false ? "board unreachable" : "board"
            }
          />
          <Pill on={ticker.hornEnabled} label="horn" />
        </div>
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={horn.toggle}
            title={horn.enabled ? "Mute goal horns" : "Play goal horns in this browser"}
            className={`inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs transition-colors ${
              horn.enabled
                ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/20"
                : "border-zinc-700 bg-zinc-800/60 text-zinc-400 hover:border-zinc-600"
            }`}
          >
            {horn.enabled ? <Volume2 size={13} /> : <VolumeX size={13} />}
            {horn.enabled ? "Horns on" : "Horns off"}
          </button>
          {horn.enabled && (
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={horn.volume}
              onChange={(event) => horn.setVolume(Number(event.target.value))}
              title={`Volume ${Math.round(horn.volume * 100)}%`}
              className="h-1 w-20 accent-emerald-500"
            />
          )}
          <span className="font-mono text-[11px] text-zinc-600">
            polling every {ticker.pollSeconds}s
          </span>
        </div>
      </header>

      {horn.blocked && (
        <div className="rounded-lg border border-amber-900/50 bg-amber-950/30 px-3 py-2 text-xs text-amber-300">
          The browser blocked audio. Click anywhere on the page, then try a goal again.
        </div>
      )}

      {ticker.error && (
        <div className="flex items-start gap-2 rounded-lg border border-red-900/50 bg-red-950/30 px-3 py-2 text-xs text-red-300">
          <span className="flex-1">{ticker.error}</span>
          <button onClick={ticker.clearError} className="text-red-400/60 hover:text-red-300">
            dismiss
          </button>
        </div>
      )}

      {/* The emulator receives the exact payload the hardware would. */}
      <section className="space-y-3">
        <div className="flex items-baseline justify-between">
          <h2 className="text-xs font-medium uppercase tracking-wider text-zinc-500">
            LED board emulator
          </h2>
          <div className="flex items-center gap-3">
            <label
              className="flex cursor-pointer items-center gap-1.5 text-[11px] text-zinc-500 hover:text-zinc-400"
              title="Paint the dimmed values the LEDs actually receive. Colours on screen are boosted by default because the board's 0x30 brightness leaves them near-black on a monitor; the payload is identical either way."
            >
              <input
                type="checkbox"
                checked={trueBrightness}
                onChange={(event) => setTrueBrightness(event.target.checked)}
                className="h-3 w-3 accent-zinc-500"
              />
              true LED brightness
            </label>
            {ticker.board && (
              <span className="font-mono text-[11px] text-zinc-600">
                {ticker.board.kind} · hold {ticker.board.holdSeconds.toFixed(1)}s
              </span>
            )}
          </div>
        </div>

        <div className="overflow-x-auto">
          <LedBoard
            payload={payload}
            messageId={ticker.board?.sentAt}
            frameMs={ticker.boardFrameMs}
            trueBrightness={trueBrightness}
          />
        </div>

        <div className="space-y-1.5">
          <p className="break-all rounded border border-zinc-800/70 bg-zinc-900/30 px-2 py-1.5 font-mono text-[10px] text-zinc-500">
            {payload || "(nothing sent yet)"}
          </p>
          {payload && (
            <div className="flex flex-wrap items-center gap-1.5">
              {markersIn(payload).map((m, index) => (
                <span
                  key={index}
                  className="inline-flex items-center gap-1 rounded border border-zinc-800 bg-zinc-900/40 px-1.5 py-0.5 font-mono text-[10px] text-zinc-500"
                  title={`Marker ${m.raw} — the LEDs receive ${m.ledCss} after the sketch folds in brightness: (channel * br) >> 8. The swatch shows the nominal colour so it is visible on screen.`}
                >
                  <span
                    className="h-2.5 w-2.5 rounded-sm"
                    style={{
                      backgroundColor: m.nominalCss,
                      boxShadow: `0 0 4px ${m.nominalCss}`,
                    }}
                  />
                  {m.raw}
                </span>
              ))}
            </div>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <select
            value={team}
            onChange={(event) => setTeam(event.target.value)}
            className="rounded-md border border-zinc-700 bg-zinc-800/60 px-2 py-1.5 text-xs text-zinc-300"
          >
            <option value="">random team</option>
            {teamsPlaying.map((abbrev) => (
              <option key={abbrev} value={abbrev}>
                {abbrev}
              </option>
            ))}
          </select>
          <Button
            onClick={() => ticker.fakeGoal(team || undefined)}
            icon={Zap}
            disabled={ticker.games.length === 0}
          >
            Fake goal
          </Button>
          <Button onClick={() => ticker.replayBoard()} icon={Repeat} disabled={!ticker.board}>
            Replay
          </Button>
          <Button onClick={() => ticker.forcePoll()} icon={RefreshCw}>
            Poll now
          </Button>
          <Button onClick={() => ticker.clearBoard()} icon={Eraser}>
            Clear
          </Button>
        </div>
      </section>

      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <div className="space-y-6">
          <GameList games={ticker.games} date={ticker.date} />
          <EventLog events={ticker.events} />
        </div>
        <QueueInspector queue={ticker.queue} />
      </div>
    </div>
  );
}
