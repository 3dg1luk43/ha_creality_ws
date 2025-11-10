from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT / "custom_components" / "ha_creality_ws"

BLOCK_PATTERNS = [
    r"time\.sleep",                 # direct blocking sleep
    r"^\s*import\s+requests\b",    # importing requests implies potential blocking HTTP
    r"^\s*from\s+requests\s+import\b",
    r"urllib\.request",             # legacy sync HTTP usage
]


def test_no_blocking_calls_in_component():
    texts = []
    for p in SRC_DIR.glob("*.py"):
        texts.append(p.read_text())
    combined = "\n".join(texts)
    for pat in BLOCK_PATTERNS:
        assert re.search(pat, combined, re.MULTILINE) is None, f"Blocking pattern {pat} found in source"

