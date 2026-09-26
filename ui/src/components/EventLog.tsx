import type { TickerEvent } from "../types";

function strengthLabel(strength?: string): string | null {
  if (strength === "pp") return "PP";
  if (strength === "sh") return "SH";
  if (strength === "en") return "EN";
  return null;
}

export default function EventLog({ events }: { events: TickerEvent[] }) {
  return (
    <div className="space-y-2">
      <h2 className="text-xs font-medium uppercase tracking-wider text-zinc-500">Events</h2>
      {events.length === 0 ? (
        <p className="text-xs text-zinc-700">Nothing yet. Fire a test goal to see the chain run.</p>
      ) : (
        <ul className="max-h-72 space-y-1 overflow-y-auto pr-1">
          {events.map((event, index) => (
            <li
              key={index}
              className="flex items-start gap-2 rounded border border-zinc-800/70 bg-zinc-900/30 px-2 py-1.5"
            >
              {event.teamColor && (
                <span
                  className="mt-1 h-2.5 w-1 shrink-0 rounded-full"
                  style={{ backgroundColor: event.teamColor }}
                />
              )}
              <div className="min-w-0 flex-1">
                <p className="truncate font-mono text-[11px] text-zinc-300">{event.text}</p>
                {event.kind === "GoalEvent" && (
                  <p className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[10px] text-zinc-600">
                    <span>
                      P{event.period} {event.timeInPeriod}
                    </span>
                    {strengthLabel(event.strength) && (
                      <span className="text-amber-500/70">{strengthLabel(event.strength)}</span>
                    )}
                    {event.assists && event.assists.length > 0 && (
                      <span className="truncate">assists: {event.assists.join(", ")}</span>
                    )}
                    {event.backfilled && (
                      <span className="text-sky-400/70" title="Several goals landed between polls">
                        caught up
                      </span>
                    )}
                  </p>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
