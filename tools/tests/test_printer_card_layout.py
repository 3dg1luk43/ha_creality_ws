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

    # The sample above proves each state is *reachable*, but not that it is the
    # whole set: a new `return "..."` branch would go unrepresented and the card
    # could drift from it silently. So read the returns out of the function too.
    declared = _declared_print_states()
    assert declared == produced, (
        "the input sample no longer covers every branch of derive_print_state: "
        f"declared {sorted(declared)}, sample produces {sorted(produced)}"
    )
    assert in_card == declared, (
        f"card has {sorted(in_card)}, derive_print_state declares {sorted(declared)}"
    )


def _declared_print_states() -> set:
    """Every string literal `derive_print_state` can return, read from source."""
    import ast
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2]
        / "custom_components/ha_creality_ws/utils.py"
    ).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == "derive_print_state":
            return {
                n.value.value
                for n in ast.walk(node)
                if isinstance(n, ast.Return)
                and isinstance(n.value, ast.Constant)
                and isinstance(n.value.value, str)
            }
    raise AssertionError("derive_print_state not found in utils.py")


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


def _card_source() -> str:
    from pathlib import Path

    return (
        Path(__file__).resolve().parents[2]
        / "custom_components/ha_creality_ws/www/k_printer_card.js"
    ).read_text(encoding="utf-8")


def test_processing_is_presented_as_an_active_print():
    """`processing` is the warm-up phase and is in BUSY_PRINT_STATES.

    The card used to treat it as active in `computeColor` only, so the icon fell
    through to the generic printer, the percentage and the progress ring were
    hidden, and -- the one that matters -- powering off got the weaker of the two
    confirmations even though a job was on the bed.
    """
    import re

    card = _card_source()

    icon_branch = re.search(
        r'if \(\[([^\]]*)\]\.includes\(st\)\) return mdi\("printer-3d-nozzle"\)', card
    )
    assert icon_branch, "the active-icon branch moved; update this test"
    assert "processing" in icon_branch.group(1), "processing must get the active icon"

    active = re.search(r'const isActivePrint = \[([^\]]*)\]\.includes\(st\)', card)
    assert active, "isActivePrint is gone; the presentation gate moved"
    for state in ("printing", "paused", "processing"):
        assert state in active.group(1), f"{state} must count as an active print"

    # The percentage and the ring both key on it, not on printing/paused.
    assert "const sec = isActivePrint ?" in card
    assert 'ring.style.setProperty("--ring-pct", isActivePrint ?' in card


def test_powering_off_mid_job_warns_for_every_busy_state():
    """The stronger confirmation must cover any state with a job on the bed."""
    import re

    card = _card_source()
    guard = re.search(r'const printing = \[([^\]]*)\]\.includes\(st\)', card)
    assert guard, "the power-off guard moved; update this test"
    for state in ("printing", "paused", "processing"):
        assert state in guard.group(1), (
            f"{state} leaves a job on the bed, so it needs the printing warning"
        )


def test_pause_and_stop_stay_restricted():
    """isActivePrint is presentation only: the commands keep their own gates.

    Pause is meaningless before the printer is actually printing, and Stop is
    deliberately not offered during `processing`.
    """
    card = _card_source()
    assert 'const isPrinting = st === "printing";' in card
    assert "hidden: !isPrinting," in card, "Pause must stay gated on printing alone"
    assert 'const showStop = isPrinting || isPaused || st === "self-testing";' in card
