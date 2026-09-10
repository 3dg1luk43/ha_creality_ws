/**
 * view_mode replaces the compact_view boolean.
 *
 * The risk here is silent: a migration that does not fire leaves an existing
 * dashboard rendering the wrong mode, and a mode-to-class mapping that is off by
 * one string leaves the card unstyled. Both are asserted rather than eyeballed.
 */

import assert from "node:assert/strict";
import { loadCard, makeHass, slotEntities } from "./cfs_card_harness.mjs";

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

const SLOT_CFG = {
  box0_slot0_filament: "sensor.printer_cfs_box_1_slot_0_filament",
  box0_slot0_color: "sensor.printer_cfs_box_1_slot_0_color",
  box0_slot0_percent: "sensor.printer_cfs_box_1_slot_0_percent",
};

function card(config) {
  const { KCFSCard } = loadCard();
  const c = new KCFSCard();
  c.setConfig({ ...SLOT_CFG, ...config });
  return c;
}

test("a new card defaults to full", () => {
  const { KCFSCard } = loadCard();
  assert.equal(KCFSCard.getStubConfig().view_mode, "full");
});

test("the stub does not carry the legacy key", () => {
  // Keeping both would make the migration unreachable: getStubConfig is spread
  // *before* the user config, so view_mode would always already be set.
  const { KCFSCard } = loadCard();
  assert.ok(!("compact_view" in KCFSCard.getStubConfig()));
});

test("compact_view: true migrates to compact", () => {
  assert.equal(card({ compact_view: true })._cfg.view_mode, "compact");
});

test("compact_view: false migrates to full", () => {
  assert.equal(card({ compact_view: false })._cfg.view_mode, "full");
});

test("the legacy key is dropped after migrating", () => {
  const c = card({ compact_view: true });
  assert.ok(!("compact_view" in c._cfg), "stale key must not be written back");
});

test("an explicit view_mode wins over the legacy key", () => {
  const c = card({ compact_view: true, view_mode: "full" });
  assert.equal(c._cfg.view_mode, "full");
});

test("mode maps to the class the stylesheet actually defines", () => {
  const { KCFSCard } = loadCard();
  // "full" is historically "normal-mode"; `${viewMode}-mode` would emit
  // "full-mode" and match nothing.
  assert.equal(KCFSCard._modeClass("full"), "normal-mode");
  assert.equal(KCFSCard._modeClass("compact"), "compact-mode");
  assert.equal(KCFSCard._modeClass("box"), "box-mode");
  assert.equal(KCFSCard._modeClass(undefined), "normal-mode");
});

test("the rendered ha-card carries the mapped class", () => {
  for (const [mode, cls] of [["full", "normal-mode"], ["compact", "compact-mode"]]) {
    const c = card({ view_mode: mode });
    assert.match(c._root.innerHTML, new RegExp(`class="${cls}"`), `${mode} -> ${cls}`);
  }
});

test("compact and full render different markup", () => {
  const states = slotEntities(1, 0, { attributes: { type: "PLA", box_id: 1, slot_id: 0 } });
  const full = card({ view_mode: "full" });
  full.hass = makeHass(states);
  const compact = card({ view_mode: "compact" });
  compact.hass = makeHass(states);
  assert.notEqual(
    full._root.getElementById("content").innerHTML,
    compact._root.getElementById("content").innerHTML,
  );
});

test("getCardSize scales with the amount configured", () => {
  // PR #75 replaced getCardSize with `return 3` and deleted getLayoutOptions,
  // silently reverting the dynamic sizing from #73. `typeof === "number"` and a
  // `<=` comparison are both satisfied by a constant, so assert that the value
  // actually responds to the config.
  // The compact branch is where the sizing is derived; full is a fixed 5 by
  // design, so asserting against it would only pin a constant.
  const oneBox = card({ view_mode: "compact", box0_slot0_filament: "sensor.a" });
  const fourBoxes = card({
    view_mode: "compact",
    box0_slot0_filament: "sensor.a",
    box1_slot0_filament: "sensor.b",
    box2_slot0_filament: "sensor.c",
    box3_slot0_filament: "sensor.d",
  });
  const states = slotEntities(1, 0, { attributes: { type: "PLA" } });
  oneBox.hass = makeHass(states);
  fourBoxes.hass = makeHass(states);

  assert.ok(
    fourBoxes.getCardSize() > oneBox.getCardSize(),
    `four boxes (${fourBoxes.getCardSize()}) must be taller than one (${oneBox.getCardSize()})`,
  );
  // And an external spool adds a row of its own.
  const withExternal = card({
    view_mode: "compact",
    box0_slot0_filament: "sensor.a",
    external_filament: "sensor.ext",
  });
  withExternal.hass = makeHass(states);
  assert.ok(
    withExternal.getCardSize() > oneBox.getCardSize(),
    "an external spool must add height",
  );
});

test("getLayoutOptions reports real grid bounds", () => {
  const c = card({ view_mode: "compact", box0_slot0_filament: "sensor.a" });
  c.hass = makeHass(slotEntities(1, 0, { attributes: { type: "PLA" } }));
  const layout = c.getLayoutOptions();

  assert.ok(layout && typeof layout === "object", "an options object");
  const keys = Object.keys(layout);
  assert.ok(keys.length > 0, `an empty object would satisfy typeof: ${keys}`);
  // HA reads grid_* keys; a stub returning {} would silently lose sizing.
  assert.ok(
    keys.some((k) => k.startsWith("grid_")),
    `expected grid_* sizing keys, got ${keys}`,
  );
});

test("compact is strictly shorter than full for the same config", () => {
  const config = {
    box0_slot0_filament: "sensor.a",
    box1_slot0_filament: "sensor.b",
  };
  const states = slotEntities(1, 0, { attributes: { type: "PLA" } });
  const full = card({ view_mode: "full", ...config });
  full.hass = makeHass(states);
  const compact = card({ view_mode: "compact", ...config });
  compact.hass = makeHass(states);

  // Strict `<`, not `<=`: equality is what a constant return value produces.
  assert.ok(
    compact.getCardSize() < full.getCardSize(),
    `compact ${compact.getCardSize()} should be less than full ${full.getCardSize()}`,
  );
});

test("the editor offers a mode select, not the legacy toggle", () => {
  const { defined } = loadCard();
  const Editor = defined.get("k-cfs-card-editor");
  const editor = new Editor();
  editor.setConfig({ ...SLOT_CFG, compact_view: true });
  // The editor migrates too, so a legacy dashboard opens showing the real mode.
  assert.equal(editor._cfg.view_mode, "compact");
  assert.ok(!("compact_view" in editor._cfg));
});

let failed = 0;
for (const [name, fn] of tests) {
  try { fn(); console.log(`ok   ${name}`); }
  catch (err) { failed += 1; console.log(`FAIL ${name}\n     ${err.message}`); }
}
console.log(`\n${tests.length - failed}/${tests.length} passed`);
process.exit(failed === 0 ? 0 : 1);
