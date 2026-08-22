/**
 * Colour presets.
 *
 * The behaviour worth pinning is not "a preset can be saved" but the failure
 * modes: a corrupt localStorage value must not break the dialog, a preset
 * mutation must not discard the form the user is filling in, and one card's
 * dialog must not reach into another's.
 */

import assert from "node:assert/strict";
import { loadCard, makeHass, slotEntities } from "./cfs_card_harness.mjs";

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

const SLOT = "sensor.printer_cfs_box_1_slot_0_filament";
const REGISTRY = { [SLOT]: { device_id: "dev_a", platform: "ha_creality_ws" } };
const ATTRS = { type: "PLA", vendor: "Creality", box_id: 1, slot_id: 0 };

async function setup(sandboxTweak) {
  const loaded = loadCard();
  if (sandboxTweak) sandboxTweak(loaded.sandbox);
  const card = new loaded.KCFSCard();
  card.setConfig({ box0_slot0_filament: SLOT });
  card.hass = makeHass(slotEntities(1, 0, { attributes: ATTRS }), { entities: REGISTRY });
  await card._resolveDeviceId();
  return { card, ...loaded };
}

/** The presets section of an open dialog. */
function presetsSection(card) {
  const slot = card._findSlot(SLOT);
  const form = card._renderEditForm(slot, () => {});
  const colourRow = form.children.find((c) => c.className === "colour-row");
  return colourRow.children.find((c) => c.className === "presets");
}

const swatchesOf = (section) => section.children.find((c) => c.className === "swatches").children;
const click = (el) => el._listeners?.click?.forEach((fn) => fn({}));

test("the standard Creality palette is offered", async () => {
  const { card } = await setup();
  const swatches = swatchesOf(presetsSection(card));
  assert.equal(swatches.length, 12, "twelve standard colours");
  assert.ok(swatches.some((s) => s.title === "Green"));
});

test("clicking a swatch fills both colour inputs", async () => {
  const { card } = await setup();
  const slot = card._findSlot(SLOT);
  const form = card._renderEditForm(slot, () => {});
  const colourRow = form.children.find((c) => c.className === "colour-row");
  const [picker, hex] = colourRow.children.find((c) => c.className === "colour-inputs").children;
  const green = swatchesOf(colourRow.children.find((c) => c.className === "presets"))
    .find((s) => s.title === "Green");
  click(green);
  assert.equal(hex.value, "#06c84f");
  assert.equal(picker.value, "#06c84f");
});

test("a saved preset appears alongside the standard ones", async () => {
  const { card } = await setup();
  card._presets = card._presets || undefined;
  const section = presetsSection(card);
  assert.equal(card._presets.save("Teal", "#008080"), true);
  const after = swatchesOf(presetsSection(card));
  assert.equal(after.length, 13);
  assert.ok(after.some((s) => s.title === "Teal"));
});

test("a preset is persisted, so a second card in the same browser sees it", async () => {
  const { card, sandbox } = await setup();
  presetsSection(card);
  card._presets.save("Teal", "#008080");

  const stored = JSON.parse(sandbox.localStorage.getItem("k-cfs-colour-presets"));
  assert.equal(stored.Teal, "#008080");

  // A fresh card reading the same storage picks it up.
  const second = new card.constructor();
  second.setConfig({ box0_slot0_filament: SLOT });
  second.hass = makeHass(slotEntities(1, 0, { attributes: ATTRS }), { entities: REGISTRY });
  await second._resolveDeviceId();
  assert.ok(swatchesOf(presetsSection(second)).some((s) => s.title === "Teal"));
});

test("an invalid colour is not stored", async () => {
  const { card } = await setup();
  presetsSection(card);
  assert.equal(card._presets.save("Bad", "not-a-colour"), false);
  assert.ok(!("Bad" in card._presets.presets));
});

test("a nameless preset is not stored", async () => {
  const { card } = await setup();
  presetsSection(card);
  assert.equal(card._presets.save("   ", "#008080"), false);
});

test("a corrupt stored value degrades to no presets", async () => {
  // Otherwise a hand-edited localStorage entry would break the whole dialog.
  const { card } = await setup((sandbox) => {
    sandbox.localStorage.setItem("k-cfs-colour-presets", "{not json");
  });
  const swatches = swatchesOf(presetsSection(card));
  assert.equal(swatches.length, 12, "standard colours still render");
});

test("a stored array rather than an object degrades safely", async () => {
  const { card } = await setup((sandbox) => {
    sandbox.localStorage.setItem("k-cfs-colour-presets", "[1,2,3]");
  });
  presetsSection(card);
  // Compared by key count, not deepEqual: objects built inside the vm sandbox
  // have a different Object.prototype and would never be deep-strict-equal.
  assert.equal(Object.keys(card._presets.presets).length, 0);
  assert.equal(swatchesOf(presetsSection(card)).length, 12);
});

test("deleting a preset leaves the standard palette intact", async () => {
  const { card } = await setup();
  presetsSection(card);
  card._presets.save("Teal", "#008080");
  assert.equal(card._presets.remove("Teal"), true);
  assert.equal(swatchesOf(presetsSection(card)).length, 12);
});

test("renaming moves the colour", async () => {
  const { card } = await setup();
  presetsSection(card);
  card._presets.save("Teal", "#008080");
  assert.equal(card._presets.rename("Teal", "Petrol"), true);
  assert.equal(card._presets.presets.Petrol, "#008080");
  assert.ok(!("Teal" in card._presets.presets));
});

test("a preset mutation does not discard the rest of the form", async () => {
  // PR #75 destroyed and reopened the whole dialog on a timer after saving a
  // preset, throwing away anything already typed.
  const { card } = await setup();
  const slot = card._findSlot(SLOT);
  const form = card._renderEditForm(slot, () => {});
  const colourRow = form.children.find((c) => c.className === "colour-row");
  const [, hex] = colourRow.children.find((c) => c.className === "colour-inputs").children;
  const section = colourRow.children.find((c) => c.className === "presets");
  const saveRow = section.children.find((c) => c.className === "preset-save");
  const [nameInput, saveBtn] = saveRow.children;

  hex.value = "#123456";
  nameInput.value = "Mine";
  click(saveBtn);

  assert.equal(card._presets.presets.Mine, "#123456");
  // Same form object, same colour still in the input.
  assert.equal(hex.value, "#123456");
  assert.equal(nameInput.value, "", "the name field clears, ready for the next one");
  assert.equal(swatchesOf(section).length, 13, "the row refreshed in place");
});

test("presets are not offered for a multi-colour spool", async () => {
  const { card } = await setup();
  const slot = { ...card._findSlot(SLOT), isMultiColour: true };
  const form = card._renderEditForm(slot, () => {});
  const colourRow = form.children.find((c) => c.className === "colour-row");
  assert.ok(!colourRow.children.some((c) => c.className === "presets"));
});

test("the real grey #cccccc can be saved as a preset", async () => {
  // _sanitizeColor returns #cccccc both for "unparseable" and for that actual
  // colour, so validating its output rejected a legitimate grey.
  const { card } = await setup();
  presetsSection(card); // opening the dialog is what creates the manager
  const storable = (colour) => {
    assert.equal(card._presets.save("Grey", colour), true, `${colour} must be storable`);
    assert.equal(card._presets.presets.Grey, "#cccccc");
    card._presets.remove("Grey");
  };
  storable("#cccccc");
  storable("CCCCCC");
  assert.equal(card._presets.save("Nope", "not-a-colour"), false);
  assert.equal(card._presets.save("Nope", ""), false);
  assert.equal(card._presets.save("", "#ff0000"), false);
});

test("a three-digit preset colour is expanded, not stored short", async () => {
  // The service only accepts six digits, so a preset stored as #abc would be
  // selectable from the palette and then rejected on save.
  const { card } = await setup();
  presetsSection(card);
  assert.equal(card._presets.save("Short", "#abc"), true);
  assert.equal(card._presets.presets.Short, "#aabbcc");
  card._presets.remove("Short");
  assert.equal(card._presets.save("Bad", "#ab"), false);
  assert.equal(card._presets.save("Bad", "#abcd"), false);
});

test("localStorage is not touched until a dialog opens", async () => {
  // A dashboard with several cards should not all hit storage just to render.
  const { card } = await setup();
  assert.equal(card._presets, undefined, "no presets manager before the dialog");
});

let failed = 0;
for (const [name, fn] of tests) {
  try { await fn(); console.log(`ok   ${name}`); }
  catch (err) { failed += 1; console.log(`FAIL ${name}\n     ${err.message}`); }
}
console.log(`\n${tests.length - failed}/${tests.length} passed`);
process.exit(failed === 0 ? 0 : 1);
