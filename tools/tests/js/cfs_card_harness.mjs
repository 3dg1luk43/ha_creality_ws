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
  appendChild(child) {
    child._parent = this;
    this.children.push(child);
    return child;
  }
  /** Real elements have this, and the card relies on it to dismiss dialogs/toasts. */
  remove() {
    const siblings = this._parent?.children;
    if (!siblings) return;
    const at = siblings.indexOf(this);
    if (at >= 0) siblings.splice(at, 1);
  }
  // Hand back a stable stub per id (#content, #theme-form, ...) so code that
  // assigns to the looked-up element works and the test can inspect it after.
  getElementById(id) {
    this._byId = this._byId || new Map();
    if (!this._byId.has(id)) this._byId.set(id, new FakeElement("div"));
    return this._byId.get(id);
  }
  // Event wiring is not under test here, so selectors return nothing.
  querySelectorAll() { return []; }
  querySelector() { return null; }
  attachShadow() { return this; }
  addEventListener(type, fn) {
    this._listeners = this._listeners || {};
    (this._listeners[type] = this._listeners[type] || []).push(fn);
  }
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
