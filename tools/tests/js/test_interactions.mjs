/**
 * Click handling.
 *
 * Every one of these was previously untestable: the harness stubbed
 * querySelectorAll to return nothing, so `_attachEventHandlers` bound nothing
 * and its entire body could be deleted with all suites still green. The card's
 * headline feature is clicking an edit button, so that gap mattered.
 */

import assert from "node:assert/strict";
import { loadCard, makeHass, slotEntities } from "./cfs_card_harness.mjs";

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

const SLOT = "sensor.printer_cfs_box_1_slot_0_filament";
const STATUS = "sensor.printer_print_status";
const ATTRS = { type: "PLA", vendor: "Creality", box_id: 1, slot_id: 0 };

function registry(extra = {}) {
  return {
    [SLOT]: { device_id: "dev_a", platform: "ha_creality_ws" },
    [STATUS]: {
      device_id: "dev_a", platform: "ha_creality_ws", translation_key: "print_status",
    },
    ...extra,
  };
}

/** A rendered card with its handlers attached. */
async function setup({ status = "idle", attributes = ATTRS, config, entities } = {}) {
  const { KCFSCard } = loadCard();
  const card = new KCFSCard();
  card.setConfig(config || { box0_slot0_filament: SLOT });
  const events = [];
  card.dispatchEvent = (ev) => { events.push(ev); return true; };
  card.hass = makeHass({
    ...slotEntities(1, 0, { attributes }),
    [STATUS]: { state: status, attributes: {} },
  }, { entities: entities || registry() });
  await card._resolveDeviceId();
  card._updateIfChanged();
  return { card, events };
}

const find = (card, selector) => card._root.querySelectorAll(selector);
const click = (el, ev = {}) => el.onclick({ stopPropagation() {}, ...ev });

// --------------------------------------------------------------------------- //
// The handlers are actually bound
// --------------------------------------------------------------------------- //

test("the edit button gets a click handler", async () => {
  const { card } = await setup();
  const buttons = find(card, ".edit-btn, .edit-btn-mini");
  assert.ok(buttons.length, "an edit button is rendered");
  assert.equal(typeof buttons[0].onclick, "function", "and it is wired up");
});

test("clicking the edit button opens the dialog for that slot", async () => {
  const { card } = await setup();
  const opened = [];
  card._showEditDialog = (eid) => opened.push(eid);

  click(find(card, ".edit-btn, .edit-btn-mini")[0]);

  assert.deepEqual(opened, [SLOT], "the dialog opens for the clicked slot");
});

test("clicking a spool card asks HA to show more info", async () => {
  const { card, events } = await setup();
  const cards = find(card, ".spool-card");
  const populated = cards.find((c) => c.dataset.eid);
  assert.ok(populated, "a populated spool card is rendered");

  click(populated);

  assert.equal(events.length, 1, "exactly one event");
  assert.equal(events[0].type, "hass-more-info");
  assert.equal(events[0].detail.entityId, SLOT);
});

test("the edit button does not also open the entity dialog", async () => {
  // Both handlers match the same subtree, so the inner one has to stop
  // propagation or editing a slot would pop the more-info dialog behind it.
  const { card, events } = await setup();
  card._showEditDialog = () => {};
  let propagationStopped = false;

  click(find(card, ".edit-btn, .edit-btn-mini")[0], {
    stopPropagation() { propagationStopped = true; },
  });

  assert.ok(propagationStopped, "the edit click must not bubble");
  assert.equal(events.length, 0, "and must not dispatch more-info");
});

// --------------------------------------------------------------------------- //
// The blocked state explains itself
// --------------------------------------------------------------------------- //

test("a blocked edit button is aria-disabled, not natively disabled", async () => {
  // A native <button disabled> never dispatches click, so the toast explaining
  // why editing is blocked was unreachable -- and the title tooltip needs a
  // hover the wall tablet this card targets does not have.
  const { card } = await setup({ status: "printing" });
  const btn = find(card, ".edit-btn, .edit-btn-mini")[0];

  assert.equal(btn.getAttribute("aria-disabled"), "true");
  assert.ok(!btn.disabled, "a native disabled attribute would swallow the click");
});

test("clicking a blocked edit button explains why", async () => {
  const { card } = await setup({ status: "printing" });
  const toasts = [];
  card._showToast = (msg) => toasts.push(msg);
  let opened = false;
  card._showEditDialog = () => { opened = true; };

  click(find(card, ".edit-btn, .edit-btn-mini")[0]);

  assert.ok(!opened, "the dialog must stay shut");
  assert.equal(toasts.length, 1, "the user gets told why");
  assert.match(toasts[0], /printer is busy/i);
});

test("a card spanning two printers explains that on click", async () => {
  const B_SLOT = "sensor.printer_b_cfs_box_1_slot_0_filament";
  const { card } = await setup({
    config: { box0_slot0_filament: SLOT, box1_slot0_filament: B_SLOT },
    entities: registry({
      [B_SLOT]: { device_id: "dev_b", platform: "ha_creality_ws" },
    }),
  });
  const toasts = [];
  card._showToast = (msg) => toasts.push(msg);

  assert.equal(card._deviceIdError, "toast_multiple_devices", "fails closed");
  click(find(card, ".edit-btn, .edit-btn-mini")[0]);

  assert.equal(toasts.length, 1);
  assert.match(toasts[0], /more than one printer/i);
});

// --------------------------------------------------------------------------- //
// Unit selector
// --------------------------------------------------------------------------- //

test("the unit selector switches the displayed CFS", async () => {
  const twoBoxes = {
    box0_slot0_filament: SLOT,
    box1_slot0_filament: "sensor.printer_cfs_box_2_slot_0_filament",
  };
  const { card } = await setup({
    config: twoBoxes,
    entities: registry({
      "sensor.printer_cfs_box_2_slot_0_filament": {
        device_id: "dev_a", platform: "ha_creality_ws",
      },
    }),
  });
  card.hass = makeHass({
    ...slotEntities(1, 0, { attributes: ATTRS }),
    ...slotEntities(2, 0, { attributes: { ...ATTRS, box_id: 2 } }),
    [STATUS]: { state: "idle", attributes: {} },
  }, { entities: registry({
    "sensor.printer_cfs_box_2_slot_0_filament": {
      device_id: "dev_a", platform: "ha_creality_ws",
    },
  }) });

  const buttons = find(card, ".unit-btn");
  assert.ok(buttons.length >= 2, `two units expected, got ${buttons.length}`);
  assert.equal(card._selectedCFS, 0);

  click(buttons[1]);
  assert.equal(card._selectedCFS, 1, "the second unit is now selected");
});

test("an unparseable unit index is ignored", async () => {
  const { card } = await setup();
  card._selectedCFS = 0;
  // Guard against a malformed data-cfs leaving the card on a nonexistent unit.
  const fake = { dataset: { cfs: "not-a-number" } };
  const buttons = find(card, ".unit-btn");
  if (buttons.length) {
    buttons[0].dataset.cfs = fake.dataset.cfs;
    click(buttons[0]);
  }
  assert.equal(card._selectedCFS, 0);
});

let failed = 0;
for (const [name, fn] of tests) {
  try { await fn(); console.log(`ok   ${name}`); }
  catch (err) { failed += 1; console.log(`FAIL ${name}\n     ${err.message}`); }
}
console.log(`\n${tests.length - failed}/${tests.length} passed`);
process.exit(failed === 0 ? 0 : 1);
