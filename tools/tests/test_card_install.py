"""Getting a new card into a browser that already has the old one.

The cards are served straight out of the integration package with cache
headers on, so the only thing that makes a browser re-fetch one is the ``?v=``
on its Lovelace resource URL. That token therefore has exactly one job: change
when the card changes, and not otherwise.
"""

import importlib.util
import json
import shutil
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


@pytest.fixture
def installed(frontend, tmp_path, monkeypatch):
    """A byte-for-byte copy of the installed package, safe to mutate.

    ``card_version`` locates everything from ``Path(__file__).parent``, read
    from the module at call time, so pointing that at a copy redirects it
    whole. The earlier form edited the real ``www/k_printer_card.js`` and
    ``manifest.json`` and restored them in ``finally``: correct on a clean
    run, but it leaves the working tree modified if the run is killed between
    the two, and two of these tests cannot run concurrently.
    """
    (tmp_path / "www").mkdir()
    shutil.copy2(COMPONENT / "manifest.json", tmp_path / "manifest.json")
    for name in (frontend.PRINTER_CARD_NAME, frontend.CFS_CARD_NAME):
        shutil.copy2(COMPONENT / "www" / name, tmp_path / "www" / name)
    monkeypatch.setattr(frontend, "__file__", str(tmp_path / "frontend.py"))
    # The copy has to hash identically, or it is not standing in for anything.
    assert frontend.card_version(frontend.PRINTER_CARD_NAME) != "missing"
    return tmp_path


def test_the_version_changes_when_the_card_does(frontend, installed):
    """The property the whole update path rests on."""
    card = installed / "www" / frontend.PRINTER_CARD_NAME
    before = frontend.card_version(frontend.PRINTER_CARD_NAME)
    card.write_bytes(card.read_bytes() + b"\n// one more byte\n")
    assert frontend.card_version(frontend.PRINTER_CARD_NAME) != before


def test_the_version_changes_when_the_release_does(frontend, installed):
    """HACS restores files from an archive and can preserve their timestamps.

    A release that only touches Python would otherwise leave every browser on
    the card it already had, which is exactly the upgrade case that matters.
    """
    manifest = installed / "manifest.json"
    before = frontend.card_version(frontend.PRINTER_CARD_NAME)
    bumped = json.loads(manifest.read_text(encoding="utf-8"))
    bumped["version"] = "9.9.9-test"
    manifest.write_text(json.dumps(bumped, indent=2), encoding="utf-8")
    assert frontend.card_version(frontend.PRINTER_CARD_NAME) != before


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
