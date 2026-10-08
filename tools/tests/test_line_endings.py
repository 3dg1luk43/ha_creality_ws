"""Line endings stay as they are (R51).

The repository mixes CRLF and LF files and has no .gitattributes. An editor or
a text-mode rewrite that converts a file turns every line of it into a diff
line, and one that converts only what it touched leaves the file mixed. Both
fail here. To convert a file on purpose, do it in a commit of its own and
update CRLF_FILES.
"""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

CRLF_FILES = {
    "custom_components/ha_creality_ws/button.py",
    "custom_components/ha_creality_ws/config_flow.py",
    "custom_components/ha_creality_ws/const.py",
    "custom_components/ha_creality_ws/coordinator.py",
    "custom_components/ha_creality_ws/entity.py",
    "custom_components/ha_creality_ws/ws_client.py",
    "custom_components/ha_creality_ws/www/k_printer_card.js",
}


def _text_files() -> dict[str, bytes]:
    try:
        listed = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True
        ).stdout.split(b"\0")
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        pytest.skip(f"not a git work tree: {exc}")
    files = {}
    for rel in filter(None, listed):
        path = ROOT / rel.decode()
        if not path.is_file():
            continue
        body = path.read_bytes()
        if b"\x00" not in body:  # binary, as git diff decides it
            files[rel.decode()] = body
    return files


def test_no_file_mixes_line_endings():
    mixed = [
        rel for rel, body in _text_files().items()
        if b"\r\n" in body and body.count(b"\n") != body.count(b"\r\n")
    ]
    assert not mixed, f"CRLF and LF lines in one file: {mixed}"


def test_the_crlf_files_are_the_known_ones():
    crlf = {rel for rel, body in _text_files().items() if b"\r\n" in body}
    assert crlf == CRLF_FILES, (
        f"converted to CRLF: {sorted(crlf - CRLF_FILES)}; "
        f"converted to LF: {sorted(CRLF_FILES - crlf)}"
    )
