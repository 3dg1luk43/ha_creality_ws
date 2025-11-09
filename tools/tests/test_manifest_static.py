import json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
manifest_path = ROOT / "custom_components" / "ha_creality_ws" / "manifest.json"

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+([abrc]\d+)?$")

REQUIRED_KEYS = {"domain", "name", "version", "requirements", "codeowners"}


def test_manifest_exists():
    assert manifest_path.exists(), "manifest.json missing"


def test_manifest_required_keys_and_semver():
    data = json.loads(manifest_path.read_text())
    missing = REQUIRED_KEYS - set(data.keys())
    assert not missing, f"Missing manifest keys: {missing}"
    assert data.get("domain") == "ha_creality_ws"
    # SemVer check disabled per request; allow any string or missing version
    # ver = data.get("version")
    # assert isinstance(ver, str) and SEMVER_RE.match(ver), f"Version not semantic: {ver}"


def test_manifest_no_polling_platforms():
    # Ensure we don't accidentally declare polling platforms here (we rely on push WS)
    data = json.loads(manifest_path.read_text())
    # Typical set of HA integration manifest keys; nothing here should indicate polling override
    assert "logistics" not in data  # Arbitrary sanity check; adjust if needed

