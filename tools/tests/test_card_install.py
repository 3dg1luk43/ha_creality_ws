"""Getting a new card into a browser that already has the old one.

The cards are served straight out of the integration package with cache
headers on, so the only thing that makes a browser re-fetch one is the ``?v=``
on its Lovelace resource URL. That token therefore has exactly one job: change
when the card changes, and not otherwise.
"""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "custom_components" / "ha_creality_ws"


@pytest.fixture(scope="module")
def frontend():
    """Load frontend.py on its own.

    By file path rather than as ``custom_components.ha_creality_ws.frontend``:
    that form runs the package __init__, which pulls in the websocket client
    and everything under it. conftest already stands in for the Home Assistant
    imports frontend.py makes, and nothing is registered in sys.modules here so
    the rest of the suite is unaffected.
    """
    spec = importlib.util.spec_from_file_location(
        "_ha_creality_frontend_under_test", COMPONENT / "frontend.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_version_is_stable_for_an_unchanged_card(frontend):
    """It used to be time.time(), so every restart invalidated every cache.

    That also rewrote the Lovelace resource entry on each start, whether or not
    anything had changed.
    """
    first = frontend.card_version(frontend.PRINTER_CARD_NAME)
    second = frontend.card_version(frontend.PRINTER_CARD_NAME)
    assert first == second
    assert first != "missing"


def test_each_card_gets_its_own_version(frontend):
    """A shared token makes editing one card re-download both."""
    printer = frontend.card_version(frontend.PRINTER_CARD_NAME)
    cfs = frontend.card_version(frontend.CFS_CARD_NAME)
    assert printer != cfs


def test_the_version_changes_when_the_card_does(frontend, tmp_path, monkeypatch):
    """The property the whole update path rests on."""
    original = (COMPONENT / "www" / frontend.PRINTER_CARD_NAME).read_bytes()
    before = frontend.card_version(frontend.PRINTER_CARD_NAME)
    target = COMPONENT / "www" / frontend.PRINTER_CARD_NAME
    try:
        target.write_bytes(original + b"\n// one more byte\n")
        assert frontend.card_version(frontend.PRINTER_CARD_NAME) != before
    finally:
        target.write_bytes(original)
    assert frontend.card_version(frontend.PRINTER_CARD_NAME) == before


def test_the_version_changes_when_the_release_does(frontend, monkeypatch):
    """HACS restores files from an archive and can preserve their timestamps.

    A release that only touches Python would otherwise leave every browser on
    the card it already had, which is exactly the upgrade case that matters.
    """
    manifest = COMPONENT / "manifest.json"
    original = manifest.read_text(encoding="utf-8")
    before = frontend.card_version(frontend.PRINTER_CARD_NAME)
    try:
        bumped = json.loads(original)
        bumped["version"] = "9.9.9-test"
        manifest.write_text(json.dumps(bumped, indent=2), encoding="utf-8")
        assert frontend.card_version(frontend.PRINTER_CARD_NAME) != before
    finally:
        manifest.write_text(original, encoding="utf-8")
    assert frontend.card_version(frontend.PRINTER_CARD_NAME) == before


def test_a_missing_card_does_not_produce_a_churning_token(frontend):
    """A random token per call would rewrite the resource entry forever."""
    assert frontend.card_version("does_not_exist.js") == "missing"


def test_hashing_stays_off_the_event_loop(frontend):
    """card_version reads files, so async_register must hand it to an executor."""
    source = (COMPONENT / "frontend.py").read_text(encoding="utf-8")
    assert "async_add_executor_job(card_version" in source, (
        "card_version is being called directly from async code"
    )
    # ...and the one other caller takes the tokens rather than computing them.
    assert "card_versions: dict[str, str]" in source
