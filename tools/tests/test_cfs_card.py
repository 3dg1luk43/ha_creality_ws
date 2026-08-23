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


JS_SUITES = sorted(p.name for p in JS_TESTS.glob("test_*.mjs"))


def test_the_javascript_suites_are_discoverable():
    """An empty glob parametrizes to nothing and drops all node coverage silently.

    Same reasoning as the "has this been renamed?" guards further down: a moved or
    renamed directory must fail loudly rather than quietly testing less.
    """
    assert JS_SUITES, f"no test_*.mjs found under {JS_TESTS}"
    # The suites that carry the behavioural coverage, by name.
    for expected in (
        "test_collector.mjs", "test_edit_dialog.mjs", "test_device_scoping.mjs",
        "test_interactions.mjs",
    ):
        assert expected in JS_SUITES, f"{expected} is missing from {JS_SUITES}"


@requires_node
@pytest.mark.parametrize("suite", JS_SUITES)
def test_javascript_suite(suite):
    """Run a node test file and surface its output on failure."""
    result = subprocess.run(
        [NODE, str(JS_TESTS / suite)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        check=False,  # the return code is asserted on below
    )
    assert result.returncode == 0, (
        f"{suite} failed:\n{result.stdout}\n{result.stderr}"
    )


@requires_node
def test_card_parses():
    """A syntax error would break every dashboard using the card."""
    for path in (CARD, PRINTER_CARD):
        result = subprocess.run(
            [NODE, "--check", str(path)],
            capture_output=True,
            text=True,
            check=False,  # the return code is asserted on below
        )
        assert result.returncode == 0, f"{path.name}: {result.stderr}"


# --------------------------------------------------------------------------- #
# Structural guards
# --------------------------------------------------------------------------- #


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
    # 260 kB, not 400: the directory is ~218 kB, and the point is to make a large
    # addition a deliberate decision rather than a side effect.
    assert total < 260_000, (
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


def _keys_used_by_the_card(source: str) -> set[str]:
    """Every translation key the card can ask for at runtime.

    A plain `_t("literal")` scan misses more than it finds: the card also builds
    keys from template literals (`label_material_${s.name}`), passes them through
    variables (`_t(key, ...)`, `_t(keyMap[metric], ...)`) and forwards stored
    error keys (`_t(this._deviceIdError)`). Each of those families is expanded
    from the values the card can actually supply, so a key deleted from the
    translation files is caught rather than being invisible to a regex.
    """
    used = set(re.findall(r'_t\(\s*"([a-z0-9_]+)"', source))

    # `_t(`label_material_${s.name}`)` -- one key per ha-form schema field.
    form_block = source.split("form.schema = [", 1)[1].split("]", 1)[0]
    for field in re.findall(r'name:\s*"([a-z0-9_]+)"', form_block):
        used.add(f"label_material_{field}")
    # The colour row is hand-built rather than part of the schema.
    used.add("label_material_color")

    # `_t(key, ...)` / `_t(keyMap[metric], ...)` -- keys held in lookup tables.
    for table in re.findall(r"keyMap\s*=\s*\{(.*?)\}", source, re.S):
        used.update(re.findall(r'"([a-z0-9_]+)"', table))
    for label_map in re.findall(r'key:\s*"([a-z0-9_]+)"', source):
        used.add(label_map)

    # `_t(this._deviceIdError)` -- the error keys that variable can hold.
    used.update(re.findall(r'_deviceIdError\s*=\s*[^;]*?"(toast_[a-z0-9_]+)"', source))
    used.update(re.findall(r'\?\s*"(toast_[a-z0-9_]+)"\s*:\s*"(toast_[a-z0-9_]+)"', source)[0]
                if re.search(r'\?\s*"toast_[a-z0-9_]+"\s*:\s*"toast_[a-z0-9_]+"', source) else [])
    return used


def test_every_translation_key_used_by_the_card_exists():
    """A missing key renders as the raw key name in the UI."""
    source = _card()
    used = _keys_used_by_the_card(source)
    assert used, "no _t() calls found -- has the i18n helper been renamed?"
    # The dynamic families must actually have been picked up.
    for expected in ("label_material_type", "label_material_color", "toast_no_device"):
        assert expected in used, f"{expected} should be discovered as a used key"

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

    # Scoped to derive_print_state, which the comment always claimed but the
    # regex did not do -- a matching string anywhere in utils.py satisfied it.
    utils_src = (
        ROOT / "custom_components" / "ha_creality_ws" / "utils.py"
    ).read_text(encoding="utf-8")
    body = utils_src.split("def derive_print_state(", 1)[1]
    body = re.split(r"\ndef ", body, maxsplit=1)[0]
    produced = set(re.findall(r'return "([a-z-]+)"', body))
    assert produced.issuperset(BUSY_PRINT_STATES), (
        f"never produced by derive_print_state: {sorted(BUSY_PRINT_STATES - produced)}"
    )
    # The card resolves the status entity by translation_key, so that is the
    # coupling worth asserting -- not the bare string.
    assert 'translation_key="print_status"' in sensor or '"print_status"' in sensor


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
    # _renderEditForm is followed by _renderPresets, not _saveMaterial: ending
    # the slice at _saveMaterial would count both methods.
    form = source.split("_renderEditForm(slot, close) {", 1)[1].split("\n  _renderPresets(", 1)[0]
    inline = re.findall(r"\.style\.[a-zA-Z]+\s*=", form)
    # Zero, not "fewer than ten": the dialog is styled entirely by the injected
    # stylesheet now, so any inline assignment is the regression starting again.
    assert not inline, f"{len(inline)} inline style assignments in _renderEditForm: {inline}"


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
    """A div with a click handler is invisible to a screen reader.

    Sliced on a signature-shape-insensitive marker, and asserting the pieces that
    matter rather than the surrounding text: the escaping on the label, and the
    aria-disabled that keeps the blocked state announced *and* clickable.
    """
    source = _card()
    marker = re.search(r"_renderEditButton\s*\([^)]*\)\s*\{", source)
    assert marker, "_renderEditButton has been renamed"
    edit_button = source[marker.end():].split("\n  _renderSpoolCard", 1)[0]

    assert '<button type="button"' in edit_button
    assert "aria-label=" in edit_button
    assert "esc(title)" in edit_button, "the label is device-supplied, so escape it"
    assert 'aria-disabled="true"' in edit_button, (
        "a native disabled attribute swallows the click that explains the block"
    )


def _service_number_bounds(field: str) -> dict[str, float]:
    """Pull one numeric field's min/max out of services.yaml.

    Scanned rather than parsed with PyYAML: CI installs only pytest, and this
    check is most valuable exactly there, so it must not need a dependency.
    """
    text = (ROOT / "custom_components" / "ha_creality_ws" / "services.yaml").read_text(
        encoding="utf-8"
    )
    block = re.search(
        rf"^    {field}:\n(.*?)(?=^    \w|^\w|\Z)", text, re.DOTALL | re.MULTILINE
    )
    assert block, f"{field} not found in services.yaml"
    bounds = {
        key: float(value)
        for key, value in re.findall(r"^\s+(min|max):\s*([0-9.]+)", block.group(1), re.MULTILINE)
    }
    assert {"min", "max"} <= set(bounds), f"{field} has no min/max in services.yaml"
    return bounds


def test_dialog_field_bounds_match_the_service():
    """The card, services.yaml and the service schema must agree on limits.

    Otherwise the dialog happily accepts a value the service then rejects.
    """
    card_form = _card().split("form.schema = [", 1)[1].split("];", 1)[0]

    for field in ("min_temp", "max_temp", "pressure"):
        expected = _service_number_bounds(field)
        entry = re.search(rf'name: "{field}".*?\}}\s*\}},', card_form, re.DOTALL)
        assert entry, f"{field} missing from the dialog schema"
        for bound in ("min", "max"):
            found = re.search(rf"\b{bound}:\s*([0-9.]+)", entry.group(0))
            assert found, f"{field}.{bound} not set in the card"
            assert float(found.group(1)) == expected[bound], (
                f"{field}.{bound}: card {found.group(1)} vs services.yaml {expected[bound]}"
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


def test_bundled_images_are_attributed():
    """Third-party artwork must be carved out of the licence, not shipped silently.

    The repository is AGPL-3.0, which purports to cover the whole work. Any image
    we do not own needs naming in NOTICE so redistributors know the grant does
    not extend to it.
    """
    notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
    images = [
        p.name for p in WWW.iterdir()
        if p.is_file() and p.suffix in {".webp", ".png", ".jpg", ".svg"}
    ]
    assert images, "no bundled images found -- has www/ moved?"
    for name in images:
        assert name in notice, f"{name} is bundled but not mentioned in NOTICE"


def test_readme_licence_matches_the_licence_file():
    """The README claimed MIT while LICENSE was AGPL-3.0."""
    licence = (ROOT / "LICENSE").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("## License", 1)[1]
    # Asserted unconditionally: wrapping these in `if AGPL in licence` meant
    # relicensing made the test pass having checked nothing.
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in licence.upper(), (
        "LICENSE is no longer AGPL; update this test and the README together"
    )
    assert "Affero" in section, "LICENSE is AGPL but the README says otherwise"
    assert "MIT" not in section, "the README still claims MIT"
