"""The issue automation in .github/workflows, ported from ha_washdata.

Actions cannot run locally, so the job scripts are extracted from the workflow
and executed by ``tools/tests/js/issue_validator_harness.mjs`` against a stubbed
GitHub API. The tests run the shipped script text, not a copy, so they fail if
the workflow drifts.

The second half pins the one thing that silently breaks every gate: the issue
forms' field labels and the heading lists the workflows match against are two
copies of the same strings. A renamed field would otherwise stop being read,
and a templated report would be closed as template-less.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
TEMPLATES = ROOT / ".github" / "ISSUE_TEMPLATE"
VALIDATOR = WORKFLOWS / "issue_validator.yml"
HARNESS = ROOT / "tools" / "tests" / "js" / "issue_validator_harness.mjs"

requires_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is required to run the workflow scripts"
)


def _workflow(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _script(job: str, path: Path = VALIDATOR) -> str:
    return _workflow(path)["jobs"][job]["steps"][0]["with"]["script"]


def _run(scenarios: list[dict]) -> dict[str, dict]:
    spec = {
        "restoreScript": _script("restore-template-label"),
        "validateScript": _script("validate-bug-report"),
        "templatelessScript": _script("close-templateless-issues"),
        "scenarios": scenarios,
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(spec, fh)
        path = fh.name
    try:
        proc = subprocess.run(
            ["node", str(HARNESS), path],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    finally:
        Path(path).unlink(missing_ok=True)
    assert proc.returncode == 0, f"harness failed:\n{proc.stderr}"
    return {r["name"]: r for r in json.loads(proc.stdout)}


LOGS = (
    "```shell\n"
    "2026-09-27 10:38:50 WARNING (MainThread) [custom_components.ha_creality_ws.coordinator]\n"
    "  K2Pro-Bosbes: print stopped at 0%\n"
    "```"
)


def _bug_body(
    heading: str = "###",
    *,
    version: str = "0.9.9",
    ha: str = "2026.9.3",
    logs: str = LOGS,
) -> str:
    """A complete bug report, as the form renders it, at the given heading level."""
    h = heading
    return "\n".join(
        [
            f"{h} Before You Submit",
            "",
            "- [x] I searched the existing issues.",
            "",
            f"{h} Describe the Bug",
            "",
            "A self-test at the start of a print is announced as a stop.",
            "",
            f"{h} Printer Model",
            "",
            "K2 Pro",
            "",
            f"{h} Integration Version",
            "",
            version,
            "",
            f"{h} Home Assistant Version",
            "",
            ha,
            "",
            f"{h} Reproduction Steps",
            "",
            "1. Start a print from Creality Print.",
            "",
            f"{h} Expected Behavior",
            "",
            "No notification.",
            "",
            f"{h} Logs / Diagnostics",
            "",
            logs,
            "",
            f"{h} Additional Context",
            "",
            "_No response_",
        ]
    )


def _scenario(name, body, labels=None, releases=None, min_ha="2026.7.0"):
    return {
        "name": name,
        "body": body,
        "labels": labels or [],
        "releases": releases or [{"tag": "v0.9.9"}, {"tag": "v0.9.8"}],
        "minHa": min_ha,
    }


def _validate_one(body, **kw):
    return _run([_scenario("one", body, ["bug"], **kw)])["one"]["validate"]


# --------------------------------------------------------------------------- #
# Behaviour, executed under node
# --------------------------------------------------------------------------- #


@requires_node
def test_api_filed_bug_report_gets_its_template_label_back():
    """GitHub applies a form's labels only on the web path; restore it."""
    res = _run([_scenario("api-bug", _bug_body("##"))])["api-bug"]
    assert res["restore"]["addedLabels"] == ["bug"]


@requires_node
def test_api_filed_bug_report_is_actually_validated():
    """The validator must run for it, and flag only the outdated version."""
    res = _run([_scenario("api-bug", _bug_body("##", version="0.9.8"))])["api-bug"]
    assert len(res["validate"]["comments"]) == 1
    comment = res["validate"]["comments"][0]
    assert "Outdated version" in comment
    assert "`0.9.8`" in comment and "v0.9.9" in comment
    for field in ("Home Assistant Version** is missing", "Logs / Diagnostics** are missing"):
        assert field not in comment, f"{field!r} wrongly reported for a ## body"
    assert res["validate"]["addedLabels"] == ["more info required"]


@requires_node
@pytest.mark.parametrize("heading", ["##", "###", "####"])
def test_fields_are_read_at_any_heading_level(heading):
    validate = _validate_one(_bug_body(heading))
    assert validate["comments"] == []
    assert validate["addedLabels"] == []


@requires_node
@pytest.mark.parametrize("indent", ["", " ", "   "])
def test_an_indented_heading_is_still_a_heading(indent):
    """CommonMark allows up to three spaces before an ATX heading, and GitHub
    renders them as headings. A body pasted from an editor that indents was
    closed as templateless and had its fields reported missing."""
    body = "\n".join(
        f"{indent}{line}" if line.startswith("#") else line
        for line in _bug_body("###").splitlines()
    )
    res = _run([_scenario("x", body)])["x"]
    assert res["templateless"]["closed"] is False
    # The form's label is restored from the headings, and every field is read.
    assert res["restore"]["addedLabels"] == ["bug"]
    assert res["validate"]["comments"] == []


@requires_node
def test_a_free_form_issue_is_still_closed_as_templateless():
    """The policy itself is unchanged: no template label and no template
    heading means the issue is closed with the pointer to the forms."""
    body = "## Description\nthe camera broke\n\n## Logs\nnone"
    res = _run([_scenario("x", body)])["x"]
    assert res["templateless"]["closed"] is True
    assert "without one of the issue templates" in res["templateless"]["comments"][0]


@requires_node
def test_four_spaces_is_code_not_a_heading():
    body = "    ### Describe the Bug\nsomething"
    res = _run([_scenario("x", body)])["x"]
    assert res["templateless"]["closed"] is True


@requires_node
def test_web_form_report_with_blank_fields_is_flagged():
    body = "\n".join(
        [
            "### Describe the Bug", "", "It breaks.", "",
            "### Integration Version", "", "_No response_", "",
            "### Home Assistant Version", "", "_No response_", "",
            "### Logs / Diagnostics", "", "_No response_",
        ]
    )
    validate = _validate_one(body)
    comment = validate["comments"][0]
    for field in ("Integration Version", "Home Assistant Version", "Logs / Diagnostics"):
        assert field in comment
    assert validate["addedLabels"] == ["more info required"]


@requires_node
def test_a_version_with_text_around_it_is_still_read():
    """`v0.9.8 (HACS)` is how people actually fill the field in."""
    assert _validate_one(_bug_body(version="v0.9.9 (HACS)"))["comments"] == []
    comment = _validate_one(_bug_body(version="v0.9.8 (HACS)"))["comments"][0]
    assert "Outdated version" in comment


@requires_node
def test_a_version_that_is_not_a_number_is_flagged():
    comment = _validate_one(_bug_body(version="latest"))["comments"][0]
    assert "is not a version number" in comment


@requires_node
def test_the_newest_release_is_found_by_version_not_by_list_order():
    """The API lists by creation date; a hotfix to an older line can come first."""
    releases = [{"tag": "v0.9.6.2"}, {"tag": "v0.9.9"}, {"tag": "v0.9.8"}]
    assert _validate_one(_bug_body(version="0.9.9"), releases=releases)["comments"] == []


@requires_node
def test_a_newer_prerelease_is_offered_but_an_older_one_is_not():
    newer = [{"tag": "v0.10.0-beta1", "prerelease": True}, {"tag": "v0.9.9"}]
    comment = _validate_one(_bug_body(version="0.9.9"), releases=newer)["comments"][0]
    assert "v0.10.0-beta1" in comment and "pre-release" in comment

    older = [{"tag": "v0.9.9"}, {"tag": "v0.9.9-alpha", "prerelease": True}]
    assert _validate_one(_bug_body(version="0.9.9"), releases=older)["comments"] == []


@requires_node
def test_home_assistant_below_the_minimum_is_flagged():
    """The minimum is read from hacs.json, so it follows the release."""
    comment = _validate_one(_bug_body(ha="2026.6.4"))["comments"][0]
    assert "not supported" in comment and "2026.7.0" in comment
    assert _validate_one(_bug_body(ha="2026.7.0"))["comments"] == []
    assert _validate_one(_bug_body(ha="2026.10.0b1"))["comments"] == []


@requires_node
def test_an_explained_na_inside_the_forms_code_fence_is_accepted():
    """The form wraps this field in ```shell. Matching the N/A rule against the
    fenced text never succeeded, so a short reason was always rejected."""
    body = _bug_body(logs="```shell\nN/A - card text overlaps\n```")
    assert _validate_one(body)["comments"] == []


@requires_node
def test_a_bare_na_is_not_enough():
    body = _bug_body(logs="```shell\nN/A\n```")
    comment = _validate_one(body)["comments"][0]
    assert "Logs / Diagnostics" in comment


@requires_node
def test_the_fence_does_not_count_toward_the_length_gate():
    """44 characters of output: under the 50 gate, but over it with the fence."""
    output = "Error: the printer said something went wrong"
    assert len(output) == 44
    body = _bug_body(logs=f"```shell\n{output}\n```")
    comment = _validate_one(body)["comments"][0]
    assert "Logs / Diagnostics" in comment


@requires_node
def test_an_attachment_counts_as_logs():
    body = _bug_body(
        logs="```shell\n[diag.json](https://github.com/user-attachments/files/1/diag.json)\n```"
    )
    assert _validate_one(body)["comments"] == []


@requires_node
def test_a_legacy_markdown_report_is_not_validated():
    """Reports filed with the pre-forms template (#124, for one) have none of
    these fields. Editing one must not earn it an 'everything is missing'."""
    body = "## Environment\n\n- Home Assistant Core version: 2026.9.3\n\n## Description\n\nx\n"
    validate = _validate_one(body)
    assert validate["comments"] == []
    assert validate["addedLabels"] == []


@requires_node
def test_feature_request_is_labelled_and_not_bug_validated():
    body = "\n".join(
        [
            "## Before You Submit", "", "- [x] yes", "",
            "## Problem or Motivation", "", "I want a thing.", "",
            "## Proposed Solution", "", "Add the thing.", "",
            "## Printers Affected", "", "K2 family", "",
            "## Alternatives Considered", "", "None.",
        ]
    )
    res = _run([_scenario("fr", body)])["fr"]
    assert res["restore"]["addedLabels"] == ["feature request"]
    assert res["validate"]["comments"] == []


@requires_node
def test_free_form_issue_is_never_labelled():
    body = "# Summary\n\nIt is broken.\n\n# Impact\n\nBad.\n"
    res = _run([_scenario("free", body)])["free"]
    assert res["restore"]["addedLabels"] == []
    assert res["validate"]["comments"] == []


@requires_node
def test_a_single_stray_heading_does_not_mislabel():
    body = "# Notes\n\nSee also:\n\n## Proposed Solution\n\nmaybe do X.\n"
    assert _run([_scenario("stray", body)])["stray"]["restore"]["addedLabels"] == []


@requires_node
def test_existing_template_label_is_left_alone():
    res = _run([_scenario("kept", _bug_body(), ["bug"])])["kept"]
    assert res["restore"]["addedLabels"] == []


# --------------------------------------------------------------------------- #
# Wiring the harness cannot see
# --------------------------------------------------------------------------- #


def test_job_triggers_are_scoped_correctly():
    """``restore-template-label`` must stay opened/reopened-only (on ``edited`` it
    would re-add a label a maintainer removed), and ``validate-bug-report`` must
    keep ``always()`` or a skipped ``needs`` would skip it too."""
    data = _workflow(VALIDATOR)
    restore = data["jobs"]["restore-template-label"]["if"]
    assert "'opened'" in restore and "'reopened'" in restore
    assert "'edited'" not in restore

    validate = data["jobs"]["validate-bug-report"]
    assert validate["needs"] == "restore-template-label"
    assert "always()" in validate["if"]
    assert "labels.*.name" not in validate["if"]


def _form_labels(name: str) -> set[str]:
    form = yaml.safe_load((TEMPLATES / name).read_text(encoding="utf-8"))
    return {f["attributes"]["label"] for f in form["body"] if f["type"] != "markdown"}


def _string_list(script: str, const: str) -> list[str]:
    m = re.search(rf"const {const} = \[(.*?)\];", script, re.S)
    assert m, f"{const} not found"
    return re.findall(r"'([^']+)'", m.group(1))


def _distinctive(script: str) -> dict[str, list[str]]:
    m = re.search(r"const DISTINCTIVE = \{(.*?)\n\s*\};", script, re.S)
    assert m, "DISTINCTIVE not found"
    return {
        label: re.findall(r"'([^']+)'", items)
        for label, items in re.findall(r"'([^']+)': \[(.*?)\]", m.group(1), re.S)
    }


FORMS = {
    "bug": "bug_report.yml",
    "feature request": "feature_request.yml",
    "documentation": "documentation.yml",
}


def test_every_form_field_is_a_known_heading():
    """A field missing from the list makes a templated report look template-less."""
    known = set(_string_list(_script("close-templateless-issues"), "KNOWN_HEADINGS"))
    for name in FORMS.values():
        missing = _form_labels(name) - known
        assert not missing, f"{name}: {sorted(missing)} not in KNOWN_HEADINGS"


def test_the_auto_assigner_uses_the_same_heading_list():
    """It skips template-less issues so the closer's issues are not assigned."""
    assign = _workflow(WORKFLOWS / "auto_assign_issues.yml")
    script = assign["jobs"]["assign"]["steps"][0]["with"]["script"]
    validator = _script("close-templateless-issues")
    assert _string_list(script, "KNOWN_HEADINGS") == _string_list(validator, "KNOWN_HEADINGS")
    assert _string_list(script, "TEMPLATE_LABELS") == _string_list(validator, "TEMPLATE_LABELS")


def test_distinctive_headings_belong_to_exactly_their_own_form():
    distinctive = _distinctive(_script("restore-template-label"))
    assert set(distinctive) == set(FORMS)
    for label, headings in distinctive.items():
        own = _form_labels(FORMS[label])
        assert set(headings) <= own, f"{label}: {set(headings) - own} not in its form"
        for other, name in FORMS.items():
            if other != label:
                shared = set(headings) & _form_labels(name)
                assert not shared, f"{label} headings {shared} also appear in {name}"


def test_no_heading_is_a_prefix_of_another_forms_field():
    """Headings match as a prefix followed by whitespace, so `Printer Model`
    would also match a `Printer Model Scope` field in another form."""
    labels = {name: _form_labels(name) for name in FORMS.values()}
    for name, own in labels.items():
        for other, theirs in labels.items():
            if other == name:
                continue
            for a in own:
                for b in theirs:
                    assert not b.startswith(a + " "), f"{name}:{a!r} prefixes {other}:{b!r}"


def test_the_fields_the_validator_reads_exist_in_the_bug_form():
    script = _script("validate-bug-report")
    read = set(re.findall(r"field(?:Line|Block)\('([^']+)'\)", script))
    assert read, "no field reads found"
    assert read <= _form_labels("bug_report.yml"), read - _form_labels("bug_report.yml")


def test_the_form_labels_match_what_the_templates_apply():
    """Every gate keys off these labels."""
    for label, name in FORMS.items():
        form = yaml.safe_load((TEMPLATES / name).read_text(encoding="utf-8"))
        assert form["labels"] == [label], name
