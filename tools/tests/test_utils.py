import importlib.util
import sys
from pathlib import Path

import pytest

# Load utils module directly from file to avoid importing package-level __init__
ROOT = Path(__file__).resolve().parents[2]
utils_path = ROOT / "custom_components" / "ha_creality_ws" / "utils.py"
spec = importlib.util.spec_from_file_location("ha_creality_ws.utils", utils_path)
assert spec is not None, f"Failed to load spec for {utils_path}"
utils = importlib.util.module_from_spec(spec)
assert utils is not None
sys.modules["ha_creality_ws.utils"] = utils
assert spec.loader is not None
spec.loader.exec_module(utils)

coerce_numbers = utils.coerce_numbers
core_version_supported = utils.core_version_supported
derive_print_state = utils.derive_print_state
derive_activity_state = utils.derive_activity_state
PREVIEW_PRINT_STATES = utils.PREVIEW_PRINT_STATES
BUSY_PRINT_STATES = utils.BUSY_PRINT_STATES
parse_model_version = utils.parse_model_version
parse_position = utils.parse_position
safe_float = utils.safe_float
extract_info_from_zeroconf = utils.extract_info_from_zeroconf


def test_coerce_numbers():
    d = {"a": "1", "b": "2.5", "c": "x", "d": 3}
    out = coerce_numbers(d)
    assert isinstance(out["a"], int)
    assert isinstance(out["b"], float)
    assert out["c"] == "x"
    assert out["d"] == 3


def test_parse_model_version_printer_and_dwin():
    s = "Printer HW Ver: 1.0; Printer SW Ver: 2.0; DWIN HW Ver: 3"
    hw, sw = parse_model_version(s)
    assert hw == "1.0"
    assert sw == "2.0"

    s2 = "DWIN HW Ver: 110; DWIN SW Ver: 220"
    hw2, sw2 = parse_model_version(s2)
    assert hw2 == "DWIN 110"
    assert sw2 == "DWIN 220"


def test_parse_position():
    d = {"curPosition": "X:12.34 Y:-5.0 Z:0.00"}
    x, y, z = parse_position(d)
    assert x == 12.34 and y == -5.0 and z == 0.0

    d2 = {"curPosition": "invalid"}
    assert parse_position(d2) == (None, None, None)


def test_safe_float():
    assert safe_float("2.5") == 2.5
    assert safe_float(None) is None


def test_extract_info_from_zeroconf_dicts():
    """Covers the host-resolution order config_flow.async_step_zeroconf depends on.

    Note the link-local address is skipped in favour of the routable one, and
    the trailing dot is stripped from an mDNS hostname.
    """
    host, _mac = extract_info_from_zeroconf({"host": "192.168.1.5"})
    assert host == "192.168.1.5"

    host, _mac = extract_info_from_zeroconf({"addresses": ["fe80::1", "10.0.0.2"]})
    assert host == "10.0.0.2"

    host, _mac = extract_info_from_zeroconf({"hostname": "printer.local."})
    assert host == "printer.local"


# --------------------------------------------------------------------------- #
# Minimum core version
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "running,expected",
    [
        ((2026, 7), True),
        ((2026, 8), True),
        ((2027, 1), True),
        ((2026, 6), False),
        ((2024, 11), False),
        ((2025, 12), False),
        # The whole reason this is a tuple compare: "2026.10" sorts *before*
        # "2026.7" as a string, so a string compare would reject a newer core.
        ((2026, 10), True),
        # Unreadable version: not being able to tell is our problem, not the
        # user's, so it must not block setup.
        (None, True),
    ],
)
def test_core_version_supported(running, expected):
    assert core_version_supported(running, (2026, 7)) is expected


def test_the_declared_minimum_matches_what_hacs_advertises():
    """hacs.json is what stops an old core installing this at all; the runtime
    check is what catches a manual install. If the two disagree, one of them
    lets people through."""
    import json

    const_spec = importlib.util.spec_from_file_location(
        "ha_creality_ws.const_for_version_test",
        ROOT / "custom_components" / "ha_creality_ws" / "const.py",
    )
    const = importlib.util.module_from_spec(const_spec)
    const_spec.loader.exec_module(const)

    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    declared = tuple(int(part) for part in hacs["homeassistant"].split(".")[:2])
    assert declared == const.MINIMUM_HA_VERSION


# --------------------------------------------------------------------------- #
# Activity state
# --------------------------------------------------------------------------- #

PRINTING_FRAME = {"printFileName": "a.gcode", "printProgress": 42, "state": 1}


def test_activity_state_matches_print_state_when_there_is_no_error():
    for frame in (
        {},
        {"printFileName": "a.gcode", "printProgress": 100},
        dict(PRINTING_FRAME),
        dict(PRINTING_FRAME, state=5),
        dict(PRINTING_FRAME, state=4),
        dict(PRINTING_FRAME, state=0),
        {"withSelfTest": 50},
    ):
        assert derive_activity_state(frame) == derive_print_state(frame), frame


def test_a_stale_error_code_does_not_mask_what_the_job_is_doing():
    """The printer leaves err.errcode set long after the fault. derive_print_state
    must still say "error" -- that is what the status sensor shows -- but callers
    deciding whether a job can be paused, or whether a preview exists, need the
    job underneath it."""
    frame = dict(PRINTING_FRAME, err={"errcode": 521, "key": 1})
    assert derive_print_state(frame) == "error"
    assert derive_activity_state(frame) == "printing"


def test_activity_state_still_reports_error_when_nothing_is_running():
    frame = {"err": {"errcode": 521}}
    assert derive_print_state(frame) == "error"
    # No job to fall back to, so it lands on idle rather than pretending to print.
    assert derive_activity_state(frame) == "idle"


def test_power_and_availability_still_win_over_a_running_job():
    assert derive_activity_state(dict(PRINTING_FRAME), power_off=True) == "off"
    assert derive_activity_state(dict(PRINTING_FRAME), available=False) == "unknown"


def test_preview_states_are_the_busy_ones_plus_completed():
    """A finished print still has its model on the bed, so its preview is real."""
    assert PREVIEW_PRINT_STATES == BUSY_PRINT_STATES | {"completed"}
    assert "idle" not in PREVIEW_PRINT_STATES
    assert "stopped" not in PREVIEW_PRINT_STATES
