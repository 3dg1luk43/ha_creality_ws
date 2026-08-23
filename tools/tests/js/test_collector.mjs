/**
 * Behavioural tests for the CFS card's data collector and render gate.
 *
 * These execute the real card under node rather than asserting on its source
 * text, so they catch logic errors a grep cannot -- a fingerprint that misses a
 * field, a gate that blanks the card, an id resolved from the wrong place.
 */

import assert from "node:assert/strict";
import { loadCard, makeHass, slotEntities } from "./cfs_card_harness.mjs";

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

/** A card wired to one CFS slot at card position 0. */
function cardForOneSlot(overrides = {}) {
  const { KCFSCard } = loadCard();
  const card = new KCFSCard();
  card.setConfig({
    box0_slot0_filament: "sensor.printer_cfs_box_1_slot_0_filament",
    box0_slot0_color: "sensor.printer_cfs_box_1_slot_0_color",
    box0_slot0_percent: "sensor.printer_cfs_box_1_slot_0_percent",
    ...overrides,
  });
  return card;
}

const FULL_ATTRS = {
  type: "PLA", vendor: "Creality", rfid: "001001",
  min_temp: 190, max_temp: 240, pressure: 0.04,
  box_id: 1, slot_id: 0, color_hex: "#ffffff",
};

// --------------------------------------------------------------------------- //
// Collector
// --------------------------------------------------------------------------- //

test("collects the fields the edit dialog prefills from", () => {
  const card = cardForOneSlot();
  card.hass = makeHass(slotEntities(1, 0, { attributes: FULL_ATTRS }));
  const slot = card._collectData().boxes[0].slots[0];
  assert.equal(slot.vendor, "Creality");
  assert.equal(slot.rfid, "001001");
  assert.equal(slot.minTemp, 190);
  assert.equal(slot.maxTemp, 240);
  assert.equal(slot.pressure, 0.04);
});

test("prefers the printer ids published by the sensor", () => {
  const card = cardForOneSlot();
  // Card position 0 would guess box 1; the attributes say box 3, which must win.
  card.hass = makeHass(slotEntities(1, 0, {
    attributes: { ...FULL_ATTRS, box_id: 3, slot_id: 2 },
  }));
  const slot = card._collectData().boxes[0].slots[0];
  assert.equal(slot.printerBoxId, 3);
  assert.equal(slot.printerSlotId, 2);
  assert.equal(slot.targetIsGuessed, false);
});

test("falls back to the entity id when attributes are missing", () => {
  const card = cardForOneSlot();
  const states = slotEntities(1, 0, { attributes: { type: "PLA" } });
  card.hass = makeHass(states);
  const slot = card._collectData().boxes[0].slots[0];
  // cfs_box_1_slot_0 is encoded in the entity id, so this is not a guess.
  assert.equal(slot.printerBoxId, 1);
  assert.equal(slot.printerSlotId, 0);
  assert.equal(slot.targetIsGuessed, false);
});

test("marks card-position guesses as guessed", () => {
  // A renamed entity carries neither the attributes nor the id convention.
  const card = cardForOneSlot({
    box0_slot0_filament: "sensor.my_renamed_spool",
    box0_slot0_color: "",
    box0_slot0_percent: "",
  });
  card.hass = makeHass({ "sensor.my_renamed_spool": { state: "PLA", attributes: {} } });
  const slot = card._collectData().boxes[0].slots[0];
  assert.equal(slot.printerBoxId, 1, "card position 0 guesses printer box 1");
  assert.equal(slot.targetIsGuessed, true);
});

test("flags multi-colour spools so editing cannot flatten them", () => {
  const card = cardForOneSlot();
  const states = slotEntities(1, 0, {
    color: "#ffa800,#ff97e1",
    attributes: FULL_ATTRS,
  });
  card.hass = makeHass(states);
  const slot = card._collectData().boxes[0].slots[0];
  assert.equal(slot.isMultiColour, true);
});

test("a single colour is not flagged as multi-colour", () => {
  const card = cardForOneSlot();
  card.hass = makeHass(slotEntities(1, 0, { color: "#ffa800", attributes: FULL_ATTRS }));
  assert.equal(card._collectData().boxes[0].slots[0].isMultiColour, false);
});

test("tolerates slots with no temps or pressure", () => {
  const card = cardForOneSlot();
  card.hass = makeHass(slotEntities(1, 0, { attributes: { type: "PLA", box_id: 1, slot_id: 0 } }));
  const slot = card._collectData().boxes[0].slots[0];
  assert.equal(slot.minTemp, undefined);
  assert.equal(slot.pressure, undefined);
  assert.equal(slot.name, "Creality Hyper PLA", "the rest still renders");
});

// --------------------------------------------------------------------------- //
// Render gate
// --------------------------------------------------------------------------- //

/** A registry that resolves this card's slot to a single device. */
const RESOLVABLE_REGISTRY = {
  "sensor.printer_cfs_box_1_slot_0_filament": {
    device_id: "dev_a", platform: "ha_creality_ws",
  },
  "sensor.printer_cfs_box_1_slot_0_color": {
    device_id: "dev_a", platform: "ha_creality_ws",
  },
  "sensor.printer_cfs_box_1_slot_0_percent": {
    device_id: "dev_a", platform: "ha_creality_ws",
  },
};

/** makeHass with a resolvable registry, so device resolution cannot add renders. */
const gateHass = (states) => makeHass(states, { entities: RESOLVABLE_REGISTRY });

/**
 * Count renders by watching the content element's innerHTML writes.
 *
 * Callers pair this with `gateHass`: _deviceIdError is part of the fingerprint,
 * so a card whose device never resolves would land an extra asynchronous render
 * and the counts below would stop isolating the state change under test.
 */
function countingCard(overrides) {
  const card = cardForOneSlot(overrides);
  card._renderCount = 0;
  const inner = card._update.bind(card);
  card._update = (data) => { card._renderCount += 1; return inner(data); };
  return card;
}

test("an unchanged state does not re-render", () => {
  const card = countingCard();
  const states = slotEntities(1, 0, { attributes: FULL_ATTRS });
  card.hass = gateHass(states);
  assert.equal(card._renderCount, 1);
  card.hass = gateHass(states);
  card.hass = gateHass(states);
  assert.equal(card._renderCount, 1, "identical states must not re-render");
});

test("a changed percent re-renders", () => {
  const card = countingCard();
  card.hass = gateHass(slotEntities(1, 0, { percent: 80, attributes: FULL_ATTRS }));
  assert.equal(card._renderCount, 1);
  card.hass = gateHass(slotEntities(1, 0, { percent: 79, attributes: FULL_ATTRS }));
  assert.equal(card._renderCount, 2);
});

test("a changed colour re-renders", () => {
  const card = countingCard();
  card.hass = gateHass(slotEntities(1, 0, { color: "#ffffff", attributes: FULL_ATTRS }));
  card.hass = gateHass(slotEntities(1, 0, { color: "#ff0000", attributes: FULL_ATTRS }));
  assert.equal(card._renderCount, 2);
});

test("switching the selected unit re-renders", () => {
  const card = countingCard();
  card.hass = gateHass(slotEntities(1, 0, { attributes: FULL_ATTRS }));
  assert.equal(card._renderCount, 1);
  card._selectedCFS = 1;
  card._updateIfChanged();
  assert.equal(card._renderCount, 2, "_selectedCFS must be in the fingerprint");
});

test("setConfig clears the snapshot so the card is not left blank", () => {
  // _render() wipes #content. If the gate still matched, nothing would repopulate
  // it and the card would show empty after every visual-editor change.
  const card = cardForOneSlot();
  const states = slotEntities(1, 0, { attributes: FULL_ATTRS });
  card.hass = gateHass(states);
  const before = card._root.getElementById("content").innerHTML;
  assert.ok(before.length > 0, "content rendered initially");

  card.setConfig({
    box0_slot0_filament: "sensor.printer_cfs_box_1_slot_0_filament",
    box0_slot0_color: "sensor.printer_cfs_box_1_slot_0_color",
    box0_slot0_percent: "sensor.printer_cfs_box_1_slot_0_percent",
  });
  assert.equal(card._snapshot, null, "snapshot must be reset by setConfig");
  card.hass = gateHass(states);
  assert.ok(
    card._root.getElementById("content").innerHTML.length > 0,
    "content must be repopulated after setConfig",
  );
});

test("no data renders the empty-state message, not a crash", () => {
  const { KCFSCard } = loadCard();
  const card = new KCFSCard();
  card.setConfig({});
  card.hass = gateHass({});
  const html = card._root.getElementById("content").innerHTML;
  assert.match(html, /no-data/);
});

// --------------------------------------------------------------------------- //

// --------------------------------------------------------------------------- //
// Gate-vs-render drift
// --------------------------------------------------------------------------- //

test("every collected slot field is visible to the render gate", () => {
  // The invariant the old source-text "only one collection loop" guard was
  // gesturing at: two readers drift, one gains a field the other forgets, and
  // the card renders something the gate cannot see. Counting a literal in the
  // source could not catch that; changing each field and demanding a re-render
  // can.
  const attrChanges = {
    type: "PETG",
    name: "Hyper PETG",
    vendor: "Elegoo",
    min_temp: 200,
    max_temp: 250,
    pressure: 0.09,
    rfid: "999999",
    box_id: 2,
    slot_id: 3,
  };

  for (const [attr, next] of Object.entries(attrChanges)) {
    const card = countingCard();
    card.hass = gateHass(slotEntities(1, 0, { attributes: FULL_ATTRS }));
    const baseline = card._renderCount;

    card.hass = gateHass(slotEntities(1, 0, {
      attributes: { ...FULL_ATTRS, [attr]: next },
    }));
    assert.ok(
      card._renderCount > baseline,
      `changing ${attr} must invalidate the gate (renders stayed at ${baseline})`,
    );
  }

  // And the three entity states, not just the attributes.
  for (const [label, states] of [
    ["filament", slotEntities(1, 0, { filament: "Other PLA", attributes: FULL_ATTRS })],
    ["color", slotEntities(1, 0, { color: "#123456", attributes: FULL_ATTRS })],
    ["percent", slotEntities(1, 0, { percent: 42, attributes: FULL_ATTRS })],
  ]) {
    const card = countingCard();
    card.hass = gateHass(slotEntities(1, 0, { attributes: FULL_ATTRS }));
    const baseline = card._renderCount;
    card.hass = gateHass(states);
    assert.ok(
      card._renderCount > baseline,
      `changing the ${label} state must invalidate the gate`,
    );
  }
});

let failed = 0;
for (const [name, fn] of tests) {
  try {
    fn();
    console.log(`ok   ${name}`);
  } catch (err) {
    failed += 1;
    console.log(`FAIL ${name}\n     ${err.message}`);
  }
}
console.log(`\n${tests.length - failed}/${tests.length} passed`);
process.exit(failed === 0 ? 0 : 1);
