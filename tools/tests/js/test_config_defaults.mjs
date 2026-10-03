/**
 * Dashboards hold only what differs from the defaults (R44).
 *
 * Both editors wrote every option into the card's YAML (62 keys for the CFS
 * card, 28 plus a 23-colour theme for the printer card), so a default changed
 * in a later release never reached a card that existed already: R42's
 * theme-following off-state icons would have stayed black on every one.
 */

import assert from "node:assert/strict";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { loadCardModule } from "./cfs_card_harness.mjs";
import {
  loadPrinterCard, makeEditor, makeEditorHass, changeForm, printerRegistry,
} from "./printer_card_harness.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const CFS_PATH = resolve(HERE, "../../../custom_components/ha_creality_ws/www/k_cfs_card.js");

const tests = [];
const test = (name, fn) => tests.push([name, fn]);
const settle = () => new Promise((done) => setTimeout(done, 200));
// Objects built inside the card's sandbox have that realm's prototypes.
const plain = (value) => JSON.parse(JSON.stringify(value));

test("the printer editor writes only what was changed", async () => {
  const { KPrinterCardEditor } = loadPrinterCard();
  const editor = makeEditor(KPrinterCardEditor, { type: "custom:k-printer-card" }, makeEditorHass({}));
  const seen = [];
  editor.dispatchEvent = (ev) => { seen.push(ev.detail.config); return true; };
  const form = editor._formEls["entities-form"];
  changeForm(form, { ...form.data, status: "sensor.k1c_print_status" });
  await settle();
  assert.deepEqual(plain(seen.at(-1)), { type: "custom:k-printer-card", status: "sensor.k1c_print_status" });
});

test("a theme is written as the colours that differ", async () => {
  const { KPrinterCard, KPrinterCardEditor } = loadPrinterCard();
  const editor = makeEditor(KPrinterCardEditor, { theme: { stop_bg: "#112233" } }, makeEditorHass({}));
  const seen = [];
  editor.dispatchEvent = (ev) => { seen.push(ev.detail.config); return true; };
  editor._dispatchConfigChange();
  await settle();
  assert.deepEqual(plain(seen.at(-1).theme), { stop_bg: "#112233" });
  // And the card puts the defaults back underneath it.
  const card = new KPrinterCard();
  card.setConfig(seen.at(-1));
  assert.equal(card._cfg.theme.stop_bg, "#112233");
  assert.equal(card._cfg.theme.pause_bg, KPrinterCard.defaultConfig().theme.pause_bg);
});

test("a new printer card starts from the first printer", () => {
  const { KPrinterCard } = loadPrinterCard();
  const hass = makeEditorHass({
    entities: printerRegistry("dev_a", "k1c"),
    devices: { dev_a: { name: "K1C" } },
  });
  const stub = KPrinterCard.getStubConfig(hass);
  assert.equal(stub.name, "K1C");
  assert.equal(stub.status, "sensor.k1c_print_status");
  assert.ok(!("theme" in stub), "no default theme in a new card");
  assert.deepEqual(plain(KPrinterCard.getStubConfig(makeEditorHass({}))), {});
});

function cfsHass() {
  const entities = {};
  const states = {};
  for (let slot = 0; slot < 4; slot += 1) {
    for (const [kind, name] of [["filament", "filament"], ["color", "color"], ["percent", "remaining"]]) {
      const id = `sensor.k2_cfs_box_1_slot_${slot + 1}_${name}`;
      entities[id] = { device_id: "dev_a", platform: "ha_creality_ws", translation_key: `cfs_slot_${kind}` };
      states[id] = { state: "x", attributes: { box_id: 1, slot_id: slot } };
    }
  }
  return { entities, states, language: "en", locale: { language: "en" } };
}

test("the CFS editor writes only what was set", () => {
  const { defined } = loadCardModule(CFS_PATH);
  const Editor = defined.get("k-cfs-card-editor");
  const ed = new Editor();
  ed.hass = cfsHass();
  ed.setConfig({ type: "custom:k-cfs-card" });
  ed.connectedCallback();
  const seen = [];
  ed.dispatchEvent = (ev) => { seen.push(ev.detail.config); return true; };
  const form = ed._root.getElementById("form");
  form.fire("value-changed", { detail: { value: { ...form.data, box0_slot0_filament: "sensor.x" } } });
  assert.deepEqual(plain(seen.at(-1)), { type: "custom:k-cfs-card", box0_slot0_filament: "sensor.x" });
});

test("a new CFS card starts from the first printer with a CFS", () => {
  const { defined } = loadCardModule(CFS_PATH);
  const Card = defined.get("k-cfs-card");
  const stub = Card.getStubConfig(cfsHass());
  assert.equal(stub.device, "dev_a");
  assert.equal(stub.box0_slot3_percent, "sensor.k2_cfs_box_1_slot_4_remaining");
  assert.ok(!("view_mode" in stub) && !("box1_temp" in stub), "no defaults or blanks in a new card");
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
