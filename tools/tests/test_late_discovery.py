"""Late entity discovery.

Platform setup deliberately does not wait for the printer, so any entity gated
on a telemetry field can be missed when Home Assistant starts while the printer
is off. The coordinator fires one discovery signal the first time such a field
appears; this checks it fires for each gating field, exactly once each.
"""

import asyncio
import sys
from types import SimpleNamespace

import pytest

from custom_components.ha_creality_ws.const import LATE_DISCOVERY_FIELDS  # noqa: E402
from custom_components.ha_creality_ws.coordinator import KCoordinator  # noqa: E402

# `from <package> import <submodule>` would execute the real package __init__,
# which needs Home Assistant proper; the dotted import above does not.
coord_mod = sys.modules[KCoordinator.__module__]


def _number_platform():
    """number.async_setup_entry, imported without executing the real package init.

    The HA modules it needs are stubbed in the shared conftest, so nothing is
    installed (or leaked) from here.
    """
    from custom_components.ha_creality_ws.number import async_setup_entry

    return async_setup_entry


class _EntryStub:
    """Just the ConfigEntry surface number.async_setup_entry touches."""

    def __init__(self, data):
        self.data = data
        self.entry_id = "entry1"
        self.options = {}
        self.unload_callbacks = []

    def async_on_unload(self, cb):
        self.unload_callbacks.append(cb)
        return cb

    def unload(self):
        for cb in reversed(self.unload_callbacks):
            cb()


def _run_number_setup(coord, entry_data):
    """Drive the real platform and return (added_entities, hass, entry, connected).

    `connected` is the dispatcher callback the platform registered, so a test can
    fire the discovery signal directly.
    """
    setup = _number_platform()
    added = []
    connected = []
    scheduled = []

    hass = SimpleNamespace(
        data={"ha_creality_ws": {}},
        loop=SimpleNamespace(call_soon=lambda fn, *a: scheduled.append((fn, a))),
    )
    entry = _EntryStub(entry_data)
    hass.data["ha_creality_ws"][entry.entry_id] = coord

    import custom_components.ha_creality_ws.number as number_mod

    original_connect = number_mod.async_dispatcher_connect
    number_mod.async_dispatcher_connect = (
        lambda _hass, _signal, target: connected.append(target) or (lambda: None)
    )
    try:
        asyncio.get_event_loop().run_until_complete(
            setup(hass, entry, lambda ents: added.extend(ents))
        )
    finally:
        number_mod.async_dispatcher_connect = original_connect

    return SimpleNamespace(
        added=added, hass=hass, entry=entry,
        fire_discovery=lambda: [cb() for cb in connected],
        flush=lambda: [fn(*a) for fn, a in scheduled],
        scheduled=scheduled,
    )


class HassStub:
    def __init__(self):
        self.loop = asyncio.get_event_loop()
        self.states = SimpleNamespace(get=lambda _eid: None)
        # The coordinator loads options from the entry on construction.
        self.config_entries = SimpleNamespace(async_get_entry=lambda _entry_id: None)


@pytest.fixture(autouse=True)
def _event_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield
    loop.close()


@pytest.fixture
def coord(monkeypatch):
    """Coordinator with the discovery dispatcher captured."""
    sent: list[str] = []
    monkeypatch.setattr(
        coord_mod, "async_dispatcher_send", lambda _hass, signal: sent.append(signal)
    )
    c = KCoordinator(HassStub(), host="1.2.3.4", config_entry_id="entry1")
    # Keep the frame handler focused on discovery.
    monkeypatch.setattr(c, "_flush_pending", lambda: asyncio.sleep(0))
    monkeypatch.setattr(c, "_check_notifications", lambda _p: asyncio.sleep(0))
    monkeypatch.setattr(c, "async_update_listeners", lambda: None)
    c.signals = sent
    return c


def _feed(c, payload):
    asyncio.get_event_loop().run_until_complete(c._handle_message(payload))


def test_maxboxtemp_is_a_gating_field():
    """Chamber control depends on it, so its arrival must trigger discovery."""
    assert "maxBoxTemp" in LATE_DISCOVERY_FIELDS
    assert "boxsInfo" in LATE_DISCOVERY_FIELDS


def test_every_capability_gate_field_can_trigger_discovery():
    """The invariant behind this tuple, stated once.

    A field that some platform reads from coord.data to decide whether to create
    an entity, but which is absent here, can never trigger the pass that would
    create it. That mismatch has now caused the same defect twice: targetBoxTemp
    for number.py's chamber control, then boxTemp for sensor.py's chamber sensor.
    """
    # number.py::_chamber_entities promotes chamber *control* on these.
    control_gates = ("targetBoxTemp", "maxBoxTemp")
    # sensor.py::add_chamber_entities promotes the chamber *sensor* on these.
    sensor_gates = ("boxTemp", "targetBoxTemp", "maxBoxTemp")

    for field in set(control_gates) | set(sensor_gates):
        assert field in LATE_DISCOVERY_FIELDS, (
            f"{field} gates an entity but cannot trigger discovery"
        )


def test_targetboxtemp_is_a_gating_field():
    """number.py gates the chamber control on it, so it must also trigger.

    A K2 Base reports a chamber target but never a maximum. With targetBoxTemp
    missing from this tuple, the gate in _chamber_entities and the signal that
    re-runs it disagreed, and the control stayed absent until a restart raced the
    right way -- the exact defect the tuple exists to prevent.
    """
    assert "targetBoxTemp" in LATE_DISCOVERY_FIELDS


def test_a_target_box_temp_only_printer_triggers_discovery(coord):
    """The runtime path, not just the constant: feed only targetBoxTemp."""
    _feed(coord, {"model": "K2", "nozzleTemp": 25})
    assert coord.signals == []

    _feed(coord, {"targetBoxTemp": 40.0})
    assert len(coord.signals) == 1, (
        "a chamber target with no maximum must still re-run entity discovery"
    )


def _gating_value(field):
    if field == "boxsInfo":
        return {"materialBoxs": []}
    return 80.0 if field == "maxBoxTemp" else 40.0


# Parametrized over the constant itself, so a field added there without a working
# trigger fails here rather than being silently untested.
@pytest.mark.parametrize("field", LATE_DISCOVERY_FIELDS)
def test_first_appearance_of_a_gating_field_triggers_discovery(coord, field):
    _feed(coord, {"model": "K2 Plus"})
    assert coord.signals == []

    _feed(coord, {field: _gating_value(field)})
    assert coord.signals == ["ha_creality_ws_new_entities_entry1"]


@pytest.mark.parametrize("field", ["boxsInfo", "maxBoxTemp"])
def test_repeated_frames_do_not_re_trigger_discovery(coord, field):
    value = 80.0 if field == "maxBoxTemp" else {"materialBoxs": []}
    for _ in range(4):
        _feed(coord, {field: value})
    assert len(coord.signals) == 1


def test_each_gating_field_triggers_once(coord):
    """A printer that reports the fields in separate frames signals for each."""
    _feed(coord, {"maxBoxTemp": 80.0})
    _feed(coord, {"boxsInfo": {"materialBoxs": []}})
    _feed(coord, {"maxBoxTemp": 80.0, "boxsInfo": {"materialBoxs": []}})
    assert len(coord.signals) == 2


def test_both_fields_in_one_frame_signal_once(coord):
    _feed(coord, {"maxBoxTemp": 80.0, "boxsInfo": {"materialBoxs": []}})
    assert len(coord.signals) == 1


def test_a_direct_data_write_of_a_gating_field_still_announces(coord):
    """Every writer of a gating field must go through merge_telemetry.

    On a K2 Base the WS feed pops targetBoxTemp:0, so the Moonraker poll is the
    only source of the field that gates the chamber control. Writing straight
    into coord.data skipped the signal *and* consumed the one-shot, so a later
    frame carrying the field was no longer "newly seen" -- the control could then
    never be created.
    """
    _feed(coord, {"model": "K2", "nozzleTemp": 25})
    assert coord.signals == []

    coord.merge_telemetry({"targetBoxTemp": 40.0})
    assert len(coord.signals) == 1, "a first-seen gating field must fire discovery"

    # Still a one-shot: a later frame with the same field must not re-fire.
    _feed(coord, {"targetBoxTemp": 45.0})
    assert len(coord.signals) == 1


def test_the_moonraker_fallback_uses_the_announcing_merge():
    """Pins the call site, since the bug was a direct dict write."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2]
        / "custom_components" / "ha_creality_ws" / "coordinator.py"
    ).read_text()
    poll = source.split("async def _poll_moonraker_extras", 1)[1]
    assert 'merge_telemetry({"targetBoxTemp"' in poll, (
        "the Moonraker poll must not write targetBoxTemp into self.data directly"
    )
    assert 'self.data["targetBoxTemp"] =' not in poll


def test_no_platform_writes_a_gating_field_directly(monkeypatch):
    """A direct `coordinator.data[field] = v` skips the signal and burns the one-shot.

    number.py's optimistic chamber-target write did exactly that. Harmless while
    the only gate on targetBoxTemp is the one that entity already satisfies, but
    it is the same trap that made the Moonraker path silently break discovery.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "custom_components" / "ha_creality_ws"
    for name in ("number.py", "sensor.py", "fan.py"):
        source = (root / name).read_text()
        for field in LATE_DISCOVERY_FIELDS:
            assert f'data["{field}"] =' not in source, (
                f"{name} writes {field} directly; use coordinator.merge_telemetry"
            )


def test_merge_telemetry_is_the_public_entry_point(coord):
    """Platforms call it, so it must not be name-mangled back to private."""
    assert hasattr(coord, "merge_telemetry")
    coord.merge_telemetry({"boxsInfo": {"materialBoxs": []}})
    assert len(coord.signals) == 1


def test_number_platform_does_not_await_the_add_callback():
    """`async_add_entities` is a sync @callback; awaiting it raises TypeError."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "custom_components" / "ha_creality_ws"
    for name in ("number.py", "sensor.py"):
        source = (root / name).read_text()
        assert "await async_add_entities" not in source, f"{name} awaits the add callback"


def test_number_platform_subscribes_to_the_discovery_signal():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2]
        / "custom_components" / "ha_creality_ws" / "number.py"
    ).read_text()
    assert "_new_entities_" in source
    assert "async_dispatcher_connect" in source


def test_the_capability_flag_is_read_inside_the_gate_not_captured():
    """Capturing it at setup froze the late pass out.

    `_chamber_entities` is the late-discovery path too, so a `False` captured
    before the printer reported anything would have permanently prevented the
    chamber number from being created.
    """
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2]
        / "custom_components" / "ha_creality_ws" / "number.py"
    ).read_text()
    gate = source.split("def _chamber_entities()", 1)[1].split("\n    ents.extend", 1)[0]
    assert "_cached_has_chamber_control" in gate, (
        "the capability must be read inside the gate, not captured at setup"
    )
    # Live telemetry promotes the capability the same way __init__ does when it
    # caches it, so a printer that was off at setup still gets the entity.
    assert "targetBoxTemp" in gate
    assert "maxBoxTemp" in gate


def _bare_coord(monkeypatch, data):
    """A coordinator carrying `data`, with the frame handler stubbed out."""
    monkeypatch.setattr(coord_mod, "async_dispatcher_send", lambda *_a, **_k: None)
    c = KCoordinator(HassStub(), host="1.2.3.4", config_entry_id="entry1")
    c.data = dict(data)
    return c


def test_target_box_temp_alone_creates_the_chamber_control(monkeypatch):
    """A chamber target with no maximum must still produce BoxTargetNumber.

    Promoting the capability and then demanding a maximum made the promotion dead
    code: the discovery signal fires once, so returning nothing left the control
    permanently absent. BoxTargetNumber already falls back to 60 C.
    """
    coord = _bare_coord(monkeypatch, {"targetBoxTemp": 40.0})
    run = _run_number_setup(coord, {"_cached_has_chamber_control": True})

    names = [type(e).__name__ for e in run.added]
    assert "BoxTargetNumber" in names, f"chamber control missing from {names}"


def test_the_chamber_control_is_created_late_when_telemetry_arrives(monkeypatch):
    """The real late-discovery path: nothing at setup, then the field appears."""
    coord = _bare_coord(monkeypatch, {})
    run = _run_number_setup(coord, {"_cached_has_chamber_control": True})

    assert "BoxTargetNumber" not in [type(e).__name__ for e in run.added], (
        "nothing gates the control yet"
    )

    # Printer comes online and reports the chamber target.
    coord.data["targetBoxTemp"] = 40.0
    run.fire_discovery()
    run.flush()  # the platform defers the add via loop.call_soon

    assert "BoxTargetNumber" in [type(e).__name__ for e in run.added], (
        "the late pass must create the control"
    )


def test_the_chamber_control_is_not_created_twice(monkeypatch):
    """Repeated discovery signals must not duplicate the entity."""
    coord = _bare_coord(monkeypatch, {"targetBoxTemp": 40.0})
    run = _run_number_setup(coord, {"_cached_has_chamber_control": True})
    before = [type(e).__name__ for e in run.added].count("BoxTargetNumber")

    run.fire_discovery()
    run.fire_discovery()
    run.flush()

    assert [type(e).__name__ for e in run.added].count("BoxTargetNumber") == before == 1


def test_no_chamber_control_without_the_capability(monkeypatch):
    """A printer with no chamber must not gain the control from telemetry alone."""
    coord = _bare_coord(monkeypatch, {"nozzleTemp": 25})
    run = _run_number_setup(coord, {"_cached_has_chamber_control": False})

    assert "BoxTargetNumber" not in [type(e).__name__ for e in run.added]
    run.fire_discovery()
    run.flush()
    assert "BoxTargetNumber" not in [type(e).__name__ for e in run.added]


def test_a_cached_maximum_alone_does_not_imply_chamber_control(monkeypatch):
    """A cached chamber *maximum* is not a control capability.

    Some models report a chamber maximum for the read-only sensor while having no
    settable chamber. Without the capability check the cached maximum alone would
    create BoxTargetNumber, giving those printers a control that does nothing.
    """
    coord = _bare_coord(monkeypatch, {"nozzleTemp": 25})
    run = _run_number_setup(coord, {
        "_cached_has_chamber_control": False,
        "_cached_max_chamber_temp": 60,
    })

    assert "BoxTargetNumber" not in [type(e).__name__ for e in run.added], (
        "a maximum without the control capability must not create the entity"
    )


def test_deferred_entity_adds_are_dropped_after_unload(monkeypatch):
    """`call_soon` cannot be cancelled, and unloading does not unschedule it.

    Executed rather than grepped: the callback is queued, the entry is unloaded,
    and only then is the queue drained -- which is the ordering the flag exists
    for.
    """
    coord = _bare_coord(monkeypatch, {})
    run = _run_number_setup(coord, {"_cached_has_chamber_control": True})

    coord.data["targetBoxTemp"] = 40.0
    run.fire_discovery()
    assert run.scheduled, "the add must be deferred, not inline"

    added_before = len(run.added)
    run.entry.unload()   # HA drains _on_unload
    run.flush()          # the already-queued callback now runs

    assert len(run.added) == added_before, (
        "a deferred add must not run against an unloaded entry"
    )


def test_chamber_target_accepts_the_cached_capability():
    """The common case must not depend on live telemetry at all."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2]
        / "custom_components" / "ha_creality_ws" / "number.py"
    ).read_text()
    assert "_cached_max_chamber_temp" in source
    assert "_cached_max_box_temp" in source
