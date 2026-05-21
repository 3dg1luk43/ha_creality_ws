from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
PRINTER_CARD = ROOT / "custom_components" / "ha_creality_ws" / "www" / "k_printer_card.js"


def _css_block(source: str, selector: str) -> str:
    match = re.search(rf"{re.escape(selector)}\s*\{{(?P<body>.*?)\n\s*\}}", source, re.S)
    assert match, f"Missing CSS block for {selector}"
    return match.group("body")


def test_printer_card_telemetry_wraps_when_roomy():
    source = PRINTER_CARD.read_text(encoding="utf-8")
    telemetry = _css_block(source, ".telemetry")

    assert "display:flex" in telemetry
    assert "flex-wrap:wrap" in telemetry
    assert "overflow-x:auto" not in telemetry
    assert "flex-wrap:nowrap" not in source


def test_printer_card_size_tracks_measured_telemetry_lines():
    source = PRINTER_CARD.read_text(encoding="utf-8")

    assert "@container (max-width:" not in source
    assert "@media (max-width:" not in source
    assert "telemetry-scroll" not in source

    assert "getCardSize() { return this._cardSize || 3; }" in source
    assert "_setupTelemetrySizeObserver()" in source
    assert "_updateTelemetryCardSize()" in source
    assert "ResizeObserver" in source
    assert "offsetTop" in source
    assert 'CustomEvent("ll-rebuild"' in source


def test_printer_card_telemetry_pills_do_not_line_break():
    source = PRINTER_CARD.read_text(encoding="utf-8")
    pill = _css_block(source, ".pill")

    assert "white-space:nowrap" in pill
    assert "flex:0 0 auto" in pill
