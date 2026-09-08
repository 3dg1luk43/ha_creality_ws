"""Translation hygiene.

Two rules this repo now holds itself to, both mechanical enough to enforce:

* every user-visible string lives in the translation files, never inline in
  Python;
* every locale carries every key, with matching placeholders.

The second one matters because a missing key renders as the raw key in the UI,
and a renamed placeholder makes the string silently resolve to empty text at
runtime -- neither shows up as an error anywhere.
"""

import ast
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "custom_components" / "ha_creality_ws"
LOCALES = COMPONENT / "translations"

PLACEHOLDER = re.compile(r"\{[a-z_]+\}")
# Below this length a fragment is too generic to attribute to a translation.
MIN_PROSE = 8

# Literals that legitimately overlap notification prose. Kept explicit rather
# than loosening the check, so each exemption has to be argued for.
#
# DEFAULT_NAME is the config *entry* title Home Assistant substitutes into the
# config flow, and a brand string rather than translatable prose. It happens to
# share a prefix with the notification title fallback; they are not the same
# string and neither should be derived from the other.
ALLOWED_OVERLAPS = frozenset({"Creality Printer (WS)"})

# Notification strings live under "common" because hassfest validates
# strings.json against a fixed list of top-level keys and rejects any other,
# and `common` is the only one shaped as a flat slug -> string mapping.
NOTIFICATION_SECTION = "common"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _flatten(obj, prefix="") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in obj.items():
        if isinstance(value, dict):
            out.update(_flatten(value, f"{prefix}{key}."))
        else:
            out[f"{prefix}{key}"] = value
    return out


def _locale_files() -> list[Path]:
    return sorted(LOCALES.glob("*.json"))


def _python_sources() -> list[Path]:
    return sorted(COMPONENT.glob("*.py"))


def _string_literals(path: Path) -> list[str]:
    """Every string literal in a module, excluding docstrings.

    Docstrings and comments are prose *about* the code, not shown to anyone, so
    they are allowed to quote a message. f-string fragments are included, which
    is the point: `f"Print '{job}' done"` is a hardcoded string.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                docstrings.add(doc)
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value not in docstrings
    ]


# --------------------------------------------------------------------------- #
# Locale completeness
# --------------------------------------------------------------------------- #


def test_there_is_more_than_one_locale():
    """Guards the tests below from passing by having nothing to compare."""
    assert len(_locale_files()) >= 2


@pytest.mark.parametrize("locale", _locale_files(), ids=lambda p: p.stem)
def test_every_locale_has_every_key(locale):
    reference = _flatten(_load(LOCALES / "en.json"))
    translated = _flatten(_load(locale))
    missing = sorted(set(reference) - set(translated))
    unexpected = sorted(set(translated) - set(reference))
    assert not missing, f"{locale.name} is missing {missing}"
    assert not unexpected, f"{locale.name} has keys en.json does not: {unexpected}"


@pytest.mark.parametrize("locale", _locale_files(), ids=lambda p: p.stem)
def test_placeholders_survive_translation(locale):
    """A renamed or dropped placeholder makes the string resolve to empty at
    runtime, with nothing but a warning in the log."""
    reference = _flatten(_load(LOCALES / "en.json"))
    translated = _flatten(_load(locale))
    mismatched = {
        key: (value, translated[key])
        for key, value in reference.items()
        if key in translated
        and sorted(PLACEHOLDER.findall(value))
        != sorted(PLACEHOLDER.findall(translated[key]))
    }
    assert not mismatched, f"{locale.name}: {mismatched}"


@pytest.mark.parametrize("locale", _locale_files(), ids=lambda p: p.stem)
def test_no_unresolved_help_placeholders(locale):
    """`{camera_help}`-style placeholders were fed from hardcoded English in
    config_flow. The prose now lives here, so a leftover placeholder would
    render literally."""
    leftovers = {
        key: value
        for key, value in _flatten(_load(locale)).items()
        if value.strip().startswith("{") and value.strip().endswith("_help}")
    }
    assert not leftovers, f"{locale.name}: {leftovers}"


def test_strings_json_matches_en():
    """`strings.json` is what Home Assistant validates against; en.json is what
    it serves. They drifting apart is invisible until a key is missing."""
    assert _flatten(_load(COMPONENT / "strings.json")) == _flatten(
        _load(LOCALES / "en.json")
    )


# --------------------------------------------------------------------------- #
# No inline user-visible text
# --------------------------------------------------------------------------- #


def test_every_notification_string_is_referenced():
    """A key nobody reads is dead weight that translators still translate."""
    keys = set(_load(COMPONENT / "strings.json")[NOTIFICATION_SECTION])
    source = "\n".join(p.read_text(encoding="utf-8") for p in _python_sources())
    unused = sorted(k for k in keys if f'"{k}"' not in source)
    assert not unused, f"notification strings nothing reads: {unused}"


def test_no_notification_text_is_hardcoded():
    """The rule, enforced: notification prose must come from strings.json.

    Checks the shipped English against every string literal in the component --
    including f-string fragments, which is where `f"Print '{job}' done"` hides.
    """
    values = _load(COMPONENT / "strings.json")[NOTIFICATION_SECTION].values()
    prose = {
        segment
        for value in values
        for segment in PLACEHOLDER.split(value)
        if len(segment.strip()) >= MIN_PROSE
    }
    assert prose, "no prose long enough to check -- the guard would be vacuous"

    offenders: list[str] = []
    for path in _python_sources():
        for literal in _string_literals(path):
            if literal in ALLOWED_OVERLAPS:
                continue
            for segment in prose:
                if segment in literal:
                    offenders.append(f"{path.name}: {literal!r} contains {segment!r}")
    assert not offenders, "hardcoded notification text:\n" + "\n".join(offenders)


def test_the_overlap_exemptions_still_exist():
    """An exemption for a literal nobody writes any more is just noise."""
    source = "\n".join(p.read_text(encoding="utf-8") for p in _python_sources())
    stale = sorted(literal for literal in ALLOWED_OVERLAPS if literal not in source)
    assert not stale, f"exemptions for literals that are gone: {stale}"


def test_no_illegal_top_level_translation_keys():
    """hassfest validates strings.json against a fixed list of top-level keys
    with a plain vol.Schema, which rejects anything else -- so a custom section
    would fail CI rather than the tests."""
    allowed = {
        "title", "config", "config_subentries", "options", "preview_features",
        "selector", "device_automation", "system_health", "config_panel",
        "application_credentials", "issues", "entity_component", "device",
        "entity", "exceptions", "services", "conditions", "triggers",
        "conversation", "common",
    }
    for path in [COMPONENT / "strings.json", *_locale_files()]:
        illegal = sorted(set(_load(path)) - allowed)
        assert not illegal, f"{path.name}: {illegal}"


def test_no_placeholder_is_wrapped_in_single_quotes():
    """hassfest rejects these outright (string_no_single_quoted_placeholders)."""
    quoted = re.compile(r"'\{\w+\}'")
    for path in [COMPONENT / "strings.json", *_locale_files()]:
        offenders = {
            key: value
            for key, value in _flatten(_load(path)).items()
            if quoted.search(value)
        }
        assert not offenders, f"{path.name}: {offenders}"


def test_no_translation_has_stray_whitespace():
    """Also a hassfest rule, and it silently misaligns joined body segments."""
    for path in [COMPONENT / "strings.json", *_locale_files()]:
        offenders = [
            key
            for key, value in _flatten(_load(path)).items()
            if value != value.strip()
        ]
        assert not offenders, f"{path.name}: {offenders}"


def test_selector_options_cover_every_camera_mode():
    """A `translation_key` selector renders the raw value when a key is missing,
    so a renamed CAM_MODE_* constant would silently show `webrtc_direct` in the
    dropdown instead of a label."""
    const_src = (COMPONENT / "const.py").read_text(encoding="utf-8")
    modes = set(re.findall(r'^CAM_MODE_\w+ = "([a-z_]+)"', const_src, re.M))
    assert modes, "no camera modes found in const.py"

    for path in [COMPONENT / "strings.json", *_locale_files()]:
        options = set(_load(path)["selector"]["camera_mode"]["options"])
        assert options == modes, f"{path.name}: {options ^ modes}"


def test_the_camera_mode_labels_are_not_hardcoded():
    """They lived inline in config_flow.py until the selector section existed."""
    labels = set(
        _load(COMPONENT / "strings.json")["selector"]["camera_mode"]["options"].values()
    )
    for path in _python_sources():
        for literal in _string_literals(path):
            assert literal not in labels, f"{path.name} hardcodes {literal!r}"


def test_notification_keys_are_valid_slugs():
    """hassfest validates the `common` section as {cv.slug: value}, so a key
    with a capital, a dash or a double underscore fails CI."""
    slug = re.compile(r"^(?!.*__)[a-z][a-z0-9_]*(?<!_)$")
    for path in [COMPONENT / "strings.json", *_locale_files()]:
        offenders = sorted(
            key for key in _load(path)[NOTIFICATION_SECTION] if not slug.match(key)
        )
        assert not offenders, f"{path.name}: {offenders}"
