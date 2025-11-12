from pathlib import Path
import json, re

ROOT = Path(__file__).resolve().parents[2]
const_path = ROOT / "custom_components" / "ha_creality_ws" / "const.py"
init_path = ROOT / "custom_components" / "ha_creality_ws" / "__init__.py"

ALLOWED_HOST_SUBSTRINGS = ["localhost", "http://", "ws://"]


def test_platforms_list_unique():
    text = init_path.read_text()
    # crude parse for PLATFORMS list line
    for line in text.splitlines():
        if line.strip().startswith("PLATFORMS") and "[" in line:
            inside = line.split("[",1)[1].rsplit("]",1)[0]
            items = [i.strip().strip("'\"") for i in inside.split(',') if i.strip()]
            assert len(items) == len(set(items)), "Duplicate platform entries in PLATFORMS"
            break


def test_no_unexpected_cloud_urls():
    suspicious = []
    for p in (ROOT / "custom_components" / "ha_creality_ws").glob("*.py"):
        text = p.read_text()
        for m in re.findall(r"https?://[A-Za-z0-9._:/-]+", text):
            if not any(sub in m for sub in ALLOWED_HOST_SUBSTRINGS):
                suspicious.append(m)
    assert not suspicious, f"Unexpected external URLs found: {suspicious}"

