import json
from pathlib import Path
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
base = ROOT / "custom_components" / "ha_creality_ws"

# Load const directly
spec_c = importlib.util.spec_from_file_location("ha_creality_ws.const", base / "const.py")
assert spec_c is not None
const = importlib.util.module_from_spec(spec_c)
assert const is not None
sys.modules["ha_creality_ws.const"] = const
assert spec_c.loader is not None
spec_c.loader.exec_module(const)

# Avoid importing sensor.py (it imports Home Assistant). We'll do static checks instead.
sensor_path = base / "sensor.py"
assert sensor_path.exists()
sensor_text = sensor_path.read_text(encoding="utf-8")


def test_manifest_exists_and_has_version():
    """Verify manifest.json exists and declares a version field."""
    p = Path(__file__).resolve().parents[2] / "custom_components" / "ha_creality_ws" / "manifest.json"
    assert p.exists()
    m = json.loads(p.read_text(encoding="utf-8"))
    assert "version" in m


def test_specs_contains_box_sensor_uid():
    """Check that sensor.py references the box_temperature uid required for K1/K2 models."""
    # Ensure SPECS includes the box_temperature uid used by sensors
    assert '"box_temperature"' in sensor_text or "'box_temperature'" in sensor_text
