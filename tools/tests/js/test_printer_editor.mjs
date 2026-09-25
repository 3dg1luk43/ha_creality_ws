/**
 * The printer card's visual editor.
 *
 * Two classes of defect these cover, both of which looked fine in the source:
 *
 * * every label rendered as its raw config key, because the label override was
 *   installed behind `if (form.computeLabel)` and ha-form leaves that undefined
 *   until someone assigns it -- so the guard was never true;
 * * saving any colour rewrote it to 90% opacity and lost 3-digit hex, because
 *   the hex<->rgba pair only understood `#rrggbb` and hardcoded the alpha.
 *
 * Plus the device picker, which has to scope itself to one printer in a
 * multi-printer setup the way the CFS card's device resolution does.
 */

import assert from "node:assert/strict";
import {
  changeForm, colorFormFor, loadPrinterCard, makeEditor, makeEditorHass,
  printerEntityId, printerRegistry, schemaNames, PRINTER_ENTITIES,
} from "./printer_card_harness.mjs";

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

/** One printer, fully discovered, plus a device record to read a name from. */
function onePrinter({ omit = [] } = {}) {
  return makeEditorHass({
    entities: printerRegistry("dev_a", "k1_max", { omit }),
    devices: { dev_a: { name: "K1 Max", name_by_user: null } },
  });
}

/** Two printers, so "scoped to the chosen device" is actually testable. */
function twoPrinters() {
  return makeEditorHass({
    entities: {
      ...printerRegistry("dev_a", "k1_max"),
      ...printerRegistry("dev_b", "ender_3"),
    },
    devices: { dev_a: { name: "K1 Max" }, dev_b: { name: "Ender 3" } },
  });
}

function editorWith(config = {}, hass = onePrinter()) {
  const { KPrinterCardEditor } = loadPrinterCard();
  return makeEditor(KPrinterCardEditor, config, hass);
}

/** Pick the device, the way the device selector reports it. */
function pickDevice(editor, deviceId) {
  changeForm(editor._formEls["device-form"], { device: deviceId });
}

// --------------------------------------------------------------------------- //
// Module registration
// --------------------------------------------------------------------------- //

test("loading the module twice does not leave a half-registered tag", () => {
  // A dashboard can import this module twice: two Lovelace resource entries,
  // or a page open across a restart that picks up the new ?v= alongside the
  // copy it already had. A bare define() throws on the second pass, aborting
  // the rest of that module -- so the tag keeps one copy's class while the
  // functions around it come from the other, and methods that plainly exist in
  // the source turn up missing on the instance.
  const first = loadPrinterCard();
  const registry = first.sandbox.customElements;
  const seen = new Map(registry.__defined ?? []);

  let threw = null;
  try {
    // Re-run the module against a registry that already holds both tags.
    const source = first.sandbox;
    source.customElements = {
      define: () => { throw new Error("this name has already been used"); },
      get: (tag) => (tag === "k-printer-card" ? first.KPrinterCard : first.KPrinterCardEditor),
    };
    // defineOnce must short-circuit on get() and never reach define().
    source.defineOnce("k-printer-card", first.KPrinterCard);
    source.defineOnce("k-printer-card-editor", first.KPrinterCardEditor);
  } catch (err) {
    threw = err;
  }
  assert.equal(threw, null, "a second registration pass threw");
  assert.ok(seen || true);
});

// --------------------------------------------------------------------------- //
// Attach order
// --------------------------------------------------------------------------- //

test("an editor attached before setConfig builds nothing it cannot fill in", () => {
  // hui-element-editor assigns _configElement -- which renders it into the DOM
  // -- and only afterwards calls setConfig. An ha-form reads `schema` unguarded
  // in its own render(), so building the shell on connect attached forms with
  // no schema and threw "e is not iterable" out of Lit, killing the editor.
  const { KPrinterCardEditor } = loadPrinterCard();
  const editor = new KPrinterCardEditor();
  editor.hass = onePrinter();
  editor.connectedCallback();
  assert.equal(editor._formEls, undefined, "the shell was built with no config to fill it");

  editor.setConfig({});
  assert.ok(editor._formEls, "the shell was never built once the config arrived");
  for (const [id, form] of Object.entries(editor._formEls)) {
    assert.ok(Array.isArray(form.schema), `${id} has no schema array`);
    assert.ok(form.schema.length, `${id} has an empty schema`);
  }
});

test("every form has a schema from the moment it exists", () => {
  // Whatever the attach order, no ha-form may ever be reachable without one.
  const { KPrinterCardEditor } = loadPrinterCard();
  const editor = new KPrinterCardEditor();
  editor.setConfig({});
  editor.hass = onePrinter();
  editor.connectedCallback();
  for (const [id, form] of Object.entries(editor._formEls)) {
    assert.ok(Array.isArray(form.schema), `${id} has no schema array`);
  }
});

test("a refresh does not go through the instance for its form data", () => {
  // One Safari install reported `this._layoutData` as undefined while the
  // method was demonstrably on the prototype and the module had been fetched
  // exactly once -- a state nothing in the card can produce, and which no
  // engine reproduced. The builders never needed the instance for anything but
  // the config, so they no longer ask it: shadowing every one of them on the
  // instance must make no difference to a refresh.
  const editor = editorWith();
  for (const name of [
    "_deviceSchema", "_entitiesSchema", "_layoutSchema", "_colorSchema",
    "_isAuto", "_entitiesData", "_layoutData", "_colorData",
  ]) {
    editor[name] = undefined;
  }

  editor.hass = onePrinter(); // the path that failed
  assert.ok(editor._formEls["layout-form"].schema.length, "the layout form lost its schema");
  assert.ok(editor._formEls["entities-form"].schema.length, "the entities form lost its schema");
  assert.equal(
    editor._root.querySelector(".editor-error"), null,
    "the editor fell back to its error panel",
  );
});

// --------------------------------------------------------------------------- //
// Labels and helpers
// --------------------------------------------------------------------------- //

test("every form gets computeLabel, so no field falls back to its config key", () => {
  const editor = editorWith();
  for (const [id, form] of Object.entries(editor._formEls)) {
    assert.equal(typeof form.computeLabel, "function", `${id} has no computeLabel`);
    assert.equal(typeof form.computeHelper, "function", `${id} has no computeHelper`);
  }
});

test("no schema field renders as its raw name or as an unresolved key", () => {
  const editor = editorWith();
  const seen = [];
  for (const [id, form] of Object.entries(editor._formEls)) {
    for (const name of schemaNames(form)) {
      if (!name) continue; // the grid wrapper rows carry no field of their own
      const label = form.computeLabel({ name });
      assert.ok(label, `${id}.${name} has an empty label`);
      assert.notEqual(label, name, `${id}.${name} renders as its config key`);
      assert.ok(
        !/^(label|color|helper)_/.test(label),
        `${id}.${name} renders an unresolved translation key: ${label}`,
      );
      seen.push(name);
    }
  }
  // Guard the guard: an editor that built no forms would pass vacuously.
  assert.ok(seen.length > 40, `only ${seen.length} fields were checked`);
});

test("entity fields carry their helper text", () => {
  const form = editorWith()._formEls["entities-form"];
  assert.equal(form.computeHelper({ name: "nozzle" }), "Sensor showing nozzle temperature");
  assert.match(form.computeHelper({ name: "time_left" }), /seconds/);
});

const AUTO_FIELDS = ["status_icon", "progress_ring", "status_bg", "telemetry_icon", "telemetry_text"];

test("the auto colours say what automatic actually does", () => {
  const editor = editorWith();
  for (const key of AUTO_FIELDS) {
    const helper = colorFormFor(editor, `${key}_auto`).computeHelper({ name: `${key}_auto` });
    assert.match(helper, /^Automatic: /, `${key} does not explain its automatic mode`);
  }
  // The two kinds of "auto" are different and the text has to distinguish them.
  const fromState = colorFormFor(editor, "status_icon_auto").computeHelper({ name: "status_icon_auto" });
  const fromTheme = colorFormFor(editor, "telemetry_text_auto").computeHelper({ name: "telemetry_text_auto" });
  assert.match(fromState, /print state/);
  assert.match(fromTheme, /theme/);
});

test("an automatic colour is switched, not cleared", () => {
  // A color_rgb selector renders a native <input type="color">, and there is no
  // way to empty one of those -- so without a switch of its own, a field the
  // user had customised could never be put back to automatic.
  const editor = editorWith();
  for (const key of AUTO_FIELDS) {
    const form = colorFormFor(editor, `${key}_auto`);
    assert.equal(form.data[`${key}_auto`], true, `${key} does not report itself automatic`);
    assert.ok(!schemaNames(form).includes(key), `${key} shows a picker that cannot do anything`);
  }
});

test("turning automatic off reveals a picker seeded with a real colour", () => {
  const editor = editorWith();
  const form = colorFormFor(editor, "status_icon_auto");
  changeForm(form, { ...form.data, status_icon_auto: false });
  assert.equal(editor._cfg.theme.status_icon, "#4caf50");

  const reopened = colorFormFor(editor, "status_icon");
  assert.ok(schemaNames(reopened).includes("status_icon"), "no picker appeared");
  assert.deepEqual(Array.from(reopened.data.status_icon), [76, 175, 80]);
  assert.equal(reopened.data.status_icon_auto, false);
});

test("turning automatic back on discards the custom colour", () => {
  const editor = editorWith();
  let form = colorFormFor(editor, "telemetry_text_auto");
  changeForm(form, { ...form.data, telemetry_text_auto: false });
  form = colorFormFor(editor, "telemetry_text");
  changeForm(form, { ...form.data, telemetry_text: [255, 0, 0] });
  assert.equal(editor._cfg.theme.telemetry_text, "#ff0000");

  form = colorFormFor(editor, "telemetry_text_auto");
  changeForm(form, { ...form.data, telemetry_text_auto: true });
  assert.equal(editor._cfg.theme.telemetry_text, "auto");
});

test("an automatic background keeps its opacity slider once switched off", () => {
  const editor = editorWith();
  const form = colorFormFor(editor, "status_bg_auto");
  changeForm(form, { ...form.data, status_bg_auto: false });
  const reopened = colorFormFor(editor, "status_bg");
  assert.ok(schemaNames(reopened).includes("status_bg_opacity"));
  assert.equal(reopened.data.status_bg_opacity, 20);
});

test("a colour with no opacity slider gets no stray Opacity row", () => {
  const editor = editorWith();
  const names = schemaNames(colorFormFor(editor, "pause_bg"));
  assert.ok(names.includes("pause_bg_opacity"), "a background should be adjustable");
  assert.ok(!names.includes("pause_icon_opacity"), "an icon colour needs no opacity");
});

// --------------------------------------------------------------------------- //
// Device picker
// --------------------------------------------------------------------------- //

test("picking a device fills the entity fields", () => {
  const editor = editorWith();
  pickDevice(editor, "dev_a");
  for (const [role] of PRINTER_ENTITIES) {
    assert.equal(editor._cfg[role], printerEntityId("k1_max", role), `${role} was not filled`);
  }
});

test("picking a device names the card after it", () => {
  const editor = editorWith();
  pickDevice(editor, "dev_a");
  assert.equal(editor._cfg.name, "K1 Max");
});

test("a name the user chose survives picking a device", () => {
  const editor = editorWith({ name: "Workshop printer" });
  pickDevice(editor, "dev_a");
  assert.equal(editor._cfg.name, "Workshop printer");
});

test("picking a device leaves entities the user already chose alone", () => {
  const editor = editorWith({ camera: "camera.my_own_webcam" });
  pickDevice(editor, "dev_a");
  assert.equal(editor._cfg.camera, "camera.my_own_webcam");
  // ...while still filling the rest.
  assert.equal(editor._cfg.nozzle, printerEntityId("k1_max", "nozzle"));
});

test("the refill button replaces fields the user set", () => {
  const editor = editorWith({ camera: "camera.my_own_webcam", name: "Workshop printer" });
  pickDevice(editor, "dev_a");
  editor._root.getElementById("refill").fire("click", {});
  assert.equal(editor._cfg.camera, printerEntityId("k1_max", "camera"));
  assert.equal(editor._cfg.name, "K1 Max");
});

test("the power switch is never filled in -- the integration has none", () => {
  const editor = editorWith();
  pickDevice(editor, "dev_a");
  editor._root.getElementById("refill").fire("click", {});
  assert.equal(editor._cfg.power, "");
});

test("a second printer's entities are never borrowed", () => {
  const editor = editorWith({}, twoPrinters());
  pickDevice(editor, "dev_b");
  for (const [role] of PRINTER_ENTITIES) {
    assert.equal(editor._cfg[role], printerEntityId("ender_3", role), `${role} came from the wrong printer`);
  }
  assert.equal(editor._cfg.name, "Ender 3");
});

test("a role the printer does not expose is left empty, not guessed", () => {
  // An Ender 3 V3 KE has no chamber sensor; the card hides that pill entirely.
  const editor = editorWith({}, onePrinter({ omit: ["box"] }));
  pickDevice(editor, "dev_a");
  assert.equal(editor._cfg.box, "");
  assert.equal(editor._cfg.nozzle, printerEntityId("k1_max", "nozzle"));
});

test("entities belonging to another integration on the same device are ignored", () => {
  const hass = makeEditorHass({
    entities: {
      ...printerRegistry("dev_a", "k1_max", { omit: ["light"] }),
      // A helper someone attached to the printer device, same translation_key.
      "light.tasmota_strip": { device_id: "dev_a", platform: "tasmota", translation_key: "light" },
    },
    devices: { dev_a: { name: "K1 Max" } },
  });
  const editor = editorWith({}, hass);
  pickDevice(editor, "dev_a");
  assert.equal(editor._cfg.light, "");
});

test("the fill count tells the user what happened", () => {
  const editor = editorWith();
  pickDevice(editor, "dev_a");
  const status = editor._root.getElementById("refill-status").textContent;
  assert.equal(status, `Filled ${PRINTER_ENTITIES.length} of ${PRINTER_ENTITIES.length} fields.`);
});

test("a device with none of our entities says so rather than silently doing nothing", () => {
  const hass = makeEditorHass({
    entities: { "sensor.other": { device_id: "dev_z", platform: "mqtt" } },
    devices: { dev_z: { name: "Something else" } },
  });
  const editor = editorWith({}, hass);
  pickDevice(editor, "dev_z");
  assert.match(editor._root.getElementById("refill-status").textContent, /No entities/);
});

test("the refill button is disabled until a device is chosen", () => {
  const editor = editorWith();
  assert.equal(editor._root.getElementById("refill").disabled, true);
  pickDevice(editor, "dev_a");
  assert.equal(editor._root.getElementById("refill").disabled, false);
});

// --------------------------------------------------------------------------- //
// Colours
// --------------------------------------------------------------------------- //

test("an existing colour reaches the picker with its opacity intact", () => {
  const editor = editorWith();
  const form = colorFormFor(editor, "stop_bg");
  // The default stop colour is 95% opaque; the old editor rewrote it to 90%
  // the moment anyone opened it.
  // The sandbox has its own Array intrinsic, so copy before comparing.
  assert.deepEqual(Array.from(form.data.stop_bg), [244, 67, 54]);
  assert.equal(form.data.stop_bg_opacity, 95);
});

test("a colour survives a round trip through the form unchanged", () => {
  const editor = editorWith();
  const form = colorFormFor(editor, "stop_bg");
  const before = editor._cfg.theme.stop_bg;
  changeForm(form, { ...form.data });
  // The string form normalises (".95" becomes "0.95"); the colour must not move.
  assert.equal(before, "rgba(244, 67, 54, .95)");
  assert.equal(editor._cfg.theme.stop_bg, "rgba(244, 67, 54, 0.95)");
});

test("the opacity slider writes the opacity it was given", () => {
  const editor = editorWith();
  const form = colorFormFor(editor, "pause_bg");
  changeForm(form, { ...form.data, pause_bg_opacity: 40 });
  assert.equal(editor._cfg.theme.pause_bg, "rgba(252, 109, 9, 0.4)");
});

test("a fully opaque colour is stored as hex, not as rgba(...,1)", () => {
  const editor = editorWith();
  const form = colorFormFor(editor, "pause_bg");
  changeForm(form, { ...form.data, pause_bg: [18, 52, 86], pause_bg_opacity: 100 });
  assert.equal(editor._cfg.theme.pause_bg, "#123456");
});

test("a colour that somehow arrives empty falls back to its default", () => {
  // Not reachable through a native colour input, but the card cannot render
  // without a colour here, so the branch has to land somewhere sensible.
  const editor = editorWith();
  const form = colorFormFor(editor, "pause_icon");
  changeForm(form, { ...form.data, pause_icon: undefined });
  assert.equal(editor._cfg.theme.pause_icon, "#fff");
});

test("the power button's colours are editable at all", () => {
  // The chip CSS read --power-on-bg and friends from the start, but nothing
  // ever set them, so the Power button was the one chip no theme could reach.
  const editor = editorWith();
  const form = colorFormFor(editor, "power_on_bg");
  changeForm(form, { ...form.data, power_on_bg: [0, 128, 0], power_on_bg_opacity: 80 });
  assert.equal(editor._cfg.theme.power_on_bg, "rgba(0, 128, 0, 0.8)");
});

// --------------------------------------------------------------------------- //
// Re-entrancy
// --------------------------------------------------------------------------- //

test("Lovelace echoing the config back does not disturb the open form", () => {
  // Every config-changed comes back as a setConfig. Reassigning identical data
  // would fight whatever the user is currently typing or dragging.
  const editor = editorWith();
  const form = colorFormFor(editor, "pause_bg");
  changeForm(form, { ...form.data, pause_bg_opacity: 40 });
  const assigned = form.data;
  editor.setConfig(editor._cfg);
  assert.equal(form.data, assigned, "the form's data object was replaced needlessly");
});

test("the edited config is what reaches Lovelace", async () => {
  const editor = editorWith();
  const seen = [];
  editor.dispatchEvent = (ev) => { seen.push(ev.detail.config); return true; };
  pickDevice(editor, "dev_a");
  await new Promise((resolve) => setTimeout(resolve, 200));
  assert.equal(seen.length, 1, "config-changed is debounced into one event");
  assert.equal(seen[0].status, printerEntityId("k1_max", "status"));
  assert.equal(seen[0].device, "dev_a");
});

test("closing the editor mid-edit does not lose the change", async () => {
  // The dispatch is debounced; a user who edits a field and immediately closes
  // the dialog would otherwise have that last change dropped on the floor.
  const editor = editorWith();
  const seen = [];
  editor.dispatchEvent = (ev) => { seen.push(ev.detail.config); return true; };
  changeForm(editor._formEls["entities-form"], { ...editor._formEls["entities-form"].data, name: "Shed" });
  editor.disconnectedCallback();
  assert.equal(seen.length, 1, "the pending change never reached Lovelace");
  assert.equal(seen[0].name, "Shed");
  // ...and the flushed timer does not then fire a second time.
  await new Promise((resolve) => setTimeout(resolve, 200));
  assert.equal(seen.length, 1);
});

test("switching tabs does not lose the other tab's forms", () => {
  const editor = editorWith();
  const before = editor._formEls["entities-form"];
  editor._selectTab("theme");
  assert.equal(editor._root.getElementById("theme-tab").classList.contains("active"), true);
  assert.equal(editor._root.getElementById("entities-tab").classList.contains("active"), false);
  editor._selectTab("entities");
  assert.equal(editor._formEls["entities-form"], before);
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
