"""What each simulated printer is, as far as the integration can tell.

A profile is everything that differs between models and firmware: the identity
fields, the limits, which sensors and controls exist, the camera, how numbers
are written on the wire, and how the firmware behaves at the edges of a print
(self-test at start, what a stop looks like, which G-code listing it answers
with).

Values are derived from what real printers send (field names, types and
formats), never copied from a capture. Where no capture exists (K2, Hi, CFS)
the profile follows the issue reports and the integration's own expectations,
and says so.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

# A K1C's limits and its fixed fields, as a full frame lists them. Everything
# here is constant on a real printer between prints; the simulator streams it
# so the full frame has the real shape and size.
K1_STATIC_FIELDS: dict[str, Any] = {
    "accelToDecelLimits": 2500,
    "accelerationLimits": 5000,
    "aiDetection": 0,
    "aiFirstFloor": 0,
    "aiPausePrint": 0,
    "aiSw": 0,
    "autoLevelResult": "",
    "bedTempAutoPid": 0,
    "connect": 1,
    "cornerVelocityLimits": 5,
    "curZOffset": 0.0,
    "enableSelfTest": 1,
    "materialDetect": 1,
    "nozzleMoveSnapshot": 0,
    "nozzleTempAutoPid": 0,
    "powerLoss": 0,
    "pressureAdvance": 0.04,
    "printId": "",
    "repoPlrStatus": 0,
    "smoothTime": 0.04,
    "tfCard": 1,
    "upgradeStatus": 0,
    "velocityLimits": 600,
    "video": 1,
    "video1": 0,
    "videoElapse": 0,
    "videoElapseFrame": 0,
    "videoElapseInterval": 0,
}

# The fields a K1-family firmware writes as six-decimal strings ("31.030000"),
# in full frames and deltas alike. Everything else is an int or a float.
STRING_NUMBER_FIELDS = frozenset({
    "nozzleTemp",
    "bedTemp0",
    "bedTemp1",
    "bedTemp2",
    "pressureAdvance",
    "realTimeFlow",
    "realTimeSpeed",
    "smoothTime",
})


def dwin_model_version(hw: str, sw: str) -> str:
    """The K1 family's version string: printer fields empty, the DWIN pair set."""
    return f"printer hw ver:;printer sw ver:;DWIN hw ver:{hw};DWIN sw ver:{sw};"


def board_model_version(board: str, sw: str) -> str:
    """A guess, for the models no capture exists for (Ender 3 V3, V3 Plus, Hi).

    The K2s and the Ender 3 V3 KE send the DWIN form instead, with the board
    code in `model` (tools/tests/fixtures/printers.json).
    """
    return f"printer hw ver:{board};printer sw ver:{sw};DWIN hw ver:;DWIN sw ver:;"


@dataclass(frozen=True)
class Profile:
    key: str
    title: str
    model: str
    model_version: str
    max_nozzle: int = 300
    max_bed: int = 100
    # Chamber: a temperature sensor, a settable target, and the reported limit.
    box_sensor: bool = False
    box_control: bool = False
    max_box: int | None = None
    # K2 Base: targetBoxTemp is 0 on the WebSocket; the real value is only in
    # Moonraker on :7125 (#52).
    box_target_ws_zero: bool = False
    moonraker: bool = False
    light: bool = True
    # LED dimming through `gcodeCmd SET_PIN PIN=LED VALUE=x` (K2 Pro/Plus).
    led_pin: str | None = None
    # "mjpeg": mjpg-streamer on :8080. "webrtc": signalling on :8000.
    # "none": nothing answers on either.
    camera: str = "mjpeg"
    # Firmware that has moved to WebRTC says so (K1C/K1 Max 1.3.5.22, K1C 2025).
    webrtc_support: bool = False
    # A CFS attached by default; the control UI can attach or detach one.
    cfs: bool = False
    # Whether the firmware knows about the CFS at all (reports cfsConnect).
    cfs_capable: bool = True
    string_numbers: bool = False
    # K2 and Hi self-test after the first printing frame of every print (#124).
    self_test_at_print_start: bool = False
    # "state4": state 4, file and progress kept, targets 0 (K1C, seen live).
    # "state0": state 0, file kept, progress reset to 0.
    # "clear": the file name is cleared.
    stop_style: str = "state4"
    # "v2": retGcodeFileInfo2 (1.3.5.22). "legacy": retGcodeFileInfo with a
    # packed string (1.3.3.x). "none": the request goes unanswered.
    gcode_listing: str = "legacy"
    static_fields: dict[str, Any] = field(default_factory=lambda: dict(K1_STATIC_FIELDS))
    notes: str = ""

    def hostname(self) -> str:
        # Kept as `creality-<key>`: the test box's entity ids are derived from
        # it (sensor.creality_k1c_*), and zeroconf injection matches it.
        return f"creality-{self.key}"

    def as_dict(self) -> dict[str, Any]:
        out = {k: getattr(self, k) for k in self.__dataclass_fields__ if k != "static_fields"}
        out["hostname"] = self.hostname()
        return out

    def with_overrides(self, **changes: Any) -> "Profile":
        return replace(self, **{k: v for k, v in changes.items() if v is not None})


PROFILES: dict[str, Profile] = {
    "k1c": Profile(
        key="k1c",
        title="K1C, firmware 1.3.3.x (MJPEG)",
        model="K1C",
        model_version=dwin_model_version("CR4CU220812S11", "1.3.3.46"),
        max_nozzle=300,
        max_bed=100,
        box_sensor=True,
        camera="mjpeg",
        # Not what a stock K1C ships with, but a supported combination and the
        # one the test box's card checks need (CFS box 1 with four slots).
        cfs=True,
        string_numbers=True,
        gcode_listing="legacy",
        notes="The test box default. boxTemp is reported, maxBoxTemp is not.",
    ),
    "k1c-1.3.5": Profile(
        key="k1c-1.3.5",
        title="K1C, firmware 1.3.5.22 (WebRTC)",
        model="K1C",
        model_version=dwin_model_version("CR4CU220812S11", "1.3.5.22"),
        max_nozzle=300,
        max_bed=100,
        box_sensor=True,
        camera="webrtc",
        webrtc_support=True,
        string_numbers=True,
        gcode_listing="v2",
        notes="#46: MJPEG on :8080 is gone, webrtcSupport: 1, WebRTC on :8000.",
    ),
    "k1": Profile(
        key="k1",
        title="K1",
        model="K1",
        model_version=dwin_model_version("CR4CU220812S11", "1.3.3.46"),
        max_nozzle=300,
        max_bed=100,
        box_sensor=True,
        camera="mjpeg",
        string_numbers=True,
    ),
    "k1max": Profile(
        key="k1max",
        title="K1 Max",
        model="CR-K1 Max",
        model_version=dwin_model_version("CR4CU220812S11", "1.3.3.46"),
        max_nozzle=300,
        max_bed=100,
        box_sensor=True,
        camera="mjpeg",
        string_numbers=True,
    ),
    "k1se": Profile(
        key="k1se",
        title="K1 SE (no camera, no light)",
        model="K1 SE",
        model_version=dwin_model_version("CR4CU220812S11", "1.3.3.46"),
        light=False,
        camera="none",
        cfs_capable=False,
        string_numbers=True,
    ),
    "k2": Profile(
        key="k2",
        title="K2 (Base): chamber target via Moonraker",
        model="F021",
        model_version=dwin_model_version("CR0CN200400C10", "1.1.0.94"),
        max_nozzle=350,
        max_bed=110,
        box_sensor=True,
        box_control=True,
        max_box=60,
        box_target_ws_zero=True,
        moonraker=True,
        camera="webrtc",
        webrtc_support=True,
        cfs=True,
        self_test_at_print_start=True,
        stop_style="state0",
        gcode_listing="v2",
        notes="Identity from #66; the rest follows #52/#124 and the integration.",
    ),
    "k2pro": Profile(
        key="k2pro",
        title="K2 Pro",
        model="F012",
        model_version=dwin_model_version("CR0CN200400C10", "1.1.6.7"),
        max_nozzle=350,
        max_bed=110,
        box_sensor=True,
        box_control=True,
        max_box=60,
        led_pin="LED",
        camera="webrtc",
        webrtc_support=True,
        cfs=True,
        self_test_at_print_start=True,
        stop_style="state0",
        gcode_listing="v2",
    ),
    "k2plus": Profile(
        key="k2plus",
        title="K2 Plus",
        model="F008",
        model_version=dwin_model_version("CR0CN240110C10", "1.1.3.13"),
        max_nozzle=350,
        max_bed=120,
        box_sensor=True,
        box_control=True,
        max_box=60,
        led_pin="LED",
        camera="webrtc",
        webrtc_support=True,
        cfs=True,
        self_test_at_print_start=True,
        stop_style="state0",
        gcode_listing="v2",
    ),
    "e3v3": Profile(
        key="e3v3",
        title="Ender-3 V3",
        model="F001",
        model_version=board_model_version("F001", "1.0.0"),
        light=False,
        camera="none",
        cfs_capable=False,
    ),
    "e3v3ke": Profile(
        key="e3v3ke",
        title="Ender-3 V3 KE",
        model="F005",
        model_version=dwin_model_version("F005", "V1.1.0.17"),
        light=False,
        camera="mjpeg",
        cfs_capable=False,
    ),
    "e3v3plus": Profile(
        key="e3v3plus",
        title="Ender-3 V3 Plus",
        model="F002",
        model_version=board_model_version("F002", "1.0.0"),
        light=False,
        camera="none",
        cfs_capable=False,
    ),
    "crealityhi": Profile(
        key="crealityhi",
        title="Creality Hi",
        model="F018",
        model_version=board_model_version("F018", "1.0.9"),
        light=True,
        camera="webrtc",
        cfs=False,
        self_test_at_print_start=True,
        stop_style="state0",
        gcode_listing="v2",
        notes="Users report a WebRTC camera (#60).",
    ),
}


# The old simulator's MODEL_CONFIGS, kept for anything that still reads it.
MODEL_CONFIGS: dict[str, dict[str, Any]] = {
    key: {
        "name": p.model,
        "box_sensor": p.box_sensor,
        "box_control": p.box_control,
        "light": p.light,
        "camera": p.camera,
    }
    for key, p in PROFILES.items()
}


def get_profile(key: str) -> Profile:
    try:
        return PROFILES[key]
    except KeyError:
        raise ValueError(f"unknown model {key!r}; choose from {sorted(PROFILES)}") from None
