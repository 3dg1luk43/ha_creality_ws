from pathlib import Path
import importlib.util, sys

ROOT = Path(__file__).resolve().parents[2]
sensor_path = ROOT / "custom_components" / "ha_creality_ws" / "sensor.py"

# Load sensor module minimally (will import Home Assistant modules; for static uniqueness we can parse text)
text = sensor_path.read_text()


def _extract_uids(src: str):
    uids = []
    for line in src.splitlines():
        line_stripped = line.strip()
        if line_stripped.startswith('{') or '"uid"' in line_stripped or "'uid'" in line_stripped:
            if '"uid"' in line_stripped:
                # naive parse
                try:
                    part = line_stripped.split('"uid"', 1)[1]
                    # find value after :
                    after_colon = part.split(':',1)[1]
                    val = after_colon.split(',')[0].strip().strip('"\'')
                    if val:
                        uids.append(val)
                except Exception:
                    pass
            elif "'uid'" in line_stripped:
                try:
                    part = line_stripped.split("'uid'", 1)[1]
                    after_colon = part.split(':',1)[1]
                    val = after_colon.split(',')[0].strip().strip("'\"")
                    if val:
                        uids.append(val)
                except Exception:
                    pass
    return uids


def test_sensor_specs_uids_unique_and_contains_box():
    uids = _extract_uids(text)
    # filter out non-values like keys referencing
    filtered = [u for u in uids if u and not u.startswith('lambda')]
    assert 'box_temperature' in filtered, 'box_temperature uid missing'
    assert len(filtered) == len(set(filtered)), 'Duplicate sensor uid detected'

