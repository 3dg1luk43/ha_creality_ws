import importlib.util
import sys
from pathlib import Path

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
parse_model_version = utils.parse_model_version
parse_position = utils.parse_position
safe_float = utils.safe_float
extract_host_from_zeroconf = utils.extract_host_from_zeroconf


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


def test_extract_host_from_zeroconf_dicts():
    info = {"host": "192.168.1.5"}
    assert extract_host_from_zeroconf(info) == "192.168.1.5"

    info2 = {"addresses": ["fe80::1", "10.0.0.2"]}
    assert extract_host_from_zeroconf(info2) == "10.0.0.2"

    info3 = {"hostname": "printer.local."}
    assert extract_host_from_zeroconf(info3) == "printer.local"
