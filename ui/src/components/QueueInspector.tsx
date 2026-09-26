// What the board is doing right now, and what is waiting behind it.
//
// The busy bar matters: the sketch refuses new writes while it is scrolling, so this is
// a direct view of why a queued message has not appeared yet.

import type { QueueState } from "../types";

const KIND_STYLE: Record<string, string> = {
  GoalEvent: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  GameEndEvent: "bg-sky-500/15 text-sky-300 border-sky-500/30",
  GameStartEvent: "bg-violet-500/15 text-violet-300 border-violet-500/30",
  SummaryTick: "bg-zinc-700/30 text-zinc-400 border-zinc-700",
};

function KindChip({ kind }: { kind: string }) {
  const style = KIND_STYLE[kind] ?? "bg-zinc-700/30 text-zinc-400 border-zinc-700";
  return (
    <span className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] font-medium ${style}`}>
      {kind.replace(/Event|Tick/, "")}
    </span>
  );
}

export default function QueueInspector({ queue }: { queue: QueueState }) {
  return (
    <div className="space-y-3">
      <h2 className="text-xs font-medium uppercase tracking-wider text-zinc-500">Board queue</h2>

      <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-3">
        <div className="mb-2 flex items-center gap-2">
          <span
            className={`h-2 w-2 rounded-full ${
              queue.busy ? "animate-pulse bg-amber-400" : "bg-zinc-600"
            }`}
          />
          <span className="text-xs text-zinc-400">
            {queue.busy ? "Busy" : "Ready"}
          </span>
          {queue.busy && (
            <span className="ml-auto font-mono text-[11px] tabular-nums text-amber-400/80">
              {queue.busySecondsRemaining.toFixed(1)}s left
            </span>
          )}
        </div>

        {queue.current ? (
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <KindChip kind={queue.current.kind} />
              <span className="font-mono text-[11px] text-zinc-600">
                hold {queue.current.holdSeconds.toFixed(1)}s
              </span>
            </div>
            <p className="truncate font-mono text-xs text-zinc-300">{queue.current.text}</p>
          </div>
        ) : (
          <p className="text-xs text-zinc-600">Nothing displayed.</p>
        )}
      </div>

      {queue.summaryPages > 1 && (
        <div className="flex items-center justify-between rounded border border-zinc-800/70 bg-zinc-900/30 px-2 py-1.5">
          <span className="text-[11px] text-zinc-500">Slate cycle</span>
          <span className="font-mono text-[11px] text-zinc-400">
            page {queue.summaryPage + 1} of {queue.summaryPages}
          </span>
        </div>
      )}

      <div className="space-y-1">
        <div className="flex items-baseline justify-between">
          <span className="text-[11px] uppercase tracking-wider text-zinc-600">Pending</span>
          <span className="font-mono text-[11px] text-zinc-600">{queue.pending.length}</span>
        </div>
        {queue.pending.length === 0 ? (
          <p className="text-xs text-zinc-700">Empty.</p>
        ) : (
          <ul className="space-y-1">
            {queue.pending.map((item, index) => (
              <li
                key={`${item.kind}-${index}`}
                className="flex items-center gap-2 rounded border border-zinc-800/70 bg-zinc-900/30 px-2 py-1.5"
              >
                <KindChip kind={item.kind} />
                <span className="truncate font-mono text-[11px] text-zinc-400">{item.text}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
