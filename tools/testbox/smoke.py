#!/usr/bin/env python3
"""End-to-end checks on the test box: what no stubbed test can prove.

    ./up.sh --fresh && ../../.venv/bin/python3 smoke.py

Adds the mock printer through the real config flow, then exercises, against a
real Home Assistant:

  setup      entities registered, numeric sensors numeric, log clean
  blanks     a booting printer's "" shows as unknown and recovers (#121)
  power      the plug switch stops and restarts the connection promptly (#45)
  notify     a real print: iPhone pushes carry native types through mobile_app,
             Android pushes strings, the plain target title and message only (#125)
  diag       the diagnostics download hides addresses, names and tokens
  host       a host change keeps every entity id and the device (#39)

Each check prints PASS/FAIL with the evidence; the exit code is the number of
failures. Takes about four minutes, most of it the mock's two-minute print.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hactl  # noqa: E402

HERE = hactl.HERE
PRINTER = hactl.PRINTER_IP
ALT_PRINTER = "172.31.78.10"
P = "sensor.creality_k1c"
IPHONE, PIXEL = "Testbox iPhone", "Testbox Pixel"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, evidence: str = "") -> bool:
    results.append((name, ok, evidence))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({evidence})" if evidence else ""))
    return ok


async def wait_for(pred: Callable[[], Awaitable[Any]], timeout: float, step: float = 1.0) -> Any:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = await pred()
        if last:
            return last
        await asyncio.sleep(step)
    return last


async def state(session: aiohttp.ClientSession, entity_id: str) -> str | None:
    try:
        return (await hactl.rest(session, "GET", f"/api/states/{entity_id}"))["state"]
    except RuntimeError:
        return None


def is_number(value: str | None) -> bool:
    try:
        float(value)  # type: ignore[arg-type]
        return True
    except (TypeError, ValueError):
        return False


async def options(session: aiohttp.ClientSession, entry: str, step: str, payload: dict) -> dict:
    flow = await hactl.rest(session, "POST", "/api/config/config_entries/options/flow", {"handler": entry})
    fid = flow["flow_id"]
    await hactl.rest(session, "POST", f"/api/config/config_entries/options/flow/{fid}", {"next_step_id": step})
    result = await hactl.rest(session, "POST", f"/api/config/config_entries/options/flow/{fid}", payload)
    try:
        await hactl.rest(session, "DELETE", f"/api/config/config_entries/options/flow/{fid}")
    except RuntimeError:
        pass
    return result


async def wait_loaded(session: aiohttp.ClientSession) -> None:
    async def loaded():
        entries = await hactl.rest(session, "GET", f"/api/config/config_entries/entry?domain={hactl.DOMAIN}")
        return entries and all(e["state"] == "loaded" for e in entries)
    await wait_for(loaded, 120)
    await asyncio.sleep(3)


def our_log_errors(since: str) -> list[str]:
    hits = []
    for line in hactl.LOG_FILE.read_text(errors="replace").splitlines():
        if line[:23] < since or "ha_creality_ws" not in line:
            continue
        if " ERROR " in line or "Traceback" in line:
            hits.append(line[:200])
    return hits


def now_stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


async def main() -> int:
    started = now_stamp()
    async with aiohttp.ClientSession() as session:
        # ---- setup ---------------------------------------------------------
        entry = await hactl._entry_id(session)
        if not entry:
            flow = await hactl.rest(session, "POST", "/api/config/config_entries/flow", {"handler": hactl.DOMAIN})
            res = await hactl.rest(
                session, "POST", f"/api/config/config_entries/flow/{flow['flow_id']}",
                {"host": PRINTER, "name": "Test K1C"},
            )
            entry = res["result"]["entry_id"]
        await wait_loaded(session)
        nozzle = await wait_for(lambda: _numeric(session, f"{P}_nozzle_temperature"), 60)
        async with hactl.WS(session) as ws:
            reg = await ws.cmd("config/entity_registry/list")
        ours = [e for e in reg if e["platform"] == hactl.DOMAIN]
        check("setup: entities registered", len(ours) >= 50, f"{len(ours)} entities")
        check("setup: nozzle temperature is a number", bool(nozzle), f"{nozzle}")
        before_ids = sorted(e["entity_id"] for e in ours)

        # ---- blanks (#121) ------------------------------------------------
        await hactl.rest(session, "POST", "/test/set", {"bedTemp0": "", "nozzleTemp": ""}, auth=False, base=hactl.PRINTER_URL)
        blank = await wait_for(lambda: _is(session, f"{P}_bed_temperature", "unknown"), 20)
        check("blanks: a blank temperature reads unknown", bool(blank))
        await hactl.rest(session, "POST", "/test/reset", {}, auth=False, base=hactl.PRINTER_URL)
        back = await wait_for(lambda: _numeric(session, f"{P}_bed_temperature"), 20)
        check("blanks: and recovers when the value returns", bool(back), f"{back}")

        # ---- power switch (#45) -------------------------------------------
        await options(session, entry, "power", {"power_switch_enabled": True, "power_switch": "input_boolean.printer_plug"})
        await wait_loaded(session)
        await hactl.rest(session, "POST", "/api/services/input_boolean/turn_off", {"entity_id": "input_boolean.printer_plug"})
        t0 = time.monotonic()
        off = await wait_for(lambda: _is(session, f"{P}_nozzle_temperature", "unavailable"), 30)
        check("power: plug off makes the printer unavailable", bool(off), f"{time.monotonic() - t0:.1f}s")
        await asyncio.sleep(15)  # long enough for the old code's backoff to grow
        await hactl.rest(session, "POST", "/api/services/input_boolean/turn_on", {"entity_id": "input_boolean.printer_plug"})
        t0 = time.monotonic()
        on = await wait_for(lambda: _numeric(session, f"{P}_nozzle_temperature"), 60)
        took = time.monotonic() - t0
        check("power: plug on brings it back within 20 s", bool(on) and took < 20, f"{took:.1f}s")

        # ---- notifications (#125) -----------------------------------------
        for name, os_name, maker, model in (
            (IPHONE, "iOS", "Apple", "iPhone15,2"), (PIXEL, "Android", "Google", "Pixel 8"),
        ):
            await _register_once(session, name, os_name, maker, model)
        await asyncio.sleep(2)
        targets = ["notify.mobile_app_testbox_iphone", "notify.mobile_app_testbox_pixel", "notify.plain_testbox"]
        await options(session, entry, "notifications", {
            "notify_targets": targets,
            "events": {"notify_live": True, "notify_completed": True, "notify_error": True,
                       "notify_minutes_to_end": False, "minutes_to_end_value": 10},
            "extras": {"notify_actions": True, "notify_preview_image": True,
                       "notify_camera_snapshot": False, "notify_tap_path": ""},
        })
        hactl.PUSH_CAPTURE.write_text("")
        hactl.NOTIFY_CAPTURE.write_text("")
        # A fresh two-minute print on the mock.
        env = {**os.environ, "PRINT_SECONDS": "120"}
        subprocess.run(["docker", "compose", "up", "-d", "--force-recreate", "printer"], cwd=HERE, env=env,
                       check=True, capture_output=True)
        finished = await wait_for(lambda: _completed_push(), 240, step=3)
        pushes = hactl._read_jsonl(hactl.PUSH_CAPTURE, None)
        ios = [p["body"] for p in pushes if p["device"] == "testbox_iphone"]
        android = [p["body"] for p in pushes if p["device"] == "testbox_pixel"]
        ios_live = [b for b in ios if (b.get("data") or {}).get("live_update") is not None]
        android_live = [b for b in android if (b.get("data") or {}).get("live_update") is not None]
        check("notify: the print completed and was announced", bool(finished), f"{len(pushes)} pushes")
        check("notify: iPhone got live-card pushes", bool(ios_live), f"{len(ios_live)}")
        check("notify: iPhone values are native types",
              bool(ios_live) and all(b["data"]["live_update"] is True and isinstance(b["data"].get("chronometer", False), bool)
                                     for b in ios_live),
              json.dumps({k: ios_live[0]["data"].get(k) for k in ("live_update", "chronometer", "progress")}) if ios_live else "")
        check("notify: core routed the iPhone card as a Live Activity",
              any(b.get("live_activity_token") for b in ios_live),
              f"events {sorted({(b.get('data') or {}).get('event') for b in ios_live} - {None})}")
        check("notify: Android values are strings",
              bool(android_live) and all(b["data"]["live_update"] == "true" for b in android_live),
              json.dumps({k: android_live[0]["data"].get(k) for k in ("live_update", "progress")}) if android_live else "")
        plain = hactl._read_jsonl(hactl.NOTIFY_CAPTURE, None)
        check("notify: the plain target gets text only",
              bool(plain) and all(not r.get("data") for r in plain), f"{len(plain)} message(s)")

        # ---- diagnostics ----------------------------------------------------
        async with session.get(
            f"{hactl.BASE_URL}/api/diagnostics/config_entry/{entry}",
            headers={"Authorization": f"Bearer {hactl.token()}"},
        ) as resp:
            text = await resp.text()
            code = resp.status
        leaks = sorted(set(re.findall(r"\b172\.31\.\d+\.\d+\b", text)))
        check("diag: download works and hides the address", code == 200 and not leaks, f"HTTP {code}, leaks {leaks}")

        # ---- host change (#39) -----------------------------------------------
        await options(session, entry, "connection", {"host": ALT_PRINTER, "polling_rate": 0})
        await wait_loaded(session)
        await wait_for(lambda: _numeric(session, f"{P}_nozzle_temperature"), 60)
        async with hactl.WS(session) as ws:
            reg = await ws.cmd("config/entity_registry/list")
            devices = await ws.cmd("config/device_registry/list")
        ours = [e for e in reg if e["platform"] == hactl.DOMAIN]
        after_ids = sorted(e["entity_id"] for e in ours)
        our_devices = [d for d in devices if any(i[0] == hactl.DOMAIN for i in d["identifiers"])]
        check("host: every entity id kept", after_ids == before_ids,
              f"{len(before_ids)} -> {len(after_ids)}")
        check("host: still one device, at the new address",
              len(our_devices) == 1 and [hactl.DOMAIN, ALT_PRINTER] in our_devices[0]["identifiers"],
              json.dumps([d["identifiers"] for d in our_devices]))
        await options(session, entry, "connection", {"host": PRINTER, "polling_rate": 0})
        await wait_loaded(session)

    errors = our_log_errors(started)
    check("log: no errors from the integration", not errors, "; ".join(errors[:3]))
    failed = sum(1 for _, ok, _ in results if not ok)
    print(f"\n{len(results) - failed}/{len(results)} passed")
    return failed


async def _numeric(session, entity_id):
    st = await state(session, entity_id)
    return st if is_number(st) else None


async def _is(session, entity_id, wanted):
    return (await state(session, entity_id)) == wanted


async def _completed_push():
    for p in hactl._read_jsonl(hactl.PUSH_CAPTURE, None):
        data = (p["body"].get("data") or {})
        if data.get("activity") == "end" or data.get("event") == "end":
            return True
    return False


async def _register_once(session, name, os_name, maker, model):
    async with hactl.WS(session) as ws:
        entries = await ws.cmd("config_entries/get", domain="mobile_app")
    if any(e.get("title") == name for e in entries):
        return
    await hactl.cmd_register_phone(SimpleArgs(name=name, os=os_name, manufacturer=maker, model=model))


class SimpleArgs:
    def __init__(self, **kw):
        self.__dict__.update(kw)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
