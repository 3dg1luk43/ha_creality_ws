"""tools/release_check.sh: the version step must fail when it cannot run."""

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _version_section(tmp_path: Path, manifest: str) -> str:
    (tmp_path / "tools").mkdir()
    shutil.copy(ROOT / "tools" / "release_check.sh", tmp_path / "tools" / "release_check.sh")
    component = tmp_path / "custom_components" / "ha_creality_ws"
    component.mkdir(parents=True)
    (component / "manifest.json").write_text(manifest, encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## 0.9.9 - Unreleased\n\n- x\n", encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(tmp_path / "tools" / "release_check.sh")],
        cwd=tmp_path, capture_output=True, text=True, timeout=300, check=False,
    )
    assert proc.returncode != 0
    return proc.stdout.split("Shipped files")[0]


def test_a_version_check_that_crashes_is_a_failure(tmp_path):
    """A manifest without `version` crashed the helper with no PROBLEM line,
    nothing was counted, and the step said nothing at all."""
    section = _version_section(tmp_path, '{"domain": "ha_creality_ws"}')
    assert "FAIL" in section and "version check could not run" in section


def test_a_version_mismatch_is_still_named(tmp_path):
    section = _version_section(tmp_path, '{"domain": "ha_creality_ws", "version": "0.9.8"}')
    assert "manifest.json says 0.9.8 but the top CHANGELOG entry is 0.9.9" in section
    assert "could not run" not in section
