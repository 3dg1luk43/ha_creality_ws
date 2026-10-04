"""How the entities present themselves to Home Assistant (R36).

Enum sensors list their states for automations, an enum's state is always one
of its options (Home Assistant refuses the write otherwise), machine limits and
the reconnect button are grouped with the diagnostics, and the print head
position, which changes many times a second while printing, is off for new
installs.
"""

from types import SimpleNamespace

import pytest

from custom_components.ha_creality_ws.sensor import (
    MAPPED_SPECS,
    SPECS,
    KMappedSensor,
    KMaxTempSensor,
    KPrintControlSensor,
    PrintStatusSensor,
)
from custom_components.ha_creality_ws.utils import PRINT_STATES


def _coord(data=None, *, available=True, power_off=False):
    return SimpleNamespace(
        client=SimpleNamespace(_host="1.2.3.4"),
        data=data or {},
        available=available,
        power_is_off=lambda: power_off,
        paused_flag=lambda: False,
        pending_pause=lambda: False,
        pending_resume=lambda: False,
        config_entry=SimpleNamespace(data={}),
    )


STATUS_FRAMES = [
    {},
    {"err": {"errcode": 1}},
    {"withSelfTest": 50},
    {"printFileName": "a", "printProgress": 100},
    {"printFileName": "a", "printProgress": 5, "state": 5},
    {"printFileName": "a", "printProgress": 5, "state": 4},
    {"printFileName": "a", "printProgress": 5, "state": 1},
    {"printFileName": "a", "printProgress": 5, "state": 0},
]


def test_the_print_status_offers_every_state_it_reports():
    sensor = PrintStatusSensor(_coord())
    assert sensor._attr_device_class == "enum"
    assert set(sensor._attr_options) == set(PRINT_STATES) - {"unknown"}


@pytest.mark.parametrize("frame", STATUS_FRAMES)
def test_the_print_status_is_always_one_of_its_options(frame):
    value = PrintStatusSensor(_coord(frame)).native_value
    assert value is None or value in PrintStatusSensor._attr_options


def test_an_unreachable_printer_has_no_status_string():
    """"unknown" as a string is not an option; None is Home Assistant's own."""
    assert PrintStatusSensor(_coord(available=False)).native_value is None


@pytest.mark.parametrize("raw", [0, 1, "1", 7, "x", None])
def test_the_filament_status_is_always_one_of_its_options(raw):
    """An unmapped code used to be reported as its raw text."""
    sensor = KMappedSensor(_coord({"materialStatus": raw}), MAPPED_SPECS[0])
    assert sensor._attr_device_class == "enum"
    assert sensor.native_value is None or sensor.native_value in sensor._attr_options
    if raw in (1, "1"):
        assert sensor.native_value == "runout"


def test_the_print_control_is_an_enum_and_a_diagnostic():
    sensor = KPrintControlSensor(_coord(available=False))
    assert sensor.native_value is None
    assert sensor._attr_options == ["queued", "ok"]
    assert sensor._attr_entity_category == "diagnostic"


def test_machine_limits_are_diagnostics():
    sensor = KMaxTempSensor(_coord(), uid="max_bed_temp", key="max_bed_temp", translation_key="max_bed_temp")
    assert sensor._attr_entity_category == "diagnostic"


def test_only_the_fast_movers_are_off_for_new_installs():
    """The head position and the real-time speed change many times a second
    while printing; everything else stays on."""
    off = {spec["uid"] for spec in SPECS if spec.get("enabled_default") is False}
    assert off == {"position_x", "position_y", "position_z", "real_time_speed"}
