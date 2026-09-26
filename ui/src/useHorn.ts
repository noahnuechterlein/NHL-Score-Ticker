// Goal horns in the browser.
//
// Independent of the engine's own speaker (TICKER_HORN_ENABLED): that plays on whatever
// machine is driving the board, which is usually not the machine you are watching this on.
//
// Browsers block audio until the user has interacted with the page, so this cannot simply
// default to on -- the first goal would be silently swallowed. The toggle doubles as that
// interaction, and priming a muted play on enable unlocks playback for the goals that
// follow.

import { useCallback, useEffect, useRef, useState } from "react";

const STORAGE_ENABLED = "nhl-ticker.horn.enabled";
const STORAGE_VOLUME = "nhl-ticker.horn.volume";

function stored<T>(key: string, fallback: T, parse: (raw: string) => T): T {
  try {
    const raw = window.localStorage.getItem(key);
    return raw === null ? fallback : parse(raw);
  } catch {
    return fallback; // private browsing, storage disabled, etc.
  }
}

export function useHorn(maxSeconds = 10) {
  const [enabled, setEnabled] = useState(() =>
    stored(STORAGE_ENABLED, false, (raw) => raw === "true"),
  );
  const [volume, setVolume] = useState(() =>
    stored(STORAGE_VOLUME, 0.7, (raw) => Math.min(1, Math.max(0, Number(raw) || 0))),
  );
  const [blocked, setBlocked] = useState(false);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const stopTimer = useRef<number | undefined>(undefined);

  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_ENABLED, String(enabled));
      window.localStorage.setItem(STORAGE_VOLUME, String(volume));
    } catch {
      // Not worth surfacing; the toggle still works for this session.
    }
  }, [enabled, volume]);

  const stop = useCallback(() => {
    window.clearTimeout(stopTimer.current);
    const audio = audioRef.current;
    if (audio) {
      audio.pause();
      audio.currentTime = 0;
    }
  }, []);

  /** Play a team's horn. A new goal cuts off the previous one, as the engine's sink does. */
  const play = useCallback(
    (abbrev: string) => {
      if (!enabled || !abbrev) return;
      stop();

      const audio = audioRef.current ?? new Audio();
      audioRef.current = audio;
      audio.src = `/api/horn/${encodeURIComponent(abbrev)}`;
      audio.volume = volume;
      audio.muted = false;

      audio
        .play()
        .then(() => {
          setBlocked(false);
          // Match the engine's cap rather than letting a 30-second file run out.
          stopTimer.current = window.setTimeout(stop, maxSeconds * 1000);
        })
        .catch(() => {
          // Autoplay still blocked: tell the UI rather than failing silently.
          setBlocked(true);
        });
    },
    [enabled, volume, maxSeconds, stop],
  );

  /**
   * Turn sound on, using the click itself to satisfy the autoplay policy.
   *
   * The muted play/pause is the standard unlock: it counts as playback started by a user
   * gesture, so later programmatic play() calls are permitted.
   */
  const toggle = useCallback(() => {
    setEnabled((current) => {
      const next = !current;
      if (next) {
        const audio = audioRef.current ?? new Audio();
        audioRef.current = audio;
        audio.muted = true;
        audio
          .play()
          .then(() => {
            audio.pause();
            audio.currentTime = 0;
            audio.muted = false;
            setBlocked(false);
          })
          .catch(() => {
            // No source loaded yet on some browsers; the first real goal will unlock it.
            audio.muted = false;
          });
      } else {
        stop();
      }
      return next;
    });
  }, [stop]);

  useEffect(() => stop, [stop]);

  return { enabled, toggle, volume, setVolume, blocked, play, stop };
}
