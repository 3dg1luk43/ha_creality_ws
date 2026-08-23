from pathlib import Path
import json, re

ROOT = Path(__file__).resolve().parents[2]

const_path = ROOT / "custom_components" / "ha_creality_ws" / "const.py"
init_path = ROOT / "custom_components" / "ha_creality_ws" / "__init__.py"

ALLOWED_HOST_SUBSTRINGS = ["localhost", "http://", "ws://"]


def test_platforms_list_unique():
    """PLATFORMS must contain no duplicate entries.

    Parsed with ast rather than scanned line-by-line: the old version only
    asserted inside a branch requiring `PLATFORMS` and `[` on the same line, so
    wrapping the list across lines -- the natural result of adding a ninth
    platform -- made the loop find nothing and the test pass having checked
    nothing.
    """
    import ast

    tree = ast.parse(init_path.read_text(encoding="utf-8"))
    items = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(t, ast.Name) and t.id == "PLATFORMS" for t in targets):
            continue
        value = node.value
        assert isinstance(value, (ast.List, ast.Tuple)), "PLATFORMS must be a literal list"
        items = [
            el.value if isinstance(el, ast.Constant) else ast.unparse(el)
            for el in value.elts
        ]
        break

    assert items is not None, "PLATFORMS assignment not found in __init__.py"
    assert items, "PLATFORMS is empty"
    assert len(items) == len(set(items)), f"duplicate platform entries: {items}"


def test_no_unexpected_cloud_urls():
    """Confirm no unexpected external (cloud) URLs are hardcoded in the integration source."""
    suspicious = []
    for p in (ROOT / "custom_components" / "ha_creality_ws").glob("*.py"):
        text = p.read_text(encoding="utf-8")
        for m in re.findall(r"https?://[A-Za-z0-9._:/-]+", text):
            if not any(sub in m for sub in ALLOWED_HOST_SUBSTRINGS):
                suspicious.append(m)
    assert not suspicious, f"Unexpected external URLs found: {suspicious}"
