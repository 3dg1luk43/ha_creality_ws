"""CFS slot sensors expose corrected colours and de-duplicated labels.

Entity-level cover for issues #113, #115 and #117 -- the helpers are unit tested
in test_cfs_filament.py; this checks they are actually wired into the sensors.
"""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

# sensor.py pulls in a handful of HA modules that conftest does not stub.
if "homeassistant.components.sensor" not in sys.modules:
    sensor_mod = MagicMock()

    class _SensorEntity:  # minimal stand-in
        pass

    sensor_mod.SensorEntity = _SensorEntity
    sys.modules["homeassistant.components.sensor"] = sensor_mod

if not hasattr(sys.modules["homeassistant.helpers.entity"], "EntityCategory"):
    sys.modules["homeassistant.helpers.entity"].EntityCategory = MagicMock()

if "homeassistant.const" not in sys.modules:
    const_mod = MagicMock()
    sys.modules["homeassistant.const"] = const_mod

from custom_components.ha_creality_ws.sensor import (  # noqa: E402
    KCFSExtSlotSensor,
    KCFSSlotSensor,
    _cfs_slot_attributes,
)


GENERIC_SLOT = {
    "id": 0,
    "vendor": "Generic",
    "type": "PLA",
    "name": "Generic PLA",
    "color": "#0ffffff",
    "percent": 100,
    "state": 1,
    "selected": 0,
}


def _coordinator(slot):
    """Coordinator stub carrying a single CFS box with one slot."""
    return SimpleNamespace(
        client=SimpleNamespace(_host="1.2.3.4"),
        data={"boxsInfo": {"materialBoxs": [{"id": 1, "type": 0, "materials": [slot]}]}},
        available=True,
        power_is_off=lambda: False,
    )


def _sensor(sensor_type, slot=None):
    slot = GENERIC_SLOT if slot is None else slot
    return KCFSSlotSensor(_coordinator(slot), box_id=1, slot_id=0, sensor_type=sensor_type)


def test_color_sensor_state_drops_leading_pad_character():
    """Issue #113: the state must be the real colour, not the padded value."""
    assert _sensor("color").native_value == "#ffffff"


def test_filament_sensor_does_not_repeat_the_vendor():
    """Issue #115: 'Generic Generic PLA' collapses to 'Generic PLA'."""
    assert _sensor("filament").native_value == "Generic PLA"


def test_filament_sensor_joins_a_distinct_vendor():
    slot = {**GENERIC_SLOT, "vendor": "Creality", "name": "Hyper PLA"}
    assert _sensor("filament", slot).native_value == "Creality Hyper PLA"


def test_percent_sensor_is_unaffected():
    assert _sensor("percent").native_value == 100


def test_slot_attributes_expose_corrected_and_raw_colour():
    attrs = _cfs_slot_attributes(GENERIC_SLOT)
    assert attrs["color_hex"] == "#ffffff"
    assert attrs["color_hex_raw"] == "#0ffffff"


def test_slot_attributes_expose_a_spool_key():
    """Issue #117: same material, different colour must not share an id."""
    white = _cfs_slot_attributes({**GENERIC_SLOT, "rfid": "001001", "color": "#0ffffff"})
    black = _cfs_slot_attributes({**GENERIC_SLOT, "rfid": "001001", "color": "#0000000"})
    assert white["rfid"] == black["rfid"] == "001001"
    assert white["spool_key"] != black["spool_key"]


def test_slot_attributes_keep_the_documented_key_set():
    assert set(_cfs_slot_attributes(GENERIC_SLOT)) == {
        "vendor",
        "type",
        "name",
        "color_hex",
        "color_hex_raw",
        "rfid",
        "spool_key",
        "state",
        "selected",
        "box_id",
        "slot_id",
        "min_temp",
        "max_temp",
        "pressure",
    }


def test_slot_attributes_expose_the_printer_ids():
    """The card writes material by printer box/slot id, so they must be published."""
    attrs = _cfs_slot_attributes(GENERIC_SLOT, 1, 2)
    assert attrs["box_id"] == 1
    assert attrs["slot_id"] == 2


def test_slot_attributes_expose_editable_material_settings():
    """The edit dialog prefills from these, so camelCase must be mapped through."""
    slot = {**GENERIC_SLOT, "minTemp": 190, "maxTemp": 240, "pressure": 0.04}
    attrs = _cfs_slot_attributes(slot)
    assert attrs["min_temp"] == 190.0
    assert attrs["max_temp"] == 240.0
    assert attrs["pressure"] == 0.04


def test_slot_attributes_tolerate_missing_material_settings():
    """Real CFS box slots often omit temps entirely; that must not raise."""
    attrs = _cfs_slot_attributes(GENERIC_SLOT)
    assert attrs["min_temp"] is None
    assert attrs["max_temp"] is None
    assert attrs["pressure"] is None


def test_box_slot_sensor_reports_its_own_ids():
    attrs = _sensor("filament").extra_state_attributes
    assert attrs["box_id"] == 1
    assert attrs["slot_id"] == 0


def test_external_slot_sensor_reports_the_printers_box_id_not_zero():
    """The external box's id is whatever the printer says; don't assume 0."""
    coord = SimpleNamespace(
        client=SimpleNamespace(_host="1.2.3.4"),
        data={
            "boxsInfo": {
                "materialBoxs": [
                    {"id": 7, "type": 1, "materials": [{**GENERIC_SLOT, "id": 0}]},
                ]
            }
        },
        available=True,
        power_is_off=lambda: False,
    )
    attrs = KCFSExtSlotSensor(coord, slot_id=0, sensor_type="filament").extra_state_attributes
    assert attrs["box_id"] == 7
    assert attrs["slot_id"] == 0
