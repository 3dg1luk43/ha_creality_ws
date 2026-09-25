/**
 * Carrying an existing card config into the rebuilt editor.
 *
 * No config key has ever been renamed or removed, so upgrading is not about
 * shape. It is about values, in two directions:
 *
 *  * the previous editor put every colour through `hexToRgba(hex, 0.9)`, so
 *    colours that can only be solid were stored at 90% and the new editor --
 *    which gives icon colours no opacity control -- cannot show that;
 *  * a config written by hand can hold things no colour picker can express,
 *    `var(--accent-color)` above all, and those must survive an edit to a
 *    neighbouring field untouched.
 */

import assert from "node:assert/strict";
import {
  changeForm, colorFormFor, loadPrinterCard, makeEditor, makeEditorHass, schemaNames,
} from "./printer_card_harness.mjs";

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

function editorWith(config) {
  const { KPrinterCardEditor } = loadPrinterCard();
  return makeEditor(KPrinterCardEditor, config, makeEditorHass());
}

function cardWith(config) {
  const { KPrinterCard } = loadPrinterCard();
  const card = new KPrinterCard();
  card.setConfig(config);
  return card;
}

// --------------------------------------------------------------------------- //
// The 90%-opacity artefact
// --------------------------------------------------------------------------- //

test("an icon colour saved by the old editor is made solid again", () => {
  // Picking white for the pause icon used to store rgba(255,255,255,0.9).
  const card = cardWith({ theme: { pause_icon: "rgba(255, 255, 255, 0.9)" } });
  assert.equal(card._cfg.theme.pause_icon, "#ffffff");
});

test("a background keeps the opacity it was given", () => {
  // Backgrounds have a slider, so their alpha is a real setting, not an artefact.
  const card = cardWith({ theme: { pause_bg: "rgba(252, 109, 9, 0.9)" } });
  assert.equal(card._cfg.theme.pause_bg, "rgba(252, 109, 9, 0.9)");
});

test("every shipped default is a colour the editor can read", () => {
  // Defaults are left in their written form ("#fff", "rgba(...,.95)") rather
  // than normalised -- there is nothing to gain by rewriting them. What must
  // hold is that the picker can parse every one: a default it cannot read
  // reaches the form as no value, and an automatic field would read as
  // customised or the reverse.
  const { KPrinterCard, sandbox } = loadPrinterCard();
  const theme = KPrinterCard.getStubConfig().theme;
  const autos = [];
  for (const [key, value] of Object.entries(theme)) {
    if (value === "auto") {
      autos.push(key);
      continue;
    }
    assert.ok(sandbox.parseColor(value), `${key} default is unreadable: ${value}`);
  }
  assert.deepEqual(autos.sort(), [
    "progress_ring", "status_bg", "status_icon", "telemetry_icon", "telemetry_text",
  ], "the set of automatic fields moved");
});

test("a migrated colour is what the editor then shows", () => {
  // The point of migrating: without it the form says #ffffff while the card
  // renders 90% white, and nothing reconciles them until someone edits it.
  const editor = editorWith({ theme: { stop_icon: "rgba(255, 255, 255, 0.9)" } });
  assert.equal(editor._cfg.theme.stop_icon, "#ffffff");
  const form = colorFormFor(editor, "stop_icon");
  assert.deepEqual(Array.from(form.data.stop_icon), [255, 255, 255]);
});

test("a theme restored from browser storage is migrated too", () => {
  const { KPrinterCard, sandbox } = loadPrinterCard();
  sandbox.localStorage._seed(
    "k-printer-card-themes",
    JSON.stringify({
      [sandbox.generateCardId({ name: "P", status: "sensor.s" })]:
        { resume_icon: "rgba(0, 0, 0, 0.9)" },
    }),
  );
  const card = new KPrinterCard();
  card.setConfig({ name: "P", status: "sensor.s" }); // no theme -> storage path
  assert.equal(card._cfg.theme.resume_icon, "#000000");
});

// --------------------------------------------------------------------------- //
// Values a colour picker cannot express
// --------------------------------------------------------------------------- //

test("a hand-written var() colour is left alone", () => {
  const card = cardWith({ theme: { pause_bg: "var(--my-orange)" } });
  assert.equal(card._cfg.theme.pause_bg, "var(--my-orange)");
});

test("a var() on an automatic field is not mistaken for automatic", () => {
  // isAutoColor used to answer "is this unparseable?", so a var() showed the
  // Automatic switch on -- and the next edit anywhere in the group wrote
  // "auto" over it.
  const editor = editorWith({ theme: { status_icon: "var(--accent-color)" } });
  const form = colorFormFor(editor, "status_icon_auto");
  assert.equal(form.data.status_icon_auto, false);
  assert.ok(schemaNames(form).includes("status_icon"), "the picker should be shown");
});

test("editing one colour does not eat an unparseable neighbour", () => {
  const editor = editorWith({
    theme: { pause_bg: "var(--my-orange)", resume_bg: "rgba(76, 175, 80, 0.9)" },
  });
  const form = colorFormFor(editor, "resume_bg");
  changeForm(form, { ...form.data, resume_bg: [1, 2, 3] });
  assert.equal(editor._cfg.theme.resume_bg, "rgba(1, 2, 3, 0.9)");
  assert.equal(editor._cfg.theme.pause_bg, "var(--my-orange)", "the var() was overwritten");
});

test("a named colour also survives an edit next to it", () => {
  const editor = editorWith({ theme: { stop_icon: "white", stop_bg: "rgba(244, 67, 54, 0.95)" } });
  const form = colorFormFor(editor, "stop_bg");
  changeForm(form, { ...form.data, stop_bg_opacity: 50 });
  assert.equal(editor._cfg.theme.stop_icon, "white");
});

// --------------------------------------------------------------------------- //
// Everything else about an old config
// --------------------------------------------------------------------------- //

test("colours the new editor added default in without touching the old ones", () => {
  // power_* and the custom off-state were never in an old config.
  const card = cardWith({ theme: { pause_bg: "rgba(252, 109, 9, 0.9)" } });
  assert.equal(card._cfg.theme.power_on_bg, "rgba(76, 175, 80, .90)");
  assert.equal(card._cfg.theme.custom_icon_off, "#000");
  assert.equal(card._cfg.theme.pause_bg, "rgba(252, 109, 9, 0.9)");
});

test("an old config with no theme at all still works", () => {
  const card = cardWith({ name: "Printer", status: "sensor.s" });
  assert.equal(card._cfg.theme.status_icon, "auto");
  assert.equal(card._cfg.name, "Printer");
});

test("entity and layout settings are carried over untouched", () => {
  const config = {
    name: "Workshop", nozzle: "sensor.n", light: "switch.l",
    button_order: ["stop", "pause"], hide_box_temp: true, custom_btn_icon: "mdi:rocket",
  };
  const card = cardWith(config);
  for (const [key, value] of Object.entries(config)) {
    assert.deepEqual(card._cfg[key], value, `${key} changed`);
  }
});

test("migration does not mutate the config it was handed", () => {
  // Lovelace hands over its own object; writing through it would edit the
  // dashboard's in-memory config behind its back.
  const { KPrinterCard } = loadPrinterCard();
  const original = { theme: { pause_icon: "rgba(255, 255, 255, 0.9)" } };
  KPrinterCard._migrateConfig(original);
  assert.equal(original.theme.pause_icon, "rgba(255, 255, 255, 0.9)");
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
