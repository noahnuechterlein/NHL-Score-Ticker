import type { Game } from "../types";

function statusLabel(game: Game): string {
  if (game.isOver) {
    if (game.periodType === "OT") return "FINAL/OT";
    if (game.periodType === "SO") return "FINAL/SO";
    return "FINAL";
  }
  if (!game.hasStarted) {
    return new Date(game.startTimeUTC).toLocaleTimeString([], {
      hour: "numeric",
      minute: "2-digit",
    });
  }
  if (game.inIntermission) return `INT ${game.period}`;
  return `P${game.period} ${game.clock ?? ""}`.trim();
}

function TeamRow({ team, winner }: { team: Game["home"]; winner: boolean }) {
  return (
    <div className="flex items-center gap-2">
      <span
        className="h-3 w-1 shrink-0 rounded-full"
        style={{ backgroundColor: team.color }}
        aria-hidden
      />
      <span className={`w-10 font-mono text-sm ${winner ? "text-zinc-100" : "text-zinc-400"}`}>
        {team.abbrev}
      </span>
      <span
        className={`ml-auto font-mono text-sm tabular-nums ${
          winner ? "text-zinc-100" : "text-zinc-500"
        }`}
      >
        {team.score}
      </span>
    </div>
  );
}

export default function GameList({ games, date }: { games: Game[]; date: string | null }) {
  if (games.length === 0) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-6 text-center text-sm text-zinc-500">
        No games on the slate.
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between">
        <h2 className="text-xs font-medium uppercase tracking-wider text-zinc-500">Games</h2>
        <span className="font-mono text-[11px] text-zinc-600">{date}</span>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        {games.map((game) => (
          <div
            key={game.id}
            className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-3 transition-colors hover:border-zinc-700"
          >
            <div className="mb-2 flex items-center justify-between">
              <span
                className={`font-mono text-[11px] ${
                  game.isLive ? "text-emerald-400" : "text-zinc-500"
                }`}
              >
                {statusLabel(game)}
              </span>
              {game.isLive && (
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" />
              )}
            </div>
            <TeamRow team={game.away} winner={game.away.score >= game.home.score} />
            <TeamRow team={game.home} winner={game.home.score >= game.away.score} />
          </div>
        ))}
      </div>
    </div>
  );
}
