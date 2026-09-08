from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

sensor_path = ROOT / "custom_components" / "ha_creality_ws" / "sensor.py"

# Load sensor module minimally (will import Home Assistant modules; for static uniqueness we can parse text)
text = sensor_path.read_text(encoding="utf-8")


def _extract_uids(src: str):
    """Naively parse sensor spec source and return a list of uid values found."""
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
    """Assert all sensor uids are unique and that box_temperature is present."""
    uids = _extract_uids(text)
    # filter out non-values like keys referencing
    filtered = [u for u in uids if u and not u.startswith('lambda')]
    assert 'box_temperature' in filtered, 'box_temperature uid missing'
    assert len(filtered) == len(set(filtered)), 'Duplicate sensor uid detected'


def test_no_two_sensors_read_the_same_telemetry_field():
    """Two sensors on one field are almost always an accidental duplicate.

    `sensor.system` and `sensor.model_info` were byte-identical for several
    releases -- same field, same attributes, different name -- and the uid
    uniqueness check above could not see it because the uids differed. Every
    user got both. If a genuine second view of one field is ever wanted, add it
    to the allowlist below with a reason.
    """
    import ast

    allowed_duplicate_fields: dict[str, str] = {
        # field: why two sensors legitimately read it
    }

    tree = ast.parse(text)
    seen: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        entry = {
            k.value: v
            for k, v in zip(node.keys, node.values)
            if isinstance(k, ast.Constant) and isinstance(k.value, str)
        }
        if "uid" not in entry or "field" not in entry:
            continue
        uid, field = entry["uid"], entry["field"]
        if not (isinstance(uid, ast.Constant) and isinstance(field, ast.Constant)):
            continue
        seen.setdefault(field.value, []).append(uid.value)

    assert seen, "found no sensor definitions to check"
    duplicates = {
        field: uids
        for field, uids in seen.items()
        if len(uids) > 1 and field not in allowed_duplicate_fields
    }
    assert not duplicates, f"more than one sensor per field: {duplicates}"
