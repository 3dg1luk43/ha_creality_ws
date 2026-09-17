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
    assert not list(ROOT.glob("ruff.toml"))
    assert not list(ROOT.glob(".flake8"))
    assert not list(ROOT.glob(".pylintrc"))
    pyproject = (ROOT / "pyproject.toml").read_text()
    assert "[tool.ruff" not in pyproject
    text = inst.read_text()
    assert "ruff configured in repo" not in text
    assert "ruff target-version" not in text
    assert "ruff-compliant" not in text


def test_preview_reason_list_matches_the_image_entity():
    """`unsupported_model` was removed from image.py; the doc must not list it."""
    image = (SRC / "image.py").read_text()
    assert "unsupported_model" not in image, "image.py changed; update the doc too"
    assert "unsupported_model" not in inst.read_text()


def test_referenced_paths_exist():
    """A layout map that names deleted files sends readers to the wrong place."""
    for rel in (
        "custom_components/ha_creality_ws/light.py",
        "tools/test_files/deploy_to_ha.sh",
    ):
        assert (ROOT / rel).exists(), f"{rel} is referenced but missing"
    text = inst.read_text()
    assert "ha_creality_ws/switch.py" not in text, "the switch platform was removed"
