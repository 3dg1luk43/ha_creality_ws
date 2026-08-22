"""The CFS card: behavioural tests under node, plus source-level guards.

The card is JavaScript, so there is no way to unit test it from Python directly.
Two approaches here:

* ``tools/tests/js/`` runs the real card in a node sandbox with a small DOM shim
  and asserts on what it collects and renders. That catches logic errors, which
  is what actually matters. Skipped when node is unavailable (CI installs only
  pytest).
* Source-level guards for properties that are structural rather than behavioural
  -- a second copy of the collection loop, leftover debug logging, a card asset
  that quietly bloats every install.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WWW = ROOT / "custom_components" / "ha_creality_ws" / "www"
CARD = WWW / "k_cfs_card.js"
PRINTER_CARD = WWW / "k_printer_card.js"
JS_TESTS = Path(__file__).parent / "js"

NODE = shutil.which("node")
requires_node = pytest.mark.skipif(
    NODE is None, reason="node is not installed (expected in CI)"
)


def _card() -> str:
    return CARD.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Behaviour, executed under node
# --------------------------------------------------------------------------- #


@requires_node
@pytest.mark.parametrize("suite", sorted(p.name for p in JS_TESTS.glob("test_*.mjs")))
def test_javascript_suite(suite):
    """Run a node test file and surface its output on failure."""
    result = subprocess.run(
        [NODE, str(JS_TESTS / suite)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, (
        f"{suite} failed:\n{result.stdout}\n{result.stderr}"
    )


@requires_node
def test_card_parses():
    """A syntax error would break every dashboard using the card."""
    for path in (CARD, PRINTER_CARD):
        result = subprocess.run(
            [NODE, "--check", str(path)], capture_output=True, text=True
        )
        assert result.returncode == 0, f"{path.name}: {result.stderr}"


# --------------------------------------------------------------------------- #
# Structural guards
# --------------------------------------------------------------------------- #


def test_only_one_slot_collection_loop():
    """PR #75 added a second, parallel box/slot loop for change detection.

    Two copies drift: one gains a field the other forgets, and the card renders
    something the gate cannot see. _collectData must be the only reader.
    """
    assert _card().count("_slot${slotId}_filament") == 1


def test_render_gate_is_wired():
    source = _card()
    assert "_collectData()" in source
    assert "_updateIfChanged()" in source
    # A recursive deep-equal is unnecessary for a fixed-shape scalar tree.
    assert "_deepEqual" not in source


def test_setconfig_resets_the_snapshot():
    """Otherwise _render() wipes #content and the gate declines to refill it."""
    source = _card()
    set_config = source.split("setConfig(config) {", 1)[1][:600]
    assert "_snapshot = null" in set_config


def test_cards_have_no_console_logging():
    """PR #75 shipped 18 console.log calls; both cards currently have none."""
    for path in (CARD, PRINTER_CARD):
        text = path.read_text(encoding="utf-8")
        assert "console.log(" not in text, f"{path.name} logs to the console"


def test_cards_do_not_use_alert():
    """alert() blocks the whole UI; confirm() is an accepted pattern here."""
    for path in (CARD, PRINTER_CARD):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"(?<![.\w])alert\s*\(", text), f"{path.name} uses alert()"


def test_www_payload_budget():
    """Every HACS install downloads this directory.

    PR #75 added a 509 KB decorative PNG to a 143 KB directory. Anything that
    large needs to be a deliberate decision, not a side effect of a card change.
    """
    total = sum(p.stat().st_size for p in WWW.rglob("*") if p.is_file())
    assert total < 400_000, (
        f"www/ is {total / 1000:.0f} kB; largest files: "
        + ", ".join(
            f"{p.name} {p.stat().st_size // 1000} kB"
            for p in sorted(
                (p for p in WWW.rglob("*") if p.is_file()),
                key=lambda p: -p.stat().st_size,
            )[:3]
        )
    )


# --------------------------------------------------------------------------- #
# i18n
# --------------------------------------------------------------------------- #


def _i18n(lang: str) -> dict:
    return json.loads((WWW / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))


def test_every_translation_key_used_by_the_card_exists():
    """A missing key renders as the raw key name in the UI."""
    source = _card()
    used = set(re.findall(r'_t\(\s*"([a-z0-9_]+)"', source))
    assert used, "no _t() calls found -- has the i18n helper been renamed?"

    remote = _i18n("en")["cfs_card"]
    # The bundled fallback dict is what shows before i18n/en.json loads.
    fallback_block = source.split("const CFS_TRANSLATIONS", 1)[1].split("\n};", 1)[0]
    bundled = set(re.findall(r"^\s{4}([a-z0-9_]+):", fallback_block, re.MULTILINE))

    assert not (used - set(remote)), f"missing from i18n/en.json: {sorted(used - set(remote))}"
    assert not (used - bundled), f"missing from CFS_TRANSLATIONS.en: {sorted(used - bundled)}"


@pytest.mark.parametrize("section", ["cfs_card", "printer_card"])
def test_english_and_spanish_have_the_same_keys(section):
    en, es = _i18n("en"), _i18n("es")
    assert set(en[section]) == set(es[section]), (
        f"{section}: only in en {sorted(set(en[section]) - set(es[section]))}; "
        f"only in es {sorted(set(es[section]) - set(en[section]))}"
    )
