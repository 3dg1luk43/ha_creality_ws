from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
inst = ROOT / ".github" / "copilot-instructions.md"
SRC = ROOT / "custom_components" / "ha_creality_ws"

REQUIRED_HEADINGS = ["## Project overview", "## Design anchors", "## Model detection and feature management"]


def test_copilot_instructions_present_and_sections():
    assert inst.exists(), "copilot-instructions.md missing"
    text = inst.read_text()
    for h in REQUIRED_HEADINGS:
        assert h in text, f"Heading missing: {h}"
    # Ensure we don't have excessive duplicate heading lines
    lines = [l for l in text.splitlines() if l.startswith("## ")]
    assert len(lines) == len(set(lines)), "Duplicate section headings detected"



# --------------------------------------------------------------------------- #
# The file is read as ground truth by review bots, so a stale claim in it turns
# into a review finding against correct code. Both of these have already cost a
# round: "ruff configured in repo" produced a demand to reformat imports to pass
# a check nothing runs, and the `unsupported_model` listing produced a demand to
# re-add a model gate deliberately removed in 43c6668.
# --------------------------------------------------------------------------- #


def test_it_does_not_claim_a_linter_is_configured():
    """Nothing in the repo configures ruff, flake8 or pylint."""
    # `.ruff.toml` counts as project configuration too, per Ruff's docs, so a
    # check for `ruff.toml` alone would let one appear while this still passed.
    for name in ("ruff.toml", ".ruff.toml", ".flake8", ".pylintrc", "tox.ini"):
        assert not list(ROOT.glob(name)), (
            f"{name} exists now; the lint claims in the instructions need revisiting"
        )
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert "[tool.ruff" not in pyproject
    text = inst.read_text()
    assert "ruff configured in repo" not in text
    assert "ruff target-version" not in text
    assert "ruff-compliant" not in text


def test_preview_reason_list_matches_the_image_entity():
    """`unsupported_model` was removed from image.py; the doc must not list it.

    Both spellings: this guarded `unsupported_model` only, so the doc was free to
    say a bare "unsupported" -- which it did, for three rounds, until someone read
    it. Scoped to lines that actually describe `preview_reason`, so an unrelated
    use of the word elsewhere in the file does not fail.
    """
    import re

    image = (SRC / "image.py").read_text()
    assert "unsupported_model" not in image, "image.py changed; update the doc too"

    text = inst.read_text()
    assert "unsupported_model" not in text
    for line in text.splitlines():
        if not re.search(r"preview_reason", line):
            continue
        assert not re.search(r"\bunsupported\w*", line, re.IGNORECASE), (
            f"the only preview_reason values are ok/not_printing/fetch_failed: {line!r}"
        )


def test_the_preview_is_not_described_as_k1_only():
    """Three review rounds in a row flagged a K1-only preview claim in this file.

    `image.py` attempts the URL for every model (the gate went in 43c6668), so any
    line that ties the *preview* to the K1 family sends an agent to reinstate it.
    K1-family mentions about model detection or MJPEG cameras are accurate and
    deliberately not matched here.
    """
    import re

    k1 = re.compile(r"k1[ -]family|k1[ -]only", re.IGNORECASE)
    preview = re.compile(r"preview|image\.py", re.IGNORECASE)
    for line in inst.read_text().splitlines():
        if not k1.search(line) or not preview.search(line):
            continue
        # An explicit note that the gate was *removed* is the one allowed form.
        assert "removed" in line.lower(), (
            f"the preview is not K1-only; it is attempted for every model: {line!r}"
        )


def test_referenced_paths_exist():
    """A layout map that names deleted files sends readers to the wrong place.

    Only paths that are actually committed belong here. `tools/test_files/` is
    gitignored (.gitignore:7), so the deploy script in it exists on a maintainer's
    machine and in no clone -- asserting it exists passes locally and fails in CI.
    """
    for rel in ("custom_components/ha_creality_ws/light.py",):
        assert (ROOT / rel).exists(), f"{rel} is referenced but missing"
    text = inst.read_text()
    assert "ha_creality_ws/switch.py" not in text, "the switch platform was removed"
