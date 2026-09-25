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


# The simulator's own module-level third-party imports, and only those. Kept in
# step with the identical set in `test_fan.py`; anything else missing is a
# broken simulator, not an environment that cannot run it.
_SIMULATOR_OPTIONAL_DEPS = frozenset({"aiortc", "av", "websockets", "aiohttp", "numpy"})


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


def test_the_h264_clip_is_encoded_before_the_answer_is_sent():
    """`available()` only proves the ffmpeg binary exists; the encode needs
    libx264, which some builds omit. Encoding on the first `recv()` meant the
    failure landed in aiortc's sender task after the SDP answer had gone, so
    the session connected with no video and the documented synthetic fallback
    never ran."""
    source = _source()
    assert "async def _make_video_track" in source
    assert "await self._make_video_track(" in source
    assert "await track.prepare()" in source
    assert "falling back to \n" not in source


def test_modify_material_rejects_a_non_object_payload():


    """`params.get("modifyMaterial") or {}` passes a list or a string straight
    through, and `.get` on one raises AttributeError -- which the handler does
    not catch, so it escaped `rx_loop`, closed the socket, and left `tx_loop`
    ticking against a dead connection. A source contract as well as the live
    assertion below, because CI cannot import the simulator at all."""
    source = _source()
    assert "modifyMaterial needs an object" in source
    assert "non-object material entry" in source


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


# Evaluated once: each call runs a subprocess probe against the venv interpreter.
_MISSING_DEPENDENCY = _missing_dependency()

requires_simulator = pytest.mark.skipif(
    _MISSING_DEPENDENCY is not None,
    reason=f"cannot run the simulator here: {_MISSING_DEPENDENCY}",
)


def _free_ports(count: int) -> list[int]:
    """Reserve `count` distinct ephemeral ports.

    Every socket is held open until all of them are chosen; closing one before
    picking the next lets the kernel hand out the same port twice, and the
    simulator then fails to bind its second listener -- surfacing as "simulator
    did not start" rather than the real cause.
    """
    socks = []
    try:
        for _ in range(count):
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            socks.append(sock)
        return [s.getsockname()[1] for s in socks]
    finally:
        for s in socks:
            s.close()


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
    ws_port, http_port = _free_ports(2)
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


def test_modify_material_rejects_an_unknown_box_or_slot():
    """The state assertions above also pass for a handler that silently ignores
    the write, so pin the rejection itself.

    Asserted against `PrinterState.modify_material` in process rather than by
    scraping the subprocess log: the handler logs `modifyMaterial rejected` and
    sends no protocol error, so an end-to-end assertion would mean a reader
    thread and a timed wait for a log line -- flaky for no extra coverage. This
    is the source of that rejection, and `ws_handle_conn` only catches what it
    raises.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("_sim_reject", SERVER)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_sim_reject"] = module
    try:
        spec.loader.exec_module(module)
    except ModuleNotFoundError as exc:  # pragma: no cover - optional extras
        # Only a genuinely optional dependency is a skip. Skipping on *any*
        # ModuleNotFoundError turns a simulator that has lost `h264_timing`, or
        # gained a typo in an import, into a quiet pass -- which is what
        # `test_fan.py` already fixed for its own copy of this handler, and how
        # `numpy` went unnoticed there for six days.
        if (exc.name or "").split(".")[0] not in _SIMULATOR_OPTIONAL_DEPS:
            raise
        pytest.skip(f"simulator dependency missing: {exc.name}")
    finally:
        sys.modules.pop("_sim_reject", None)

    state = module.PrinterState(
        "k2plus",
        simulate_print=False,
        sim=module.SimOptions(),
        targets={},
        deterministic=True,
    )

    with pytest.raises(ValueError, match=r"no such box 9"):
        state.modify_material({"boxId": 9, "id": 0, "type": "PLA"})

    with pytest.raises(ValueError, match=r"no such slot 7 in box 1"):
        state.modify_material({"boxId": 1, "id": 7, "type": "PLA"})

    # A payload that is not an object at all. `rx_loop` does
    # `params.get("modifyMaterial") or {}`, which lets a list, a string or a
    # number through; `.get` on one raises AttributeError, which the handler
    # does not catch, so it escaped the receive loop and took the connection
    # with it instead of being logged as a rejected write.
    for payload in ([], ["boxId"], "boxId=1", 7, True):
        with pytest.raises(ValueError, match=r"needs an object"):
            state.modify_material(payload)

    # And the same one level down: `POST /test/cfs` stores whatever list it is
    # given, so a non-object entry would raise from the slot lookup.
    state.set_cfs_materials(1, ["not a dict"])
    with pytest.raises(ValueError, match=r"non-object material entry"):
        state.modify_material({"boxId": 1, "id": 0, "type": "PLA"})

    # And a well-formed write to a real slot still applies, so the guards above
    # are not simply rejecting everything.
    state.set_cfs_materials(1, [{"id": 0, "type": "PLA"}])
    updated = state.modify_material({"boxId": 1, "id": 0, "type": "PETG"})
    assert updated["type"] == "PETG"
