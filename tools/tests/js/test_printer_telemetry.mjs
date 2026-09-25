/**
 * The printer card's telemetry row.
 *
 * The row has to stay on one line for as long as it can: the pills scale with
 * the card in CSS, then the unit suffixes are dropped, and only then does it
 * wrap. The first two steps are continuous or measured rather than stepped at a
 * breakpoint on purpose -- the row's line count is what getCardSize() reports
 * to Lovelace, so a breakpoint makes the card's height jump.
 *
 * Dropping the units is the part that needs code, and the part that can
 * oscillate: the pills grow to fill the row, so their widths always add up to
 * the row's whether the content fits or not. Wrapping is the only honest signal
 * left, which means the card finds out by trying -- and a retry at the width
 * that just failed would hide, restore, and hide the units forever.
 */

import assert from "node:assert/strict";
import { loadPrinterCard } from "./printer_card_harness.mjs";

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

/** Geometry the card can measure, since the shim has no layout engine. */
function geometryStubs() {
  return {
    getComputedStyle: (el) => ({
      display: el?.style?.display ?? "",
      visibility: el?.style?.visibility ?? "",
      columnGap: el?._computed?.columnGap ?? "0px",
      paddingLeft: el?._computed?.paddingLeft ?? "0px",
      paddingRight: el?._computed?.paddingRight ?? "0px",
    }),
  };
}

const PILL_WIDTHS = [60, 60, 55, 50, 60]; // nozzle, bed, box, time, layers
const UNIT_WIDTHS = [20, 20, 20, 0, 0];   // only the three temperatures carry one
const GAP = 6;
const LINE_HEIGHT = 30;

// With four visible pills (the chamber pill hides itself when unconfigured):
//   with units    60+20 + 60+20 + 50 + 60 + 3 gaps of 6 = 288
//   without units 60    + 60    + 50 + 60 + 3 gaps of 6 = 248
const WIDTH_WITH_UNITS = 288;
const WIDTH_WITHOUT_UNITS = 248;

/**
 * Lay the row out the way flex-wrap would, and expose it as offsetTop.
 *
 * Summed widths would tell the card nothing now that the pills grow, so it
 * reads the line count instead -- which means the shim has to actually wrap.
 * offsetTop is a getter so that toggling `compact` mid-measurement relays out,
 * exactly as it would in a browser.
 */
function layoutTelemetry(card, available) {
  const telemetry = card._root.querySelector(".telemetry");
  telemetry.clientWidth = available;
  telemetry._computed = { columnGap: `${GAP}px`, paddingLeft: "0px", paddingRight: "0px" };
  telemetry._available = available;

  const relayout = () => {
    const compact = telemetry.classList.contains("compact");
    const key = `${compact}:${telemetry._available}`;
    if (telemetry._layoutKey === key) return;
    telemetry._layoutKey = key;

    let line = 0;
    let used = 0;
    telemetry.children.forEach((pill, i) => {
      if (pill.style.display === "none") {
        pill._top = 0;
        return;
      }
      const width = PILL_WIDTHS[i] + (compact ? 0 : UNIT_WIDTHS[i]);
      if (used && used + GAP + width > telemetry._available) {
        line += 1;
        used = width;
      } else {
        used += (used ? GAP : 0) + width;
      }
      pill._top = line * LINE_HEIGHT;
    });
  };

  telemetry.children.forEach((pill) => {
    if ("offsetTop" in pill && typeof Object.getOwnPropertyDescriptor(pill, "offsetTop")?.get === "function") {
      return;
    }
    Object.defineProperty(pill, "offsetTop", {
      configurable: true,
      get: () => {
        relayout();
        return pill._top ?? 0;
      },
    });
  });
  telemetry._layoutKey = null;
  return telemetry;
}

function makeCard(config = {}) {
  const { KPrinterCard } = loadPrinterCard(geometryStubs());
  const card = new KPrinterCard();
  card.setConfig({ name: "Printer", ...config });
  return card;
}

/** Resize, re-measure, and report whether the units are hidden. */
function resize(card, available) {
  const telemetry = layoutTelemetry(card, available);
  card._updateTelemetryDensity();
  return telemetry.classList.contains("compact");
}

// --------------------------------------------------------------------------- //
// Splitting the unit off the value
// --------------------------------------------------------------------------- //

test("a formatted state splits into value and unit", () => {
  const { sandbox } = loadPrinterCard();
  assert.deepEqual({ ...sandbox.splitUnit("210.0 °C", { attributes: { unit_of_measurement: "°C" } }) },
    { value: "210.0", unit: "°C" });
});

test("a state with no unit keeps all of its text", () => {
  const { sandbox } = loadPrinterCard();
  assert.deepEqual({ ...sandbox.splitUnit("42", { attributes: {} }) }, { value: "42", unit: "" });
});

test("a placeholder is never mistaken for a unit", () => {
  const { sandbox } = loadPrinterCard();
  // "-" is what an unavailable sensor renders as; splitting "°C" off it would
  // leave an empty pill.
  assert.deepEqual({ ...sandbox.splitUnit("-", { attributes: { unit_of_measurement: "°C" } }) },
    { value: "-", unit: "" });
});

test("a value that merely ends in its unit's letters is not truncated", () => {
  const { sandbox } = loadPrinterCard();
  assert.deepEqual({ ...sandbox.splitUnit("idle", { attributes: { unit_of_measurement: "e" } }) },
    { value: "idl", unit: "e" });
});

// --------------------------------------------------------------------------- //
// Dropping the units
// --------------------------------------------------------------------------- //

test("a roomy card keeps its units", () => {
  const card = makeCard();
  assert.equal(resize(card, WIDTH_WITH_UNITS + 40), false);
});

test("a card that just fits keeps its units", () => {
  const card = makeCard();
  assert.equal(resize(card, WIDTH_WITH_UNITS), false);
});

test("units are dropped rather than letting the row wrap", () => {
  const card = makeCard();
  resize(card, WIDTH_WITH_UNITS + 40);
  assert.equal(resize(card, WIDTH_WITHOUT_UNITS + 10), true);
});

test("dropping the units actually puts the row back on one line", () => {
  const card = makeCard();
  resize(card, WIDTH_WITH_UNITS + 40);
  const telemetry = layoutTelemetry(card, WIDTH_WITHOUT_UNITS + 10);
  card._updateTelemetryDensity();
  assert.equal(card._telemetryLineCount(telemetry), 1);
});

test("units come back once there is room for them again", () => {
  const card = makeCard();
  resize(card, WIDTH_WITH_UNITS + 40);
  assert.equal(resize(card, WIDTH_WITHOUT_UNITS + 10), true);
  assert.equal(resize(card, WIDTH_WITH_UNITS + 40), false);
});

test("the units are not retried at the width that just rejected them", () => {
  // The whole hazard: restoring them here wraps the row, hides them again, and
  // repeats for as long as the card stays this size.
  const card = makeCard();
  resize(card, WIDTH_WITH_UNITS + 40);
  resize(card, WIDTH_WITH_UNITS - 1);
  assert.equal(resize(card, WIDTH_WITH_UNITS - 1), true);
});

test("a card parked on the boundary settles instead of flickering", () => {
  const card = makeCard();
  resize(card, WIDTH_WITH_UNITS + 40);
  const first = resize(card, WIDTH_WITH_UNITS - 1);
  for (let i = 0; i < 5; i += 1) {
    assert.equal(resize(card, WIDTH_WITH_UNITS - 1), first, `flipped on pass ${i + 2}`);
  }
});

test("a row too narrow even without units keeps them hidden", () => {
  const card = makeCard();
  resize(card, WIDTH_WITH_UNITS + 40);
  // Narrower than the short form: the row wraps either way, so hiding the
  // units is still the best it can do and they stay hidden.
  assert.equal(resize(card, WIDTH_WITHOUT_UNITS - 60), true);
});

test("a card with no width yet is left alone", () => {
  // A card measured before layout, or while detached, reports 0. Compacting on
  // that would show every freshly-added card without its units.
  const card = makeCard();
  assert.equal(resize(card, 0), false);
});

test("a hidden pill is not counted against the available width", () => {
  const card = makeCard();
  const telemetry = layoutTelemetry(card, WIDTH_WITH_UNITS);
  // The chamber pill is already hidden -- a KE has no chamber sensor. Were it
  // counted, this width would look 61px short and the units would go.
  assert.equal(telemetry.children[2].style.display, "none");
  card._updateTelemetryDensity();
  assert.equal(telemetry.classList.contains("compact"), false);
});

/** A card whose time-left pill reads from a sensor the test can move. */
function cardWithClock(seconds) {
  const card = makeCard({ time_left: "sensor.left" });
  card._hass = {
    states: { "sensor.left": { state: String(seconds), attributes: { unit_of_measurement: "s" } } },
    language: "en",
    locale: { language: "en" },
  };
  card._update();
  return card;
}

function setClock(card, seconds) {
  card._hass.states["sensor.left"].state = String(seconds);
  card._update();
}

test("a shorter reading lets the units try again without a resize", () => {
  // 1:02:25 dropping to 2:25 frees real room, and no resize is coming to
  // prompt a retry, so the text change has to be what re-arms it.
  const card = cardWithClock(3745);
  resize(card, WIDTH_WITH_UNITS + 40);
  resize(card, WIDTH_WITH_UNITS - 1);
  assert.equal(card._telemetryUnitsFailedAt, WIDTH_WITH_UNITS - 1);

  setClock(card, 145);
  assert.equal(card._telemetryUnitsFailedAt, 0, "the failed width outlived the text that caused it");
});

test("an unchanged reading leaves the retry where it was", () => {
  // Otherwise every poll would strip and restore the units, costing a reflow
  // each time to reach the same answer.
  const card = cardWithClock(3745);
  resize(card, WIDTH_WITH_UNITS + 40);
  resize(card, WIDTH_WITH_UNITS - 1);

  setClock(card, 3745);
  assert.equal(card._telemetryUnitsFailedAt, WIDTH_WITH_UNITS - 1);
});

test("a re-render does not leave a stale measurement behind", () => {
  const card = makeCard();
  resize(card, WIDTH_WITH_UNITS + 40);
  resize(card, WIDTH_WITH_UNITS - 1);
  assert.ok(card._telemetryUnitsFailedAt > 0);
  card.setConfig({ name: "Printer" });
  assert.equal(card._telemetryUnitsFailedAt, 0, "the old row's measurement outlived the old row");
});

// --------------------------------------------------------------------------- //

const run = async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try {
      await fn();
      console.log(`ok   ${name}`);
    } catch (err) {
      failed += 1;
      console.error(`FAIL ${name}\n     ${err.message}`);
    }
  }
  if (failed) {
    console.error(`\n${failed} of ${tests.length} failed`);
    process.exit(1);
  }
  console.log(`\n${tests.length} passed`);
};
run();
