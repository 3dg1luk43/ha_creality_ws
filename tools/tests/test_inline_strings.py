"""User-facing errors and notifications take their text from strings.json (R33).

test_translations.py checks the strings that exist; it cannot see an English
sentence typed straight into a raise or a persistent notification, and several
had been (the CFS service errors, its notifications, the diagnostic notice).
This walks the integration's source for them.
"""

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "custom_components/ha_creality_ws"

# Errors Home Assistant shows to the user.
USER_ERRORS = {
    "HomeAssistantError",
    "ServiceValidationError",
    "ConfigEntryError",
    "ConfigEntryAuthFailed",
}
NOTIFY_CALLS = {"pn_async_create", "async_create"}

# The one deliberate exception: a core too old to resolve translation keys can
# only be told in English (see async_setup_entry).
ALLOWED = {("__init__.py", "async_setup_entry", "ConfigEntryError")}


def _is_text(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return bool(node.value.strip())
    return isinstance(node, ast.JoinedStr)


def _name(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _offences(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []

    def visit(node: ast.AST, function: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function = node.name
        if isinstance(node, ast.Call):
            name = _name(node.func)
            texts = []
            if name in USER_ERRORS:
                texts = [a for a in node.args if _is_text(a)]
            elif name in NOTIFY_CALLS:
                texts = [k.value for k in node.keywords if k.arg in ("title", "message") and _is_text(k.value)]
                texts += [a for a in node.args[1:] if _is_text(a)]
            if texts and (path.name, function, name) not in ALLOWED:
                found.append(f"{path.name}:{node.lineno} {name}() in {function}")
        for child in ast.iter_child_nodes(node):
            visit(child, function)

    visit(tree, "<module>")
    return found


def test_no_inline_text_in_user_errors_or_notifications():
    offences = [o for path in sorted(PACKAGE.glob("*.py")) for o in _offences(path)]
    assert not offences, "inline user-visible text, use a translation key:\n" + "\n".join(offences)


def test_the_check_sees_an_inline_message(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text(
        "def handler(hass):\n"
        "    pn_async_create(hass, title='Done', message=f'{hass} ok')\n"
        "    raise ServiceValidationError('bad input')\n",
        encoding="utf-8",
    )
    assert len(_offences(sample)) == 2


def test_the_check_lets_translation_keys_through(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text(
        "def handler(hass, strings):\n"
        "    pn_async_create(hass, title=_fill(strings, 'k'), message=_fill(strings, 'm'))\n"
        "    raise ServiceValidationError(translation_domain=DOMAIN, translation_key='k')\n",
        encoding="utf-8",
    )
    assert _offences(sample) == []
