// Single WebSocket connection to the engine, with reconnect.

import { useCallback, useEffect, useRef, useState } from "react";
import type { Game, QueueState, ServerMessage, TickerEvent } from "./types";

const RECONNECT_MS = 2000;
const MAX_LOG = 40;

export interface BoardState {
  payload: string;
  text: string;
  kind: string;
  holdSeconds: number;
}

const EMPTY_QUEUE: QueueState = {
  busy: false,
  busySecondsRemaining: 0,
  current: null,
  pending: [],
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
  const [hornEnabled, setHornEnabled] = useState(false);
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
            setHornEnabled(message.hornEnabled);
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
            });
            break;
          case "boardMessage":
            setBoard({
              payload: message.payload,
              text: message.text,
              kind: message.kind,
              holdSeconds: message.holdSeconds,
            });
            break;
          case "boardClear":
            setBoard(null);
            break;
          case "event": {
            const { type: _type, ...event } = message;
            setEvents((previous) => [event, ...previous].slice(0, MAX_LOG));
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
    hornEnabled,
    error,
    clearError: () => setError(null),
    fakeGoal: (team?: string) =>
      post(`/api/fake-goal${team ? `?team=${encodeURIComponent(team)}` : ""}`),
    forcePoll: () => post("/api/poll"),
    clearBoard: () => post("/api/clear"),
    replayBoard: () => board && setBoard({ ...board }),
  };
}
