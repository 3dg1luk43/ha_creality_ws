"""The simulated printer: temperatures, motion, fans, the print job, faults.

Pure Python on purpose. Everything that decides *what* the printer reports
lives here and in `frames.py`, so it can be unit tested in CI without aiortc,
av or numpy; the servers only carry it over the wire.

The job lifecycle follows real printers, including the parts the integration
was built around after they bit (see docs/internal/reference/07-simulator.md):

* a print starts with `state 1`, progress 0, while the bed and nozzle heat;
* K2 and Hi then self-test, reported as `withSelfTest` 1..99 (#124);
* progress reads 100 while a minute or so is still left, then 99 once more,
  then the job finishes: `state 0`, progress 100, the file still selected;
* a stop is one of three shapes depending on firmware (state 4 with progress
  kept, state 0 with progress reset, or the file cleared);
* a pause freezes progress and the time left.
"""
from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .cfs import MATERIAL_WRITABLE_KEYS, Cfs
from .clock import Clock
from .profiles import MODEL_CONFIGS, PROFILES, Profile, get_profile

__all__ = [
    "MODEL_CONFIGS",
    "MATERIAL_WRITABLE_KEYS",
    "Phase",
    "PrinterState",
    "SimOptions",
    "_M106_RE",
]

GCODE_DIR = "/usr/data/printer_data/gcodes"

# Fan control the integration sends over `gcodeCmd`: M106 P<channel> S<0-255>
# `\b` after the opcode is load-bearing: every group after it is optional, so
# without it `M1061 S30` or `M1069` matched with no P and no S, and handle_gcode
# then set channel 0 to 0% and marked it manual -- pinning the model fan off for
# the rest of the run while reporting the command as handled.
_M106_RE = re.compile(r"^M106\b(?:\s+P(?P<p>\d+))?(?:\s+S(?P<s>\d+(?:\.\d+)?))?", re.IGNORECASE)
_SET_PIN_RE = re.compile(r"^SET_PIN\s+PIN=(?P<pin>\S+)\s+VALUE=(?P<value>[0-9.]+)", re.IGNORECASE)


class Phase(str, Enum):
    IDLE = "idle"
    STARTING = "starting"  # heating before the first layer
    SELF_TEST = "self_test"
    PRINTING = "printing"
    FINISHING = "finishing"  # the one "99" report before the end
    PAUSED = "paused"
    SWAP = "swap"  # a CFS swap: state 0 mid-print (R29)
    COMPLETED = "completed"
    STOPPED = "stopped"


ACTIVE_PHASES = {Phase.STARTING, Phase.SELF_TEST, Phase.PRINTING, Phase.FINISHING, Phase.PAUSED, Phase.SWAP}


@dataclass
class SimOptions:
    total_print_seconds: int = 600
    total_layers: int = 120
    total_objects: int = 6
    self_test_seconds: int = 5
    # movement bounds (mm)
    max_x: float = 235.0
    max_y: float = 235.0
    max_z: float = 250.0
    # Seconds from the end at which progress already reads 100.
    finish_tail_seconds: float = 30.0
    # How long a finished job keeps 100% before progress drops to 0 with the
    # file still selected (R10). 0 keeps it at 100.
    finished_reset_seconds: float = 0.0
    # Heating before the first layer gives up waiting after this long.
    max_preheat_seconds: float = 20.0


@dataclass
class Job:
    name: str
    duration: float
    layers: int
    objects: list[dict[str, Any]]
    filament_mm: int
    started_mono: float
    started_wall: float
    active_seconds: float = 0.0
    phase_until: float | None = None
    self_test_pct: int = 0
    resume_phase: Phase | None = None

    @property
    def path(self) -> str:
        return f"{GCODE_DIR}/{self.name}"


def _objects(count: int) -> list[dict[str, Any]]:
    """The `objects` a Klipper firmware lists, laid out on a grid."""
    out = []
    for i in range(count):
        cx, cy = 60 + (i % 3) * 60, 60 + (i // 3) * 60
        out.append({
            "name": f"part{i + 1}.STL_ID_{i}_COPY_0",
            "center": [cx, cy],
            "polygon": [[cx - 15, cy - 15], [cx + 15, cy - 15], [cx + 15, cy + 15], [cx - 15, cy + 15]],
        })
    return out


class PrinterState:
    """One printer. `tick()` advances it; `snapshot()` reads it."""

    MATERIAL_WRITABLE_KEYS = MATERIAL_WRITABLE_KEYS

    def __init__(
        self,
        model_key: str,
        simulate_print: bool = False,
        sim: SimOptions | None = None,
        targets: dict[str, float] | None = None,
        deterministic: bool = False,
        cfs_variant: str = "default",
        *,
        clock: Clock | None = None,
        profile: Profile | None = None,
        cfs: str = "auto",
    ) -> None:
        self.profile = profile or get_profile(model_key if model_key in PROFILES else "k2plus")
        self.model_key = self.profile.key
        self.sim = sim or SimOptions()
        self.clock = clock or Clock()
        # Deterministic mode strips every source of randomness (temperature
        # oscillation, fan jitter, XYZ drift) so two runs produce identical
        # telemetry for the same clock.
        self.deterministic = deterministic
        self.cfs_variant = cfs_variant
        targets = targets or {}
        self.job_targets = {
            "nozzle": float(targets.get("nozzle") or 220.0),
            "bed": float(targets.get("bed") or 60.0),
            "box": float(targets.get("box") or 0.0),
        }

        # Fields forced by POST /test/set, applied last.
        self._overrides: dict[str, Any] = {}
        # Overrides that lapse by themselves: field -> (value, until).
        self._timed_overrides: dict[str, tuple[Any, float]] = {}
        self._t0 = self.clock.now()
        self._last_tick = self._t0
        self._last_cfs_tick = self._t0

        # temperatures
        self.nozzle_target = 0.0
        self.bed_target = 0.0
        self.box_target = 0.0
        self.nozzle = 25.0
        self.bed = 25.0
        self.box = 24.0

        # motion
        self.pos = [0.0, 0.0, 0.0]
        self.homed = {"X": 0, "Y": 0, "Z": 0}
        self.homing_until: float | None = None
        self._homing_axes: list[str] = ["X", "Y", "Z"]

        # fans (0-100), and the M106 channels the client took manual control of
        self.fans = {0: 0, 1: 0, 2: 0}
        self._manual_fans: set[int] = set()

        self.light_on = True
        self.led_value: float | None = None
        self.feedrate = 100
        self.flowrate = 100

        self.material_status = 0
        self.error_code = 0
        self.error_key = 0
        self.self_test_style = "withSelfTest"  # or "state2" (R29)
        self.stop_style = self.profile.stop_style
        self._stop_style_used = self.stop_style
        self._self_test_pending = False
        self._self_test_started = 0.0
        self.gcode_listing = self.profile.gcode_listing

        # job
        self.phase = Phase.IDLE
        self.job: Job | None = None
        self.last_job_name = ""
        self.progress = 0
        self.with_self_test = 0
        self.print_job_time = 0
        self.print_left_time = 0
        self.used_mm = 0
        self.layer = 0
        self.total_layers = 0
        self.real_time_flow = 0.0
        self.real_time_speed = 0.0
        self.finished_at: float | None = None
        self.excluded: list[str] = []

        attached = {"auto": self.profile.cfs, "on": True, "off": False}.get(cfs, self.profile.cfs)
        self.cfs = Cfs(attached=attached and self.profile.cfs_capable, variant=cfs_variant,
                       deterministic=deterministic)

        self.events: list[tuple[float, str]] = []
        if simulate_print:
            self.start_print()

    # ================================================================ helpers
    def _log(self, text: str) -> None:
        self.events.append((self.clock.wall(), text))
        del self.events[:-200]

    @property
    def cfg(self) -> dict[str, Any]:
        return MODEL_CONFIGS[self.model_key]

    @property
    def cfs_enabled(self) -> bool:
        return self.cfs.attached

    # ================================================================ job control
    def start_print(
        self,
        name: str = "demo.gcode",
        seconds: float | None = None,
        layers: int | None = None,
        objects: int | None = None,
        self_test: bool | None = None,
    ) -> None:
        """Start (or restart) a print. Restarting resets the job clock, which is
        how the integration tells a reprint of the same file from the old job."""
        now = self.clock.now()
        self.job = Job(
            name=name,
            duration=float(seconds or self.sim.total_print_seconds),
            layers=int(layers or self.sim.total_layers),
            objects=_objects(int(objects if objects is not None else self.sim.total_objects)),
            filament_mm=1536,
            started_mono=now,
            started_wall=self.clock.wall(),
        )
        self.last_job_name = name
        self.excluded = []
        self.progress = 0
        self.used_mm = 0
        self.layer = 0
        self.total_layers = self.job.layers
        self.print_job_time = 0
        self.print_left_time = int(self.job.duration)
        self.finished_at = None
        self.with_self_test = 0 if self.with_self_test != 100 else 100
        self.nozzle_target = self.job_targets["nozzle"]
        self.bed_target = self.job_targets["bed"]
        if self.profile.box_control and self.job_targets["box"]:
            self.box_target = self.job_targets["box"]
        wants_self_test = self.profile.self_test_at_print_start if self_test is None else self_test
        self._self_test_pending = bool(wants_self_test)
        self.phase = Phase.STARTING
        self.homed = {"X": 1, "Y": 1, "Z": 1}
        self._log(f"print started: {name}, {int(self.job.duration)} s")

    def pause(self) -> bool:
        if self.phase in (Phase.SELF_TEST,) or self._homing():
            # The printer is mid-move; a pause sent now is ignored.
            self._log("pause ignored: self-test or homing")
            return False
        if self.phase in (Phase.STARTING, Phase.PRINTING, Phase.FINISHING, Phase.SWAP) and self.job:
            self.job.resume_phase = Phase.PRINTING if self.phase != Phase.STARTING else Phase.STARTING
            self.phase = Phase.PAUSED
            self._log("paused")
            return True
        return False

    def resume(self) -> bool:
        if self.phase == Phase.PAUSED and self.job:
            self.phase = self.job.resume_phase or Phase.PRINTING
            self._log("resumed")
            return True
        return False

    STOP_STYLES = ("state4", "state0", "clear")

    def stop(self, style: str | None = None) -> bool:
        style = style or self.stop_style
        if style not in self.STOP_STYLES:
            raise ValueError(f"stop style is one of {self.STOP_STYLES}")
        if self.phase not in ACTIVE_PHASES:
            return False
        self.phase = Phase.STOPPED
        self.nozzle_target = self.bed_target = 0.0
        self.real_time_flow = self.real_time_speed = 0.0
        self.print_left_time = 0
        if style == "state0":
            self.progress = 0
        elif style == "clear":
            self.progress = 0
            self.job = None
        self._stop_style_used = style
        self._log(f"stopped ({style})")
        return True

    def finish_now(self) -> bool:
        """Skip to the end of the running job."""
        if not (self.job and self.phase in ACTIVE_PHASES):
            return False
        self.job.active_seconds = self.job.duration
        if self.phase in (Phase.PAUSED, Phase.STARTING, Phase.SELF_TEST, Phase.SWAP):
            self.phase = Phase.PRINTING
        return True

    def cfs_swap(self, seconds: float = 20.0) -> bool:
        """A mid-print stretch of `state 0`, as during a CFS filament swap (R29)."""
        if not (self.phase == Phase.PRINTING and self.job):
            return False
        self.job.phase_until = self.clock.now() + seconds
        self.phase = Phase.SWAP
        self._log(f"CFS swap for {seconds:.0f} s")
        return True

    def clear_job(self) -> None:
        """Back to a clean idle printer, as after a reboot."""
        self.phase = Phase.IDLE
        self.job = None
        self.progress = 0
        self.print_job_time = self.print_left_time = 0
        self.used_mm = self.layer = self.total_layers = 0
        self.with_self_test = 0
        self.nozzle_target = self.bed_target = 0.0

    # ================================================================ faults
    def set_error(self, code: int, key: int = 0) -> None:
        self.error_code, self.error_key = int(code), int(key)
        self._log(f"error {code}/{key}")

    def clear_error(self) -> None:
        self.error_code = self.error_key = 0
        self._log("error cleared")

    def runout(self) -> None:
        """Filament runs out: the flag goes up and the printer pauses."""
        self.material_status = 1
        if self.phase in (Phase.PRINTING, Phase.FINISHING) and self.job:
            self.job.resume_phase = Phase.PRINTING
            self.phase = Phase.PAUSED
        self._log("filament runout")

    def resolve_runout(self, resume: bool = False) -> None:
        self.material_status = 0
        if resume:
            self.resume()
        self._log("runout resolved")

    def blank_fields(self, fields: list[str], seconds: float) -> None:
        """Report `fields` as "" for a while, as a booting printer does (#121)."""
        if isinstance(fields, str):
            fields = [f.strip() for f in fields.split(",") if f.strip()]
        until = self.clock.now() + seconds
        for name in fields:
            self._timed_overrides[str(name)] = ("", until)

    # ================================================================ commands
    # The names the old simulator exposed, kept so tests and scripts using them
    # keep working.
    def set_material_status(self, status: int) -> None:
        self.material_status = int(status)

    def set_pause(self, paused: bool) -> bool:
        return self.pause() if paused else self.resume()

    def set_stop(self) -> None:
        self.stop()

    def set_light(self, on: bool) -> bool:
        if self.profile.light:
            self.light_on = bool(on)
        return self.profile.light

    def set_box_temp(self, temp: float) -> bool:
        if self.profile.box_control:
            self.box_target = float(temp)
        return self.profile.box_control

    def set_nozzle_temp(self, temp: float) -> None:
        self.nozzle_target = max(0.0, min(float(self.profile.max_nozzle), float(temp)))

    def set_bed_temp(self, temp: float) -> None:
        self.bed_target = max(0.0, min(float(self.profile.max_bed), float(temp)))

    def set_feedrate(self, pct: float) -> None:
        self.feedrate = int(round(float(pct)))

    def set_flowrate(self, pct: float) -> None:
        self.flowrate = int(round(float(pct)))

    def set_fan_pct(self, channel: int, pct: float) -> None:
        """Apply an M106 fan command (P0 model, P1 case, P2 auxiliary/side)."""
        if channel not in self.fans:
            return
        self.fans[channel] = int(round(max(0.0, min(100.0, float(pct)))))
        self._manual_fans.add(channel)

    def handle_gcode(self, cmd: str) -> bool:
        """Handle the G-code commands the integration actually sends."""
        text = (cmd or "").strip()
        m = _M106_RE.match(text)
        if m:
            channel = int(m.group("p") or 0)
            # A bare `M106` is full speed, not off: Klipper's cmd_M106 reads
            # `gcmd.get_float("S", 255.)`, and Creality's own K1 macro sets
            # `tmp = 255` when S is absent.
            s_val = float(m.group("s")) if m.group("s") is not None else 255.0
            self.set_fan_pct(channel, s_val / 255.0 * 100.0)
            return True
        m = _SET_PIN_RE.match(text)
        if m and self.profile.led_pin and m.group("pin").upper() == self.profile.led_pin.upper():
            # The LED's dim level. Real firmware never reports it back.
            self.led_value = float(m.group("value"))
            self._log(f"LED level {self.led_value}")
            return True
        return False

    def set_autohome(self, axes: str, seconds: float = 4.0) -> None:
        """Home: `deviceState` 7 for a few seconds, then the axes read homed."""
        self._homing_axes = [a for a in "XYZ" if a in (axes or "XYZ").upper()] or ["X", "Y", "Z"]
        self.homing_until = self.clock.now() + float(seconds)
        self._log(f"homing {''.join(self._homing_axes)}")

    def _homing(self) -> bool:
        return self.homing_until is not None and self.clock.now() < self.homing_until

    # ================================================================ CFS
    def get_cfs_info(self) -> dict[str, Any]:
        return self.cfs.info()

    def modify_material(self, payload: Any) -> dict[str, Any]:
        return self.cfs.modify_material(payload)

    def set_cfs_materials(self, box_id: int, materials: list) -> bool:
        return self.cfs.set_materials(box_id, materials)

    # ================================================================ listings
    def get_gcode_file_info(self) -> dict[str, Any] | None:
        """Answer `reqGcodeFile` the way the firmware does, or None for none.

        The reply covers *every* file on the printer, not just the running
        one; the integration has to pick its job out by `path`. `consumables`
        is filament length in mm; `filamentWeight` is grams as a string, empty
        on files the printer did not slice itself.
        """
        current = self.last_job_name or "demo.gcode"
        files = [
            ("older_job.gcode", 7422, "22.32", 8300),
            (current, 1536, "4.62", int(self.job.duration) if self.job else 1552),
            ("3DBenchy.gcode", 3723, "", 983),
        ]
        if self.gcode_listing == "none":
            return None
        if self.gcode_listing == "legacy":
            packed = "".join(
                f"{GCODE_DIR}:{name}:{mm * 1800}:0.200000:1790401399:{seconds}:"
                f"/tmp/creality/local_gcode/humbnail/{name.rsplit('.', 1)[0]};"
                for name, mm, _g, seconds in files
            )
            return {"retGcodeFileInfo": {"totalNum": len(files), "fileInfo": packed}}

        def _entry(name: str, mm: int, grams: str, seconds: int) -> dict[str, Any]:
            return {
                "custom_types": 1,
                "type": 8,
                "name": name,
                "path": f"{GCODE_DIR}/{name}",
                "file_size": mm * 1800,
                "create_time": 1790401399,
                "timeCost": seconds,
                "consumables": mm,
                "material": "PLA",
                "nozzleTemp": 22000,
                "bedTemp": 6000,
                "software": "OrcaSlicer" if grams else "Creality",
                "thumbnail": f"/usr/data//creality/local_gcode/humbnail/{name}.png",
                "preview": f"/usr/data//creality/local_gcode/original/{name}.png",
                "materialColors": "#26A69A" if grams else "",
                "materialIds": "09001" if grams else "",
                "filamentWeight": grams,
                # Trailing whitespace is what the printer really sends.
                "match": "T1A=  " if grams else "",
            }

        return {"retGcodeFileInfo2": [_entry(*f) for f in files]}

    def print_objects(self) -> dict[str, Any]:
        """The `reqPrintObjects` reply: three keys, `objects` a JSON *string*."""
        import json

        objects = self.job.objects if (self.job and self.phase in ACTIVE_PHASES) else []
        current = ""
        if objects and self.phase in (Phase.PRINTING, Phase.FINISHING, Phase.PAUSED):
            idx = min(len(objects) - 1, int(self.layer) % len(objects))
            current = objects[idx]["name"]
        excluded = json.dumps(self.excluded) if self.excluded else "[ ]"
        return {
            "current_object": current,
            "excluded_objects": excluded,
            "objects": json.dumps(objects, indent=1) if objects else "[ ]",
        }

    def probed_matrix(self) -> dict[str, Any]:
        val = [
            {"x": f"{5 + 55 * (i % 5):.6f}", "y": f"{5 + 55 * (i // 5):.6f}",
             "z": f"{0.02 * math.sin(i):.6f}"}
            for i in range(25)
        ]
        return {"probedMatrix": {"num": 25, "val": val}}

    # ================================================================ tick
    def tick(self) -> None:
        now = self.clock.now()
        dt = max(0.0, min(5.0, now - self._last_tick))
        self._last_tick = now
        self._tick_homing(now)
        if now - self._last_cfs_tick >= 10:
            self._last_cfs_tick = now
            self.cfs.tick()
        self._tick_temps(dt)
        self._tick_job(now, dt)
        for name, (_value, until) in list(self._timed_overrides.items()):
            if now >= until:
                del self._timed_overrides[name]

    def _tick_homing(self, now: float) -> None:
        if self.homing_until is not None and now >= self.homing_until:
            for axis in self._homing_axes:
                self.homed[axis] = 1
                self.pos["XYZ".index(axis)] = 0.0
            self.homing_until = None

    def _tick_temps(self, dt: float) -> None:
        box_target = self.box_target if self.profile.box_control else 24.0 + 0.04 * max(0.0, self.nozzle - 25.0)
        if self.deterministic:
            self.nozzle = self.nozzle_target or 25.0
            self.bed = self.bed_target or 25.0
            self.box = box_target or 24.0
            return
        k = 1.0 - math.exp(-dt / 2.5)

        def converge(cur: float, tgt: float) -> float:
            tgt = tgt if tgt > 0 else 25.0
            nxt = cur + (tgt - cur) * k
            return nxt + random.uniform(-0.15, 0.15) * (dt / 0.2)

        self.nozzle = converge(self.nozzle, self.nozzle_target)
        self.bed = converge(self.bed, self.bed_target)
        self.box = converge(self.box, box_target)

    def _heated(self) -> bool:
        return abs(self.nozzle - self.nozzle_target) < 4 and abs(self.bed - self.bed_target) < 3

    def _tick_job(self, now: float, dt: float) -> None:
        job = self.job
        if job is None or self.phase not in ACTIVE_PHASES:
            if self.phase == Phase.COMPLETED and self.sim.finished_reset_seconds and self.finished_at:
                if now - self.finished_at >= self.sim.finished_reset_seconds and self.progress:
                    self.progress = 0
                    self._log("finished job's progress reset to 0 (file kept)")
            return

        self.print_job_time = int(now - job.started_mono)

        if self.phase == Phase.STARTING:
            if self._heated() or now - job.started_mono >= self.sim.max_preheat_seconds or self.deterministic:
                if self._self_test_pending:
                    self.phase = Phase.SELF_TEST
                    job.phase_until = now + max(1.0, float(self.sim.self_test_seconds))
                    self._self_test_started = now
                    self.with_self_test = 1
                else:
                    self.phase = Phase.PRINTING
                    self.with_self_test = 100
            return

        if self.phase == Phase.SELF_TEST:
            span = max(1.0, float(self.sim.self_test_seconds))
            done = (now - self._self_test_started) / span
            self.with_self_test = max(1, min(99, int(done * 100)))
            if now >= (job.phase_until or now):
                self.with_self_test = 100
                self.phase = Phase.PRINTING
            return

        if self.phase == Phase.SWAP:
            if now >= (job.phase_until or now):
                self.phase = Phase.PRINTING
            return

        if self.phase == Phase.PAUSED:
            return

        if self.phase == Phase.FINISHING:
            if now >= (job.phase_until or now):
                self._complete(now)
            return

        # PRINTING
        job.active_seconds = min(job.duration, job.active_seconds + dt * (self.feedrate / 100.0))
        left = max(0.0, job.duration - job.active_seconds)
        self.print_left_time = int(math.ceil(left))
        tail = min(self.sim.finish_tail_seconds, job.duration * 0.1)
        if left <= 0:
            # One last report of 99 before the end, held long enough that a
            # one-second delta cycle carries it.
            self.progress = 99
            self.phase = Phase.FINISHING
            job.phase_until = now + 1.5
            return
        if left <= tail:
            self.progress = 100
        else:
            self.progress = min(99, int(job.active_seconds / job.duration * 100))
        frac = job.active_seconds / job.duration
        self.layer = max(1, min(job.layers, int(frac * job.layers) + 1))
        self.used_mm = int(job.filament_mm * frac)
        if self.cfs.attached and not self.deterministic:
            self.cfs.consume(dt * 0.02)
        self._tick_motion(dt)

    def _complete(self, now: float) -> None:
        self.phase = Phase.COMPLETED
        self.progress = 100
        self.print_left_time = 0
        self.nozzle_target = self.bed_target = 0.0
        self.real_time_flow = self.real_time_speed = 0.0
        self.finished_at = now
        self._log("print completed")

    def _tick_motion(self, dt: float) -> None:
        if self.deterministic:
            self.pos = [100.0, 100.0, 10.0]
            for ch, val in ((0, 70), (1, 60), (2, 50)):
                if ch not in self._manual_fans:
                    self.fans[ch] = val
            self.real_time_flow = 12.0
            self.real_time_speed = 150.0
            return
        bridge = 20 if random.random() < 0.1 else 0
        for ch, (mean, sd) in ((0, (70 + bridge, 15)), (1, (60, 10)), (2, (50 + bridge, 20))):
            if ch not in self._manual_fans:
                self.fans[ch] = int(min(100, max(0, random.gauss(mean, sd))))
        self.pos[0] = max(0.0, min(self.sim.max_x, self.pos[0] + random.uniform(-3, 3)))
        self.pos[1] = max(0.0, min(self.sim.max_y, self.pos[1] + random.uniform(-3, 3)))
        self.pos[2] = round(0.2 * self.layer, 2)
        self.real_time_flow = round(random.uniform(5.0, 17.0), 6)
        self.real_time_speed = round(random.uniform(100.0, 200.0), 6)

    # ================================================================ reading
    def state_code(self) -> int:
        """The numeric `state` field."""
        if self.phase in (Phase.STARTING, Phase.PRINTING, Phase.FINISHING):
            return 1
        if self.phase == Phase.SELF_TEST:
            return 2 if self.self_test_style == "state2" else 1
        if self.phase == Phase.PAUSED:
            return 5
        if self.phase == Phase.STOPPED and self._stop_style_used == "state4":
            return 4
        return 0

    def device_state(self) -> int:
        if self._homing():
            return 7
        return 1 if self.phase in ACTIVE_PHASES else 0

    def values(self) -> dict[str, Any]:
        """Every field as a real printer reports it, before formatting and
        overrides. `frames.py` turns this into what goes on the wire."""
        p = self.profile
        job = self.job
        file_name = job.path if job else ""
        d: dict[str, Any] = dict(p.static_fields)
        d.update({
            "model": p.model,
            "hostname": p.hostname(),
            "modelVersion": p.model_version,
            "nozzleTemp": round(self.nozzle, 2),
            "bedTemp0": round(self.bed, 2),
            "bedTemp1": 0.0,
            "bedTemp2": 0.0,
            "targetNozzleTemp": int(round(self.nozzle_target)),
            "targetBedTemp0": int(round(self.bed_target)),
            "targetBedTemp1": 0,
            "targetBedTemp2": 0,
            "maxNozzleTemp": p.max_nozzle,
            "maxBedTemp": p.max_bed,
            "curPosition": f"X:{self.pos[0]:.2f} Y:{self.pos[1]:.2f} Z:{self.pos[2]:.2f}",
            "autohome": f"X:{self.homed['X']} Y:{self.homed['Y']} Z:{self.homed['Z']}",
            "deviceState": self.device_state(),
            "state": self.state_code(),
            "err": {"errcode": self.error_code, "key": self.error_key},
            "withSelfTest": self.with_self_test,
            "printFileName": file_name,
            "printProgress": self.progress,
            "dProgress": self.progress,
            "printJobTime": self.print_job_time,
            "printLeftTime": self.print_left_time,
            "printStartTime": int(job.started_wall) if job else 0,
            "usedMaterialLength": self.used_mm,
            "realTimeFlow": self.real_time_flow,
            "realTimeSpeed": self.real_time_speed,
            "layer": self.layer,
            "TotalLayer": self.total_layers,
            "curFeedratePct": self.feedrate,
            "curFlowratePct": self.flowrate,
            "modelFanPct": self.fans[0],
            "caseFanPct": self.fans[1],
            "auxiliaryFanPct": self.fans[2],
            "fan": 1 if self.fans[0] else 0,
            "fanCase": 1 if self.fans[1] else 0,
            "fanAuxiliary": 1 if self.fans[2] else 0,
            "materialStatus": self.material_status,
        })
        if p.box_sensor:
            d["boxTemp"] = int(round(self.box))
        if p.max_box is not None:
            d["maxBoxTemp"] = p.max_box
        if p.box_control:
            d["targetBoxTemp"] = 0 if p.box_target_ws_zero else int(round(self.box_target))
        if p.light:
            d["lightSw"] = 1 if self.light_on else 0
        if p.webrtc_support:
            d["webrtcSupport"] = 1
        if p.cfs_capable:
            d["cfsConnect"] = 1 if self.cfs.attached else 0
        return d

    def overrides(self) -> dict[str, Any]:
        out = {name: value for name, (value, _until) in self._timed_overrides.items()}
        out.update(self._overrides)
        return out

    def snapshot(self) -> dict[str, Any]:
        """The full frame, formatted, with test overrides applied last."""
        from .frames import format_frame

        d = format_frame(self.values(), string_numbers=self.profile.string_numbers)
        d.update(self.overrides())
        return d

    # ================================================================ test control
    def apply_overrides(self, values: dict[str, Any]) -> None:
        """Force telemetry fields (POST /test/set). None removes an override."""
        for key, value in (values or {}).items():
            if value is None:
                self._overrides.pop(key, None)
                self._timed_overrides.pop(key, None)
            else:
                self._overrides[key] = value

    def clear_overrides(self) -> None:
        self._overrides.clear()
        self._timed_overrides.clear()

    def moonraker_status(self) -> dict[str, Any]:
        """What Moonraker on :7125 reports for the K2 Base chamber fan."""
        return {
            "temperature_fan chamber_fan": {
                "temperature": round(self.box, 2),
                "target": float(self.box_target),
                "speed": 0.0,
            }
        }

    def describe(self) -> dict[str, Any]:
        """Everything the control UI shows, beyond the telemetry itself."""
        job = self.job
        return {
            "phase": self.phase.value,
            "profile": self.profile.as_dict(),
            "job": None if job is None else {
                "name": job.name,
                "path": job.path,
                "duration": job.duration,
                "active_seconds": round(job.active_seconds, 1),
                "layers": job.layers,
                "objects": len(job.objects),
            },
            "homing": self._homing(),
            "stop_style": self.stop_style,
            "self_test_style": self.self_test_style,
            "gcode_listing": self.gcode_listing,
            "cfs_echo_colour": self.cfs.echo_colour,
            "led_value": self.led_value,
            "overrides": dict(self._overrides),
            "timed_blanks": {k: max(0.0, round(until - self.clock.now(), 1))
                             for k, (_v, until) in self._timed_overrides.items()},
            "finished_reset_seconds": self.sim.finished_reset_seconds,
            "events": [{"t": t, "text": text} for t, text in self.events[-50:]],
        }
