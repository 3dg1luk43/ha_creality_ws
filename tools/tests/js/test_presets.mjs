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

test("a saved preset is written to localStorage", async () => {
  const { card, sandbox } = await setup();
  presetsSection(card);
  card._presets.save("Teal", "#008080");

  const stored = JSON.parse(sandbox.localStorage.getItem("k-cfs-colour-presets"));
  assert.equal(stored.Teal, "#008080");
});

test("a preset already in localStorage is loaded back", async () => {
  // The read side, in a fresh sandbox: within one page the shared store is held
  // in memory, so nothing re-enters _load() and a broken loader would only show
  // up after a reload -- exactly when the user would notice their palette gone.
  const { card } = await setup((sandbox) => {
    sandbox.localStorage._seed(
      "k-cfs-colour-presets",
      JSON.stringify({ Teal: "#008080", Rust: "#b7410e" }),
    );
  });

  const swatches = swatchesOf(presetsSection(card));
  assert.equal(swatches.length, 14, "twelve standard colours plus the two stored");
  assert.ok(swatches.some((sw) => sw.title === "Teal"));
  assert.ok(swatches.some((sw) => sw.title === "Rust"));
  assert.equal(card._presets.presets.Rust, "#b7410e");
});

test("a preset saved in one page is visible after a reload", async () => {
  // Two sandboxes sharing one storage payload, which is what a reload is.
  const first = await setup();
  presetsSection(first.card);
  first.card._presets.save("Teal", "#008080");
  const payload = first.sandbox.localStorage.getItem("k-cfs-colour-presets");

  const second = await setup((sandbox) => {
    sandbox.localStorage._seed("k-cfs-colour-presets", payload);
  });
  assert.equal(second.card._presets === undefined, true, "not loaded until asked");
  presetsSection(second.card);
  assert.equal(second.card._presets.presets.Teal, "#008080");
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

test("right-clicking a custom swatch deletes it", async () => {
  // remove() is only reachable through the contextmenu listener, which also
  // calls preventDefault, gates its toast on the return value and rebuilds the
  // row. Calling remove() directly exercised none of that.
  const { card } = await setup();
  const section = presetsSection(card);
  card._presets.save("Teal", "#008080");
  const swatches = swatchesOf(presetsSection(card));
  assert.equal(swatches.length, 13);
  const custom = swatches.find((sw) => sw.title === "Teal");
  assert.ok(custom, "the custom swatch is rendered");

  let prevented = false;
  custom.fire("contextmenu", { preventDefault() { prevented = true; } });
  assert.ok(prevented, "the browser context menu must be suppressed");
  assert.ok(!("Teal" in card._presets.presets), "the preset is gone");
  assert.equal(swatchesOf(presetsSection(card)).length, 12, "the row rebuilt");
  assert.ok(section, "the section under test existed");
});

test("right-clicking a standard swatch does not delete it", async () => {
  const { card } = await setup();
  const swatches = swatchesOf(presetsSection(card));
  const standard = swatches.find((sw) => sw.title === "Green");
  standard.fire("contextmenu", { preventDefault() {} });
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

test("two cards on one dashboard share the preset store", async () => {
  // Each card used to build its own manager, caching localStorage in its own
  // constructor -- so a save or delete in one dialog left the other card serving
  // a stale copy until the page reloaded.
  const loaded = loadCard();
  const mk = () => {
    const card = new loaded.KCFSCard();
    card.setConfig({ box0_slot0_filament: SLOT });
    card.hass = makeHass(slotEntities(1, 0, { attributes: ATTRS }), { entities: REGISTRY });
    return card;
  };
  const a = mk();
  const b = mk();
  // Opening each dialog is what creates the manager.
  presetsSection(a);
  presetsSection(b);

  assert.equal(a._presets, b._presets, "both cards must use one store");
  a._presets.save("Teal", "#008080");
  assert.equal(b._presets.presets.Teal, "#008080", "a save is visible to the other card");
  a._presets.remove("Teal");
  assert.ok(!("Teal" in b._presets.presets), "a delete is visible to the other card");
});

test("localStorage is not touched until a dialog opens", async () => {
  // A dashboard with several cards should not all hit storage just to render.
  // Asserted on actual reads: `card._presets === undefined` is also true when the
  // store is built eagerly at module scope, which is the thing this forbids.
  const { card, sandbox } = await setup();
  const key = "k-cfs-colour-presets";

  assert.ok(
    !sandbox.localStorage._reads.includes(key),
    `storage was read before any dialog opened: ${sandbox.localStorage._reads}`,
  );
  assert.equal(card._presets, undefined, "and no manager exists yet");

  presetsSection(card);
  assert.ok(
    sandbox.localStorage._reads.includes(key),
    "opening the dialog is what should read it",
  );
});

let failed = 0;
for (const [name, fn] of tests) {
  try { await fn(); console.log(`ok   ${name}`); }
  catch (err) { failed += 1; console.log(`FAIL ${name}\n     ${err.message}`); }
}
console.log(`\n${tests.length - failed}/${tests.length} passed`);
process.exit(failed === 0 ? 0 : 1);
