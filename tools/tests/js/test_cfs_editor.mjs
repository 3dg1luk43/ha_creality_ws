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
