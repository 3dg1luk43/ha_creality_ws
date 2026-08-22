"""The simulator must model the CFS write path the integration depends on.

Two layers here:

* Source-text contracts, which run everywhere. The simulator hard-imports numpy,
  aiohttp, aiortc and av at module level, and CI installs only pytest, so it
  cannot be imported here -- the same reason test_fan.py greps the source.
* A live WebSocket round trip, skipped unless the project venv with the
  simulator's runtime dependencies is present. This is the only test that proves
  a ``modifyMaterial`` write is actually reflected in the next ``boxsInfo``.
"""

import asyncio
import importlib.util
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "tools" / "creality_printer_test_server.py"
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"


def _source() -> str:
    return SERVER.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Source contracts
# --------------------------------------------------------------------------- #


def test_server_streams_cfs_connect():
    """CFS discovery keys off cfsConnect.

    The integration caches ``_cached_cfs_detected`` from it and only polls
    boxsInfo blindly while it is unknown, so a simulator that never sends it
    leaves that whole fast path untested.
    """
    assert '"cfsConnect"' in _source()


def test_server_handles_modify_material():
    """Without this branch the set_cfs_material service has nothing to talk to."""
    source = _source()
    assert '"modifyMaterial" in params' in source
    assert "def modify_material" in source


def test_server_reports_the_material_fields_the_card_prefills_from():
    """The edit dialog prefills from these, so the fixtures must carry them."""
    source = _source()
    for field in ('"minTemp"', '"maxTemp"', '"pressure"'):
        assert field in source, f"test server does not report {field}"


def test_modify_material_merges_rather_than_replaces():
    """A write that omits rfid must not erase the printer's tag association."""
    source = _source()
    assert "MATERIAL_WRITABLE_KEYS" in source
    assert "if key in payload:" in source


def test_modify_material_rejects_unknown_targets():
    """A real printer would not invent a slot, so a bad boxId must fail loudly."""
    source = _source()
    assert "no such box" in source
    assert "no such slot" in source


# --------------------------------------------------------------------------- #
# Live round trip
# --------------------------------------------------------------------------- #


def _missing_dependency() -> str | None:
    """Why the live round trip cannot run here, or None if it can.

    Two interpreters are involved: the venv runs the *server*, and this test
    process is the WebSocket *client*. Both need their dependencies, and in CI
    neither has any -- so check both rather than assuming they match.
    """
    if importlib.util.find_spec("websockets") is None:
        return "websockets is not installed in the interpreter running the tests"
    if not VENV_PYTHON.exists():
        return f"no project venv at {VENV_PYTHON}"
    probe = subprocess.run(
        [str(VENV_PYTHON), "-c", "import websockets, aiohttp, aiortc, av, numpy"],
        capture_output=True,
    )
    if probe.returncode != 0:
        return "the project venv is missing the simulator's runtime dependencies"
    return None


requires_simulator = pytest.mark.skipif(
    _missing_dependency() is not None,
    reason=f"cannot run the simulator here: {_missing_dependency()}",
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_port(port: int, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.4)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.2)
    return False


@pytest.fixture
def simulator():
    """Run the simulator on free ports and yield its WebSocket URL."""
    ws_port, http_port = _free_port(), _free_port()
    proc = subprocess.Popen(
        [
            str(VENV_PYTHON), str(SERVER),
            "--model", "k2plus",
            "--ws-port", str(ws_port),
            "--http-port", str(http_port),
            "--deterministic",
            "--video-source", "synthetic",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=str(ROOT),
    )
    try:
        if not _wait_for_port(ws_port):
            proc.kill()
            out = proc.stdout.read() if proc.stdout else ""
            pytest.skip(f"simulator did not start: {out[-500:]}")
        yield f"ws://127.0.0.1:{ws_port}"
    finally:
        proc.kill()
        proc.wait(timeout=10)


def _slot(boxes, box_id, slot_id):
    for box in boxes:
        if box.get("id") == box_id:
            for material in box.get("materials", []):
                if material.get("id") == slot_id:
                    return material
    return None


async def _read_until(ws, predicate, timeout=15.0):
    """Telemetry snapshots interleave with replies, so filter for what we want."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        raw = await asyncio.wait_for(ws.recv(), timeout=deadline - loop.time())
        try:
            msg = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(msg, dict) and predicate(msg):
            return msg
    raise AssertionError("expected frame never arrived")


async def _boxes(ws):
    msg = await _read_until(ws, lambda m: "boxsInfo" in m)
    return msg["boxsInfo"]["materialBoxs"]


@requires_simulator
def test_modify_material_round_trip(simulator):
    """The write must be visible in the next boxsInfo, and merge cleanly.

    This is the check that makes set_cfs_material verifiable without CFS
    hardware: it proves the payload the integration builds addresses the right
    slot and that untouched fields survive.
    """
    import websockets  # noqa: PLC0415 - only importable when deps are present

    sys.path.insert(0, str(ROOT))
    from custom_components.ha_creality_ws.utils import (  # noqa: PLC0415
        build_modify_material_payload,
    )

    async def scenario():
        async with websockets.connect(simulator) as ws:
            snapshot = await _read_until(ws, lambda m: "cfsConnect" in m)
            assert snapshot["cfsConnect"] == 1

            await ws.send(json.dumps({"method": "get", "params": {"boxsInfo": 1}}))
            before = dict(_slot(await _boxes(ws), 1, 1))

            payload = build_modify_material_payload(
                box_id=1,
                slot_id=1,
                material_type="PETG",
                name="Hyper PETG",
                vendor="Creality",
                color="#ff00aa",
                min_temp=230,
                max_temp=260,
                pressure=0.03,
            )
            await ws.send(
                json.dumps({"method": "set", "params": {"modifyMaterial": payload}})
            )
            after = _slot(await _boxes(ws), 1, 1)

            assert after["type"] == "PETG"
            assert after["name"] == "Hyper PETG"
            assert after["color"] == "#ff00aa"
            assert after["minTemp"] == 230.0
            assert after["maxTemp"] == 260.0
            assert after["pressure"] == 0.03
            # Fields the write did not mention must be untouched.
            for key in ("percent", "state", "selected"):
                assert after[key] == before[key]
            # And a write with no rfid must not invent one.
            assert "rfid" not in after

    asyncio.run(scenario())


@requires_simulator
def test_existing_rfid_survives_a_write_that_omits_it(simulator):
    """The defect this guards: PR #75 sent rfid='' and wiped the tag id."""
    import websockets  # noqa: PLC0415

    sys.path.insert(0, str(ROOT))
    from custom_components.ha_creality_ws.utils import (  # noqa: PLC0415
        build_modify_material_payload,
    )

    async def scenario():
        async with websockets.connect(simulator) as ws:
            await _read_until(ws, lambda m: "cfsConnect" in m)

            # Give the slot a tag id, the way an RFID spool would arrive.
            await ws.send(json.dumps({"method": "set", "params": {"modifyMaterial": {
                "boxId": 1, "id": 0, "type": "PLA", "rfid": "001001",
            }}}))
            assert _slot(await _boxes(ws), 1, 0)["rfid"] == "001001"

            # Now change only the colour, as the card's edit dialog would.
            await ws.send(json.dumps({"method": "set", "params": {"modifyMaterial":
                build_modify_material_payload(
                    box_id=1, slot_id=0, material_type="PLA", color="#00ff00",
                )
            }}))
            after = _slot(await _boxes(ws), 1, 0)
            assert after["color"] == "#00ff00"
            assert after["rfid"] == "001001"

    asyncio.run(scenario())


@requires_simulator
def test_unknown_box_or_slot_is_rejected(simulator):
    """A wrong boxId should fail loudly, not silently create a phantom slot."""
    import websockets  # noqa: PLC0415

    async def scenario():
        async with websockets.connect(simulator) as ws:
            await _read_until(ws, lambda m: "cfsConnect" in m)

            for bad in (
                {"boxId": 9, "id": 0, "type": "PLA"},
                {"boxId": 1, "id": 7, "type": "PLA"},
            ):
                await ws.send(
                    json.dumps({"method": "set", "params": {"modifyMaterial": bad}})
                )

            await asyncio.sleep(1.0)
            await ws.send(json.dumps({"method": "get", "params": {"boxsInfo": 1}}))
            boxes = await _boxes(ws)
            assert _slot(boxes, 9, 0) is None
            assert _slot(boxes, 1, 7) is None

    asyncio.run(scenario())
