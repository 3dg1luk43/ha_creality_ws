"""Building the ``modifyMaterial`` payload that writes filament data to a CFS.

The printer merges this payload into the slot it already holds, so a key that is
present-but-wrong overwrites real data. These tests pin the two properties that
matter: absent fields stay absent, and input the printer would not accept is
rejected instead of coerced.
"""

import pytest

from custom_components.ha_creality_ws.utils import (
    BUSY_PRINT_STATES,
    build_modify_material_payload,
    derive_print_state,
    normalize_material_color,
)


def test_minimal_payload_only_carries_the_addressing_and_type():
    """Anything the caller did not supply must not appear at all."""
    payload = build_modify_material_payload(box_id=1, slot_id=2, material_type="PLA")
    assert payload == {"boxId": 1, "id": 2, "type": "PLA"}


def test_full_payload_maps_snake_case_to_the_printers_camel_case():
    payload = build_modify_material_payload(
        box_id=1,
        slot_id=0,
        material_type="PETG",
        name="Hyper PETG",
        vendor="Creality",
        color="#ff00aa",
        min_temp=230,
        max_temp=260,
        pressure=0.03,
        rfid="001001",
    )
    assert payload == {
        "boxId": 1,
        "id": 0,
        "type": "PETG",
        "name": "Hyper PETG",
        "vendor": "Creality",
        "color": "#ff00aa",
        "minTemp": 230.0,
        "maxTemp": 260.0,
        "pressure": 0.03,
        "rfid": "001001",
    }


def test_absent_rfid_is_omitted_rather_than_blanked():
    """Sending rfid='' would wipe the tag association on an RFID spool."""
    for absent in (None, "", "   "):
        payload = build_modify_material_payload(
            box_id=1, slot_id=0, material_type="PLA", rfid=absent
        )
        assert "rfid" not in payload


def test_supplied_rfid_is_passed_through():
    payload = build_modify_material_payload(
        box_id=1, slot_id=0, material_type="PLA", rfid="001001"
    )
    assert payload["rfid"] == "001001"


def test_zero_valued_settings_survive():
    """0 is a real value; a truthiness check would silently drop it."""
    payload = build_modify_material_payload(
        box_id=0, slot_id=0, material_type="PLA", pressure=0.0, min_temp=0
    )
    assert payload["pressure"] == 0.0
    assert payload["minTemp"] == 0.0
    assert payload["boxId"] == 0
    assert payload["id"] == 0


def test_blank_material_type_is_rejected():
    with pytest.raises(ValueError, match="type"):
        build_modify_material_payload(box_id=1, slot_id=0, material_type="  ")


def test_inverted_temperature_range_is_rejected_not_clamped():
    """A user who typed min 240 / max 200 should be told, not quietly corrected."""
    with pytest.raises(ValueError, match="max_temp"):
        build_modify_material_payload(
            box_id=1, slot_id=0, material_type="PLA", min_temp=240, max_temp=200
        )


def test_equal_temperatures_are_allowed():
    payload = build_modify_material_payload(
        box_id=1, slot_id=0, material_type="PLA", min_temp=210, max_temp=210
    )
    assert payload["minTemp"] == payload["maxTemp"] == 210.0


@pytest.mark.parametrize("bad", [-0.1, 1.5])
def test_out_of_range_pressure_is_rejected(bad):
    with pytest.raises(ValueError, match="pressure"):
        build_modify_material_payload(
            box_id=1, slot_id=0, material_type="PLA", pressure=bad
        )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("#06c84f", "#06c84f"),
        ("06c84f", "#06c84f"),
        ("#06C84F", "#06c84f"),
        ("  #06C84F  ", "#06c84f"),
    ],
)
def test_colour_is_normalised_to_lowercase_six_digit_hex(raw, expected):
    assert normalize_material_color(raw) == expected


def test_rgb_list_is_rejected_with_a_pointed_message():
    """Guards the color_rgb-selector mistake: HA hands back [r, g, b]."""
    with pytest.raises(ValueError, match="color_rgb"):
        normalize_material_color([6, 200, 79])


@pytest.mark.parametrize("bad", ["nonsense", "#fff", "#0ffffff", "", "#12345g"])
def test_unwritable_colours_are_rejected(bad):
    with pytest.raises(ValueError):
        normalize_material_color(bad)


@pytest.mark.parametrize("multi", ["#0ffa800,#0ff97e1", "#ffa800;#ff97e1"])
def test_multi_colour_spools_are_not_flattened(multi):
    """A two-colour spool cannot be written as one colour, so refuse to try."""
    with pytest.raises(ValueError, match="multi-colour"):
        normalize_material_color(multi)


# --------------------------------------------------------------------------- #
# derive_print_state -- shared by PrintStatusSensor and the write guard
# --------------------------------------------------------------------------- #


def test_power_off_and_disconnected_take_priority():
    assert derive_print_state({}, power_off=True) == "off"
    assert derive_print_state({}, available=False) == "unknown"


def test_error_and_self_test_are_detected():
    assert derive_print_state({"err": {"errcode": 521}}) == "error"
    assert derive_print_state({"withSelfTest": 50}) == "self-testing"


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({}, "idle"),
        ({"printFileName": "a.gcode", "state": 1, "printProgress": 50}, "printing"),
        ({"printFileName": "a.gcode", "state": 0, "printProgress": 0}, "processing"),
        ({"printFileName": "a.gcode", "state": 5, "printProgress": 50}, "paused"),
        ({"printFileName": "a.gcode", "state": 4, "printProgress": 50}, "stopped"),
        ({"printFileName": "a.gcode", "state": 1, "printProgress": 100}, "completed"),
        # No filename means nothing is loaded, whatever `state` claims.
        ({"state": 1}, "idle"),
    ],
)
def test_operational_states(data, expected):
    assert derive_print_state(data) == expected


def test_paused_flag_overrides_a_stale_state_code():
    data = {"printFileName": "a.gcode", "state": 1, "printProgress": 50}
    assert derive_print_state(data, paused_flag=True) == "paused"


def test_unparseable_progress_does_not_read_as_completed():
    data = {"printFileName": "a.gcode", "state": 1, "printProgress": "n/a"}
    assert derive_print_state(data) == "printing"


def test_busy_states_are_exactly_the_uninterruptible_ones():
    assert BUSY_PRINT_STATES == {"printing", "paused", "processing", "self-testing"}
    for state in ("idle", "completed", "stopped", "off", "unknown", "error"):
        assert state not in BUSY_PRINT_STATES
