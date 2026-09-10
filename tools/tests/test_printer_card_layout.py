from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
PRINTER_CARD = ROOT / "custom_components" / "ha_creality_ws" / "www" / "k_printer_card.js"


def _css_block(source: str, selector: str) -> str:
    """Return a CSS rule body for a selector in the printer card source."""
    match = re.search(rf"{re.escape(selector)}\s*\{{(?P<body>.*?)\n\s*\}}", source, re.S)
    assert match, f"Missing CSS block for {selector}"
    return match.group("body")


def test_printer_card_telemetry_wraps_when_roomy():
    """Keep the existing telemetry row wrapping behavior for roomy layouts."""
    source = PRINTER_CARD.read_text(encoding="utf-8")
    telemetry = _css_block(source, ".telemetry")
    assert "display:flex" in telemetry
    assert "flex-wrap:wrap" in telemetry
    assert "overflow-x:auto" not in telemetry
    assert "flex-wrap:nowrap" not in telemetry


def test_printer_card_size_tracks_measured_telemetry_lines():
    """Ensure wrapped telemetry updates Lovelace card size without breakpoints."""
    source = PRINTER_CARD.read_text(encoding="utf-8")
    assert "@container (max-width:" not in source
    assert "@media (max-width:" not in source
    assert "telemetry-scroll" not in source
    assert re.search(
        r"getCardSize\(\)\s*\{\s*return\s+this\._cardSize\s*\|\|\s*3;\s*\}",
        source,
    )
    assert "_setupTelemetrySizeObserver()" in source
    assert "_updateTelemetryCardSize()" in source
    assert "ResizeObserver" in source
    assert "offsetTop" in source
    assert 'CustomEvent("ll-rebuild"' in source


def test_printer_card_telemetry_pills_do_not_line_break():
    """Ensure each telemetry pill remains a stable single-line item."""
    source = PRINTER_CARD.read_text(encoding="utf-8")
    pill = _css_block(source, ".pill")
    assert "white-space:nowrap" in pill
    assert "flex:0 0 auto" in pill


def test_card_print_states_match_the_integration():
    """The card's state list must be exactly what derive_print_state can return.

    It had drifted: `"resuming"` and `"pausing"` were tested for in four places
    and neither has ever been a possible value, so those branches were dead and
    the paused colour depended on a state that never arrives. Mirrored and
    cross-checked the way k_cfs_card.js does with BUSY_PRINT_STATES.
    """
    import re
    from pathlib import Path

    from custom_components.ha_creality_ws.utils import derive_print_state

    card = (
        Path(__file__).resolve().parents[2]
        / "custom_components/ha_creality_ws/www/k_printer_card.js"
    ).read_text(encoding="utf-8")
    block = card.split("const PRINT_STATES = new Set([", 1)[1].split("]);", 1)[0]
    in_card = set(re.findall(r'"([a-z-]+)"', block))

    # Every state the function can actually produce, derived rather than listed.
    produced = {
        derive_print_state({}, power_off=True),
        derive_print_state({}, available=False),
        derive_print_state({"err": {"errcode": 1}}),
        derive_print_state({"withSelfTest": 50}),
        derive_print_state({"printFileName": "a", "printProgress": 100}),
        derive_print_state({"printFileName": "a", "printProgress": 5, "state": 5}),
        derive_print_state({"printFileName": "a", "printProgress": 5, "state": 4}),
        derive_print_state({"printFileName": "a", "printProgress": 5, "state": 1}),
        derive_print_state({"printFileName": "a", "printProgress": 5, "state": 0}),
        derive_print_state({}),
    }
    assert in_card == produced, (
        f"card has {sorted(in_card)}, derive_print_state produces {sorted(produced)}"
    )


def test_the_card_only_branches_on_real_states():
    """Guards against a new impossible state being introduced elsewhere."""
    import re
    from pathlib import Path

    card = (
        Path(__file__).resolve().parents[2]
        / "custom_components/ha_creality_ws/www/k_printer_card.js"
    ).read_text(encoding="utf-8")
    block = card.split("const PRINT_STATES = new Set([", 1)[1].split("]);", 1)[0]
    known = set(re.findall(r'"([a-z-]+)"', block))

    # Every state compared against `st`, the normalised status string.
    compared: set[str] = set()
    for match in re.finditer(r'st === "([a-z-]+)"', card):
        compared.add(match.group(1))
    for match in re.finditer(r'\[([^\]]*?)\]\.includes\(st\)', card):
        compared.update(re.findall(r'"([a-z-]+)"', match.group(1)))

    assert compared, "found no state comparisons to check"
    unknown = sorted(compared - known)
    assert not unknown, f"card branches on states derive_print_state cannot return: {unknown}"
