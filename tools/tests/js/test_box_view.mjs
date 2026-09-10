/**
 * Box view: the CFS unit photo with a spool overlay per bay.
 *
 * The overlay geometry is tied to one specific image, so the interesting
 * assertions are about refusing to draw when the layout would not match.
 */

import assert from "node:assert/strict";
import { loadCard, makeHass, slotEntities } from "./cfs_card_harness.mjs";

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

const ATTRS = (slot) => ({ type: "PLA", vendor: "Creality", box_id: 1, slot_id: slot });

/** A card in box mode with `count` populated slots, optionally an external spool. */
function boxCard(count, { external = false } = {}) {
  const { KCFSCard } = loadCard();
  const card = new KCFSCard();
  const cfg = { view_mode: "box" };
  const states = {};
  for (let s = 0; s < count; s += 1) {
    cfg[`box0_slot${s}_filament`] = `sensor.printer_cfs_box_1_slot_${s}_filament`;
    cfg[`box0_slot${s}_color`] = `sensor.printer_cfs_box_1_slot_${s}_color`;
    cfg[`box0_slot${s}_percent`] = `sensor.printer_cfs_box_1_slot_${s}_percent`;
    Object.assign(states, slotEntities(1, s, { attributes: ATTRS(s) }));
  }
  if (external) {
    cfg.external_filament = "sensor.printer_cfs_external_filament";
    cfg.external_color = "sensor.printer_cfs_external_color";
    cfg.external_percent = "sensor.printer_cfs_external_percent";
    states["sensor.printer_cfs_external_filament"] = {
      state: "Creality Hyper PETG",
      attributes: { type: "PETG", vendor: "Creality", name: "Hyper PETG" },
    };
    states["sensor.printer_cfs_external_color"] = { state: "#00ff00", attributes: {} };
    states["sensor.printer_cfs_external_percent"] = {
      state: "55", attributes: { unit_of_measurement: "%" },
    };
  }
  card.setConfig(cfg);
  card.hass = makeHass(states);
  return card;
}

const html = (card) => card._root.getElementById("content").innerHTML;

test("a four-bay unit renders the box view", () => {
  const out = html(boxCard(4));
  assert.match(out, /class="box-view"/);
  assert.match(out, /cfs_box\.webp/);
});

test("the image is served from the integration path", () => {
  // A constant, not a scan of document.scripts: that returned a different base
  // under the ?v= cache-buster.
  assert.match(html(boxCard(4)), /src="\/ha_creality_ws\/cfs_box\.webp"/);
});

test("the image has alt text", () => {
  assert.match(html(boxCard(4)), /alt="Creality CFS unit"/);
});

test("one bay per slot", () => {
  const out = html(boxCard(4));
  assert.equal((out.match(/class="bay"/g) || []).length, 4);
});

test("each bay carries its slot colour", () => {
  assert.match(html(boxCard(4)), /--spool-color: #ffffff/);
});

test("a non-four-bay unit falls back rather than mispositioning spools", () => {
  for (const count of [1, 2, 3]) {
    const out = html(boxCard(count));
    assert.ok(!/class="box-view"/.test(out), `${count} slots must not use box view`);
    assert.ok(out.length > 0, "something still renders");
  }
});

test("box mode maps to the box-mode card class", () => {
  const card = boxCard(4);
  assert.match(card._root.innerHTML, /class="box-mode"/);
});

test("bays are editable", () => {
  // _renderEditButton always emits the <button>, varying only aria-disabled and
  // the icon -- so matching the class alone passes for an all-locked box view.
  const out = html(boxCard(4));
  assert.match(out, /edit-btn-mini/);
  assert.ok(!/aria-disabled/.test(out), "the bays must be editable, not locked");
  assert.match(out, /mdi:pencil/, "and show a pencil rather than a lock");
});

test("box mode keeps a configured external spool", () => {
  // A four-slot box takes the box-view path, which does not fall back to the
  // normal renderer -- so the external spool and its edit button used to vanish
  // from the card entirely.
  const out = html(boxCard(4, { external: true }));
  assert.match(out, /class="box-view"/, "still the box view");
  assert.match(out, /class="external-section"/, "the external spool must survive");
  assert.match(out, /Hyper PETG/);
});

test("box mode without an external spool renders no external section", () => {
  assert.ok(!/class="external-section"/.test(html(boxCard(4))));
});

test("no hardcoded white separators", () => {
  // PR #75 used rgba(255,255,255,0.5), which inverts badly in light themes.
  const style = boxCard(4)._root.innerHTML;
  assert.ok(!/rgba\(255,\s*255,\s*255/.test(style), "use theme variables");
});

let failed = 0;
for (const [name, fn] of tests) {
  try { fn(); console.log(`ok   ${name}`); }
  catch (err) { failed += 1; console.log(`FAIL ${name}\n     ${err.message}`); }
}
console.log(`\n${tests.length - failed}/${tests.length} passed`);
process.exit(failed === 0 ? 0 : 1);
