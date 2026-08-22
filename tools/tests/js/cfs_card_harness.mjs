/**
 * Run k_cfs_card.js under node so its behaviour can be asserted, not just grepped.
 *
 * The card is a custom element, so it needs a minimal slice of the DOM. Only what
 * the code paths under test actually touch is shimmed -- enough to construct the
 * element, hand it a config and a hass object, and inspect what it collects and
 * renders. Anything beyond that is deliberately left undefined so a test that
 * strays into unshimmed territory fails loudly instead of passing vacuously.
 */

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const HERE = dirname(fileURLToPath(import.meta.url));
export const CARD_PATH = resolve(HERE, "../../../custom_components/ha_creality_ws/www/k_cfs_card.js");

/** Minimal element stand-in: records innerHTML and supports the queries the card makes. */
class FakeElement {
  constructor(tag = "div") {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.dataset = {};
    this.style = {};
    this._html = "";
  }
  set innerHTML(value) { this._html = String(value); }
  get innerHTML() { return this._html; }
  appendChild(child) { this.children.push(child); return child; }
  // The card only ever looks up #content and a handful of classes; returning an
  // empty list for selectors is fine because event wiring is not under test here.
  getElementById(id) {
    if (id === "content") {
      this._content = this._content || new FakeElement("div");
      return this._content;
    }
    return null;
  }
  querySelectorAll() { return []; }
  querySelector() { return null; }
  attachShadow() { return this; }
  addEventListener() {}
  dispatchEvent() { return true; }
}

/**
 * Load the card into a fresh sandbox.
 * @returns {{KCFSCard: Function, defined: Map<string, Function>}}
 */
export function loadCard() {
  const defined = new Map();

  class HTMLElement extends FakeElement {}

  const sandbox = {
    HTMLElement,
    CustomEvent: class { constructor(type, init) { this.type = type; Object.assign(this, init); } },
    customElements: { define: (tag, cls) => defined.set(tag, cls) },
    document: { createElement: (tag) => new FakeElement(tag) },
    // i18n is fetched at runtime; never resolve it so tests exercise the bundled
    // English fallback rather than depending on the JSON files.
    fetch: () => new Promise(() => {}),
    console,
    setTimeout,
    clearTimeout,
    JSON,
    Math,
    Number,
    String,
    Object,
    Array,
    RegExp,
    Promise,
    isNaN,
    parseInt,
    parseFloat,
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;

  const context = vm.createContext(sandbox);
  vm.runInContext(readFileSync(CARD_PATH, "utf8"), context, { filename: CARD_PATH });

  const KCFSCard = defined.get("k-cfs-card");
  if (!KCFSCard) throw new Error("k-cfs-card was not registered");
  return { KCFSCard, defined, sandbox };
}

/** Build a hass stub whose states come from a plain {entity_id: {state, attributes}} map. */
export function makeHass(states = {}, extra = {}) {
  return {
    states,
    language: "en",
    locale: { language: "en" },
    formatEntityState: (st) => String(st?.state ?? "—"),
    ...extra,
  };
}

/** A CFS slot's three sensors, named the way the integration names them. */
export function slotEntities(boxId, slotId, { filament, color, percent, attributes = {} } = {}) {
  const base = `sensor.printer_cfs_box_${boxId}_slot_${slotId}`;
  return {
    [`${base}_filament`]: { state: filament ?? "Creality Hyper PLA", attributes },
    [`${base}_color`]: { state: color ?? "#ffffff", attributes: {} },
    [`${base}_percent`]: { state: String(percent ?? 80), attributes: { unit_of_measurement: "%" } },
  };
}

/** Card config pointing card position `pos` at the sensors for box/slot. */
export function slotConfig(pos, slotId, boxId, slot = 0) {
  const base = `sensor.printer_cfs_box_${boxId}_slot_${slot}`;
  return {
    [`box${pos}_slot${slotId}_filament`]: `${base}_filament`,
    [`box${pos}_slot${slotId}_color`]: `${base}_color`,
    [`box${pos}_slot${slotId}_percent`]: `${base}_percent`,
  };
}
