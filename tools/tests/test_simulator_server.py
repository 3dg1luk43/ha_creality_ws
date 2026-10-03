"""The simulator's servers and control actions, on ephemeral ports.

Skipped where websockets and aiohttp are missing (CI installs neither); the
printer model itself is covered without them in test_simulator_core.py.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("websockets")
pytest.importorskip("aiohttp")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import websockets  # noqa: E402

from simulator.cli import build_argparser, prepare  # noqa: E402
from simulator.server import Simulator  # noqa: E402


def _args(model="k2plus", **extra) -> argparse.Namespace:
    argv = ["--model", model, "--ws-port", "0", "--http-port", "0", "--mjpeg-port", "0",
            "--web-port", "0", "--moonraker-port", "0", "--control-port", "0",
            "--deterministic", "--video-source", "synthetic"]
    for key, value in extra.items():
        argv += [f"--{key.replace('_', '-')}", str(value)]
    return prepare(build_argparser().parse_args(argv))


def _run(scenario):
    async def main():
        sim = Simulator(_args())
        await sim.start()
        try:
            return await scenario(sim)
        finally:
            await sim.stop()

    return asyncio.run(main())


def _ws_port(sim) -> int:
    return next(iter(sim._ws_server.sockets)).getsockname()[1]


def test_settings_are_checked():
    """`frames: "bogus"` and `silent: "false"` used to be stored and answered ok."""
    async def scenario(sim):
        for bad in ({"frames": "bogus"}, {"silent": "false"}, {"nonsense": 1}, {"first_frame_delay": "x"}):
            with pytest.raises(ValueError):
                await sim.act("settings", bad)
        await sim.act("settings", {"frames": "full", "silent": True, "stop_style": "clear"})
        return sim.settings.frames, sim.settings.silent, sim.state.stop_style

    assert _run(scenario) == ("full", True, "clear")


def test_actions_say_when_they_did_nothing():
    async def scenario(sim):
        return [
            (await sim.act("stop", {}))["applied"],
            (await sim.act("finish_now", {}))["applied"],
            (await sim.act("cfs_swap", {}))["applied"],
        ]

    assert _run(scenario) == [False, False, False]


def test_an_override_needs_a_key_and_null_lifts_a_timed_blank():
    async def scenario(sim):
        with pytest.raises(ValueError):
            await sim.act("set_override", {"value": 1})
        await sim.act("blank_fields", {"fields": "boxTemp", "seconds": 60})
        blanked = sim.state.snapshot()["boxTemp"]
        await sim.act("set_override", {"key": "boxTemp", "value": None})
        return blanked, sim.state.snapshot()["boxTemp"]

    blanked, after = _run(scenario)
    assert blanked == "" and after != ""


def test_reading_the_state_does_not_change_the_cfs():
    """Polling the control UI changed what the printer reported in boxsInfo."""
    async def scenario(sim):
        sim.state.cfs.deterministic = False
        first = sim.describe()["cfs"]
        second = sim.describe()["cfs"]
        return first == second

    assert _run(scenario)


def test_a_client_gets_a_full_frame_then_only_changes():
    async def scenario(sim):
        async with websockets.connect(f"ws://127.0.0.1:{_ws_port(sim)}") as ws:
            first = json.loads(await ws.recv())
            sim.state.set_nozzle_temp(200)
            sim.state.tick()
            delta = json.loads(await asyncio.wait_for(ws.recv(), 5))
            return len(first), delta

    size, delta = _run(scenario)
    assert size > 50
    assert "targetNozzleTemp" in delta and "model" not in delta


def test_power_off_refuses_connections_and_power_on_boots_blank():
    async def scenario(sim):
        port = _ws_port(sim)
        await sim.act("power_off", {})
        with pytest.raises(OSError):
            await websockets.connect(f"ws://127.0.0.1:{port}", open_timeout=2)
        await sim.act("power_on", {"boot_blanks": 30})
        async with websockets.connect(f"ws://127.0.0.1:{_ws_port(sim)}") as ws:
            return json.loads(await ws.recv())

    first = _run(scenario)
    assert first["nozzleTemp"] == "" and first["printFileName"] == ""


def test_switching_the_model_rebuilds_the_printer():
    async def scenario(sim):
        await sim.act("switch_profile", {"key": "k1c-1.3.5"})
        async with websockets.connect(f"ws://127.0.0.1:{_ws_port(sim)}") as ws:
            return json.loads(await ws.recv())

    first = _run(scenario)
    assert first["model"] == "K1C" and first["webrtcSupport"] == 1


def test_numbers_must_be_finite_and_a_bad_call_changes_nothing():
    """inf/nan reached /api/state as invalid JSON; a call with one bad key
    still applied the good ones before failing."""
    async def scenario(sim):
        for bad in ({"first_frame_delay": "inf"}, {"finished_reset_seconds": "nan"},
                    {"first_frame_delay": True}, {"frames": "full", "stop_style": "bogus"}):
            with pytest.raises(ValueError):
                await sim.act("settings", bad)
        for action, args in (("targets", {"nozzle": "nan"}), ("start_print", {"seconds": -5}),
                             ("start_print", {"layers": 0}), ("home", {"seconds": "inf"})):
            with pytest.raises(ValueError):
                await sim.act(action, args)
        json.dumps(sim.describe(), allow_nan=False)
        return sim.settings.frames, sim.state.phase.value

    assert _run(scenario) == ("delta", "idle")
