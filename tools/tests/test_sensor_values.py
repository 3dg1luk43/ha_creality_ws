"""Numeric sensor states are numbers or None, never a raw telemetry string.

Home Assistant rejects a numeric sensor's state write when the value is not a
number, and the entity keeps its previous state: after a power-on, that is
"unavailable" (#121, whose log shows `non-numeric value: ''` for `bedTemp0` and
`nozzleTemp`). The client's state is cumulative, so one blank frame used to
strand the sensor until the printer happened to resend that key.
"""

import math
from types import SimpleNamespace

import pytest

from custom_components.ha_creality_ws.sensor import (
    SPECS,
    KCFSBoxSensor,
    KCFSSlotSensor,
    KSimpleFieldSensor,
    PrintLeftTimeSensor,
)
from custom_components.ha_creality_ws.utils import coerce_numbers, numeric_state


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", None),
        ("   ", None),
        ("N/A", None),
        ("nan", None),
        ("inf", None),
        (float("nan"), None),
        (float("inf"), None),
        (True, None),
        (None, None),
        ({"a": 1}, None),
        (42, 42),
        (1.5, 1.5),
        ("42", 42),
        ("42.0", 42.0),
        ("1e3", 1000.0),
        (" 7 ", 7),
    ],
)
def test_numeric_state(raw, expected):
    assert numeric_state(raw) == expected
    # An int must stay an int: 128 -> 128.0 would change the state string.
    assert type(numeric_state(raw)) is type(expected)


def _coordinator(data):
    return SimpleNamespace(
        client=SimpleNamespace(_host="1.2.3.4"),
        data=data,
        available=True,
        power_is_off=lambda: False,
    )


def _spec(uid):
    return next(spec for spec in SPECS if spec["uid"] == uid)


def _sensor(uid, data):
    return KSimpleFieldSensor(_coordinator(data), _spec(uid))


def test_a_blank_temperature_from_a_booting_printer_is_unknown_not_rejected():
    """The exact #121 frame, through the client's own coercion."""
    frame = coerce_numbers({"bedTemp0": "", "nozzleTemp": ""})
    assert _sensor("bed_temperature", frame).native_value is None
    assert _sensor("nozzle_temperature", frame).native_value is None


@pytest.mark.parametrize("spec", SPECS, ids=lambda spec: spec["uid"])
def test_no_spec_sensor_ever_returns_a_string(spec):
    """Every SPECS sensor is numeric, so a blank in any of its fields
    (including the curPosition string the positions are parsed from) must not
    reach Home Assistant as text."""
    blank = {
        key: ""
        for key in (
            spec["field"], "printProgress", "dProgress", "curPosition",
        )
    }
    value = KSimpleFieldSensor(_coordinator(blank), spec).native_value
    assert value is None or isinstance(value, (int, float))


def test_a_layer_count_stays_an_integer():
    frame = coerce_numbers({"layer": "128"})
    value = _sensor("current_layer", frame).native_value
    assert value == 128 and isinstance(value, int)


def test_a_real_zero_percent_is_not_replaced_by_the_last_jobs_progress():
    """`printProgress or dProgress` treated a real 0% as missing, so the start
    of a job showed the previous job's 100%."""
    assert _sensor("print_progress", {"printProgress": 0, "dProgress": 100}).native_value == 0


def test_progress_falls_back_when_the_primary_field_is_missing_or_blank():
    assert _sensor("print_progress", {"dProgress": 37}).native_value == 37
    assert _sensor("print_progress", {"printProgress": "", "dProgress": 37}).native_value == 37


def test_a_blank_time_left_is_unknown():
    sensor = PrintLeftTimeSensor(_coordinator({"printLeftTime": ""}))
    assert sensor.native_value is None


def test_blank_cfs_box_and_slot_values_are_unknown():
    data = {
        "boxsInfo": {
            "materialBoxs": [
                {
                    "id": 1,
                    "type": 0,
                    "temp": "",
                    "humidity": "",
                    "materials": [{"id": 0, "percent": ""}],
                }
            ]
        }
    }
    coord = _coordinator(data)
    assert KCFSBoxSensor(coord, box_id=1, sensor_type="temp").native_value is None
    assert KCFSBoxSensor(coord, box_id=1, sensor_type="humidity").native_value is None
    slot = KCFSSlotSensor(coord, box_id=1, slot_id=0, sensor_type="percent")
    assert slot.native_value is None


def test_numeric_strings_inside_cfs_data_still_read_as_numbers():
    """boxsInfo is nested, so the client's top-level coercion never reaches it."""
    data = {"boxsInfo": {"materialBoxs": [{"id": 1, "type": 0, "temp": "25.5"}]}}
    value = KCFSBoxSensor(_coordinator(data), box_id=1, sensor_type="temp").native_value
    assert math.isclose(value, 25.5)
