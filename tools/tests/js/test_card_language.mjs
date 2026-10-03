/**
 * The cards' own words follow the user's language (R33).
 *
 * The card picker's names and descriptions, and the printer card's default
 * title "3D Printer", were typed into the source in English. The picker entry
 * is registered before any strings can load, so it is filled in once they
 * arrive; the default title is no longer written into new cards' YAML.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { FakeElement, loadCardModule } from "./cfs_card_harness.mjs";
import { loadPrinterCard, PRINTER_CARD_PATH } from "./printer_card_harness.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const WWW = resolve(HERE, "../../../custom_components/ha_creality_ws/www");
const CFS_PATH = resolve(WWW, "k_cfs_card.js");
const I18N = Object.fromEntries(
  ["en", "es"].map((lang) => [lang, JSON.parse(readFileSync(resolve(WWW, "i18n", `${lang}.json`), "utf8"))]),
);

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

const SPANISH = { language: "es", locale: { language: "es" }, states: {} };

/** Serve the shipped i18n files, as the integration's static path does. */
async function fetch(url) {
  const lang = String(url).match(/(\w+)\.json/)?.[1];
  return { ok: Boolean(I18N[lang]), json: async () => I18N[lang] };
}

function page(hass) {
  return {
    fetch,
    document: {
      createElement: (tag) => new FakeElement(tag),
      querySelector: (sel) => (sel === "home-assistant" ? { hass } : null),
    },
  };
}

const settle = () => new Promise((done) => setTimeout(done, 10));

for (const [path, type, section] of [
  [PRINTER_CARD_PATH, "k-printer-card", "printer_card"],
  [CFS_PATH, "k-cfs-card", "cfs_card"],
]) {
  test(`the card picker lists ${type} in the page's language`, async () => {
    const customCards = [];
    loadCardModule(path, { customCards, ...page(SPANISH) });
    await settle();
    const entry = customCards.find((card) => card.type === type);
    assert.equal(entry.name, I18N.es[section].picker_name);
    assert.equal(entry.description, I18N.es[section].picker_description);
  });

  test(`${type} is still listed when the strings cannot load`, () => {
    const customCards = [];
    loadCardModule(path, { customCards });
    const entry = customCards.find((card) => card.type === type);
    assert.equal(entry.name, I18N.en[section].picker_name);
  });
}

test("a new printer card is not given an English name", () => {
  const { KPrinterCard } = loadPrinterCard();
  assert.equal(KPrinterCard.getStubConfig().name, "");
});

for (const name of [undefined, "3D Printer"]) {
  test(`a printer card named ${JSON.stringify(name)} shows the default in the user's language`, async () => {
    // "3D Printer" is what every card used to be created with.
    const { KPrinterCard } = loadPrinterCard(page(SPANISH));
    const card = new KPrinterCard();
    card.setConfig(name === undefined ? {} : { name });
    card.hass = SPANISH;
    await settle();
    card.hass = { ...SPANISH };
    assert.equal(card._root.getElementById("name").textContent, I18N.es.printer_card.default_name);
  });
}

test("a printer card the user named keeps its name", async () => {
  const { KPrinterCard } = loadPrinterCard(page(SPANISH));
  const card = new KPrinterCard();
  card.setConfig({ name: "Taller" });
  card.hass = SPANISH;
  await settle();
  assert.equal(card._root.getElementById("name").textContent, "Taller");
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
