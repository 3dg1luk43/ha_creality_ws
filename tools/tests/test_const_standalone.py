"""`const.py` must be importable without Home Assistant.

Several places depend on this: `notification_rules.py` keeps its option-coercion
helpers out of `const.py` for exactly this reason, and the tooling in `tools/`
reads constants without a Home Assistant install. The guard used to be implicit
-- a module-level `exec_module` in test_manifest_and_specs.py that would simply
fail collection -- which made it easy to mistake for setup code.
"""

import ast
import importlib.util
from pathlib import Path

CONST_PATH = (
    Path(__file__).resolve().parents[2] / "custom_components" / "ha_creality_ws" / "const.py"
)


def test_const_imports_with_no_home_assistant():
    """Executed in isolation, so a new `from homeassistant...` import fails here."""
    spec = importlib.util.spec_from_file_location("ha_creality_ws_const_isolated", CONST_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.DOMAIN == "ha_creality_ws"


def test_const_declares_no_imports_at_all():
    """Stronger than the above: it must not import *anything*.

    A stdlib import would pass the execution test while still making `const.py`
    something other than a flat table of values.
    """
    tree = ast.parse(CONST_PATH.read_text(encoding="utf-8"))
    imports = [
        ast.dump(node)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    assert not imports, f"const.py should have no imports, found {len(imports)}"
