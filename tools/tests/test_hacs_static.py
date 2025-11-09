import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
hacs_path = ROOT / "hacs.json"

REQUIRED_KEYS = {"name", "content_in_root"}


def test_hacs_exists():
    assert hacs_path.exists(), "hacs.json missing"


def test_hacs_required_keys():
    data = json.loads(hacs_path.read_text())
    missing = REQUIRED_KEYS - set(data.keys())
    assert not missing, f"Missing hacs.json keys: {missing}"
    assert data.get("name"), "HACS name empty"
    assert isinstance(data.get("content_in_root"), bool), "content_in_root must be bool"
    # Optional keys like render_readme/homeassistant may be present; 'domains' is not supported

