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


def _strip_comments(text: str) -> str:
    """Drop // and /* */ comments so "does the code do X" guards mean it.

    Crude but adequate here: it can also blank a // inside a string literal,
    which only ever makes these checks more permissive, never less.
    """
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"(?m)//.*$", "", text)


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
        code = _strip_comments(path.read_text(encoding="utf-8"))
        assert "console.log(" not in code, f"{path.name} logs to the console"


def test_cards_do_not_use_alert():
    """alert() blocks the whole UI; confirm() is an accepted pattern here."""
    for path in (CARD, PRINTER_CARD):
        code = _strip_comments(path.read_text(encoding="utf-8"))
        assert not re.search(r"(?<![.\w])alert\s*\(", code), f"{path.name} uses alert()"


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


# --------------------------------------------------------------------------- #
# Cross-language contracts
# --------------------------------------------------------------------------- #


def test_card_busy_states_match_the_integration():
    """The card and the service must agree on what "busy" means.

    The card greys out the edit button; the service refuses the write. If they
    disagree, a user either sees an editable slot the service then rejects, or a
    locked one they are actually allowed to change.
    """
    from custom_components.ha_creality_ws.utils import BUSY_PRINT_STATES

    block = _card().split("const BUSY_PRINT_STATES = new Set([", 1)[1].split("]);", 1)[0]
    in_card = set(re.findall(r'"([a-z-]+)"', block))
    assert in_card == set(BUSY_PRINT_STATES), (
        f"card has {sorted(in_card)}, integration has {sorted(BUSY_PRINT_STATES)}"
    )


def test_card_busy_states_are_real_sensor_states():
    """Guards against using display strings instead of raw states."""
    sensor = (
        ROOT / "custom_components" / "ha_creality_ws" / "sensor.py"
    ).read_text(encoding="utf-8")
    from custom_components.ha_creality_ws.utils import BUSY_PRINT_STATES

    # derive_print_state is the only producer of these values.
    utils_src = (
        ROOT / "custom_components" / "ha_creality_ws" / "utils.py"
    ).read_text(encoding="utf-8")
    produced = set(re.findall(r'return "([a-z-]+)"', utils_src))
    assert BUSY_PRINT_STATES <= produced, (
        f"never produced by derive_print_state: {sorted(BUSY_PRINT_STATES - produced)}"
    )
    assert "print_status" in sensor


def test_device_lookup_is_scoped_and_fails_closed():
    """PR #75's fallback wrote to whichever Creality device came first."""
    source = _strip_comments(_card())
    assert "config/entity_registry/list" not in source, (
        "listing the whole registry is both slower and admin-only"
    )
    assert "this._hass?.entities" in source or "this._hass.entities" in source
    # The mixed-printer branch must exist.
    assert "toast_multiple_devices" in source
    assert "devices.size !== 1" in source


def test_busy_lookup_is_device_scoped():
    """PR #75 scanned every entity for '_print_status'."""
    source = _card()
    assert 'translation_key === "print_status"' in source
    assert "entry.device_id === deviceId" in source


def test_edit_dialog_is_styled_by_classes_not_inline_styles():
    """PR #75 set ~90 inline styles because its dialog could not see the CSS.

    It appended to document.body, outside the shadow root, so the injected
    <style> never reached it. Rendering inside the shadow root is what makes the
    stylesheet apply -- and this counts the inline assignments that would come
    back if that regressed.
    """
    source = _strip_comments(_card())
    form = source.split("_renderEditForm(slot, close) {", 1)[1].split("\n  async _saveMaterial", 1)[0]
    inline = len(re.findall(r"\.style\.[a-zA-Z]+\s*=", form))
    assert inline < 10, f"{inline} inline style assignments in _renderEditForm"


def test_dialog_attaches_to_the_shadow_root():
    source = _strip_comments(_card())
    assert "document.body.appendChild" not in source
    assert "this._root.appendChild(overlay)" in source


def test_edit_button_is_reachable_without_hover():
    """A wall tablet is HA's primary surface and cannot hover.

    PR #75 left the buttons at opacity 0 outside :hover, making the whole
    feature unreachable there.
    """
    source = _card()
    assert "@media (hover: hover)" in source, (
        "the hover-reveal must be gated so touch devices keep the button visible"
    )
    assert ":focus-visible" in source, "keyboard users need a visible affordance"


def test_edit_button_is_a_real_button_with_a_label():
    source = _card()
    edit_button = source.split("_renderEditButton(slot, mini = false) {", 1)[1].split("\n  _renderSpoolCard", 1)[0]
    assert '<button type="button"' in edit_button
    assert "aria-label=" in edit_button


def test_dialog_field_bounds_match_the_service():
    """The card, services.yaml and the service schema must agree on limits.

    Otherwise the dialog happily accepts a value the service then rejects.
    """
    import yaml

    card_form = _card().split("form.schema = [", 1)[1].split("];", 1)[0]
    services = yaml.safe_load(
        (ROOT / "custom_components" / "ha_creality_ws" / "services.yaml").read_text()
    )["set_cfs_material"]["fields"]

    for field in ("min_temp", "max_temp", "pressure"):
        selector = services[field]["selector"]["number"]
        entry = re.search(rf'name: "{field}".*?\}}\s*\}},', card_form, re.DOTALL)
        assert entry, f"{field} missing from the dialog schema"
        text = entry.group(0)
        for bound in ("min", "max"):
            expected = selector[bound]
            found = re.search(rf"\b{bound}:\s*([0-9.]+)", text)
            assert found, f"{field}.{bound} not set in the card"
            assert float(found.group(1)) == float(expected), (
                f"{field}.{bound}: card {found.group(1)} vs services.yaml {expected}"
            )


# --------------------------------------------------------------------------- #
# Static asset serving
# --------------------------------------------------------------------------- #


def test_card_assets_are_registered_explicitly():
    """PR #75 served the whole www/ directory and commented out the per-card
    registrations, changing how the cards themselves are served as a side effect
    of adding an image -- and exposing the stray .code-workspace file with it."""
    frontend = (
        ROOT / "custom_components" / "ha_creality_ws" / "frontend.py"
    ).read_text(encoding="utf-8")

    assert "ASSETS = [" in frontend, "assets must be listed explicitly"
    # The per-card registration must still happen.
    assert "_register_static_path(self.hass, integration_url, serve_path)" in frontend

    listed = set(re.findall(r'"([^"]+\.(?:webp|png|jpg|svg))"', frontend))
    on_disk = {
        p.name for p in WWW.iterdir()
        if p.is_file() and p.suffix in {".webp", ".png", ".jpg", ".svg"}
    }
    assert on_disk <= listed, f"unregistered assets: {sorted(on_disk - listed)}"


def test_the_www_directory_is_not_served_wholesale():
    """Registering the directory would publish every file in it.

    www/ contains ha_creality_ws.code-workspace, a dev artefact. Serving each
    known file keeps that out and, more importantly, keeps the cards' own
    serving mechanism independent of whatever assets get added later.
    """
    frontend = _strip_comments(
        (ROOT / "custom_components" / "ha_creality_ws" / "frontend.py").read_text(
            encoding="utf-8"
        )
    )
    # The i18n subdirectory is registered as a directory on purpose; the www root
    # must not be.
    directory_registrations = re.findall(
        r"_register_static_path\(\s*[^,]+,\s*([^,]+),\s*str\(([^)]+)\)", frontend
    )
    for _url, path_expr in directory_registrations:
        assert path_expr.strip() not in {"www_dir_path", "www_path.parent"}, (
            f"www/ is registered as a directory via {path_expr}"
        )
    assert 'INTEGRATION_URL_BASE.rstrip("/")' not in frontend, (
        "registering the base URL as a directory serves all of www/"
    )


def test_asset_referenced_by_the_card_exists():
    """A typo here is a broken image in every box-view dashboard."""
    for name in re.findall(r'ASSET_URL_BASE\}([\w.\-]+)"', _card()):
        assert (WWW / name).exists(), f"card references missing asset: {name}"
