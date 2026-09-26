// Wire types for the engine's WebSocket feed and REST endpoints.
//
// Hand-maintained to match nhl_ticker/core/serialize.py and runner.py. Kept small and in
// one place so the two sides are easy to diff by eye.

export interface TeamSide {
  abbrev: string;
  name: string;
  score: number;
  sog: number;
  color: string;
}

export interface Game {
  id: number;
  state: string;
  hasStarted: boolean;
  isLive: boolean;
  isOver: boolean;
  period: number | null;
  periodType: string | null;
  clock: string | null;
  inIntermission: boolean;
  startTimeUTC: string;
  home: TeamSide;
  away: TeamSide;
  goalCount: number;
}

export interface QueueItem {
  kind: string;
  text: string;
}

export interface QueueState {
  busy: boolean;
  busySecondsRemaining: number;
  current: { text: string; kind: string; holdSeconds: number } | null;
  pending: QueueItem[];
  /** How many pages the slate summary splits into, and which one comes next. */
  summaryPages: number;
  summaryPage: number;
}

export interface TickerEvent {
  kind: string;
  priority: number;
  payload: string;
  text: string;
  gameId?: number;
  scorer?: string;
  team?: string;
  teamColor?: string;
  strength?: string;
  period?: number;
  timeInPeriod?: string;
  assists?: string[];
  backfilled?: boolean;
  highlightUrl?: string | null;
}

export interface Snapshot {
  date: string | null;
  games: Game[];
  queue: QueueState;
  pollSeconds: number;
  boardEnabled: boolean;
  hornEnabled: boolean;
  lastError: string | null;
}

export type ServerMessage =
  | ({ type: "snapshot" } & Snapshot)
  | { type: "scoreboard"; date: string; games: Game[]; pollSeconds: number }
  | ({ type: "event" } & TickerEvent)
  | { type: "board"; payload: string }
  | { type: "boardClear" }
  | {
      type: "boardMessage";
      payload: string;
      text: string;
      kind: string;
      holdSeconds: number;
      sentAt: number;
    }
  | ({ type: "queue" } & QueueState)
  | { type: "error"; message: string };
