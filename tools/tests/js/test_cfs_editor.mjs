/**
 * The CFS card's editor survives Lovelace echoing every change back (R22).
 *
 * Lovelace answers each config-changed with a fresh setConfig, and the editor
 * rebuilt its whole DOM on every setConfig: typing in the title lost focus after
 * each character, and changing the display mode jumped back to the first tab.
 */

import assert from "node:assert/strict";
import { loadCardModule } from "./cfs_card_harness.mjs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const CARD_PATH = resolve(HERE, "../../../custom_components/ha_creality_ws/www/k_cfs_card.js");

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

function editor(config = {}) {
  const { defined } = loadCardModule(CARD_PATH);
  const Editor = defined.get("k-cfs-card-editor");
  const ed = new Editor();
  ed.hass = { states: {}, language: "en", locale: { language: "en" } };
  ed.setConfig(config);
  ed.connectedCallback();
  return ed;
}

function changed(ed) {
  const seen = [];
  ed.dispatchEvent = (ev) => { seen.push(ev.detail.config); return true; };
  return seen;
}

test("typing in a field does not rebuild the form under the cursor", () => {
  const ed = editor({ name: "CFS" });
  const form = ed._root.getElementById("form");
  const seen = changed(ed);
  form.fire("value-changed", { detail: { value: { ...form.data, name: "CFS 1" } } });
  assert.equal(seen.length, 1);
  // What Lovelace does with it:
  ed.setConfig(seen[0]);
  assert.equal(ed._root.getElementById("form"), form, "the form was rebuilt");
});

test("the echoed config does not replace the form's data", () => {
  const ed = editor({ name: "CFS" });
  const form = ed._root.getElementById("form");
  const seen = changed(ed);
  form.fire("value-changed", { detail: { value: { ...form.data, name: "CFS 1" } } });
  const assigned = ed._root.getElementById("form").data;
  ed.setConfig(seen[0]);
  assert.equal(
    ed._root.getElementById("form").data, assigned,
    "the data object was swapped for identical data",
  );
});

test("the selected tab survives the echo", () => {
  const ed = editor();
  ed._root.querySelectorAll(".tab")[1].onclick();
  const theme = ed._root.getElementById("theme-form");
  const seen = changed(ed);
  theme.fire("value-changed", { detail: { value: { ...theme.data, view_mode: "compact" } } });
  ed.setConfig(seen[0]);
  assert.equal(ed._root.getElementById("theme-tab").classList.contains("active"), true);
  assert.equal(ed._root.getElementById("entities-tab").classList.contains("active"), false);
});

test("a config that really changed reaches the forms", () => {
  const ed = editor({ name: "CFS" });
  ed.setConfig({ name: "Workshop" });
  assert.equal(ed._root.getElementById("form").data.name, "Workshop");
});

// --------------------------------------------------------------------------- //
// Placeholder substitution (R28)
// --------------------------------------------------------------------------- //

function cardInstance() {
  const { defined } = loadCardModule(CARD_PATH);
  const Card = defined.get("k-cfs-card");
  const c = new Card();
  c._hass = { states: {}, language: "en", locale: { language: "en" } };
  return c;
}

test("a dollar sign in a value is inserted as typed", () => {
  // A replacement *string* expands `$&` to the matched placeholder.
  const c = cardInstance();
  assert.equal(c._t("toast_preset_saved", { name: "Teal $& Co" }), "Preset \u201cTeal $& Co\u201d saved");
});

test("a value that contains another placeholder is not substituted again", () => {
  // Substituted key by key, the first value's "{slot}" was then filled in by
  // the second key.
  const c = cardInstance();
  assert.equal(
    c._t("label_slot_filament", { box: "{slot}", slot: 2 }),
    "Box {slot} Slot 2 Filament",
  );
});

// --------------------------------------------------------------------------- //
// The card picker (R28)
// --------------------------------------------------------------------------- //

const PRINTER_PATH = resolve(HERE, "../../../custom_components/ha_creality_ws/www/k_printer_card.js");

for (const [path, type] of [[CARD_PATH, "k-cfs-card"], [PRINTER_PATH, "k-printer-card"]]) {
  test(`a second copy of the module does not list ${type} twice`, () => {
    // Two resource entries with different ?v= load the module twice into one
    // page, which shares one window.customCards.
    const customCards = [];
    const { reload } = loadCardModule(path, { customCards });
    reload();
    assert.equal(customCards.filter((c) => c.type === type).length, 1);
  });
}

// --------------------------------------------------------------------------- //
// Filling the fields from a printer (R43)
// --------------------------------------------------------------------------- //

function printerHass({ attributes = true } = {}) {
  const entities = {};
  const states = {};
  const add = (entityId, translationKey, attrs = {}) => {
    entities[entityId] = { device_id: "dev_a", platform: "ha_creality_ws", translation_key: translationKey };
    states[entityId] = { state: "x", attributes: attributes ? attrs : {} };
  };
  for (const box of [1, 2]) {
    add(`sensor.k2_cfs_box_${box}_temperature`, "cfs_box_temp", { box_id: box });
    add(`sensor.k2_cfs_box_${box}_humidity`, "cfs_box_humidity", { box_id: box });
    for (let slot = 0; slot < 4; slot += 1) {
      for (const [kind, name] of [["filament", "filament"], ["color", "color"], ["percent", "remaining"]]) {
        add(`sensor.k2_cfs_box_${box}_slot_${slot + 1}_${name}`, `cfs_slot_${kind}`, { box_id: box, slot_id: slot });
      }
    }
  }
  add("sensor.k2_cfs_external_filament", "cfs_ext_filament");
  add("sensor.k2_cfs_external_color", "cfs_ext_color");
  add("sensor.k2_cfs_external_remaining", "cfs_ext_percent");
  // An install from before R20 keeps "Box 0 Slot 1" sensors for the external spool.
  add("sensor.k2_cfs_box_0_slot_1_filament", "cfs_slot_filament", { box_id: 0, slot_id: 0 });
  // Another printer's sensor must not leak in.
  entities["sensor.other_cfs_box_1_slot_1_filament"] = { device_id: "dev_b", platform: "ha_creality_ws", translation_key: "cfs_slot_filament" };
  return { states, entities, language: "en", locale: { language: "en" } };
}

function editorFor(hass, config = {}) {
  const { defined } = loadCardModule(CARD_PATH);
  const Editor = defined.get("k-cfs-card-editor");
  const ed = new Editor();
  ed.hass = hass;
  ed.setConfig(config);
  ed.connectedCallback();
  return ed;
}

for (const withAttributes of [true, false]) {
  test(`picking the printer fills every CFS field${withAttributes ? "" : " (from entity ids)"}`, () => {
    const ed = editorFor(printerHass({ attributes: withAttributes }));
    const seen = changed(ed);
    ed._root.getElementById("device-form").fire("value-changed", { detail: { value: { device: "dev_a" } } });
    const cfg = seen.at(-1);
    assert.equal(cfg.device, "dev_a");
    assert.equal(cfg.box0_slot0_filament, "sensor.k2_cfs_box_1_slot_1_filament");
    assert.equal(cfg.box0_slot3_percent, "sensor.k2_cfs_box_1_slot_4_remaining");
    assert.equal(cfg.box1_slot2_color, "sensor.k2_cfs_box_2_slot_3_color");
    assert.equal(cfg.box1_humidity, "sensor.k2_cfs_box_2_humidity");
    assert.equal(cfg.external_percent, "sensor.k2_cfs_external_remaining");
    assert.ok(!Object.values(cfg).includes("sensor.k2_cfs_box_0_slot_1_filament"), "box 0 is the external holder");
    assert.ok(!Object.values(cfg).includes("sensor.other_cfs_box_1_slot_1_filament"));
    assert.equal(ed._root.getElementById("refill-status").textContent, "Filled 31 of 31 fields.");
  });
}

test("picking the printer keeps fields already chosen; the button replaces them", () => {
  const ed = editorFor(printerHass(), { box0_slot0_filament: "sensor.mine" });
  const seen = changed(ed);
  ed._root.getElementById("device-form").fire("value-changed", { detail: { value: { device: "dev_a" } } });
  assert.equal(seen.at(-1).box0_slot0_filament, "sensor.mine");
  ed._root.getElementById("refill")._listeners.click.forEach((fn) => fn({}));
  assert.equal(seen.at(-1).box0_slot0_filament, "sensor.k2_cfs_box_1_slot_1_filament");
});

test("the fill button waits for a printer", () => {
  const ed = editorFor(printerHass());
  assert.equal(ed._root.getElementById("refill").disabled, true);
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
