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
/** Whether `el` matches a descendant chain like [".spool-mini-wrapper", ".spool-mini"]. */
function _matchesChain(el, steps) {
  const last = steps[steps.length - 1];
  if (!_matchesStep(el, last)) return false;
  let node = el._parent;
  for (let i = steps.length - 2; i >= 0; i -= 1) {
    while (node && !_matchesStep(node, steps[i])) node = node._parent;
    if (!node) return false;
    node = node._parent;
  }
  return true;
}

function _matchesStep(el, step) {
  if (step.startsWith(".")) {
    return String(el.className || "").split(/\s+/).includes(step.slice(1));
  }
  return el.tagName === step.toUpperCase();
}

const _VOID_TAGS = new Set(["img", "br", "hr", "input", "meta", "link"]);

/**
 * Parse the card's own generated markup into FakeElements.
 *
 * Deliberately small: the input is always produced by this card's template
 * literals, so it is well-formed and needs no error recovery. Attributes become
 * both `dataset` entries (for data-*) and plain properties, and a valueless
 * attribute such as `disabled` becomes `true` -- which is what the click handler
 * reads.
 */
function _parseHtml(html) {
  const roots = [];
  const stack = [];
  const push = (el) => {
    const parent = stack[stack.length - 1];
    if (parent) {
      el._parent = parent;
      parent.children.push(el);
    } else {
      roots.push(el);
    }
  };

  const tagRe = /<(\/?)([a-zA-Z][-\w]*)((?:\s+[-\w:]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+))?)*)\s*(\/?)>/g;
  let match;
  let cursor = 0;
  while ((match = tagRe.exec(html)) !== null) {
    const text = html.slice(cursor, match.index).trim();
    if (text && stack.length) {
      const parent = stack[stack.length - 1];
      parent.textContent = (parent.textContent || "") + text;
    }
    cursor = tagRe.lastIndex;

    const [, closing, tag, attrText, selfClose] = match;
    if (closing) {
      for (let i = stack.length - 1; i >= 0; i -= 1) {
        if (stack[i].tagName === tag.toUpperCase()) {
          stack.length = i;
          break;
        }
      }
      continue;
    }

    const el = new FakeElement(tag);
    for (const attr of attrText.matchAll(/([-\w:]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g)) {
      const name = attr[1];
      const raw = attr[2] ?? attr[3] ?? attr[4];
      const value = raw === undefined ? true : raw;
      if (name.startsWith("data-")) {
        const key = name.slice(5).replace(/-([a-z])/g, (_m, c) => c.toUpperCase());
        el.dataset[key] = value === true ? "" : value;
      } else if (name === "class") {
        el.className = value === true ? "" : value;
      } else {
        el[name] = value;
      }
      el.setAttribute(name, value === true ? "" : value);
    }
    push(el);
    if (!selfClose && !_VOID_TAGS.has(tag.toLowerCase())) stack.push(el);
  }
  return roots;
}

class FakeElement {
  constructor(tag = "div") {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.dataset = {};
    this.style = {};
    this._html = "";
  }
  set innerHTML(value) {
    this._html = String(value);
    // Assigning innerHTML replaces the subtree. The markup is parsed rather than
    // discarded: the card binds its click handlers by class selector against
    // whatever innerHTML produced, so a shim that kept no elements left every
    // click path -- including the edit button, this PR's headline feature --
    // silently untested.
    this.children.length = 0;
    for (const child of _parseHtml(this._html)) {
      child._parent = this;
      this.children.push(child);
    }
  }
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
  /**
   * Resolve an id against the parsed tree, falling back to a stable stub.
   *
   * The tree lookup matters: the card writes its content into
   * `getElementById("content")`, and a detached stub would keep that content out
   * of `_root`, so the class selectors in _attachEventHandlers would find
   * nothing and every click path would look tested while being unreachable.
   */
  getElementById(id) {
    for (const el of this._descendants()) {
      if (el.id === id) return el;
    }
    this._byId = this._byId || new Map();
    if (!this._byId.has(id)) {
      const stub = new FakeElement("div");
      stub.id = id;
      stub._parent = this;
      this._byId.set(id, stub);
      this.children.push(stub);
    }
    return this._byId.get(id);
  }
  /**
   * Match the subset of CSS the card actually uses.
   *
   * Selectors seen in _attachEventHandlers: comma-separated lists of `.class`
   * and one `.class .class` descendant pair. Anything else throws rather than
   * silently matching nothing -- a selector this cannot parse would otherwise
   * make the handler it belongs to untestable without saying so.
   */
  querySelectorAll(selector) {
    const parts = String(selector).split(",").map((p) => p.trim()).filter(Boolean);
    const out = [];
    for (const part of parts) {
      const steps = part.split(/\s+/);
      for (const step of steps) {
        if (!/^[.a-zA-Z][-\w]*$/.test(step)) {
          throw new Error(`harness querySelectorAll cannot parse: ${part}`);
        }
      }
      for (const el of this._descendants()) {
        if (_matchesChain(el, steps) && !out.includes(el)) out.push(el);
      }
    }
    return out;
  }
  querySelector(selector) {
    return this.querySelectorAll(selector)[0] ?? null;
  }
  /** Every element below this one, depth-first. */
  _descendants() {
    const out = [];
    const walk = (node) => {
      for (const child of node.children) {
        out.push(child);
        walk(child);
      }
    };
    walk(this);
    return out;
  }
  setAttribute(name, value) {
    this._attrs = this._attrs || {};
    this._attrs[name] = String(value);
  }
  getAttribute(name) { return this._attrs?.[name] ?? null; }
  get classList() {
    const self = this;
    return {
      add: (...names) => {
        const have = new Set(String(self.className || "").split(/\s+/).filter(Boolean));
        names.forEach((n) => have.add(n));
        self.className = [...have].join(" ");
      },
      remove: (...names) => {
        const have = new Set(String(self.className || "").split(/\s+/).filter(Boolean));
        names.forEach((n) => have.delete(n));
        self.className = [...have].join(" ");
      },
      contains: (name) => String(self.className || "").split(/\s+/).includes(name),
    };
  }
  attachShadow() { return this; }
  addEventListener(type, fn) {
    this._listeners = this._listeners || {};
    (this._listeners[type] = this._listeners[type] || []).push(fn);
  }
  removeEventListener(type, fn) {
    const listeners = this._listeners?.[type];
    if (!listeners) return;
    const at = listeners.indexOf(fn);
    if (at >= 0) listeners.splice(at, 1);
  }
  /** Invoke the registered handlers for `type`, the way a real event would. */
  fire(type, event = {}) {
    (this._listeners?.[type] || []).forEach((fn) => fn(event));
  }
  focus() { this._focused = true; }
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
    // Access-counting, so "storage is not touched until X" is assertable rather
    // than inferred from a field still being undefined.
    localStorage: (() => {
      const store = new Map();
      const reads = [];
      const writes = [];
      return {
        getItem: (k) => { reads.push(k); return store.has(k) ? store.get(k) : null; },
        setItem: (k, v) => { writes.push(k); store.set(k, String(v)); },
        removeItem: (k) => store.delete(k),
        clear: () => store.clear(),
        /** Keys read since load, for tests that care about *when* storage is hit. */
        _reads: reads,
        _writes: writes,
        /** Seed a value without counting it as a read or a write. */
        _seed: (k, v) => store.set(k, String(v)),
      };
    })(),
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

