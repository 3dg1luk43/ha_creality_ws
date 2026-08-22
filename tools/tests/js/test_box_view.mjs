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

/** A card in box mode with `count` populated slots. */
function boxCard(count) {
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
  assert.match(html(boxCard(4)), /edit-btn-mini/);
});

test("no hardcoded white separators", () => {
  // PR #75 used rgba(255,255,255,0.5), which inverts badly in light themes.
  const { sandbox } = loadCard();
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
