from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]

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


def test_no_em_dashes_anywhere():
    """House rule: no em dash (U+2014) in the repository.

    Enforced rather than trusted because it is invisible in review -- an em
    dash and a double hyphen look near-identical in a diff, and the character
    arrives easily from pasted prose or a model's own output.

    The replacement depends on what the dash was doing. In prose it is ` -- `,
    matching the comment style used throughout. Where a card renders a dash as
    its placeholder for an unknown value it is a plain `-`, because that string
    is also compared by equality in several places and the two must agree. In
    user-visible copy neither substitution reads well, so those were reworded
    instead: reach for a semicolon or a comma rather than pasting `--` into
    something a user will read.
    """
    import subprocess

    tracked = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        capture_output=True, check=True,
    ).stdout.split(b"\0")

    offenders = []
    for rel in tracked:
        if not rel:
            continue
        path = ROOT / rel.decode()
        if not path.is_file():
            continue
        try:
            body = path.read_bytes()
        except OSError:
            continue
        if b"\xe2\x80\x94" not in body:
            continue
        if b"\x00" in body:
            # Binary. The rule is about text a reviewer reads, and compressed
            # data hits these three bytes by chance -- so the bundled WebP
            # could fail this on an unrelated re-encode and report a line of
            # image data as an em dash. A NUL is the same heuristic `git diff`
            # uses to call a file binary.
            continue
        for number, line in enumerate(body.split(b"\n"), 1):
            if b"\xe2\x80\x94" in line:
                offenders.append(
                    f"{rel.decode()}:{number}: "
                    f"{line.decode('utf-8', 'replace').strip()[:80]}"
                )

    assert not offenders, "em dashes found:\n" + "\n".join(offenders)
