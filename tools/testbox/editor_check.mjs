// Open each bundled card's editor through Home Assistant's own edit dialog and
// report the page errors it causes (R79).
//
//   node editor_check.mjs            # after card_check.mjs has built its dashboard
//
// Mounting an editor by hand, outside the dialog, logs errors the real dialog
// may not; this is the check that tells the two apart. Exit code is the number
// of editors that logged errors.
import { chromium } from "playwright";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const BASE = process.env.TESTBOX_URL || "http://127.0.0.1:8322";
const TOKEN = readFileSync(resolve(HERE, "config/.testbox_token"), "utf8").trim();
const DASHBOARD = "creality-card-check";

const EDITORS = [
  { view: "cards", card: "k-printer-card", editor: "k-printer-card-editor" },
  { view: "sections", card: "k-cfs-card", editor: "k-cfs-card-editor" },
];

async function openEditor(page, { view, card, editor }) {
  await page.goto(`${BASE}/${DASHBOARD}/${view}?edit=1`, { waitUntil: "networkidle" });
  await page.waitForTimeout(3000);
  const errors = [];
  const onError = (err) => errors.push(String(err));
  const onConsole = (msg) => { if (msg.type() === "error") errors.push(msg.text()); };
  page.on("pageerror", onError);
  page.on("console", onConsole);
  // Masonry views wrap a card in hui-card-options (an Edit button); sections
  // views in hui-card-edit-mode (click the card).
  const options = page.locator("hui-card-options", { has: page.locator(card) }).first();
  if (await options.count()) {
    await options.getByRole("button", { name: /edit/i }).first().click();
  } else {
    await page.locator("hui-card-edit-mode", { has: page.locator(card) }).first().click();
  }
  let mounted = true;
  try {
    await page.locator(`hui-dialog-edit-card ${editor}`).first().waitFor({ state: "attached", timeout: 15000 });
  } catch {
    mounted = false;
  }
  await page.waitForTimeout(5000);
  page.off("pageerror", onError);
  page.off("console", onConsole);
  await page.keyboard.press("Escape");
  return { editor, mounted, errors: [...new Set(errors)] };
}

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
await context.addInitScript(([base, token]) => {
  localStorage.setItem("hassTokens", JSON.stringify({
    hassUrl: base, clientId: `${base}/`, access_token: token,
    token_type: "Bearer", expires_in: 1e9, expires: Date.now() + 1e12, refresh_token: "",
  }));
}, [BASE, TOKEN]);
const page = await context.newPage();
let failed = 0;
for (const target of EDITORS) {
  const result = await openEditor(page, target);
  const bad = !result.mounted || result.errors.length > 0;
  failed += bad ? 1 : 0;
  console.log(`${bad ? "FAIL" : "ok  "} ${result.editor}: ${result.mounted ? "opened in the edit dialog" : "never opened"}, ${result.errors.length} distinct page error(s)`);
  for (const e of result.errors.slice(0, 5)) console.log(`     ${e.slice(0, 200)}`);
}
await browser.close();
process.exit(failed);
