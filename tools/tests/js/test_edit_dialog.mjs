/**
 * The material edit dialog.
 *
 * The payload assembly is where quiet data loss lives: a truthiness check that
 * drops a zero, a colour flattened from a two-colour spool, an rfid replaced
 * with "". Each of those is asserted on the actual service call the card makes.
 */

import assert from "node:assert/strict";
import { loadCard, makeHass, slotEntities } from "./cfs_card_harness.mjs";

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

const SLOT = "sensor.printer_cfs_box_1_slot_0_filament";
const REGISTRY = {
  [SLOT]: { device_id: "dev_a", platform: "ha_creality_ws" },
  "sensor.printer_cfs_box_1_slot_0_color": { device_id: "dev_a", platform: "ha_creality_ws" },
  "sensor.printer_cfs_box_1_slot_0_percent": { device_id: "dev_a", platform: "ha_creality_ws" },
  "sensor.printer_print_status": {
    device_id: "dev_a", platform: "ha_creality_ws", translation_key: "print_status",
  },
};

const ATTRS = {
  type: "PLA", vendor: "Creality", rfid: "001001",
  min_temp: 190, max_temp: 240, pressure: 0.04,
  box_id: 1, slot_id: 0,
};

/** A card with one editable slot; `calls` records every service call. */
async function setup({ attributes = ATTRS, color = "#ffffff", status = "idle" } = {}) {
  const { KCFSCard } = loadCard();
  const card = new KCFSCard();
  card.setConfig({
    box0_slot0_filament: SLOT,
    box0_slot0_color: "sensor.printer_cfs_box_1_slot_0_color",
    box0_slot0_percent: "sensor.printer_cfs_box_1_slot_0_percent",
  });
  const calls = [];
  const states = {
    ...slotEntities(1, 0, { color, attributes }),
    "sensor.printer_print_status": { state: status, attributes: {} },
  };
  card.hass = makeHass(states, {
    entities: REGISTRY,
    callService: async (domain, service, data) => { calls.push({ domain, service, data }); },
  });
  await card._resolveDeviceId();
  return { card, calls, slot: card._findSlot(SLOT) };
}

const saved = (calls) => calls.find((c) => c.service === "set_cfs_material")?.data;

// --------------------------------------------------------------------------- //
// Payload assembly
// --------------------------------------------------------------------------- //

test("addresses the printer's box and slot, not the card position", async () => {
  const { card, calls, slot } = await setup({
    attributes: { ...ATTRS, box_id: 3, slot_id: 2 },
  });
  assert.equal(await card._saveMaterial(slot, { type: "PLA" }), true);
  const data = saved(calls);
  assert.equal(data.box_id, 3);
  assert.equal(data.slot_id, 2);
  assert.equal(data.device_id, "dev_a");
});

test("passes the existing rfid through instead of blanking it", async () => {
  const { card, calls, slot } = await setup();
  await card._saveMaterial(slot, { type: "PLA", color: "#00ff00" });
  assert.equal(saved(calls).rfid, "001001");
});

test("omits rfid entirely when the slot has none", async () => {
  const { card, calls, slot } = await setup({ attributes: { ...ATTRS, rfid: undefined } });
  await card._saveMaterial(slot, { type: "PLA" });
  assert.ok(!("rfid" in saved(calls)));
});

test("a zero pressure is sent, not replaced by a default", async () => {
  // `parseFloat(0) || 0.04` would silently write 0.04 here.
  const { card, calls, slot } = await setup();
  await card._saveMaterial(slot, { type: "PLA", pressure: 0 });
  assert.equal(saved(calls).pressure, 0);
});

test("blank optional fields are omitted rather than sent empty", async () => {
  const { card, calls, slot } = await setup();
  await card._saveMaterial(slot, { type: "PLA", name: "  ", vendor: "" });
  const data = saved(calls);
  assert.ok(!("name" in data));
  assert.ok(!("vendor" in data));
});

test("a colour without the leading hash is normalised", async () => {
  const { card, calls, slot } = await setup();
  await card._saveMaterial(slot, { type: "PLA", color: "06c84f" });
  assert.equal(saved(calls).color, "#06c84f");
});

test("refreshes CFS info after a successful write", async () => {
  const { card, calls, slot } = await setup();
  await card._saveMaterial(slot, { type: "PLA" });
  assert.ok(calls.some((c) => c.service === "request_cfs_info"));
});

// --------------------------------------------------------------------------- //
// Validation: refuse rather than silently correct
// --------------------------------------------------------------------------- //

test("an inverted temperature range is refused, not clamped", async () => {
  const { card, calls, slot } = await setup();
  const ok = await card._saveMaterial(slot, { type: "PLA", min_temp: 240, max_temp: 200 });
  assert.equal(ok, false);
  assert.equal(calls.length, 0, "nothing must be sent");
});

test("equal temperatures are allowed", async () => {
  const { card, calls, slot } = await setup();
  assert.equal(await card._saveMaterial(slot, { type: "PLA", min_temp: 210, max_temp: 210 }), true);
  assert.equal(saved(calls).min_temp, 210);
});

test("a malformed colour is refused", async () => {
  const { card, calls, slot } = await setup();
  assert.equal(await card._saveMaterial(slot, { type: "PLA", color: "not-a-colour" }), false);
  assert.equal(calls.length, 0);
});

test("an empty material type is refused", async () => {
  const { card, calls, slot } = await setup();
  assert.equal(await card._saveMaterial(slot, { type: "   " }), false);
  assert.equal(calls.length, 0);
});

test("a failing service call reports false and does not claim success", async () => {
  const { card, slot } = await setup();
  card._hass.callService = async () => { throw new Error("printer said no"); };
  assert.equal(await card._saveMaterial(slot, { type: "PLA" }), false);
});

// --------------------------------------------------------------------------- //
// Dialog behaviour
// --------------------------------------------------------------------------- //

/** Overlays currently attached to the shadow root (toasts are children too). */
const overlays = (card) => card.children.filter((c) => c.className === "edit-overlay");

test("the dialog attaches inside the shadow root", async () => {
  // PR #75 appended to document.body, which put it beyond the injected <style>.
  const { card } = await setup();
  assert.equal(overlays(card).length, 0);
  card._showEditDialog(SLOT);
  assert.equal(overlays(card).length, 1);
});

test("a busy printer refuses to open the dialog", async () => {
  const { card } = await setup({ status: "printing" });
  card._showEditDialog(SLOT);
  assert.equal(overlays(card).length, 0, "no dialog while printing");
  // ...and the user is told why rather than being ignored.
  assert.ok(
    card.children.some((c) => c.className === "cfs-toast"),
    "a toast must explain the refusal",
  );
});

test("the edit button is disabled and shows a lock while printing", async () => {
  const { card, slot } = await setup({ status: "printing" });
  const html = card._renderEditButton(slot);
  // The tooltip reads "Editing is disabled while the printer is busy", so a bare
  // /disabled/ would pass even with the attribute removed from the <button>.
  assert.match(html, /aria-disabled="true"/);
  assert.match(html, /mdi:lock/);
});

test("the edit button is enabled and shows a pencil when idle", async () => {
  const { card, slot } = await setup();
  const html = card._renderEditButton(slot);
  assert.ok(!/aria-disabled="true"/.test(html));
  assert.match(html, /mdi:pencil/);
});

test("a multi-colour spool cannot have its colour written", async () => {
  const { card, calls, slot } = await setup({ color: "#ffa800,#ff97e1" });
  assert.equal(slot.isMultiColour, true);
  // The dialog passes color: undefined for these; the type still saves.
  assert.equal(await card._saveMaterial(slot, { type: "PETG", color: undefined }), true);
  const data = saved(calls);
  assert.ok(!("color" in data), "colour must be left untouched");
  assert.equal(data.type, "PETG");
});

test("an unaddressable slot cannot be edited", async () => {
  // Two halves: the affordance is locked, *and* the dialog refuses if something
  // opens it anyway. Only asserting the first left _showEditDialog free to drop
  // its guard, and _saveMaterial copies printerBoxId straight into the payload
  // without re-validating -- so the service would receive box_id: null.
  const { card, slot } = await setup({ attributes: { type: "PLA" } });
  // The entity id supplies box 1 via the naming convention, so the unaddressable
  // case has to be forced -- and forced through _findSlot, which is what
  // _showEditDialog consults.
  const orphan = { ...slot, printerBoxId: null };
  card._findSlot = (eid) => (eid === SLOT ? orphan : null);

  assert.match(card._renderEditButton(orphan), /aria-disabled="true"/);

  const toasts = [];
  card._showToast = (msg) => toasts.push(msg);
  card._showEditDialog(SLOT);

  assert.ok(
    !card._root.children.some((c) => c.className === "edit-overlay"),
    "the dialog must refuse to open",
  );
  assert.equal(toasts.length, 1, "and say why");
});

test("cancel closes the dialog", async () => {
  const { card } = await setup();
  card._showEditDialog(SLOT);
  assert.equal(overlays(card).length, 1);
  const form = overlays(card)[0].children[0].children[0];
  const cancel = form.children.find((c) => c.className === "dialog-actions")
    .children.find((b) => b.className === "dialog-btn secondary");
  cancel._listeners.click.forEach((fn) => fn({}));
  assert.equal(overlays(card).length, 0, "cancel must dismiss the overlay");
});

test("only one toast is shown at a time", async () => {
  const { card } = await setup({ status: "printing" });
  card._showEditDialog(SLOT);
  card._showEditDialog(SLOT);
  const toasts = card.children.filter((c) => c.className === "cfs-toast");
  assert.equal(toasts.length, 1, "a second toast must replace the first");
});

test("a guessed target warns in the dialog", async () => {
  const { card } = await setup();
  const slot = { ...card._findSlot(SLOT), targetIsGuessed: true };
  const form = card._renderEditForm(slot, () => {});
  const warned = form.children.some((c) => c.className === "edit-warning");
  assert.ok(warned, "a guessed box id must be surfaced before saving");
});

test("a confirmed target does not warn", async () => {
  const { card, slot } = await setup();
  const form = card._renderEditForm(slot, () => {});
  assert.ok(!form.children.some((c) => c.className === "edit-warning"));
});

test("the name field prefills the bare material name, not the display label", async () => {
  // The filament sensor's *state* is "Creality Hyper PLA" (vendor + name). Writing
  // that back into `name` duplicates the vendor inside it -- the field-duplication
  // problem PR #75 listed as unresolved.
  const { card } = await setup({
    attributes: { ...ATTRS, name: "Hyper PLA", vendor: "Creality" },
  });
  const slot = card._findSlot(SLOT);
  assert.equal(slot.name, "Creality Hyper PLA", "display label is the composed one");
  assert.equal(slot.materialName, "Hyper PLA", "raw name is kept separately");

  const form = card._renderEditForm(slot, () => {});
  const haForm = form.children.find((c) => c.tagName === "HA-FORM");
  assert.equal(haForm.data.name, "Hyper PLA", "the dialog prefills the bare name");
  assert.equal(haForm.data.vendor, "Creality");
});

test("a save round-trip does not accumulate the vendor in the name", async () => {
  const { card, calls } = await setup({
    attributes: { ...ATTRS, name: "Hyper PLA", vendor: "Creality" },
  });
  const slot = card._findSlot(SLOT);
  const form = card._renderEditForm(slot, () => {});
  const haForm = form.children.find((c) => c.tagName === "HA-FORM");
  await card._saveMaterial(slot, { ...haForm.data, color: "#00ff00" });
  assert.equal(saved(calls).name, "Hyper PLA");
});

// --------------------------------------------------------------------------- //
// Markup safety and dialog semantics
// --------------------------------------------------------------------------- //

test("device-supplied values are escaped before reaching innerHTML", async () => {
  // Filament names and colours come from printer telemetry. A quote or angle
  // bracket would otherwise close the attribute or element it sits in.
  const hostile = '"><img src=x onerror=alert(1)>';
  const { card } = await setup({
    attributes: { ...ATTRS, type: hostile, name: hostile },
    color: hostile,
  });
  const slot = card._findSlot(SLOT);
  const boxes = [{
    id: 1,
    slots: [slot, slot, slot, slot],
    temp: hostile,
    humidity: hostile,
    humidityColor: hostile,
  }];
  const external = { ...slot, entity_id: hostile, percentText: hostile };
  const rendered = {
    spoolCard: card._renderSpoolCard(slot),
    spoolMini: card._renderSpoolMini(slot),
    editButton: card._renderEditButton(slot),
    boxMode: card._renderBoxMode(boxes, external),
    normalMode: card._renderNormalMode(boxes, external),
    compactMode: card._renderCompactMode(boxes, external),
  };
  for (const [where, html] of Object.entries(rendered)) {
    // The payload must survive only as escaped text, never as markup. Checking
    // for the verbatim string is enough: any unescaped interpolation reproduces
    // it exactly, and escaping turns every character of it into an entity.
    assert.ok(!html.includes(hostile), `${where}: the raw payload was interpolated`);
    assert.ok(!html.includes("<img src=x"), `${where}: an injected <img survived`);
  }
  assert.ok(
    rendered.spoolCard.includes("&lt;img"),
    "the payload should still be visible, escaped",
  );
});

test("the edit dialog announces itself and closes on Escape", async () => {
  const { card } = await setup();
  card._showEditDialog(SLOT);
  const overlay = card._root.children.find((c) => c.className === "edit-overlay");
  assert.ok(overlay, "the overlay is in the shadow root");
  const dialog = overlay.children.find((c) => c.className === "edit-dialog");
  assert.equal(dialog.getAttribute("role"), "dialog");
  assert.equal(dialog.getAttribute("aria-modal"), "true");
  assert.ok(dialog.getAttribute("aria-label"), "the dialog is labelled");
  assert.equal(dialog._focused, true, "focus moves into the dialog");

  overlay.fire("keydown", { key: "Escape", stopPropagation() {} });
  assert.ok(
    !card._root.children.includes(overlay),
    "Escape must dismiss the dialog",
  );
});

let failed = 0;
for (const [name, fn] of tests) {
  try { await fn(); console.log(`ok   ${name}`); }
  catch (err) { failed += 1; console.log(`FAIL ${name}\n     ${err.message}`); }
}
console.log(`\n${tests.length - failed}/${tests.length} passed`);
process.exit(failed === 0 ? 0 : 1);
