/**
 * Keyboard, screen-reader and contrast basics for both cards (R42).
 *
 * The chips removed their focus outline with nothing in its place, off-state
 * icons were fixed black (2.26:1 on a dark theme), the CFS temperature was a
 * light orange (1.73:1 on a light theme), the editor tabs were plain divs a
 * keyboard could not reach, the toast was silent to screen readers, the pulse
 * ignored reduced motion, and Tab walked out of the modal edit dialog.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { loadCard, loadCardModule, makeHass, slotEntities } from "./cfs_card_harness.mjs";
import { loadPrinterCard, makeEditor, makeEditorHass } from "./printer_card_harness.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const CFS_PATH = resolve(HERE, "../../../custom_components/ha_creality_ws/www/k_cfs_card.js");
const CFS_SOURCE = readFileSync(CFS_PATH, "utf8");

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

function printerCard(theme) {
  const { KPrinterCard } = loadPrinterCard();
  const card = new KPrinterCard();
  card.setConfig({
    name: "K1C", status: "sensor.s", light: "light.l", pause_btn: "button.p", stop_btn: "button.x",
    ...(theme ? { theme } : {}),
  });
  card.hass = {
    states: {
      "sensor.s": { state: "printing", attributes: {} },
      "light.l": { state: "off", attributes: {} },
      "button.p": { state: "unknown", attributes: {} },
      "button.x": { state: "unknown", attributes: {} },
    },
    language: "en", locale: { language: "en" },
    formatEntityState: (st) => String(st?.state ?? "-"),
    callService: async () => {},
  };
  return card;
}

// --------------------------------------------------------------------------- //
// Printer card
// --------------------------------------------------------------------------- //

test("an off chip's icon follows the theme's text colour", () => {
  const html = printerCard()._root.innerHTML;
  for (const key of ["light", "power", "custom"]) {
    assert.ok(html.includes(`--${key}-icon-off: var(--primary-text-color)`), key);
  }
});

test("a colour the user picked for an off icon is kept", () => {
  const html = printerCard({ light_icon_off: "#123456" })._root.innerHTML;
  assert.ok(html.includes("--light-icon-off: #123456"));
});

test("chips and the title show where keyboard focus is", () => {
  const html = printerCard()._root.innerHTML;
  assert.ok(/\.chip:focus-visible[^{]*\{[^}]*outline:\s*2px solid/.test(html));
  assert.ok(html.includes(".title.click:focus-visible"));
});

test("every chip has an accessible name", () => {
  const card = printerCard();
  const chips = card._root.getElementById("chips-container").children;
  assert.ok(chips.length >= 2);
  for (const chip of chips) {
    assert.ok(chip.getAttribute("aria-label"), `${chip.id} has no aria-label`);
  }
});

test("the printer editor's tabs are keyboard-reachable tabs", () => {
  const { KPrinterCardEditor } = loadPrinterCard();
  const editor = makeEditor(KPrinterCardEditor, {}, makeEditorHass({}));
  const tabs = editor._root.querySelectorAll(".tab");
  assert.equal(tabs.length, 2);
  for (const tab of tabs) {
    assert.equal(tab.tagName.toLowerCase(), "button");
    assert.equal(tab.getAttribute("role"), "tab");
  }
  editor._selectTab("theme");
  assert.deepEqual(tabs.map((t) => t.getAttribute("aria-selected")), ["false", "true"]);
});

// --------------------------------------------------------------------------- //
// CFS card
// --------------------------------------------------------------------------- //

test("CFS readings take their contrast from the theme", () => {
  assert.ok(!/\.env-mini \.temp \{\s*color: #ffb74d/.test(CFS_SOURCE), "the bare light orange is back");
  assert.ok(CFS_SOURCE.includes("color-mix(in srgb, #ffb74d 45%, var(--primary-text-color))"));
  assert.ok(CFS_SOURCE.includes("color-mix(in srgb, var(--hum-color, #64b5f6) 45%, var(--primary-text-color))"));
  assert.ok(!CFS_SOURCE.includes('style="color: ${KCFSCard._sanitizeColor('), "humidity colour set directly again");
});

test("the status pulse stops for reduced motion", () => {
  assert.ok(/@media \(prefers-reduced-motion: reduce\)\s*\{\s*\.status-badge \{ animation: none; \}/.test(CFS_SOURCE));
});

async function cfsCard(status = "idle") {
  const { KCFSCard } = loadCard();
  const card = new KCFSCard();
  const SLOT = "sensor.printer_cfs_box_1_slot_0_filament";
  card.setConfig({
    box0_slot0_filament: SLOT,
    box0_slot0_color: "sensor.printer_cfs_box_1_slot_0_color",
    box0_slot0_percent: "sensor.printer_cfs_box_1_slot_0_percent",
  });
  card.hass = makeHass({
    ...slotEntities(1, 0, {
      color: "#ffffff",
      attributes: { type: "PLA", vendor: "Creality", min_temp: 190, max_temp: 240, box_id: 1, slot_id: 0 },
    }),
    "sensor.printer_print_status": { state: status, attributes: {} },
  }, {
    entities: {
      [SLOT]: { device_id: "dev_a", platform: "ha_creality_ws" },
      "sensor.printer_cfs_box_1_slot_0_color": { device_id: "dev_a", platform: "ha_creality_ws" },
      "sensor.printer_cfs_box_1_slot_0_percent": { device_id: "dev_a", platform: "ha_creality_ws" },
      "sensor.printer_print_status": {
        device_id: "dev_a", platform: "ha_creality_ws", translation_key: "print_status",
      },
    },
    callService: async () => {},
  });
  await card._resolveDeviceId();
  return { card, SLOT };
}

test("the toast is announced", async () => {
  const { card } = await cfsCard();
  card._showToast("Saved");
  const toast = card.children.find((c) => c.className === "cfs-toast");
  assert.equal(toast.getAttribute("role"), "status");
  assert.equal(toast.getAttribute("aria-live"), "polite");
});

test("Tab cannot leave the edit dialog", async () => {
  const { card, SLOT } = await cfsCard();
  card._showEditDialog(SLOT);
  const overlay = card.children.find((c) => c.className === "edit-overlay");
  const [before, dialog, after] = overlay.children;
  assert.equal(dialog.className, "edit-dialog");
  for (const sentinel of [before, after]) {
    assert.equal(sentinel.className, "focus-sentinel");
    assert.equal(sentinel.tabIndex, 0);
  }
  // Tabbing past the end lands back on the dialog...
  dialog._focused = false;
  after._listeners.focus.forEach((fn) => fn({}));
  assert.ok(dialog._focused, "Tab past the last control must return to the dialog");
  // ...and Shift+Tab before the start lands on the last button.
  before._listeners.focus.forEach((fn) => fn({}));
  const buttons = Array.from(dialog.querySelectorAll("button")).filter((btn) => !btn.disabled);
  assert.ok(buttons.length && buttons[buttons.length - 1]._focused);
});

test("the CFS editor's tabs are keyboard-reachable tabs", () => {
  const { defined } = loadCardModule(CFS_PATH);
  const Editor = defined.get("k-cfs-card-editor");
  const ed = new Editor();
  ed.hass = { states: {}, language: "en", locale: { language: "en" } };
  ed.setConfig({});
  ed.connectedCallback();
  const tabs = ed._root.querySelectorAll(".tab");
  for (const tab of tabs) {
    assert.equal(tab.tagName.toLowerCase(), "button");
    assert.equal(tab.getAttribute("role"), "tab");
  }
  tabs[1].onclick();
  assert.deepEqual(tabs.map((t) => t.getAttribute("aria-selected")), ["false", "true"]);
});

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
await run();
