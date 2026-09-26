// Single WebSocket connection to the engine, with reconnect.

import { useCallback, useEffect, useRef, useState } from "react";
import type { Game, QueueState, ServerMessage, TickerEvent } from "./types";

const RECONNECT_MS = 2000;
const MAX_LOG = 40;

/** The most recent goal, carrying a unique `at` so two goals by the same team both fire. */
export interface GoalPing {
  team: string;
  scorer: string;
  at: number;
}

export interface BoardState {
  payload: string;
  text: string;
  kind: string;
  holdSeconds: number;
  /** Identity of this particular write. Replaying the same text must still restart the
   *  scroll, so the emulator keys off this rather than off the payload string. */
  sentAt: number;
}

const EMPTY_QUEUE: QueueState = {
  busy: false,
  busySecondsRemaining: 0,
  current: null,
  pending: [],
  summaryPages: 0,
  summaryPage: 0,
};

export function useTicker() {
  const [connected, setConnected] = useState(false);
  const [games, setGames] = useState<Game[]>([]);
  const [date, setDate] = useState<string | null>(null);
  const [queue, setQueue] = useState<QueueState>(EMPTY_QUEUE);
  const [board, setBoard] = useState<BoardState | null>(null);
  const [events, setEvents] = useState<TickerEvent[]>([]);
  const [pollSeconds, setPollSeconds] = useState(0);
  const [boardEnabled, setBoardEnabled] = useState(false);
  const [boardFrameMs, setBoardFrameMs] = useState(33);
  const [boardOnline, setBoardOnline] = useState<boolean | null>(null);
  const [hornEnabled, setHornEnabled] = useState(false);
  const [hornMaxSeconds, setHornMaxSeconds] = useState(10);
  const [lastGoal, setLastGoal] = useState<GoalPing | null>(null);
  const [error, setError] = useState<string | null>(null);

  const socketRef = useRef<WebSocket | null>(null);
  const retryRef = useRef<number | undefined>(undefined);

  useEffect(() => {
    let closed = false;

    const connect = () => {
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const socket = new WebSocket(`${protocol}//${window.location.host}/ws`);
      socketRef.current = socket;

      socket.onopen = () => setConnected(true);
      socket.onclose = () => {
        setConnected(false);
        if (!closed) retryRef.current = window.setTimeout(connect, RECONNECT_MS);
      };
      socket.onmessage = (raw) => {
        const message = JSON.parse(raw.data) as ServerMessage;
        switch (message.type) {
          case "snapshot":
            setDate(message.date);
            setGames(message.games);
            setQueue(message.queue);
            setPollSeconds(message.pollSeconds);
            setBoardEnabled(message.boardEnabled);
            setBoardFrameMs(message.boardFrameMs);
            setBoardOnline(message.boardOnline);
            setHornEnabled(message.hornEnabled);
            setHornMaxSeconds(message.hornMaxSeconds);
            setError(message.lastError);
            break;
          case "scoreboard":
            setDate(message.date);
            setGames(message.games);
            setPollSeconds(message.pollSeconds);
            setError(null);
            break;
          case "queue":
            setQueue({
              busy: message.busy,
              busySecondsRemaining: message.busySecondsRemaining,
              current: message.current,
              pending: message.pending,
              summaryPages: message.summaryPages,
              summaryPage: message.summaryPage,
            });
            break;
          case "boardMessage":
            setBoard({
              payload: message.payload,
              text: message.text,
              kind: message.kind,
              holdSeconds: message.holdSeconds,
              sentAt: message.sentAt,
            });
            break;
          case "boardClear":
            setBoard(null);
            break;
          case "event": {
            const { type: _type, ...event } = message;
            setEvents((previous) => [event, ...previous].slice(0, MAX_LOG));
            if (event.kind === "GoalEvent" && event.team) {
              // Distinct object per goal, so an effect fires even for a repeat scorer.
              setLastGoal({
                team: event.team,
                scorer: event.scorer ?? "",
                at: Date.now(),
              });
            }
            break;
          }
          case "error":
            setError(message.message);
            break;
        }
      };
    };

    connect();
    return () => {
      closed = true;
      window.clearTimeout(retryRef.current);
      socketRef.current?.close();
    };
  }, []);

  const post = useCallback(async (path: string) => {
    const response = await fetch(path, { method: "POST" });
    if (!response.ok) {
      const body = await response.json().catch(() => ({ detail: response.statusText }));
      setError(body.detail ?? "request failed");
    }
    return response;
  }, []);

  return {
    connected,
    date,
    games,
    queue,
    board,
    events,
    pollSeconds,
    boardEnabled,
    boardFrameMs,
    boardOnline,
    hornEnabled,
    hornMaxSeconds,
    lastGoal,
    error,
    clearError: () => setError(null),
    fakeGoal: (team?: string) =>
      post(`/api/fake-goal${team ? `?team=${encodeURIComponent(team)}` : ""}`),
    forcePoll: () => post("/api/poll"),
    clearBoard: () => post("/api/clear"),
    // A fresh sentAt is what makes the emulator restart; copying the object alone left
    // the payload string unchanged, so the scroll never reset and Replay did nothing.
    replayBoard: (): void => {
      setBoard((current) => (current ? { ...current, sentAt: Date.now() / 1000 } : current));
    },
  };
}
