// Guards the font's row orientation.
//
// The emulator once rendered every glyph upside down: the sketch's rowMask[] is declared
// bottom-row-first, and indexing it directly with the visual row swapped top for bottom.
// It went unnoticed because the original smoke test rendered "BOS 3-2" -- B, O and S are
// near enough vertically symmetric to look correct either way.
//
// So this checks only *vertically asymmetric* glyphs, which are the ones that can actually
// detect a flip. Run with `npm run check:font` (Node 22.6+ strips the types natively).

import { FONT, GLYPH_COLS, ROW_MASK_TOP_DOWN, ROWS, glyphFor } from "../src/font.ts";

/** Render one character to ASCII art using the drawing-order masks. */
function render(ch: string): string[] {
  const glyph = glyphFor(ch);
  const rows: string[] = [];
  for (let row = 0; row < ROWS; row++) {
    let line = "";
    for (let col = 0; col < GLYPH_COLS; col++) {
      line += glyph[col] & ROW_MASK_TOP_DOWN[row] ? "#" : ".";
    }
    rows.push(line);
  }
  return rows;
}

const EXPECTED: Record<string, string[]> = {
  A: [".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
  L: ["#....", "#....", "#....", "#....", "#....", "#....", "#####"],
  7: ["#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."],
  J: ["..###", "...#.", "...#.", "...#.", "...#.", "#..#.", ".##.."],
  2: [".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"],
};

let failed = 0;
for (const [ch, expected] of Object.entries(EXPECTED)) {
  const actual = render(ch);
  if (actual.join("\n") === expected.join("\n")) {
    console.log(`  ok   ${ch}`);
    continue;
  }
  failed++;
  console.error(`  FAIL ${ch}  (expected | actual)`);
  for (let row = 0; row < ROWS; row++) {
    console.error(`         ${expected[row]}   ${actual[row]}`);
  }
}

// A flip is undetectable on a symmetric glyph, so make sure the set above stays useful.
for (const ch of Object.keys(EXPECTED)) {
  const rows = render(ch);
  if (rows.join("\n") === [...rows].reverse().join("\n")) {
    failed++;
    console.error(`  FAIL ${ch} is vertically symmetric and cannot detect a flip`);
  }
}

if (FONT.length !== 95) {
  failed++;
  console.error(`  FAIL expected 95 glyphs (ASCII 32..126), found ${FONT.length}`);
}

if (failed) {
  console.error(`\nfont check failed (${failed})`);
  process.exit(1);
}
console.log(`font check passed (${Object.keys(EXPECTED).length} glyphs, ${FONT.length} in table)`);
