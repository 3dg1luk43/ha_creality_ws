/**
 * What the printer card's chips do, and what reaches its markup (R46).
 *
 * The custom chip showed fans and covers as on/off but only ever turned them
 * on, and "ran" an automation by enabling it. Theme colours went into a
 * <style> element and icon names into attributes unescaped, so a value with
 * `;`, `}` or a quote could rewrite the card's CSS or markup.
 */

import assert from "node:assert/strict";
import { loadPrinterCard } from "./printer_card_harness.mjs";

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

function card(config, states = {}) {
  const { KPrinterCard } = loadPrinterCard();
  const c = new KPrinterCard();
  c.setConfig({ name: "K1C", status: "sensor.s", ...config });
  const calls = [];
  c.hass = {
    states: { "sensor.s": { state: "idle", attributes: {} }, ...states },
    language: "en", locale: { language: "en" }, formatEntityState: (st) => String(st?.state ?? "-"),
    callService: async (domain, service, data) => { calls.push([domain, service, data.entity_id]); },
  };
  return { c, calls };
}

function tapCustom(c) {
  const chip = c._root.getElementById("custom");
  c._root.getElementById("chips-container")._listeners.click.forEach((fn) => fn({ target: { closest: () => chip } }));
}

for (const [entity, state, expected] of [
  ["fan.exhaust", "on", ["homeassistant", "toggle", "fan.exhaust"]],
  ["cover.enclosure", "open", ["homeassistant", "toggle", "cover.enclosure"]],
  ["automation.purge", "on", ["automation", "trigger", "automation.purge"]],
  ["script.purge", "off", ["homeassistant", "turn_on", "script.purge"]],
  ["button.purge", "unknown", ["button", "press", "button.purge"]],
]) {
  test(`the custom chip on ${entity.split(".")[0]} does what it means`, async () => {
    const { c, calls } = card({ custom_btn: entity }, { [entity]: { state, attributes: {} } });
    tapCustom(c);
    await new Promise((done) => setTimeout(done, 10));
    assert.deepEqual(calls, [expected]);
  });
}

test("a theme value cannot rewrite the card's CSS", () => {
  const { c } = card({ theme: { stop_bg: "red; } :host { display:none } .x {", pause_bg: "</style><img src=x>" } });
  const html = c._root.innerHTML;
  assert.ok(!html.includes("red; }"), "a CSS injection survived");
  assert.ok(!html.includes("<img src=x>"), "a markup injection survived");
  assert.ok(html.includes("--stop-bg: rgba(244, 67, 54, .95)"), "the default stands in");
});

test("an ordinary theme colour still applies", () => {
  const { c } = card({ theme: { stop_bg: "rgba(10, 20, 30, 0.5)", pause_bg: "var(--accent-color)" } });
  const html = c._root.innerHTML;
  assert.ok(html.includes("--stop-bg: rgba(10, 20, 30, 0.5)"));
  assert.ok(html.includes("--pause-bg: var(--accent-color)"));
});

test("an icon name cannot break out of its attribute", () => {
  const { c } = card(
    { pause_btn: "button.p", pause_btn_icon: 'mdi:pause" onmouseover="alert(1)' },
    { "sensor.s": { state: "printing", attributes: {} }, "button.p": { state: "unknown", attributes: {} } },
  );
  const html = c._root.getElementById("chips-container").innerHTML;
  assert.ok(!html.includes('" onmouseover="'), "the quote was not escaped");
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
