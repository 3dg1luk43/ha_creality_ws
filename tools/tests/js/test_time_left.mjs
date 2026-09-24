/**
 * Printer card: the time-left pill, across display units.
 *
 * Home Assistant rewrites the state of a DURATION sensor into whichever unit the
 * user picked in the entity settings, so a card that reads the raw state and
 * assumes seconds shows "0s" for a job with 12 minutes left the moment someone
 * switches that entity to hours. These run the card's own helpers, pulled out of
 * the module header -- everything above `class KPrinterCard` is pure functions
 * and constants, so it evaluates without a DOM.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const HERE = dirname(fileURLToPath(import.meta.url));
const CARD_PATH = resolve(HERE, "../../../custom_components/ha_creality_ws/www/k_printer_card.js");

function loadHelpers() {
  const src = readFileSync(CARD_PATH, "utf8");
  const end = src.indexOf("class KPrinterCard");
  assert.ok(end > 0, "the card no longer defines class KPrinterCard");
  const context = vm.createContext({});
  vm.runInContext(`${src.slice(0, end)}\nthis.durationToSeconds = durationToSeconds; this.fmtTimeLeft = fmtTimeLeft;`, context);
  return context;
}

const { durationToSeconds, fmtTimeLeft } = loadHelpers();
const stateObj = (state, unit) => ({ state, attributes: unit ? { unit_of_measurement: unit } : {} });
const pill = (state, unit) => fmtTimeLeft(durationToSeconds(stateObj(state, unit)));

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test("seconds, the integration's native unit, pass through", () => {
  assert.equal(pill("720", "s"), "12:00");
  assert.equal(pill("7325", "s"), "2:02:05");
});

test("an entity displayed in hours is not read as seconds", () => {
  // The reported bug: 12 minutes left, entity set to hours, card showed 0.
  assert.equal(pill("0.2", "h"), "12:00");
  assert.equal(pill("2.5", "h"), "2:30:00");
});

test("minutes and days convert too", () => {
  assert.equal(pill("12", "min"), "12:00");
  assert.equal(pill("0.5", "d"), "12:00:00");
});

test("a missing or unrecognised unit is taken as seconds", () => {
  assert.equal(pill("720"), "12:00");
  assert.equal(pill("720", "fortnights"), "12:00");
});

test("unavailable, unknown and empty states read as no time left", () => {
  for (const s of ["unavailable", "unknown", "", null, undefined]) {
    assert.equal(durationToSeconds(stateObj(s, "h")), 0, `state ${s}`);
  }
  assert.equal(durationToSeconds(undefined), 0, "no entity configured");
});

test("sub-minute remainders still render", () => {
  assert.equal(pill("45", "s"), "45s");
  // 0.005 h is 18s: the hours case must not floor away before scaling.
  assert.equal(pill("0.005", "h"), "18s");
});

let failed = 0;
for (const [name, fn] of tests) {
  try { fn(); console.log(`ok   ${name}`); }
  catch (err) { failed += 1; console.log(`FAIL ${name}\n     ${err.message}`); }
}
console.log(`\n${tests.length - failed}/${tests.length} passed`);
process.exit(failed === 0 ? 0 : 1);
