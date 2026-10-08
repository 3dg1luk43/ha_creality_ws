#!/usr/bin/env node
/**
 * Load the bundled cards in a real browser against the test box and check them.
 *
 *   cd tools/testbox && npm install      # once; Playwright reuses cached browsers
 *   node card_check.mjs [--seconds 20] [--width 390]
 *
 * Builds a throwaway storage dashboard (`/creality-card-check`) holding two
 * printer cards bound to the mock printer, one of them named in Czech, opens it
 * at phone width in headless Chromium, and watches it while the mock's
 * telemetry changes. Fails on:
 *
 *  - an error card (the card threw in setConfig: btoa on a non-Latin-1 name);
 *  - an `ll-rebuild` after the cards have settled, or more than one rebuilt
 *    element per card (Lovelace answers each ll-rebuild with a NEW element; a
 *    card that keeps asking is in a rebuild loop: 12 elements in 20 s at
 *    300 px before the fix);
 *  - a card whose shadow DOM is replaced by an unrelated state change (every
 *    hass assignment used to rebuild it, dropping focus each time);
 *  - a CFS card in a sections view overlapping the card below it (R23).
 *
 * The unit harness proves the logic; this proves it against the real Lovelace,
 * whose `hui-card` is what turns ll-rebuild into a new element.
 */
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const HERE = dirname(fileURLToPath(import.meta.url));
const BASE = process.env.TESTBOX_URL || "http://127.0.0.1:8322";
const TOKEN = readFileSync(resolve(HERE, "config/.testbox_token"), "utf8").trim();
const arg = (name, fallback) => {
  const i = process.argv.indexOf(`--${name}`);
  return i > 0 ? Number(process.argv[i + 1]) : fallback;
};
const SECONDS = arg("seconds", 20);
const WIDTH = arg("width", 390);
const DASHBOARD = "creality-card-check";

function printerCard(name) {
  return {
    type: "custom:k-printer-card",
    name,
    status: `sensor.${P}_print_status`,
    progress: `sensor.${P}_print_progress`,
    time_left: `sensor.${P}_print_time_left`,
    nozzle: `sensor.${P}_nozzle_temperature`,
    bed: `sensor.${P}_bed_temperature`,
    box: `sensor.${P}_chamber_temperature`,
    layer: `sensor.${P}_working_layer`,
    total_layers: `sensor.${P}_total_layers`,
    light: `light.${P}_light`,
    camera: `camera.${P}_printer_camera`,
  };
}

// The printer's entity prefix: whichever mock model the box was started with.
let P = "creality_k1c";

// Node 20 has no global WebSocket, so dashboard setup goes through hactl.py.
const PY = resolve(HERE, "../../.venv/bin/python3");
function hactl(...args) {
  const out = execFileSync(PY, [resolve(HERE, "hactl.py"), ...args], { encoding: "utf8" });
  return out.trim() ? JSON.parse(out) : null;
}

function cfsCard(viewMode) {
  const card = { type: "custom:k-cfs-card", name: "CFS", view_mode: viewMode };
  for (let slot = 0; slot < 4; slot += 1) {
    card[`box0_slot${slot}_filament`] = `sensor.${P}_cfs_box_1_slot_${slot + 1}_filament`;
    card[`box0_slot${slot}_color`] = `sensor.${P}_cfs_box_1_slot_${slot + 1}_color`;
    card[`box0_slot${slot}_percent`] = `sensor.${P}_cfs_box_1_slot_${slot + 1}_remaining`;
  }
  card.box0_temp = `sensor.${P}_cfs_box_1_temperature`;
  card.box0_humidity = `sensor.${P}_cfs_box_1_humidity`;
  card.external_filament = `sensor.${P}_cfs_external_filament`;
  card.external_color = `sensor.${P}_cfs_external_color`;
  card.external_percent = `sensor.${P}_cfs_external_remaining`;
  return card;
}

function detectPrinter() {
  const out = execFileSync(PY, [resolve(HERE, "hactl.py"), "states", "sensor."], { encoding: "utf8" });
  const match = out.match(/sensor\.(\S+)_print_status\b/);
  if (match) P = match[1];
}

function ensureDashboard() {
  detectPrinter();
  const dashboards = hactl("ws", "lovelace/dashboards/list") || [];
  if (!dashboards.some((d) => d.url_path === DASHBOARD)) {
    hactl(
      "ws", "lovelace/dashboards/create", `url_path=${DASHBOARD}`, "title=Card check",
      "mode=storage", "require_admin=false", "show_in_sidebar=false",
    );
  }
  const below = { type: "markdown", content: "The card below the CFS card." };
  const config = {
    views: [
      {
        title: "Cards",
        path: "cards",
        cards: [printerCard("Tiskárna č.1 – dílna"), printerCard("K1C")],
      },
      {
        // The CFS card in a sections view, each followed by another card, so
        // overlap is measurable (R23).
        title: "Sections",
        path: "sections",
        type: "sections",
        sections: [
          { type: "grid", cards: [cfsCard("full"), below] },
          { type: "grid", cards: [cfsCard("compact"), below] },
        ],
      },
    ],
  };
  hactl("ws", "lovelace/config/save", `url_path=${DASHBOARD}`, `config=${JSON.stringify(config)}`);
}

async function main() {
  ensureDashboard();
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: WIDTH, height: 900 } });
  // The frontend's own token store: a long-lived token with a far expiry
  // skips the login form.
  await context.addInitScript(([base, token]) => {
    localStorage.setItem("hassTokens", JSON.stringify({
      hassUrl: base, clientId: `${base}/`, access_token: token,
      token_type: "Bearer", expires_in: 1e9, expires: Date.now() + 1e12,
      refresh_token: "",
    }));
    window.__llRebuild = 0;
    window.__created = 0;
    document.addEventListener("ll-rebuild", () => { window.__llRebuild += 1; }, true);
    const define = customElements.define.bind(customElements);
    customElements.define = (tag, cls, opts) => {
      if (tag === "k-printer-card") {
        const Wrapped = class extends cls {
          constructor() { super(); window.__created += 1; }
        };
        return define(tag, Wrapped, opts);
      }
      return define(tag, cls, opts);
    };
  }, [BASE, TOKEN]);

  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (err) => errors.push(String(err)));
  page.on("console", (msg) => { if (msg.type() === "error") errors.push(msg.text()); });
  await page.goto(`${BASE}/${DASHBOARD}/cards`, { waitUntil: "networkidle" });
  await page.waitForFunction(() => window.__created >= 2, null, { timeout: 30000 });
  await page.waitForTimeout(3000);

  const deepCards = `(() => {
    const found = [];
    const walk = (root) => {
      for (const el of root.querySelectorAll("*")) {
        if (el.tagName === "K-PRINTER-CARD" || el.tagName === "HUI-ERROR-CARD") found.push(el);
        if (el.shadowRoot) walk(el.shadowRoot);
      }
    };
    walk(document);
    return found;
  })()`;
  const snapshot = await page.evaluate(`(() => {
    const cards = ${deepCards};
    window.__shells = cards.filter((c) => c.tagName === "K-PRINTER-CARD")
      .map((c) => c.shadowRoot && c.shadowRoot.querySelector("ha-card"));
    return {
      printer: cards.filter((c) => c.tagName === "K-PRINTER-CARD").length,
      error: cards.filter((c) => c.tagName === "HUI-ERROR-CARD").length,
      sizes: cards.filter((c) => c.tagName === "K-PRINTER-CARD").map((c) => c.getCardSize()),
    };
  })()`);
  const rebuildsBefore = await page.evaluate(() => window.__llRebuild);
  await page.waitForTimeout(SECONDS * 1000);
  const after = await page.evaluate(`(() => {
    const cards = ${deepCards}.filter((c) => c.tagName === "K-PRINTER-CARD");
    const shells = cards.map((c) => c.shadowRoot && c.shadowRoot.querySelector("ha-card"));
    return {
      created: window.__created,
      llRebuild: window.__llRebuild,
      sameShells: shells.filter((s) => window.__shells.includes(s)).length,
      shells: shells.length,
      sizes: cards.map((c) => c.getCardSize()),
    };
  })()`);
  // The CFS card in a sections view: its bottom edge must not cross into the
  // card placed below it in the same section.
  await page.goto(`${BASE}/${DASHBOARD}/sections`, { waitUntil: "networkidle" });
  await page.waitForTimeout(4000);
  const sections = await page.evaluate(`(() => {
    const all = [];
    const walk = (root) => {
      for (const el of root.querySelectorAll("*")) {
        if (el.tagName === "K-CFS-CARD" || el.tagName === "HUI-MARKDOWN-CARD") all.push(el);
        if (el.shadowRoot) walk(el.shadowRoot);
      }
    };
    walk(document);
    const rect = (el) => {
      const card = (el.shadowRoot && el.shadowRoot.querySelector("ha-card")) || el;
      const r = card.getBoundingClientRect();
      return { top: Math.round(r.top), bottom: Math.round(r.bottom), left: Math.round(r.left) };
    };
    const cfs = all.filter((e) => e.tagName === "K-CFS-CARD").map(rect);
    const md = all.filter((e) => e.tagName === "HUI-MARKDOWN-CARD").map(rect);
    return cfs.map((c) => {
      const next = md.filter((m) => Math.abs(m.left - c.left) < 5 && m.top >= c.top)
        .sort((a, b) => a.top - b.top)[0];
      return { cfs: c, below: next || null, overlap: next ? Math.max(0, c.bottom - next.top) : null };
    });
  })()`);
  await browser.close();

  const report = { width: WIDTH, seconds: SECONDS, ...snapshot, rebuildsBefore, after, sections, errors };
  console.log(JSON.stringify(report, null, 2));
  const problems = [];
  if (snapshot.error) problems.push(`${snapshot.error} error card(s)`);
  if (snapshot.printer < 2) problems.push(`only ${snapshot.printer} printer card(s) rendered`);
  // One rebuild per card while it settles on its size is expected; a card
  // still asking after the settling time is in a loop.
  const during = after.llRebuild - rebuildsBefore;
  if (during > 0) problems.push(`${during} ll-rebuild event(s) while watching (rebuild loop)`);
  if (after.created > 2 * after.shells) problems.push(`${after.created} card elements created for ${after.shells} cards (rebuild loop)`);
  if (after.sameShells < after.shells) problems.push("a card's DOM was rebuilt by a state update");
  if (sections.length < 2) problems.push(`only ${sections.length} CFS card(s) in the sections view`);
  for (const s of sections) {
    if (s.overlap === null) problems.push("a CFS card has no card below it to measure against");
    else if (s.overlap > 1) problems.push(`a CFS card overlaps the card below by ${s.overlap}px`);
  }
  if (problems.length) {
    console.error(`FAIL: ${problems.join("; ")}`);
    process.exit(1);
  }
  console.log("ok: no error cards, no rebuild loop, DOM kept across updates");
}

main().catch((err) => { console.error(err); process.exit(2); });
