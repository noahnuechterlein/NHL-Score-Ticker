// Parsing the board payload, mirroring `ledText::parseText` in LEDWebText.ino.
//
// The engine sends the emulator the *same string* it sends the hardware, so this parser
// has to agree with the sketch's byte for byte -- including its quirks.

export interface BoardChar {
  ch: string;
  r: number;
  g: number;
  b: number;
}

/** The sketch initialises colour to (10, 10, 10) before it reads any marker. */
const DEFAULT_CHANNEL = 10;

/** '~' plus eight hex digits: RRGGBB then a brightness byte. */
export const MARKER_LEN = 9;

function hex(text: string, from: number, to: number): number {
  const value = parseInt(text.slice(from, to), 16);
  return Number.isNaN(value) ? 0 : value;
}

/**
 * Expand a payload into per-character colours.
 *
 * Brightness is folded in the way the sketch does it -- `(channel * br) >> 8` -- which is
 * why a nominal #ff0000 at brightness 0x30 reaches the LEDs as (47, 0, 0). The emulator
 * reproduces that rather than showing the nominal colour, so what you see on screen is
 * what the board would actually light up.
 */
export function parsePayload(payload: string): BoardChar[] {
  const out: BoardChar[] = [];
  let r = DEFAULT_CHANNEL;
  let g = DEFAULT_CHANNEL;
  let b = DEFAULT_CHANNEL;

  for (let i = 0; i < payload.length; i++) {
    if (payload[i] === "~") {
      // parseText only reads the marker if the whole thing is present, but it advances
      // past it either way -- a truncated marker at the end swallows the remainder.
      if (i + MARKER_LEN <= payload.length) {
        const br = hex(payload, i + 7, i + 9);
        r = (hex(payload, i + 1, i + 3) * br) >> 8;
        g = (hex(payload, i + 3, i + 5) * br) >> 8;
        b = (hex(payload, i + 5, i + 7) * br) >> 8;
      }
      i += MARKER_LEN - 1; // the loop's i++ supplies the ninth
      continue;
    }
    out.push({ ch: payload[i], r, g, b });
  }
  return out;
}

/** The payload with colour markers removed -- what a person reads off the board. */
export function plainText(payload: string): string {
  return parsePayload(payload)
    .map((c) => c.ch)
    .join("");
}

/** Colour markers in the order they appear, for the payload inspector. */
export function markersIn(payload: string): { raw: string; css: string }[] {
  const found: { raw: string; css: string }[] = [];
  for (let i = 0; i < payload.length; i++) {
    if (payload[i] !== "~") continue;
    const raw = payload.slice(i, i + MARKER_LEN);
    if (raw.length === MARKER_LEN) {
      const br = hex(raw, 7, 9);
      const r = (hex(raw, 1, 3) * br) >> 8;
      const g = (hex(raw, 3, 5) * br) >> 8;
      const b = (hex(raw, 5, 7) * br) >> 8;
      found.push({ raw, css: `rgb(${r}, ${g}, ${b})` });
    }
    i += MARKER_LEN - 1;
  }
  return found;
}
