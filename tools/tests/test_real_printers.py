"""Detection against what real printers report (fixtures/printers.json, R48).

The other detection tests feed shapes no printer sends: `"K2 Plus"` as the
model, `"F012"` as the whole modelVersion. A real K2 Plus sends `"F008"` as the
model and a DWIN version string, and a real K1 sends `"K1"`, which no test used.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from simulator.profiles import PROFILES  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "ha_creality_ws_utils_real", ROOT / "custom_components" / "ha_creality_ws" / "utils.py"
)
utils = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(utils)

PRINTERS = json.loads((Path(__file__).parent / "fixtures" / "printers.json").read_text(encoding="utf-8"))["printers"]
IDS = [p["id"] for p in PRINTERS]
FLAGS = sorted({p["expect"]["flag"] for p in PRINTERS})


@pytest.mark.parametrize("printer", PRINTERS, ids=IDS)
def test_the_model_is_recognised(printer):
    detected = utils.ModelDetection(printer["frame"])
    expect = printer["expect"]
    # Exactly its own flag among the ones the corpus covers: a K2 Pro must not
    # also pass as a K2 Base (R41), nor a K1 as a K1C.
    assert {f for f in FLAGS if getattr(detected, f)} == {expect["flag"]}


@pytest.mark.parametrize("printer", PRINTERS, ids=IDS)
def test_the_device_page_names_the_model(printer):
    """A K2 Pro's device page said "F012" (R80)."""
    expect = printer["expect"]
    assert utils.ModelDetection(printer["frame"]).display_model() == (expect["name"], expect["model_id"])


@pytest.mark.parametrize("printer", PRINTERS, ids=IDS)
def test_the_camera_type_is_detected(printer):
    assert utils.detect_camera_type(printer["frame"]) == printer["expect"]["camera"]


@pytest.mark.parametrize("printer", [p for p in PRINTERS if "simulator" in p["expect"]],
                         ids=[p["id"] for p in PRINTERS if "simulator" in p["expect"]])
def test_the_simulator_reports_the_same_identity(printer):
    """The test box checks the integration against the simulator, so its
    identity strings must be the real ones, not a guess at their shape."""
    profile = PROFILES[printer["expect"]["simulator"]]
    frame = printer["frame"]
    assert profile.model == frame["model"]
    assert profile.model_version == frame["modelVersion"]
    if "webrtcSupport" in frame:
        assert profile.webrtc_support == (frame["webrtcSupport"] == 1)


def test_a_model_name_containing_hi_is_not_a_creality_hi():
    """R66: "hi" was matched as a substring of any model name."""
    for model in ("Chimera", "K1 High Speed", "Shield"):
        assert not utils.ModelDetection({"model": model}).is_creality_hi, model
    for model in ("Creality Hi", "Hi", "F018"):
        assert utils.ModelDetection({"model": model}).is_creality_hi, model
