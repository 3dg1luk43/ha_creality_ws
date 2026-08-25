"""CFS filament parsing: colour normalisation, labels and spool keys.

Covers issues #113 (leading pad character in the colour hex), #115 (vendor
repeated in the filament label) and #117 (colour fix + a per-spool identifier
that distinguishes two spools of the same material but different colours).
"""

import pytest

from custom_components.ha_creality_ws.utils import (
    build_spool_key,
    format_filament_label,
    normalize_color_hex,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Creality writes seven hex chars: one pad char plus the real RRGGBB.
        ("#0ffffff", "#ffffff"),
        ("#0000000", "#000000"),
        ("#01b04ae", "#1b04ae"),
        ("#0FFFFFF", "#ffffff"),
        ("0ffffff", "#ffffff"),
        # Already-correct values pass through (normalised to lowercase + '#').
        ("#ffffff", "#ffffff"),
        ("1b04ae", "#1b04ae"),
        ("#fff", "#fff"),
    ],
)
def test_normalize_color_hex_keeps_last_six_digits(raw, expected):
    assert normalize_color_hex(raw) == expected


@pytest.mark.parametrize("raw", ["", "N/A", "unknown", "#12345", "red", None, 123, 1.5])
def test_normalize_color_hex_leaves_non_hex_untouched(raw):
    """Sentinels and unknown formats must survive unchanged, not be mangled."""
    assert normalize_color_hex(raw) == raw


def test_normalize_color_hex_handles_multi_colour_lists():
    assert normalize_color_hex("#0ffffff,#0000000") == "#ffffff,#000000"
    assert normalize_color_hex("#0ffffff;#01b04ae") == "#ffffff;#1b04ae"


@pytest.mark.parametrize(
    ("vendor", "name", "material_type", "expected"),
    [
        # Issue #115: the printer repeats the vendor inside the name.
        ("Generic", "Generic PLA", "PLA", "Generic PLA"),
        ("generic", "Generic PLA", "PLA", "Generic PLA"),
        # Distinct vendor and name still get joined.
        ("Creality", "Hyper PLA", "PLA", "Creality Hyper PLA"),
        # Missing name falls back to the material type.
        ("Generic", None, "PLA", "Generic PLA"),
        ("Creality", "", "PETG", "Creality PETG"),
        # A missing vendor is left out rather than invented.
        (None, "Hyper PLA", "PLA", "Hyper PLA"),
        ("", None, "PLA", "PLA"),
        # Nothing usable at all.
        (None, None, None, "Unknown"),
    ],
)
def test_format_filament_label(vendor, name, material_type, expected):
    assert format_filament_label(vendor, name, material_type) == expected


def test_spool_key_distinguishes_colours_of_the_same_material():
    """Issue #117: the printer's rfid is a material id, shared across colours."""
    white = build_spool_key(
        rfid="001001", vendor="Creality", material_type="PLA",
        name="Hyper PLA", color="#0ffffff",
    )
    black = build_spool_key(
        rfid="001001", vendor="Creality", material_type="PLA",
        name="Hyper PLA", color="#0000000",
    )
    assert white != black
    assert white == "001001_ffffff"
    assert black == "001001_000000"


def test_spool_key_falls_back_to_vendor_and_name_without_rfid():
    assert build_spool_key(
        vendor="Creality", material_type="PLA", name="Hyper PLA", color="#0ffffff"
    ) == "creality-hyper-pla_ffffff"


def test_spool_key_flattens_a_multi_colour_spool():
    """A spool reporting several colours must still yield one flat token."""
    assert build_spool_key(
        vendor="Generic", material_type="PLA", name="Generic PLA Silk",
        color="#0ffa800,#0ff97e1",
    ) == "generic-generic-pla-silk_ffa800-ff97e1"


def test_spool_key_ignores_a_non_hex_colour():
    """Sentinels must not leak into the key."""
    assert build_spool_key(rfid="001001", color="N/A") == "001001"
    assert build_spool_key(rfid="001001", color="unknown") == "001001"


def test_spool_key_uses_the_material_name_when_vendor_is_absent():
    assert build_spool_key(material_type="PETG", name="PETG", color="#1b04ae") == "petg_1b04ae"


def test_spool_key_is_none_when_slot_is_empty():
    assert build_spool_key() is None
    assert build_spool_key(rfid="", color="") is None
