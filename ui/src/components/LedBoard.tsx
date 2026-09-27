// Pixel-accurate emulator for the 22-character WS2812 board.
//
// The scroll here is a direct transcription of `ledTextDisplay()` in LEDWebText.ino: one
// pixel column per frame, wrapping at (length + 3) * COLS, with the tail of the message
// reappearing from the left once the buffer runs past the end. Messages that fit inside
// the 22-character window do not scroll at all, exactly as on the hardware.

import { useEffect, useMemo, useRef, useState } from "react";
import { displayRgb, rgbCss } from "../display";
import { CELL_COLS, GLYPH_COLS, ROW_MASK_TOP_DOWN, ROWS, glyphFor } from "../font";
import { parsePayload, type BoardChar } from "../payload";

/** CHARS in the sketch: how many character cells are physically on the board. */
const VISIBLE_CHARS = 22;

/** Blank cells the sketch inserts before the message wraps. */
const SCROLL_GAP = 3;

const PIXEL_SIZE = 7;
const PIXEL_GAP = 2;

interface Props {
  payload: string;
  /** Changes on every board write, including a replay of identical text. */
  messageId?: number;
  /** Milliseconds per frame. Comes from the engine's board_frame_ms so the emulator and
   *  the hardware stay in step once that is calibrated. */
  frameMs?: number;
  /** Paint the dimmed values the LEDs actually receive, rather than the legible boost.
   *  Useful when comparing against real hardware. */
  trueBrightness?: boolean;
  paused?: boolean;
}

export default function LedBoard({
  payload,
  messageId = 0,
  frameMs = 33,
  trueBrightness = false,
  paused = false,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [offset, setOffset] = useState(0);

  const chars: BoardChar[] = useMemo(() => parsePayload(payload), [payload]);
  const scrolls = chars.length > VISIBLE_CHARS;
  const cycleColumns = (chars.length + SCROLL_GAP) * CELL_COLS;

  // Derive the scroll position from elapsed wall-clock time rather than counting timer
  // ticks.
  //
  // Counting ticks loses time whenever a frame runs long -- a canvas redraw at 30fps
  // easily costs more than the 33ms budget -- and the loss accumulates. Meanwhile the
  // engine holds the board for a fixed number of *seconds*, so a slow emulator got its
  // message swapped out before the text had finished scrolling. Only about 15% slippage
  // is enough to truncate it.
  //
  // Reading the clock makes dropped frames irrelevant: the scroll may stutter, but it
  // always sits where it should for the time elapsed, and finishes when the board does.
  // (requestAnimationFrame pauses in a hidden tab, which is fine -- on return, elapsed
  // time jumps the scroll straight to the right position.)
  //
  // Restarting keys on messageId as well as the text, so replaying an identical message
  // still starts the scroll over.
  useEffect(() => {
    if (!scrolls || paused) {
      setOffset(0);
      return;
    }
    const columns = Math.max(1, cycleColumns);
    const startedAt = performance.now();
    let frame = 0;
    const step = (now: number) => {
      setOffset(Math.floor((now - startedAt) / frameMs) % columns);
      frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [scrolls, paused, frameMs, cycleColumns, payload, messageId]);

  const pitch = PIXEL_SIZE + PIXEL_GAP;
  const width = VISIBLE_CHARS * CELL_COLS * pitch;
  const height = ROWS * pitch;

  // Sizing is its own effect: assigning canvas.width resets the whole canvas, and doing
  // that inside the draw effect meant reallocating it on every frame, 30 times a second.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    canvas.getContext("2d")?.setTransform(dpr, 0, 0, dpr, 0, 0);
  }, [width, height]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    ctx.fillStyle = "#09090b";
    ctx.fillRect(0, 0, width, height);

    for (let column = 0; column < VISIBLE_CHARS * CELL_COLS; column++) {
      const absolute = offset + column;
      const charIndex = Math.floor(absolute / CELL_COLS);
      const pixelColumn = absolute % CELL_COLS;

      let cell: BoardChar | undefined;
      if (charIndex < chars.length) {
        cell = chars[charIndex];
      } else if (charIndex >= chars.length + SCROLL_GAP && scrolls) {
        // The sketch wraps the head of the message back into view behind the gap.
        cell = chars[charIndex - chars.length - SCROLL_GAP];
      }

      for (let row = 0; row < ROWS; row++) {
        const x = column * pitch;
        const y = row * pitch;

        // The sketch never lights the last column of a cell: it is the letter spacing.
        const lit =
          cell !== undefined &&
          pixelColumn < CELL_COLS - 1 &&
          (glyphFor(cell.ch)[pixelColumn] & ROW_MASK_TOP_DOWN[row]) !== 0;

        if (lit) {
          // Display-only transform; the payload and the LED values are untouched.
          const css = rgbCss(displayRgb(cell!, trueBrightness));
          ctx.fillStyle = css;
          ctx.shadowColor = css;
          ctx.shadowBlur = trueBrightness ? 6 : 8;
        } else {
          ctx.fillStyle = "#18181b";
          ctx.shadowBlur = 0;
        }
        ctx.beginPath();
        ctx.arc(x + PIXEL_SIZE / 2, y + PIXEL_SIZE / 2, PIXEL_SIZE / 2, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    ctx.shadowBlur = 0;
  }, [chars, offset, scrolls, trueBrightness]);

  const progress = scrolls ? Math.round((offset / Math.max(1, cycleColumns)) * 100) : 100;

  return (
    <div className="space-y-2">
      <div className="inline-block rounded-lg border border-zinc-800 bg-zinc-950 p-3 shadow-inner">
        <canvas ref={canvasRef} className="block" />
      </div>
      <div className="flex items-center gap-3 font-mono text-[11px] text-zinc-500">
        <span>
          {chars.length} chars · {GLYPH_COLS}x{ROWS} glyphs · {VISIBLE_CHARS} cells
        </span>
        {scrolls ? (
          <span className="text-amber-500/80">scrolling {progress}%</span>
        ) : (
          <span className="text-zinc-600">static (fits the window)</span>
        )}
      </div>
    </div>
  );
}
