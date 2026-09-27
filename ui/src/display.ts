// Turning LED-accurate colours into something readable on a monitor.
//
// This is **display only**. It never touches the payload: the bytes sent to the board, the
// colour markers in them, and the inspector's reported values are all unchanged. The board
// itself is dim by design and looks fine in a dark room; a 7px circle on a bright screen
// does not.
//
// Two problems, in order:
//
// 1. The sketch folds brightness in as `(channel * br) >> 8`, so at the default 0x30 every
//    colour arrives at roughly 18% intensity -- Washington's #ff0000 becomes rgb(47,0,0),
//    a luma of 10/255. Dividing that back out recovers the team's actual hex colour.
// 2. Some team colours are genuinely near-black even at full intensity: Chicago's #480000
//    is luma 15 and Los Angeles' #231f20 is 33. Those need lifting regardless of
//    brightness, so the peak channel is scaled up to a floor with the hue left intact.

import type { BoardChar } from "./payload";

/** Lowest acceptable peak channel. Below this a colour reads as "off" on screen. */
const MIN_PEAK = 180;

type Rgb = [number, number, number];

function clamp(value: number): number {
  return Math.max(0, Math.min(255, Math.round(value)));
}

/**
 * The colour to actually paint for a cell.
 *
 * With `trueBrightness`, returns exactly what the LEDs receive -- faithful, and the right
 * choice when checking against real hardware. Otherwise returns the team's nominal colour
 * lifted to be legible, scaling all three channels together so the hue is preserved and
 * teams stay distinguishable.
 */
export function displayRgb(cell: BoardChar, trueBrightness = false): Rgb {
  if (trueBrightness) return [cell.r, cell.g, cell.b];

  const peak = Math.max(cell.nominalR, cell.nominalG, cell.nominalB);
  if (peak === 0) return [0, 0, 0];

  // Scaling by a single factor keeps the ratios between channels, so #480000 brightens
  // into a red rather than drifting toward white.
  const gain = peak < MIN_PEAK ? MIN_PEAK / peak : 1;
  return [
    clamp(cell.nominalR * gain),
    clamp(cell.nominalG * gain),
    clamp(cell.nominalB * gain),
  ];
}

export function rgbCss([r, g, b]: Rgb): string {
  return `rgb(${r}, ${g}, ${b})`;
}
