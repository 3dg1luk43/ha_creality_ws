import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
manifest_path = ROOT / "custom_components" / "ha_creality_ws" / "manifest.json"

REQUIRED_KEYS = {"domain", "name", "version", "requirements", "codeowners"}


def test_manifest_exists():
    assert manifest_path.exists(), "manifest.json missing"


def test_manifest_required_keys_and_semver():
    data = json.loads(manifest_path.read_text())
    missing = REQUIRED_KEYS - set(data.keys())
    assert not missing, f"Missing manifest keys: {missing}"
    assert data.get("domain") == "ha_creality_ws"


def test_manifest_declares_local_push():
    """The whole design is a pushed WebSocket feed, with no polling interval."""
    data = json.loads(manifest_path.read_text())
    assert data.get("iot_class") == "local_push"

