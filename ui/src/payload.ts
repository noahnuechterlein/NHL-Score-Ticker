// Parsing the board payload, mirroring `ledText::parseText` in LEDWebText.ino.
//
// The engine sends the emulator the *same string* it sends the hardware, so this parser
// has to agree with the sketch's byte for byte -- including its quirks.

export interface BoardChar {
  ch: string;
  /** Colour as the LEDs receive it, after the sketch folds brightness in. */
  r: number;
  g: number;
  b: number;
  /** Colour as written in the marker, before that fold. Kept so the renderer can undo
   *  the dimming for the screen without the payload or the LED values changing. */
  nominalR: number;
  nominalG: number;
  nominalB: number;
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
interface Colour {
  r: number;
  g: number;
  b: number;
  nominalR: number;
  nominalG: number;
  nominalB: number;
}

function decodeMarker(raw: string): Colour | null {
  if (raw.length < MARKER_LEN) return null;
  const br = hex(raw, 7, 9);
  const nominalR = hex(raw, 1, 3);
  const nominalG = hex(raw, 3, 5);
  const nominalB = hex(raw, 5, 7);
  return {
    r: (nominalR * br) >> 8,
    g: (nominalG * br) >> 8,
    b: (nominalB * br) >> 8,
    nominalR,
    nominalG,
    nominalB,
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
 * Carries both the post-brightness values the LEDs receive and the nominal values from
 * the marker. The renderer paints the nominal ones lifted for legibility by default --
 * see display.ts -- and the true ones when asked.
 */
export function parsePayload(payload: string): BoardChar[] {
  const out: BoardChar[] = [];
  let colour: Colour = {
    r: DEFAULT_CHANNEL,
    g: DEFAULT_CHANNEL,
    b: DEFAULT_CHANNEL,
    nominalR: DEFAULT_CHANNEL,
    nominalG: DEFAULT_CHANNEL,
    nominalB: DEFAULT_CHANNEL,
  };

  for (const step of walk(payload)) {
    if ("marker" in step) {
      const decoded = decodeMarker(step.marker);
      if (decoded) colour = decoded;
      continue;
    }
    out.push({ ch: step.ch, ...colour });
  }
  return out;
}

/** The payload with colour markers removed -- what a person reads off the board. */
export function plainText(payload: string): string {
  let out = "";
  for (const step of walk(payload)) if ("ch" in step) out += step.ch;
  return out;
}

export interface MarkerInfo {
  raw: string;
  /** What the LEDs receive, after the brightness fold. */
  ledCss: string;
  /** The nominal colour from the marker, for a swatch you can actually see. */
  nominalCss: string;
}

/** Colour markers in the order they appear, for the payload inspector. */
export function markersIn(payload: string): MarkerInfo[] {
  const found: MarkerInfo[] = [];
  for (const step of walk(payload)) {
    if (!("marker" in step)) continue;
    const decoded = decodeMarker(step.marker);
    if (decoded) {
      found.push({
        raw: step.marker,
        ledCss: `rgb(${decoded.r}, ${decoded.g}, ${decoded.b})`,
        nominalCss: `rgb(${decoded.nominalR}, ${decoded.nominalG}, ${decoded.nominalB})`,
      });
    }
  }
  return found;
}
