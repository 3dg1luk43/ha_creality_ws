/**
 * Run k_printer_card.js under node so the editor can be asserted on, not grepped.
 *
 * The DOM shim is the CFS card's -- both cards touch the same small slice of it
 * -- so only the printer-specific fixtures live here. The bug that prompted
 * these tests (`if (form.computeLabel)`, a guard ha-form never satisfies, so
 * every label rendered as its raw config key) is invisible to a source-level
 * check: the code that builds the right labels was all present and correct.
 */

import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { FakeElement, loadCardModule } from "./cfs_card_harness.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
export const PRINTER_CARD_PATH = resolve(
  HERE,
  "../../../custom_components/ha_creality_ws/www/k_printer_card.js",
);

export { FakeElement };

/**
 * Load the printer card and its editor into a fresh sandbox.
 * @param {!Object=} overrides Extra globals (geometry stubs, mostly).
 */
export function loadPrinterCard(overrides = {}) {
  const { defined, sandbox, reload } = loadCardModule(PRINTER_CARD_PATH, overrides);
  const KPrinterCard = defined.get("k-printer-card");
  const KPrinterCardEditor = defined.get("k-printer-card-editor");
  if (!KPrinterCard) throw new Error("k-printer-card was not registered");
  if (!KPrinterCardEditor) throw new Error("k-printer-card-editor was not registered");
  return { KPrinterCard, KPrinterCardEditor, defined, sandbox, reload };
}

/**
 * The integration's entities, as [card role, entity id pattern, translation_key].
 *
 * Mirrors what sensor.py / button.py / light.py / camera.py actually register.
 * test_printer_card_editor.py cross-checks the translation keys against the
 * Python source, so a renamed key fails here rather than silently stopping the
 * device picker from finding that entity.
 */
export const PRINTER_ENTITIES = [
  ["camera", "camera.{slug}", "printer_camera"],
  ["status", "sensor.{slug}_print_status", "print_status"],
  ["progress", "sensor.{slug}_print_progress", "print_progress"],
  ["time_left", "sensor.{slug}_print_left_time", "print_left_time"],
  ["nozzle", "sensor.{slug}_nozzle_temperature", "nozzle_temperature"],
  ["bed", "sensor.{slug}_bed_temperature", "bed_temperature"],
  ["box", "sensor.{slug}_chamber_temperature", "chamber_temperature"],
  ["layer", "sensor.{slug}_current_layer", "current_layer"],
  ["total_layers", "sensor.{slug}_total_layers", "total_layers"],
  ["light", "light.{slug}_light", "light"],
  ["pause_btn", "button.{slug}_pause_print", "pause_print"],
  ["resume_btn", "button.{slug}_resume_print", "resume_print"],
  ["stop_btn", "button.{slug}_stop_print", "stop_print"],
];

/** The entity id this integration would give `role` on the printer `slug`. */
export function printerEntityId(slug, role) {
  const found = PRINTER_ENTITIES.find(([name]) => name === role);
  if (!found) throw new Error(`no such printer role: ${role}`);
  return found[1].replace("{slug}", slug);
}

/**
 * Entity-registry entries for one printer.
 * @param {string} deviceId
 * @param {string} slug Entity-id stem, i.e. the printer's slugified name.
 * @param {{omit?: !Array<string>, platform?: string}=} options `omit` leaves a
 *     role out, the way a KE has no chamber sensor.
 */
export function printerRegistry(deviceId, slug, { omit = [], platform = "ha_creality_ws" } = {}) {
  const registry = {};
  for (const [role, pattern, translationKey] of PRINTER_ENTITIES) {
    if (omit.includes(role)) continue;
    registry[pattern.replace("{slug}", slug)] = {
      device_id: deviceId,
      platform,
      translation_key: translationKey,
    };
  }
  return registry;
}

/** A hass stub carrying the registries the editor reads. */
export function makeEditorHass({ entities = {}, devices = {}, states = {} } = {}) {
  return {
    states,
    entities,
    devices,
    language: "en",
    locale: { language: "en" },
    formatEntityState: (st) => String(st?.state ?? "-"),
  };
}

/** An editor wired up in the order Home Assistant wires it. */
export function makeEditor(KPrinterCardEditor, config = {}, hass = null) {
  const editor = new KPrinterCardEditor();
  if (hass) editor.hass = hass;
  editor.setConfig(config);
  editor.connectedCallback();
  return editor;
}

/** Fire a form's value-changed the way ha-form does. */
export function changeForm(form, value) {
  form.fire("value-changed", { detail: { value }, stopPropagation() {} });
}

/** Every field name a form's schema binds, flattening grid rows. */
export function schemaNames(form) {
  return (form.schema || []).flatMap(
    (item) => (Array.isArray(item.schema) ? item.schema.map((s) => s.name) : [item.name]),
  );
}

/** The colour form holding `key`, found by schema rather than by index. */
export function colorFormFor(editor, key) {
  for (const [id, form] of Object.entries(editor._formEls)) {
    if (!id.startsWith("color-form-")) continue;
    if (schemaNames(form).includes(key)) return form;
  }
  throw new Error(`no colour form holds ${key}`);
}
