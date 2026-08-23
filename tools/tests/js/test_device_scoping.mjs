/**
 * Multi-printer correctness.
 *
 * Both defects here were silent in a single-printer setup, which is why they
 * survived review: PR #75 resolved the device by taking the first Creality
 * device in the registry, and detected "busy" by scanning every entity in the
 * system. With two printers the first writes filament data to the wrong machine
 * and the second locks a card that is not printing.
 */

import assert from "node:assert/strict";
import { loadCard, makeHass, slotEntities } from "./cfs_card_harness.mjs";

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

const A_SLOT = "sensor.printer_a_cfs_box_1_slot_0_filament";
const B_SLOT = "sensor.printer_b_cfs_box_1_slot_0_filament";

/** Registry entries for two printers, each with a slot sensor and a status sensor. */
function twoPrinterRegistry() {
  return {
    [A_SLOT]: { device_id: "dev_a", platform: "ha_creality_ws" },
    "sensor.printer_a_print_status": {
      device_id: "dev_a", platform: "ha_creality_ws", translation_key: "print_status",
    },
    [B_SLOT]: { device_id: "dev_b", platform: "ha_creality_ws" },
    "sensor.printer_b_print_status": {
      device_id: "dev_b", platform: "ha_creality_ws", translation_key: "print_status",
    },
  };
}

function cardFor(entityId, states, registry) {
  const { KCFSCard } = loadCard();
  const card = new KCFSCard();
  card.setConfig({ box0_slot0_filament: entityId });
  card.hass = makeHass(states, { entities: registry, callWS: async () => { throw new Error("no ws"); } });
  return card;
}

function statesForBoth({ aStatus = "idle", bStatus = "idle" } = {}) {
  return {
    [A_SLOT]: { state: "Creality Hyper PLA", attributes: { box_id: 1, slot_id: 0 } },
    [B_SLOT]: { state: "Creality Hyper PLA", attributes: { box_id: 1, slot_id: 0 } },
    "sensor.printer_a_print_status": { state: aStatus, attributes: {} },
    "sensor.printer_b_print_status": { state: bStatus, attributes: {} },
  };
}

// --------------------------------------------------------------------------- //
// Device resolution
// --------------------------------------------------------------------------- //

test("resolves the device that owns the card's entities", async () => {
  const card = cardFor(A_SLOT, statesForBoth(), twoPrinterRegistry());
  assert.equal(await card._resolveDeviceId(), "dev_a");
});

test("a card for printer B resolves to B, not the first device", async () => {
  const card = cardFor(B_SLOT, statesForBoth(), twoPrinterRegistry());
  assert.equal(await card._resolveDeviceId(), "dev_b");
});

test("no matching entity fails closed rather than picking a device", async () => {
  const card = cardFor("sensor.not_in_registry", statesForBoth(), twoPrinterRegistry());
  assert.equal(await card._resolveDeviceId(), null);
  assert.equal(card._deviceIdError, "toast_no_device");
});

test("a card mixing two printers is refused, not resolved to one", async () => {
  const { KCFSCard } = loadCard();
  const card = new KCFSCard();
  card.setConfig({ box0_slot0_filament: A_SLOT, box1_slot0_filament: B_SLOT });
  card.hass = makeHass(statesForBoth(), { entities: twoPrinterRegistry() });
  assert.equal(await card._resolveDeviceId(), null);
  assert.equal(card._deviceIdError, "toast_multiple_devices");
});

test("setConfig invalidates the cached device", async () => {
  const registry = twoPrinterRegistry();
  const card = cardFor(A_SLOT, statesForBoth(), registry);
  assert.equal(await card._resolveDeviceId(), "dev_a");
  card.setConfig({ box0_slot0_filament: B_SLOT });
  assert.equal(card._deviceId, undefined, "cache must be dropped");
  card.hass = makeHass(statesForBoth(), { entities: registry });
  assert.equal(await card._resolveDeviceId(), "dev_b");
});

// --------------------------------------------------------------------------- //
// Busy detection
// --------------------------------------------------------------------------- //

test("this printer printing reads as busy", async () => {
  const card = cardFor(A_SLOT, statesForBoth({ aStatus: "printing" }), twoPrinterRegistry());
  await card._resolveDeviceId();
  assert.equal(card._isPrinterBusy(), true);
});

test("the OTHER printer printing does not lock this card", async () => {
  // The bug: a global scan for *_print_status matched printer B's sensor.
  const card = cardFor(A_SLOT, statesForBoth({ aStatus: "idle", bStatus: "printing" }), twoPrinterRegistry());
  await card._resolveDeviceId();
  assert.equal(card._isPrinterBusy(), false);
});

test("busy states are exactly the uninterruptible ones", async () => {
  for (const state of ["printing", "paused", "processing", "self-testing"]) {
    const card = cardFor(A_SLOT, statesForBoth({ aStatus: state }), twoPrinterRegistry());
    await card._resolveDeviceId();
    assert.equal(card._isPrinterBusy(), true, `${state} should be busy`);
  }
  for (const state of ["idle", "completed", "stopped", "off", "unknown", "error"]) {
    const card = cardFor(A_SLOT, statesForBoth({ aStatus: state }), twoPrinterRegistry());
    await card._resolveDeviceId();
    assert.equal(card._isPrinterBusy(), false, `${state} should not be busy`);
  }
});

test("an unresolvable status is treated as not busy", async () => {
  // Registry without a print_status entry: refusing to edit would be worse than
  // letting the server-side guard catch it.
  const registry = { [A_SLOT]: { device_id: "dev_a", platform: "ha_creality_ws" } };
  const card = cardFor(A_SLOT, statesForBoth({ aStatus: "printing" }), registry);
  await card._resolveDeviceId();
  assert.equal(card._isPrinterBusy(), false);
});

test("a renamed status entity is still found via translation_key", async () => {
  const registry = {
    [A_SLOT]: { device_id: "dev_a", platform: "ha_creality_ws" },
    "sensor.totally_renamed": {
      device_id: "dev_a", platform: "ha_creality_ws", translation_key: "print_status",
    },
  };
  const states = {
    [A_SLOT]: { state: "Creality Hyper PLA", attributes: {} },
    "sensor.totally_renamed": { state: "printing", attributes: {} },
  };
  const card = cardFor(A_SLOT, states, registry);
  await card._resolveDeviceId();
  assert.equal(card._isPrinterBusy(), true);
});

test("a status entity discovered late is picked up, not cached as missing", async () => {
  // This PR adds late entity discovery, so print_status can enter the registry
  // after the first lookup. Memoizing the miss left _isPrinterBusy() false for
  // good, and the lock affordance never appeared.
  const registryWithoutStatus = {
    [A_SLOT]: { device_id: "dev_a", platform: "ha_creality_ws" },
  };
  const card = cardFor(A_SLOT, statesForBoth({ aStatus: "printing" }), registryWithoutStatus);
  await card._resolveDeviceId();
  assert.equal(card._isPrinterBusy(), false, "nothing to find yet");

  // The status sensor is created by the late-discovery pass.
  card.hass = makeHass(statesForBoth({ aStatus: "printing" }), {
    entities: twoPrinterRegistry(),
  });
  assert.equal(card._isPrinterBusy(), true, "the late entity must be found");
});

test("busy state is part of the fingerprint", async () => {
  // Otherwise the lock affordance goes stale for the whole print.
  const card = cardFor(A_SLOT, statesForBoth(), twoPrinterRegistry());
  await card._resolveDeviceId();
  let renders = 0;
  const inner = card._update.bind(card);
  card._update = (d) => { renders += 1; return inner(d); };

  card.hass = makeHass(statesForBoth({ aStatus: "idle" }), { entities: twoPrinterRegistry() });
  const baseline = renders;
  card.hass = makeHass(statesForBoth({ aStatus: "printing" }), { entities: twoPrinterRegistry() });
  assert.ok(renders > baseline, "a busy transition must re-render");
});

test("_deviceIdError is part of the fingerprint", async () => {
  // _renderEditButton reads _deviceIdError to pick the lock icon, the tooltip
  // and the disabled attribute. Resolution finishes after the first render, so
  // if the fingerprint ignores it the .then() sees no change, skips the
  // re-render, and the buttons stay enabled on a card that cannot resolve its
  // printer -- every click then refused by the guard in _showEditDialog.
  const card = cardFor(A_SLOT, statesForBoth(), twoPrinterRegistry());
  await card._resolveDeviceId();
  card.hass = makeHass(statesForBoth(), { entities: twoPrinterRegistry() });

  let renders = 0;
  const inner = card._update.bind(card);
  card._update = (d) => { renders += 1; return inner(d); };

  // Settle the gate, then change _deviceIdError and nothing else.
  card._updateIfChanged();
  const baseline = renders;
  card._deviceIdError = "toast_multiple_devices";
  card._updateIfChanged();
  assert.ok(
    renders > baseline,
    "a device-resolution error must invalidate the render gate on its own",
  );
});

test("a card spanning two printers renders its edit button locked", async () => {
  const { KCFSCard } = loadCard();
  const card = new KCFSCard();
  card.setConfig({ box0_slot0_filament: A_SLOT, box1_slot0_filament: B_SLOT });
  card.hass = makeHass(statesForBoth(), { entities: twoPrinterRegistry() });
  await card._resolveDeviceId();

  assert.equal(card._deviceIdError, "toast_multiple_devices", "the card fails closed");
  const button = card._renderEditButton(card._findSlot(A_SLOT));
  assert.match(button, /\sdisabled>/, "the button is disabled");
  assert.match(button, /mdi:lock/, "and shows the lock");
});

test("a card that cannot resolve is retried once, not on every update", async () => {
  // The empty-registry retry needs exactly one attempt. Without an "already
  // retried" guard, a mixed-printer card sits at _deviceId === null with a
  // populated registry forever, so every telemetry frame cleared the error,
  // rendered the buttons unlocked, then re-resolved and rendered them locked --
  // flip-flopping for the whole session, and re-issuing the callWS fallback.
  const { KCFSCard } = loadCard();
  const card = new KCFSCard();
  card.setConfig({ box0_slot0_filament: A_SLOT, box1_slot0_filament: B_SLOT });

  let resolves = 0;
  const inner = card._resolveDeviceId.bind(card);
  card._resolveDeviceId = () => { resolves += 1; return inner(); };

  for (let i = 0; i < 5; i += 1) {
    card.hass = makeHass(statesForBoth({ aStatus: "idle" }), { entities: twoPrinterRegistry() });
    await Promise.resolve();
    await Promise.resolve();
  }

  assert.equal(card._deviceIdError, "toast_multiple_devices", "still failing closed");
  assert.ok(card._deviceId === null, "and still unresolved");
  assert.ok(
    resolves <= 2,
    `resolution must not repeat per update, ran ${resolves} times`,
  );
});

test("a resolve in flight during setConfig cannot write the old device id", async () => {
  // The pre-2023.4 fallback awaits callWS, and entityIds was captured before the
  // await. Reconfiguring the card mid-flight would otherwise cache printer A's
  // device id against printer B's entities -- and _saveMaterial would write
  // filament data to the wrong machine for the rest of the session.
  const { KCFSCard } = loadCard();
  const card = new KCFSCard();

  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const perEntity = { [A_SLOT]: "dev_a", [B_SLOT]: "dev_b" };

  card.setConfig({ box0_slot0_filament: A_SLOT });
  // No hass.entities, so _resolveDeviceId falls back to callWS per entity.
  card.hass = makeHass(statesForBoth(), {
    entities: {},
    callWS: async ({ entity_id }) => {
      await gate;
      return { device_id: perEntity[entity_id] ?? null };
    },
  });

  const inFlight = card._resolveDeviceId();
  card.setConfig({ box0_slot0_filament: B_SLOT }); // different printer
  release();
  await inFlight;

  assert.notEqual(card._deviceId, "dev_a", "the stale answer must be discarded");

  card.hass = makeHass(statesForBoth(), {
    entities: {},
    callWS: async ({ entity_id }) => ({ device_id: perEntity[entity_id] ?? null }),
  });
  assert.equal(await card._resolveDeviceId(), "dev_b", "and B must still resolve");
});

test("the legacy fallback rejects a card spanning two printers", async () => {
  // Stopping at the first device_id resolved a mixed card to whichever entity
  // answered first, so _saveMaterial wrote filament data to the wrong machine --
  // the exact defect the hass.entities path was written to avoid.
  const { KCFSCard } = loadCard();
  const card = new KCFSCard();
  card.setConfig({ box0_slot0_filament: A_SLOT, box1_slot0_filament: B_SLOT });
  const perEntity = { [A_SLOT]: "dev_a", [B_SLOT]: "dev_b" };
  const asked = [];
  card.hass = makeHass(statesForBoth(), {
    entities: {}, // no hass.entities, so the callWS fallback runs
    callWS: async ({ entity_id }) => {
      asked.push(entity_id);
      return { device_id: perEntity[entity_id] ?? null };
    },
  });

  assert.equal(await card._resolveDeviceId(), null, "a mixed card must fail closed");
  assert.equal(card._deviceIdError, "toast_multiple_devices");
  assert.ok(asked.includes(A_SLOT) && asked.includes(B_SLOT), "every entity is asked");
});

let failed = 0;
for (const [name, fn] of tests) {
  try { await fn(); console.log(`ok   ${name}`); }
  catch (err) { failed += 1; console.log(`FAIL ${name}\n     ${err.message}`); }
}
console.log(`\n${tests.length - failed}/${tests.length} passed`);
process.exit(failed === 0 ? 0 : 1);
