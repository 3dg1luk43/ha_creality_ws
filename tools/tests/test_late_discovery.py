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


@pytest.mark.parametrize("field", ["boxsInfo", "maxBoxTemp", "targetBoxTemp"])
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


def test_target_box_temp_alone_creates_the_chamber_control():
    """Promoting the capability then demanding a max made the promotion dead code.

    `BoxTargetNumber` already falls back to 60 C, and the discovery signal fires
    once -- so returning nothing here left the control permanently absent.
    """
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2]
        / "custom_components" / "ha_creality_ws" / "number.py"
    ).read_text()
    gate = source.split("def _chamber_entities()", 1)[1].split("\n    ents.extend", 1)[0]
    # The targetBoxTemp branch must return the entity before the max-temp gate.
    before_max = gate.split("_cached_max_chamber_temp", 1)[0]
    assert "BoxTargetNumber(coord)" in before_max, (
        "targetBoxTemp must create the control without waiting for a maximum"
    )


def test_deferred_entity_adds_are_dropped_after_unload():
    """`call_soon` cannot be cancelled, and unloading does not unschedule it."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "custom_components" / "ha_creality_ws"
    for name in ("number.py", "sensor.py"):
        source = (root / name).read_text()
        assert "hass.loop.call_soon(_add_if_live" in source, (
            f"{name} defers the raw add callback, so it can run against a "
            "removed entry"
        )
        assert "entry.async_on_unload(_mark_unloaded)" in source, name


def test_chamber_target_accepts_the_cached_capability():
    """The common case must not depend on live telemetry at all."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2]
        / "custom_components" / "ha_creality_ws" / "number.py"
    ).read_text()
    assert "_cached_max_chamber_temp" in source
    assert "_cached_max_box_temp" in source
