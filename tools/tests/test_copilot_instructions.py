from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
inst = ROOT / ".github" / "copilot-instructions.md"

REQUIRED_HEADINGS = ["## Project overview", "## Design anchors", "## Model detection and feature management"]


def test_copilot_instructions_present_and_sections():
    assert inst.exists(), "copilot-instructions.md missing"
    text = inst.read_text()
    for h in REQUIRED_HEADINGS:
        assert h in text, f"Heading missing: {h}"
    # Ensure we don't have excessive duplicate heading lines
    lines = [l for l in text.splitlines() if l.startswith("## ")]
    assert len(lines) == len(set(lines)), "Duplicate section headings detected"

