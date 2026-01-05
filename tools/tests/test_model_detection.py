import importlib.util
import sys
from pathlib import Path

# Load utils(ModelDetection) directly
ROOT = Path(__file__).resolve().parents[2]
utils_path = ROOT / "custom_components" / "ha_creality_ws" / "utils.py"
spec = importlib.util.spec_from_file_location("ha_creality_ws.utils", utils_path)
assert spec is not None
utils = importlib.util.module_from_spec(spec)
sys.modules["ha_creality_ws.utils"] = utils
assert spec.loader is not None
spec.loader.exec_module(utils)

ModelDetection = utils.ModelDetection

CASES = [
    ({"model": "CR-K1", "modelVersion": ""}, {
        "is_k1_family": True, "has_box_sensor": True, "has_box_control": False, "has_light": True,
        "resolved_model": "CR-K1"
    }),
    ({"model": "K1C", "modelVersion": ""}, {
        "is_k1c": True, "has_box_sensor": True, "has_box_control": False, "has_light": True,
        "resolved_model": "K1C"
    }),
    ({"model": "", "modelVersion": "F012"}, {  # K2 Pro by code
        "is_k2_pro": True, "is_k2_family": True, "has_box_sensor": True, "has_box_control": True,
        "resolved_model": "K2 Pro"
    }),
    ({"model": "", "modelVersion": "F021"}, {  # K2 base
        "is_k2_base": True, "is_k2_family": True, "has_box_sensor": True, "has_box_control": True,
        "resolved_model": "K2"
    }),
    ({"model": "", "modelVersion": "F008"}, {  # K2 Plus
        "is_k2_plus": True, "is_k2_family": True, "has_box_sensor": True, "has_box_control": True,
        "resolved_model": "K2 Plus"
    }),
    ({"model": "", "modelVersion": "F018"}, {  # Creality Hi
        "is_creality_hi": True, "has_box_sensor": False, "has_box_control": False, "has_light": True,
        "resolved_model": "Creality Hi"
    }),
    ({"model": "", "modelVersion": "F005"}, {  # Ender 3 V3 KE
        "is_ender_v3_ke": True, "is_ender_v3_family": True, "has_box_sensor": False, "has_light": False,
        "resolved_model": "Ender 3 V3 KE"
    }),
    ({"model": "", "modelVersion": "F002"}, {  # Ender 3 V3 Plus
        "is_ender_v3_plus": True, "is_ender_v3_family": True, "has_box_sensor": False, "has_light": False,
        "resolved_model": "Ender 3 V3 Plus"
    }),
    ({"model": "", "modelVersion": "F001"}, {  # Ender 3 V3
        "is_ender_v3": True, "is_ender_v3_family": True, "has_box_sensor": False, "has_light": False,
        "resolved_model": "Ender 3 V3"
    }),
]


def test_model_detection_cases():
    for inp, expect in CASES:
        md = ModelDetection(inp)
        for key, val in expect.items():
            if key == "resolved_model":
                assert md.resolved_model() == val
            else:
                # Expect boolean flags to match exactly
                assert getattr(md, key) == val, f"Expected {key}=={val} for {inp}, got {getattr(md, key)}"
