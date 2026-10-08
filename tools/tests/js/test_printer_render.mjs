/**
 * The printer card's render cost, its size across rebuilds, and its card id.
 *
 * Three defects, each invisible to a source grep:
 *  - every `hass` assignment rebuilt the whole shadow DOM, although Home
 *    Assistant assigns a new hass for any state change anywhere;
 *  - Lovelace answers `ll-rebuild` with a NEW element, which started at size 3,
 *    measured the same wrapped row and fired again: a rebuild loop;
 *  - the card id came from `btoa(name)`, which throws above U+00FF, so a card
 *    named in Czech, CJK or with an emoji crashed, and in the editor the edit
 *    was silently never saved.
 */

import assert from "node:assert/strict";
import {
  loadPrinterCard,
  makeEditor,
  makeEditorHass,
  changeForm,
  printerRegistry,
} from "./printer_card_harness.mjs";

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

const STATUS = "sensor.k1c_print_status";
const NOZZLE = "sensor.k1c_nozzle_temperature";

function hassWith(states) {
  return {
    states,
    language: "en",
    locale: { language: "en" },
    formatEntityState: (st) => String(st?.state ?? "-"),
    callService: async () => {},
  };
}

function st(state) {
  return { state, attributes: {} };
}

function counted(card) {
  const calls = { render: 0, update: 0 };
  const render = card._render.bind(card);
  const update = card._update.bind(card);
  card._render = () => { calls.render += 1; render(); };
  card._update = () => { calls.update += 1; update(); };
  return calls;
}

// --------------------------------------------------------------------------- //
// Render cost
// --------------------------------------------------------------------------- //

test("an unrelated state change does not touch the card", () => {
  const { KPrinterCard } = loadPrinterCard();
  const card = new KPrinterCard();
  card.setConfig({ name: "K1C", status: STATUS, nozzle: NOZZLE });
  const states = { [STATUS]: st("printing"), [NOZZLE]: st("210") };
  card.hass = hassWith(states);
  const calls = counted(card);

  card.hass = hassWith({ ...states, "sensor.kitchen_temperature": st("21") });
  assert.deepEqual(calls, { render: 0, update: 0 });
});

test("a change to a shown entity updates without rebuilding the DOM", () => {
  const { KPrinterCard } = loadPrinterCard();
  const card = new KPrinterCard();
  card.setConfig({ name: "K1C", status: STATUS, nozzle: NOZZLE });
  const states = { [STATUS]: st("printing"), [NOZZLE]: st("210") };
  card.hass = hassWith(states);
  const shell = card._root.querySelector("ha-card");
  const calls = counted(card);

  card.hass = hassWith({ ...states, [NOZZLE]: st("215") });
  assert.deepEqual(calls, { render: 0, update: 1 });
  assert.equal(card._root.querySelector("ha-card"), shell, "the shell was rebuilt");
});

test("a new config renders once, not twice", () => {
  const { KPrinterCard } = loadPrinterCard();
  const card = new KPrinterCard();
  card.setConfig({ name: "K1C", status: STATUS });
  const calls = counted(card);
  card.setConfig({ name: "K1C", status: STATUS, nozzle: NOZZLE });
  assert.equal(calls.render, 1);
});

test("a state that appears after the first hass is still shown", () => {
  // The 150 ms follow-up timer existed to catch states that arrive late; the
  // relevance check does it, since a missing state is a changed one.
  const { KPrinterCard } = loadPrinterCard();
  const card = new KPrinterCard();
  card.setConfig({ name: "K1C", status: STATUS });
  card.hass = hassWith({});
  const calls = counted(card);
  card.hass = hassWith({ [STATUS]: st("idle") });
  assert.equal(calls.update, 1);
});

test("a language change repaints the strings", () => {
  const { KPrinterCard } = loadPrinterCard();
  const card = new KPrinterCard();
  card.setConfig({ name: "K1C", status: STATUS });
  const states = { [STATUS]: st("idle") };
  card.hass = hassWith(states);
  const calls = counted(card);
  card.hass = { ...hassWith(states), language: "es", locale: { language: "es" } };
  assert.equal(calls.update, 1);
});

// --------------------------------------------------------------------------- //
// Size across Lovelace's rebuilds
// --------------------------------------------------------------------------- //

test("a rebuilt card reports the size its predecessor measured, and stays put", () => {
  const { KPrinterCard } = loadPrinterCard();
  const config = { name: "K1C", status: STATUS };

  const first = new KPrinterCard();
  first.setConfig(config);
  first._telemetryLineCount = () => 2;
  const fired = [];
  first.dispatchEvent = (ev) => { fired.push(ev.type); return true; };
  first._updateTelemetryCardSize();
  assert.deepEqual(fired, ["ll-rebuild"]);
  assert.equal(first.getCardSize(), 4);

  // What hui-card does with that event: a brand-new element, same config.
  const second = new KPrinterCard();
  second.setConfig(config);
  assert.equal(second.getCardSize(), 4, "the rebuilt card fell back to 3");
  second._telemetryLineCount = () => 2;
  const again = [];
  second.dispatchEvent = (ev) => { again.push(ev.type); return true; };
  second._updateTelemetryCardSize();
  assert.deepEqual(again, [], "the rebuilt card asked for another rebuild");
});

test("a card that gets wider again shrinks back", () => {
  // The card runs in its own vm context, so its clock is injected.
  const clock = { now: 1_000_000 };
  const FakeDate = class extends Date {
    static now() { return clock.now; }
  };
  const { KPrinterCard } = loadPrinterCard({ Date: FakeDate });
  const card = new KPrinterCard();
  card.setConfig({ name: "K1C", status: STATUS });
  card._telemetryLineCount = () => 2;
  card.dispatchEvent = () => true;
  card._updateTelemetryCardSize();
  assert.equal(card.getCardSize(), 4);
  card._telemetryLineCount = () => 1;
  clock.now += 5000; // past the ll-rebuild throttle
  card._updateTelemetryCardSize();
  assert.equal(card.getCardSize(), 3);
});

// --------------------------------------------------------------------------- //
// Card id
// --------------------------------------------------------------------------- //

for (const name of ["Tiskárna č.1", "Printer – shed", "打印机", "K1C 🖨️"]) {
  test(`a card named ${JSON.stringify(name)} loads`, () => {
    const { KPrinterCard } = loadPrinterCard();
    const card = new KPrinterCard();
    card.setConfig({ name, status: STATUS });
    assert.match(card._cardId, /^[a-zA-Z0-9]{1,16}$/);
  });
}

test("a Latin-1 name keeps the id its stored theme was saved under", () => {
  const { KPrinterCard } = loadPrinterCard();
  const card = new KPrinterCard();
  card.setConfig({ name: "Tiskárna", status: STATUS });
  const legacy = Buffer.from(`Tiskárna-${STATUS}`, "latin1")
    .toString("base64").replace(/[^a-zA-Z0-9]/g, "").substring(0, 16);
  assert.equal(card._cardId, legacy);
});

test("renaming a card to a non-Latin-1 name in the editor is saved", async () => {
  const { KPrinterCardEditor } = loadPrinterCard();
  const hass = makeEditorHass({
    entities: printerRegistry("dev_a", "k1c"),
    devices: { dev_a: { name: "K1C" } },
  });
  const editor = makeEditor(KPrinterCardEditor, {}, hass);
  const seen = [];
  editor.dispatchEvent = (ev) => { seen.push(ev.detail.config); return true; };
  const form = editor._formEls["entities-form"];
  changeForm(form, { ...form.data, name: "Tiskárna č.1" });
  await new Promise((resolve) => setTimeout(resolve, 200));
  assert.equal(seen.length, 1, "the edit never reached Lovelace");
  assert.equal(seen[0].name, "Tiskárna č.1");
});

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
await run();
