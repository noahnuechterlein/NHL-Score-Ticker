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
 * Brightness is folded the way the sketch does it -- `(channel * br) >> 8` -- which is
 * why a nominal #ff0000 at brightness 0x30 reaches the LEDs as (47, 0, 0).
 */
function decodeMarker(raw: string): { r: number; g: number; b: number } | null {
  if (raw.length < MARKER_LEN) return null;
  const br = hex(raw, 7, 9);
  return {
    r: (hex(raw, 1, 3) * br) >> 8,
    g: (hex(raw, 3, 5) * br) >> 8,
    b: (hex(raw, 5, 7) * br) >> 8,
  };
}

/**
 * Walk a payload, yielding each colour marker and each visible character in order.
 *
 * `parseText` only reads a marker if the whole thing is present, but it advances past it
 * either way -- a truncated marker at the end swallows the remainder. Parsing and marker
 * extraction both used to re-implement this walk; sharing it keeps them from drifting.
 */
function* walk(payload: string): Generator<{ marker: string } | { ch: string }> {
  for (let i = 0; i < payload.length; i++) {
    if (payload[i] === "~") {
      yield { marker: payload.slice(i, i + MARKER_LEN) };
      i += MARKER_LEN - 1; // the loop's i++ supplies the ninth
      continue;
    }
    yield { ch: payload[i] };
  }
}

/**
 * Expand a payload into per-character colours.
 *
 * The emulator reproduces the post-brightness values rather than the nominal ones, so
 * what you see on screen is what the board would actually light up.
 */
export function parsePayload(payload: string): BoardChar[] {
  const out: BoardChar[] = [];
  let r = DEFAULT_CHANNEL;
  let g = DEFAULT_CHANNEL;
  let b = DEFAULT_CHANNEL;

  for (const step of walk(payload)) {
    if ("marker" in step) {
      const decoded = decodeMarker(step.marker);
      if (decoded) ({ r, g, b } = decoded);
      continue;
    }
    out.push({ ch: step.ch, r, g, b });
  }
  return out;
}

/** The payload with colour markers removed -- what a person reads off the board. */
export function plainText(payload: string): string {
  let out = "";
  for (const step of walk(payload)) if ("ch" in step) out += step.ch;
  return out;
}

/** Colour markers in the order they appear, for the payload inspector. */
export function markersIn(payload: string): { raw: string; css: string }[] {
  const found: { raw: string; css: string }[] = [];
  for (const step of walk(payload)) {
    if (!("marker" in step)) continue;
    const decoded = decodeMarker(step.marker);
    if (decoded) {
      found.push({
        raw: step.marker,
        css: `rgb(${decoded.r}, ${decoded.g}, ${decoded.b})`,
      });
    }
  }
  return found;
}
