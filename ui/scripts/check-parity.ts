// Guards the agreement between the engine's payload writer and the emulator's reader.
//
// The whole emulator design rests on one claim: the browser is handed the *same bytes* the
// hardware gets, so what you see on screen is what the board would show. That only holds
// if the two parsers agree, and they are written in different languages against the same
// quirky firmware format (`~RRGGBBLL` markers, consumed-not-displayed, a truncated marker
// at the end swallowing the remainder).
//
// The source of truth is engine/tests/fixtures/wire_format.json -- a golden snapshot the
// Python side generates and pins. Reading it here means no Python is needed to run this,
// and any drift in either direction fails the build.
//
// Run with `npm run check:parity` (Node 22.6+ strips the types natively).

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { markersIn, parsePayload, plainText } from "../src/payload.ts";

const here = dirname(fileURLToPath(import.meta.url));
const snapshotPath = join(here, "..", "..", "engine", "tests", "fixtures", "wire_format.json");

interface Snapshot {
  /** The engine's own reading of each payload, keyed by case name. */
  plain: Record<string, string | string[]>;
  [caseName: string]: string | string[] | Record<string, string | string[]>;
}

const snapshot: Snapshot = JSON.parse(readFileSync(snapshotPath, "utf-8"));

/** What the engine read out of a payload, for the same case name and index. */
function expectedText(name: string): string | undefined {
  const match = /^(.*)\[(\d+)\]$/.exec(name);
  const entry = snapshot.plain[match ? match[1] : name];
  if (entry === undefined) return undefined;
  return Array.isArray(entry) ? entry[Number(match![2])] : entry;
}

/** Every payload in the snapshot, flattened, skipping the URL-encoded cases. */
function payloads(): { name: string; payload: string }[] {
  const out: { name: string; payload: string }[] = [];
  for (const [name, value] of Object.entries(snapshot)) {
    if (name.endsWith("_url") || name === "plain") continue;
    const payloads = Array.isArray(value) ? value : [value as string];
    for (const [index, payload] of payloads.entries()) {
      out.push({ name: Array.isArray(value) ? `${name}[${index}]` : name, payload });
    }
  }
  return out;
}

let failed = 0;

function check(label: string, condition: boolean, detail = "") {
  if (condition) return;
  failed++;
  console.error(`  FAIL ${label}${detail ? `\n         ${detail}` : ""}`);
}

const cases = payloads();
check("snapshot has cases", cases.length > 0);

for (const { name, payload } of cases) {
  const chars = parsePayload(payload);
  const text = plainText(payload);

  // THE assertion: this parser must read the payload exactly as the engine did. Everything
  // else below is a self-consistency check and cannot catch a disagreement between the two
  // languages -- both TS functions share one marker walk, so a wrong MARKER_LEN would skew
  // them identically and pass.
  const expected = expectedText(name);
  check(`${name}: expected text recorded`, expected !== undefined);
  if (expected !== undefined) {
    check(
      `${name}: matches the engine's reading`,
      text === expected,
      `engine: ${JSON.stringify(expected)}
         emulator: ${JSON.stringify(text)}`,
    );
  }

  // Markers are consumed, so the visible text must never contain one.
  check(`${name}: markers stripped`, !text.includes("~"), JSON.stringify(text));

  check(
    `${name}: parsePayload and plainText agree`,
    chars.length === text.length,
    `${chars.length} chars vs ${text.length}`,
  );

  // Engine-side folding already guarantees pure ASCII; a mismatch means one side drifted.
  check(
    `${name}: pure ASCII`,
    [...text].every((c) => c.charCodeAt(0) >= 32 && c.charCodeAt(0) <= 126),
    JSON.stringify(text),
  );

  // Every marker must decode, and brightness must actually have been folded in: at 0x30 no
  // channel can exceed 0x30, which is what makes the emulator's dimming faithful.
  for (const marker of markersIn(payload)) {
    check(`${name}: marker ${marker.raw} decodes`, marker.ledCss.startsWith("rgb("));
  }
  for (const cell of chars) {
    check(
      `${name}: '${cell.ch}' folded below nominal`,
      cell.r <= cell.nominalR && cell.g <= cell.nominalG && cell.b <= cell.nominalB,
      `rgb(${cell.r},${cell.g},${cell.b}) vs nominal rgb(${cell.nominalR},${cell.nominalG},${cell.nominalB})`,
    );
  }
}

// Spot-check content the engine is known to produce, so this cannot pass on empty strings.
const goal = snapshot.goal as string;
check("goal payload reads as a goal", plainText(goal).includes("Goal!"), plainText(goal));
const accented = snapshot.goal_accented as string;
check(
  "accented names arrive ASCII-folded",
  plainText(accented).includes("Stutzle"),
  plainText(accented),
);

if (failed) {
  console.error(`\nparity check failed (${failed})`);
  process.exit(1);
}
console.log(`parity check passed (${cases.length} payloads from the engine's snapshot)`);
