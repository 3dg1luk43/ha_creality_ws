"""The printer card's visual editor: contracts a node test cannot see.

The behaviour lives in ``tools/tests/js/test_printer_editor.mjs`` and
``test_printer_telemetry.mjs``. What is left here is the cross-language half --
the device picker matches entities by ``translation_key``, so a key renamed in
``sensor.py`` breaks the picker with nothing in the JavaScript to notice -- plus
a source guard for the specific shape of the bug that prompted the rewrite.
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "custom_components" / "ha_creality_ws"
WWW = COMPONENT / "www"
PRINTER_CARD = WWW / "k_printer_card.js"
HARNESS = Path(__file__).parent / "js" / "printer_card_harness.mjs"


def _card() -> str:
    return PRINTER_CARD.read_text(encoding="utf-8")


def _strip_comments(text: str) -> str:
    """Drop // and /* */ comments so "does the code do X" guards mean it."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"(?m)//.*$", "", text)


def _device_roles() -> dict[str, tuple[str, str]]:
    """The card's role -> (translation_key, domain) map, read from the source."""
    block = _card().split("const DEVICE_ROLE_ENTITIES = {", 1)[1].split("\n};", 1)[0]
    roles = {
        name: (key, domain)
        for name, key, domain in re.findall(
            r'(\w+):\s*\{\s*translationKey:\s*"([\w]+)",\s*domain:\s*"(\w+)"\s*\}', block
        )
    }
    assert roles, "DEVICE_ROLE_ENTITIES did not parse -- has its shape changed?"
    return roles


# --------------------------------------------------------------------------- #
# The device picker's contract with the integration
# --------------------------------------------------------------------------- #


def test_every_prefilled_role_names_a_real_entity():
    """A renamed translation_key silently stops the picker finding that entity.

    Nothing throws and nothing logs: the role is simply absent from the match,
    so the field is left blank and the user is told "Filled 12 of 13" with no
    hint as to which one went missing.
    """
    entity_strings = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))["entity"]
    for role, (key, domain) in _device_roles().items():
        assert domain in entity_strings, f"{role}: no {domain} entities are declared"
        assert key in entity_strings[domain], (
            f"{role}: no {domain} entity declares translation_key {key!r}"
        )


def test_every_prefilled_role_is_actually_registered_in_python():
    """strings.json can outlive the entity it described; the source cannot."""
    sources = "\n".join(
        path.read_text(encoding="utf-8") for path in COMPONENT.glob("*.py")
    )
    # Both spellings the integration uses: `_attr_translation_key = "x"` on a
    # class, and `"translation_key": "x"` inside a sensor spec dict.
    declared = set(re.findall(r'translation_key"?\s*[=:]\s*"([\w]+)"', sources))
    for role, (key, _domain) in _device_roles().items():
        assert key in declared, f"{role}: nothing in the integration sets translation_key {key!r}"


def test_the_power_switch_is_not_prefilled():
    """The integration creates no power switch, so there is nothing to guess.

    Filling it from the device would have to pick an arbitrary switch, and
    getting that wrong turns the printer off mid-print.
    """
    assert "power" not in _device_roles()
    assert not list(COMPONENT.glob("switch.py")), (
        "the integration now has switches; revisit whether power can be prefilled"
    )


def test_the_test_fixture_matches_the_card():
    """The node suite builds its registry from its own copy of the role list."""
    harness = HARNESS.read_text(encoding="utf-8")
    block = harness.split("export const PRINTER_ENTITIES = [", 1)[1].split("\n];", 1)[0]
    fixture = {
        role: key for role, _pattern, key in
        re.findall(r'\["(\w+)",\s*"([^"]+)",\s*"(\w+)"\]', block)
    }
    card = {role: key for role, (key, _domain) in _device_roles().items()}
    assert fixture == card, "the harness fixture has drifted from DEVICE_ROLE_ENTITIES"


# --------------------------------------------------------------------------- #
# Source guards
# --------------------------------------------------------------------------- #


def test_label_overrides_are_not_installed_behind_a_feature_check():
    """ha-form leaves computeLabel undefined until someone assigns it.

    The editor used to wrap the assignment in ``if (form.computeLabel)``, which
    is therefore never true, so the override never took and every field in the
    editor rendered as its raw config key -- ``time_left``, ``custom_btn``. The
    code that computed the right labels was all present and correct, which is
    why it survived review.
    """
    for path in (PRINTER_CARD, WWW / "k_cfs_card.js"):
        source = _strip_comments(path.read_text(encoding="utf-8"))
        offenders = re.findall(r"if\s*\([^)]*\.compute(?:Label|Helper)\b[^)]*\)", source)
        assert not offenders, f"{path.name} guards a compute* assignment: {offenders}"


def test_the_editor_renders_no_untranslated_prose():
    """Every user-visible string in the editor goes through _t()."""
    editor = _card().split("class KPrinterCardEditor", 1)[1]
    template = editor.split("this._root.innerHTML = `", 1)[1].split("`;", 1)[0]
    assert "<ha-form" in template, "the editor shell did not parse"
    # Every text node between two tags. The shell is built empty and filled in
    # by _refresh, so all of them should be whitespace or an interpolation.
    stray = [
        text.strip()
        for text in re.findall(r">([^<>]*)<", template)
        if text.strip() and not text.strip().startswith("${")
    ]
    assert not stray, f"untranslated text in the editor template: {stray}"


def test_the_bundled_fallback_matches_i18n_en():
    """CARD_TRANSLATIONS.en is what renders until i18n/en.json loads.

    Mirrors the same guard on the CFS card: if the fetch fails outright it is
    what the user reads for good, so the two drifting apart means the editor
    can label a field one way or the other depending on how far the load got.
    """
    block = _card().split("const CARD_TRANSLATIONS = {", 1)[1].split("\n};", 1)[0]
    en_block = block.split("en: {", 1)[1].split("\n  },", 1)[0]
    bundled = dict(
        (key, json.loads(f'"{value}"'))
        for key, value in re.findall(
            r'^\s{4}([a-z0-9_]+):\s*"((?:[^"\\]|\\.)*)"', en_block, re.MULTILINE
        )
    )
    assert bundled, "the en fallback block did not parse -- has its shape changed?"

    remote = json.loads((WWW / "i18n" / "en.json").read_text(encoding="utf-8"))["printer_card"]
    assert set(bundled) == set(remote), (
        f"only bundled {sorted(set(bundled) - set(remote))}; "
        f"only in i18n {sorted(set(remote) - set(bundled))}"
    )
    differing = {k: (v, remote[k]) for k, v in bundled.items() if remote[k] != v}
    assert not differing, f"fallback text differs from i18n/en.json: {differing}"


@pytest.mark.parametrize("field", ["status_icon", "progress_ring", "status_bg",
                                   "telemetry_icon", "telemetry_text"])
def test_every_automatic_colour_explains_itself(field):
    """"auto" meant two different things and the editor said neither.

    For the status icon and ring it means "follow the print state"; for the
    backgrounds and telemetry it means "inherit the Home Assistant theme". A
    user who picked a colour also had no way back short of editing YAML.
    """
    remote = json.loads((WWW / "i18n" / "en.json").read_text(encoding="utf-8"))["printer_card"]
    helper = remote.get(f"helper_auto_{field}")
    assert helper, f"{field} defaults to auto with nothing explaining what that does"
    assert helper.startswith("Automatic:"), helper
