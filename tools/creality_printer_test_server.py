#!/usr/bin/env python3
"""
Unified Creality printer test server (WS telemetry + video).

Features
- Single process that serves:
  - WebSocket telemetry on ws://<host>:<ws_port> (default 9999)
  - HTTP server on http://<host>:<http_port> (default 8000) for:
    - POST /call/webrtc_local (WebRTC signaling for K2 family)
    - GET  /stream.mjpeg (MJPEG stream for K1/Ender/Hi models)
    - GET  / (tiny info page)
- Models and capabilities aligned with the integration's utils.ModelDetection mapping.
- Default video: 1920x1080 @ 30fps, overridable via CLI.
- Simulation
  - Expand status: self-testing -> homing -> printing/paused/idle.
  - User-set print duration (seconds), temps, and object count.
  - Position (X/Y/Z) jitters while printing, with reasonable bounds.
  - Temps converge to targets and oscillate ±0.1–0.2°C.
  - Dynamic working layer and object index during the print.
  - Random fan values: caseFan/modelFan/sideFan for bridge-like phases.
  - Light and box-temp controls obey model capabilities.

Requirements
  aiohttp, aiortc, av, numpy
  Pillow is optional for MJPEG; if missing, MJPEG endpoint will warn and 500.

Usage examples
  python3 tools/creality_printer_test_server.py \
    --host 0.0.0.0 --ws-port 9999 --http-port 8000 --model k2plus \
    --print-seconds 600 --objects 8 --target-nozzle 210 --target-bed 60

"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import math
import random
import signal
import time
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Dict, Optional
import contextlib
import sys

import numpy as np
from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription, MediaStreamTrack
from aiortc.contrib.media import MediaBlackhole
import av
import shutil

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger("creality_printer_test_server")


# -----------------------------------------------------------------------------
# Models and capabilities
# -----------------------------------------------------------------------------

MODEL_CONFIGS: dict[str, dict[str, Any]] = {
    # name matches device-reported "model" for integration detection
    # camera: "webrtc" or "mjpeg"
    # box_sensor: has box temp sensor
    # box_control: can set target box temp
    # light: has light switch
    "k1c": {"name": "K1C", "box_sensor": True, "box_control": False, "light": True, "camera": "mjpeg"},
    "k1": {"name": "CR-K1", "box_sensor": True, "box_control": False, "light": True, "camera": "mjpeg"},
    "k1max": {"name": "CR-K1 Max", "box_sensor": True, "box_control": False, "light": True, "camera": "mjpeg"},
    "k1se": {"name": "K1 SE", "box_sensor": False, "box_control": False, "light": False, "camera": "mjpeg"},
    "k2": {"name": "F021", "box_sensor": True, "box_control": False, "light": True, "camera": "webrtc"},
    "k2pro": {"name": "F012", "box_sensor": True, "box_control": True, "light": True, "camera": "webrtc"},
    "k2plus": {"name": "F008", "box_sensor": True, "box_control": True, "light": True, "camera": "webrtc"},
    "e3v3": {"name": "F001", "box_sensor": False, "box_control": False, "light": False, "camera": "mjpeg"},
    "e3v3ke": {"name": "F005", "box_sensor": False, "box_control": False, "light": False, "camera": "mjpeg"},
    "e3v3plus": {"name": "F002", "box_sensor": False, "box_control": False, "light": False, "camera": "mjpeg"},
    # Creality Hi (F018): no box sensor/control, light only
    "crealityhi": {"name": "F018", "box_sensor": False, "box_control": False, "light": True, "camera": "mjpeg"},
}


# -----------------------------------------------------------------------------
# Synthetic media tracks
# -----------------------------------------------------------------------------


class SyntheticVideoTrack(MediaStreamTrack):
    kind = "video"

    def __init__(self, width: int = 1920, height: int = 1080, fps: int = 30):
        super().__init__()
        self.width = width
        self.height = height
        self.fps = fps
        self._frame_dur = 1 / fps
        self._t0 = asyncio.get_event_loop().time()
        self._video_pts = 0
        self._video_time_base = Fraction(1, fps)

    async def recv(self):
        # Maintain nominal frame pacing without blocking the event loop
        await asyncio.sleep(self._frame_dur)
        t = asyncio.get_event_loop().time() - self._t0
        # Offload heavy numpy work to a background thread so Ctrl+C remains responsive
        img = await asyncio.to_thread(self._bars, self.width, self.height, t)
        frame = av.VideoFrame.from_ndarray(img, format="rgb24")
        frame.pts = int(self._video_pts)
        frame.time_base = self._video_time_base
        self._video_pts += 1
        if self._video_pts % 120 == 0:
            LOGGER.info(
                "Generated video frame %d at t=%.1fs (%.1ffps)",
                self._video_pts,
                t,
                self._video_pts / t if t > 0 else 0,
            )
        return frame

    def _bars(self, w: int, h: int, t: float) -> np.ndarray:
        x = np.linspace(0, 1, w, dtype=np.float32)
        y = np.linspace(0, 1, h, dtype=np.float32)[:, None]
        r = (np.sin(2 * math.pi * (x + 0.10 * t)) * 0.5 + 0.5)
        g = (np.sin(2 * math.pi * (x * 0.5 + 0.07 * t)) * 0.5 + 0.5)
        b = (np.sin(2 * math.pi * (x * 0.25 + 0.05 * t)) * 0.5 + 0.5)
        img = np.stack([
            np.broadcast_to(r, (h, w)),
            np.broadcast_to(g, (h, w)),
            np.broadcast_to(b, (h, w)),
        ], axis=-1)
        img *= (0.7 + 0.3 * y)[:, :, None]
        img = np.clip(img * 255, 0, 255).astype(np.uint8)

        # basic moving text overlay
        return self._draw_text(img, f"K-Printer {int(t):04d}s", 20 + int(40 * math.sin(t)), 40, (255, 255, 0))

    def _draw_text(self, img: np.ndarray, text: str, x: int, y: int, color: tuple[int, int, int]) -> np.ndarray:
        # super crude 6x8 block font for a subset of ASCII
        cw, ch = 6, 8
        for i, chv in enumerate(text):
            cx, cy = x + i * (cw + 2), y
            if chv == ' ':
                continue
            if cy + 8 >= img.shape[0] or cx + 6 >= img.shape[1] or cx < 0 or cy < 0:
                continue
            # draw bounding box-ish strokes
            img[cy:cy + 1, cx:cx + cw] = color
            img[cy + ch:cy + ch + 1, cx:cx + cw] = color
            img[cy:cy + ch, cx:cx + 1] = color
            img[cy:cy + ch, cx + cw - 1:cx + cw] = color
        return img


class SyntheticAudioTrack(MediaStreamTrack):
    kind = "audio"

    def __init__(self, samplerate: int = 48000, tone_hz: float = 440.0):
        super().__init__()
        self.samplerate = samplerate
        self.tone_hz = tone_hz
        self._t = 0.0
        self._audio_pts = 0
        self._audio_time_base = Fraction(1, samplerate)

    async def recv(self):
        await asyncio.sleep(0.02)
        samples = int(self.samplerate * 0.02)
        # Generate samples off the event loop to remain responsive
        def _gen():
            t = (np.arange(samples) + self._t) / self.samplerate
            data = 0.1 * np.sin(2 * math.pi * self.tone_hz * t)
            pcm = (data * 32767).astype(np.int16)
            return np.expand_dims(pcm, axis=0)  # mono

        pcm2 = await asyncio.to_thread(_gen)
        self._t += samples
        frame = av.AudioFrame.from_ndarray(pcm2, format="s16", layout="mono")
        frame.sample_rate = self.samplerate
        frame.pts = int(self._audio_pts)
        frame.time_base = self._audio_time_base
        self._audio_pts += samples
        return frame


# -----------------------------------------------------------------------------
# FFmpeg-backed sources (CPU-optimized C code generation and encoding)
# -----------------------------------------------------------------------------


class FFmpegVideoTrack(MediaStreamTrack):
    """Video track reading raw frames from an ffmpeg testsrc2 pipeline.

    We use asyncio subprocess to read RGB24 frames at width*height*3 bytes.
    This avoids Python-side heavy math and relies on ffmpeg's optimized code.
    """

    kind = "video"

    def __init__(self, width: int, height: int, fps: int, ffmpeg_bin: str = "ffmpeg"):
        super().__init__()
        self.width = width
        self.height = height
        self.fps = fps
        self.ffmpeg_bin = ffmpeg_bin
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._frame_len = self.width * self.height * 3  # rgb24
        self._time_base = Fraction(1, fps)
        self._pts = 0

    async def _ensure_proc(self):
        if self._proc is not None and self._proc.returncode is None:
            return
        if not shutil.which(self.ffmpeg_bin):
            raise RuntimeError("ffmpeg binary not found")
        # Generate a moving test pattern at the desired size and fps
        # -f lavfi -i testsrc2 produces synthetic frames; output RGB24 rawvideo
        self._proc = await asyncio.create_subprocess_exec(
            self.ffmpeg_bin,
            "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc2=size={self.width}x{self.height}:rate={self.fps}",
            "-pix_fmt", "rgb24",
            "-f", "rawvideo", "pipe:1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def recv(self):
        await self._ensure_proc()
        assert self._proc and self._proc.stdout
        # Read exactly one frame worth of bytes; this blocks until available
        data = await self._proc.stdout.readexactly(self._frame_len)
        # Construct frame without heavy Python math
        arr = np.frombuffer(data, dtype=np.uint8).reshape((self.height, self.width, 3))
        frame = av.VideoFrame.from_ndarray(arr, format="rgb24")
        frame.pts = self._pts
        frame.time_base = self._time_base
        self._pts += 1
        return frame

    async def _stop(self):
        try:
            if self._proc and self._proc.returncode is None:
                self._proc.terminate()
                try:
                    await asyncio.wait_for(self._proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    self._proc.kill()
        except Exception:
            pass


# -----------------------------------------------------------------------------
# Printer state and simulation
# -----------------------------------------------------------------------------


def _osc(value: float, span_low: float = 0.1, span_high: float = 0.2) -> float:
    """Oscillate value by a small random amount between ±span_low..±span_high."""
    span = random.uniform(span_low, span_high)
    return value + random.uniform(-span, span)


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


class PrinterState:
    def __init__(
        self,
        model_key: str,
        simulate_print: bool,
        sim: SimOptions,
        targets: dict[str, float],
    ) -> None:
        self._cfg = MODEL_CONFIGS.get(model_key, MODEL_CONFIGS["k2plus"])  # default
        self.model_key = model_key
        self.simulate_print = simulate_print
        self.sim = sim
        self._t0 = time.monotonic()
        self._paused = False
        self._light_on = False
        self._state_code = 0  # 0 idle, 1 printing, 2 self-test, 5 paused
        self._device_state = 0  # 0 idle, 7 homing

        # print timeline
        self._progress = 0
        self._print_start_ts: Optional[float] = None
        self._self_test_end = self._t0 + (sim.self_test_seconds if simulate_print else 0)

        # temperatures
        self._nozzle_temp_target = float(targets.get("nozzle", 0.0))
        self._bed_temp_target = float(targets.get("bed", 0.0))
        self._box_temp_target = float(targets.get("box", 0.0)) if self._cfg.get("box_control") else 0.0
        self._nozzle_temp = 25.0
        self._bed_temp = 25.0
        self._box_temp = 26.0

        # motion
        self._pos_x = 0.0
        self._pos_y = 0.0
        self._pos_z = 0.0

        # job
        self._print_file = "demo.gcode"
        self._objects_total = int(sim.total_objects)
        self._objects_list = [{"name": f"obj{i+1}", "index": i + 1} for i in range(self._objects_total)]
        self._cur_object_idx = 0
        self._layer_total = int(sim.total_layers)
        self._cur_layer = 0

        # process
        self._used_material_length = 0.0
        self._real_time_flow = 0.0

        # controls
        self._feedrate_pct = 100.0
        self._flowrate_pct = 100.0

        # fans (0-100)
        self._case_fan = 0
        self._model_fan = 0
        self._side_fan = 0

        # CFS state
        self._cfs_boxes = [
            {
                "id": 0,
                "state": 0,
                "type": 1,
                "materials": [
                    {
                        "id": 0,
                        "vendor": "Generic",
                        "type": "PLA",
                        "color": "#01b04ae",
                        "name": "Generic PLA",
                        "minTemp": 190,
                        "maxTemp": 240,
                        "selected": 0,
                        "percent": 100,
                        "state": 1,
                    }
                ],
            },
            {
                "id": 1,
                "state": 1,
                "type": 0,
                "materials": [
                    {
                        "id": 0,
                        "vendor": "Creality",
                        "type": "PLA",
                        "name": "Hyper PLA",
                        "color": "#0000000",
                        "percent": 95,
                        "state": 1,
                        "selected": 1,
                    },
                    {
                        "id": 1,
                        "vendor": "Creality",
                        "type": "PLA",
                        "name": "Hyper PLA",
                        "color": "#0ffffff",
                        "percent": 80,
                        "state": 1,
                        "selected": 0,
                    },
                    {
                        "id": 2,
                        "vendor": "Creality",
                        "type": "PLA",
                        "name": "Hyper PLA",
                        "color": "#0ffa800",
                        "percent": 100,
                        "state": 1,
                        "selected": 0,
                    },
                    {
                        "id": 3,
                        "vendor": "Creality",
                        "type": "PLA",
                        "name": "Hyper PLA",
                        "color": "#0ff97e1",
                        "percent": 75,
                        "state": 1,
                        "selected": 0,
                    },
                ],
            },
        ]

        # errors
        self._error_code = 0

        if self.simulate_print:
            self._state_code = 2 if time.monotonic() < self._self_test_end else 1
            if self._state_code == 1:
                self._print_start_ts = time.monotonic()

    # ----------------------- control mutations -----------------------
    def set_pause(self, paused: bool) -> None:
        self._paused = paused
        self._state_code = 5 if paused else (1 if self._progress < 100 else 0)

    def set_stop(self) -> None:
        self._paused = False
        self._progress = 0
        self._cur_layer = 0
        self._used_material_length = 0.0
        self._real_time_flow = 0.0
        self._device_state = 0
        self._print_start_ts = None
        self._state_code = 0

    def set_light(self, on: bool) -> None:
        if self._cfg.get("light"):
            self._light_on = on

    def set_box_temp(self, temp: float) -> None:
        if self._cfg.get("box_control"):
            self._box_temp_target = float(temp)

    def set_nozzle_temp(self, temp: float) -> None:
        self._nozzle_temp_target = float(temp)

    def set_bed_temp(self, temp: float) -> None:
        self._bed_temp_target = float(temp)

    def set_feedrate(self, pct: float) -> None:
        self._feedrate_pct = float(pct)

    def set_flowrate(self, pct: float) -> None:
        self._flowrate_pct = float(pct)

    def set_autohome(self, axes: str) -> None:
        self._device_state = 7
        # simulate quick homing pulse
        self._pos_x = 0.0 if "X" in axes or "x" in axes else self._pos_x
        self._pos_y = 0.0 if "Y" in axes or "y" in axes else self._pos_y
        self._pos_z = 0.0 if "Z" in axes or "z" in axes else self._pos_z
        self._device_state = 0

    def get_cfs_info(self) -> dict[str, Any]:
        """Generate a realistic CFS status payload."""
        # Update dynamic fields in CFS boxes
        for box in self._cfs_boxes:
            if box.get("type") == 0:  # Box with sensors
                box["temp"] = round(_osc(28.0, 0.5, 1.0), 1)
                box["humidity"] = round(_osc(40.0, 1.0, 2.0), 1)

        return {
            "boxsInfo": {
                "same_material": [
                    ["001001", "0000000", [{"boxId": 1, "materialId": 0}], "PLA"],
                    ["001001", "0ffffff", [{"boxId": 1, "materialId": 1}], "PLA"],
                ],
                "materialBoxs": self._cfs_boxes,
            }
        }

    # ----------------------- tick/update loops -----------------------
    def _tick_temps(self):
        # move temps towards targets with slight oscillation
        def converge(cur: float, tgt: float) -> float:
            if tgt is None:
                tgt = 0.0
            # proportional step
            delta = (tgt - cur) * 0.10
            nxt = cur + delta
            # Use a small variable oscillation between ±0.1 and ±0.2
            return _osc(nxt, 0.1, 0.2)

        self._nozzle_temp = converge(self._nozzle_temp, self._nozzle_temp_target)
        self._bed_temp = converge(self._bed_temp, self._bed_temp_target)
        if self._cfg.get("box_sensor"):
            # in non-control models, follow ambient/nozzle a bit
            box_target = self._box_temp_target if self._cfg.get("box_control") else (
                26.0 + 0.05 * max(0.0, self._nozzle_temp - 25.0)
            )
            self._box_temp = converge(self._box_temp, box_target)

    def _tick_print(self):
        now = time.monotonic()
        if not self.simulate_print:
            self._state_code = 0 if not self._paused else 5
            return

        # self-test phase
        if now < self._self_test_end:
            self._state_code = 2
            return

        # printing phase
        if self._print_start_ts is None:
            self._print_start_ts = now
        if self._paused:
            self._state_code = 5
        else:
            self._state_code = 1 if self._progress < 100 else 0

        if self._state_code == 1:
            elapsed = now - self._print_start_ts
            pct = max(0.0, min(100.0, 100.0 * (elapsed / float(self.sim.total_print_seconds))))
            # gently progress forward only
            self._progress = max(self._progress, int(pct))
            self._used_material_length = self._progress * 10.0
            self._cur_layer = int(self._progress / 100.0 * self._layer_total)
            # advance object index at rough milestones
            self._cur_object_idx = min(self._objects_total, 1 + int(self._progress / (100 / max(1, self._objects_total))))
            self._real_time_flow = 0.5 + (self._progress / 100.0) * 0.5

            # fans jitter; make side/model fans spike occasionally (bridges)
            self._case_fan = int(min(100, max(0, random.gauss(60, 10))))
            bridge_boost = 20 if random.random() < 0.1 else 0
            self._model_fan = int(min(100, max(0, random.gauss(70 + bridge_boost, 15))))
            self._side_fan = int(min(100, max(0, random.gauss(50 + bridge_boost, 20))))

            # random walk on XYZ
            def jitter(v: float, step: float, mx: float) -> float:
                v2 = v + random.uniform(-step, step)
                return max(0.0, min(mx, v2))

            self._pos_x = jitter(self._pos_x, 3.0, self.sim.max_x)
            self._pos_y = jitter(self._pos_y, 3.0, self.sim.max_y)
            self._pos_z = jitter(self._pos_z, 0.2, self.sim.max_z)

    def tick(self):
        self._tick_temps()
        self._tick_print()

    # ----------------------- telemetry snapshot -----------------------
    def snapshot(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "model": self._cfg["name"],
            "hostname": f"creality-{self.model_key}",
            "modelVersion": f"Printer HW Ver: {self._cfg['name']}; Printer SW Ver: test-1",

            # temps
            "nozzleTemp": round(self._nozzle_temp, 2),
            "bedTemp0": round(self._bed_temp, 2),
            "targetNozzleTemp": round(self._nozzle_temp_target, 1),
            "targetBedTemp0": round(self._bed_temp_target, 1),
            "maxNozzleTemp": 300.0,
            "maxBedTemp": 120.0,

            # pos
            "curPosition": f"X:{self._pos_x:.2f} Y:{self._pos_y:.2f} Z:{self._pos_z:.2f}",
            "deviceState": self._device_state,

            # status + error
            "state": self._state_code,  # 0 idle, 1 printing, 2 self-test, 5 paused
            "err": {"errcode": self._error_code},

            # job
            "objects_list": self._objects_list,
            "curObjectIndex": self._cur_object_idx,
            "printFileName": self._print_file if self.simulate_print else "",
            "printProgress": self._progress if self.simulate_print else 0,
            "dProgress": self._progress if self.simulate_print else 0,
            "printJobTime": int(max(0, (time.monotonic() - (self._print_start_ts or self._t0))) if self.simulate_print else 0),
            "printLeftTime": max(0, self.sim.total_print_seconds - int(time.monotonic() - (self._print_start_ts or self._t0))) if self.simulate_print else 0,

            # material/flow
            "usedMaterialLength": round(self._used_material_length, 1),
            "realTimeFlow": round(self._real_time_flow, 3),

            # layers
            "layer": self._cur_layer,
            "TotalLayer": self._layer_total,

            # control params
            "feedratePct": self._feedrate_pct,
            "flowratePct": self._flowrate_pct,
            "curFeedratePct": self._feedrate_pct,
            "curFlowratePct": self._flowrate_pct,

            # fans
            "caseFan": self._case_fan,
            "modelFan": self._model_fan,
            "sideFan": self._side_fan,
        }

        if self._cfg.get("box_sensor"):
            d.update({
                "boxTemp": round(self._box_temp, 2),
                "maxBoxTemp": 80.0,
            })
            if self._cfg.get("box_control"):
                d["targetBoxTemp"] = round(self._box_temp_target, 1)

        if self._cfg.get("light"):
            d["lightSw"] = 1 if self._light_on else 0

        return d


# -----------------------------------------------------------------------------
# WebSocket server (telemetry + control)
# -----------------------------------------------------------------------------


async def ws_handle_conn(ws: Any, state: PrinterState):
    LOGGER.info("🔌 WS client connected from %s", getattr(ws, "remote_address", "?"))

    async def rx_loop():
        async for raw in ws:
            try:
                if isinstance(raw, (bytes, bytearray)):
                    raw = raw.decode("utf-8", "ignore")
                if raw == "ok":
                    continue
                msg = json.loads(raw)
            except Exception:
                continue

            if isinstance(msg, dict) and msg.get("method") == "get":
                params = msg.get("params", {})
                if "boxsInfo" in params:
                    await ws_safe_send(ws, state.get_cfs_info())
                else:
                    await ws_safe_send(ws, state.snapshot())
            elif isinstance(msg, dict) and msg.get("method") == "set":
                params = msg.get("params", {})
                handled = False
                if "pause" in params:
                    state.set_pause(bool(int(params.get("pause") or 0)))
                    handled = True
                elif "stop" in params:
                    state.set_stop()
                    handled = True
                elif "nozzleTempControl" in params:
                    state.set_nozzle_temp(float(params.get("nozzleTempControl") or 0))
                    handled = True
                elif "bedTempControl" in params:
                    bc = params.get("bedTempControl", {})
                    temp = float(bc.get("val", 0) if isinstance(bc, dict) else bc or 0)
                    state.set_bed_temp(temp)
                    handled = True
                elif "boxTempControl" in params or "targetBoxTemp" in params:
                    state.set_box_temp(float(params.get("boxTempControl") or params.get("targetBoxTemp") or 0))
                    handled = True
                elif "lightSw" in params or "light" in params:
                    val = params.get("lightSw") if params.get("lightSw") is not None else params.get("light")
                    state.set_light(bool(int(val or 0)))
                    handled = True
                elif "autohome" in params:
                    state.set_autohome(str(params.get("autohome") or "XYZ"))
                    handled = True
                elif "setFeedratePct" in params:
                    state.set_feedrate(float(params.get("setFeedratePct") or 100))
                    handled = True
                elif "setFlowratePct" in params:
                    state.set_flowrate(float(params.get("setFlowratePct") or 100))
                    handled = True
                elif "gcodeCmd" in params:
                    # no-op placeholder
                    handled = True

                if handled:
                    await ws_safe_send(ws, state.snapshot())
            else:
                LOGGER.debug("WS recv: %s", msg)

    async def tx_loop():
        await ws_safe_send(ws, state.snapshot())
        hb_t = 0.0
        snap_t = 0.0
        while True:
            await asyncio.sleep(0.2)
            state.tick()
            now = time.monotonic()
            if now - hb_t >= 10.0:
                await ws_safe_send(ws, {"ModeCode": "heart_beat"})
                hb_t = now
            if now - snap_t >= 2.0:
                await ws_safe_send(ws, state.snapshot())
                snap_t = now

    try:
        await asyncio.gather(rx_loop(), tx_loop())
    except Exception:
        pass
    finally:
        LOGGER.info("🔌 WS client disconnected")


async def ws_safe_send(ws: Any, obj: Any):
    try:
        await ws.send(json.dumps(obj, separators=(",", ":")))
    except Exception:
        pass


# -----------------------------------------------------------------------------
# HTTP server: WebRTC signaling and MJPEG streaming
# -----------------------------------------------------------------------------


CALL_PATH = "/call/webrtc_local"


class HttpServer:
    def __init__(self, host: str, port: int, cam_mode: str, width: int, height: int, fps: int, audio: bool,
                 video_source: str = "synthetic", ffmpeg_bin: str = "ffmpeg") -> None:
        self.host = host
        self.port = port
        self.cam_mode = cam_mode  # "webrtc" or "mjpeg"
        self.width = width
        self.height = height
        self.fps = fps
        self.audio = audio
        self.video_source = video_source
        self.ffmpeg_bin = ffmpeg_bin
        self.app = web.Application()
        self.app.add_routes([
            web.get("/", self.handle_root),
            web.post(CALL_PATH, self.handle_call),
            web.get(CALL_PATH, self.handle_probe),
            web.get("/stream.mjpeg", self.handle_mjpeg),
        ])
        self._sessions: set[MediaBlackhole | RTCPeerConnection] = set()
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.BaseSite] = None

    async def handle_root(self, request: web.Request):
        return web.Response(text=(
            "Creality unified test server\n\n"
            f"WebRTC: POST {CALL_PATH} | MJPEG: GET /stream.mjpeg\n"
        ), content_type="text/plain")

    async def handle_probe(self, request: web.Request):
        # match Creality behavior: GET returns 405 to signal presence
        return web.Response(status=405, text="Method Not Allowed")

    async def handle_call(self, request: web.Request):
        if self.cam_mode != "webrtc":
            return web.Response(status=404, text="WebRTC not enabled for this model")
        # Accept multiple payload formats and always answer as base64 JSON (Creality style)
        # Supported inputs:
        #  - base64(JSON{"type":"offer","sdp":"v=0..."})   [go2rtc creality client]
        #  - JSON {"type":"offer","sdp":"v=0..."}
        #  - base64("v=0...") or plain "v=0..." (raw SDP)
        response_mode = "base64_json"
        try:
            raw = await request.read()
            ctype = (request.headers.get("Content-Type") or "").lower()
            LOGGER.debug(
                "/call/webrtc_local content-type=%s body_len=%d raw_head=%r",
                ctype,
                len(raw),
                raw[:16],
            )

            payload: dict | None = None
            raw_stripped = raw.strip()

            def _payload_from_json(b: bytes) -> dict | None:
                try:
                    obj = json.loads(b.decode("utf-8"))
                    return obj if isinstance(obj, dict) else None
                except Exception:
                    return None

            def _payload_from_sdp_text(b: bytes) -> dict | None:
                try:
                    s = b.decode("utf-8", errors="ignore").lstrip("\ufeff\n\r\t ")
                except Exception:
                    return None
                if s.startswith("v=0"):
                    return {"type": "offer", "sdp": s}
                return None

            # Try base64 first (Creality/go2rtc path)
            decoded: bytes | None = None
            try:
                decoded = base64.b64decode(raw_stripped, validate=False)
            except Exception:
                decoded = None

            if decoded:
                # base64(JSON) or base64(SDP)
                LOGGER.debug("decoded base64 head=%r", decoded[:16])
                payload = _payload_from_json(decoded)
                if not payload:
                    payload = _payload_from_sdp_text(decoded)
                    if payload:
                        LOGGER.debug("parsed mode=b64_sdp")
                else:
                    LOGGER.debug("parsed mode=b64_json")

            # If not base64 or failed - try plain JSON
            if not payload and ("application/json" in ctype or raw_stripped.startswith(b"{")):
                payload = _payload_from_json(raw_stripped)
                if payload:
                    LOGGER.debug("parsed mode=json")

            # Finally, try plain SDP text
            if not payload:
                payload = _payload_from_sdp_text(raw_stripped)
                if payload:
                    LOGGER.debug("parsed mode=plain_sdp")

            if not isinstance(payload, dict) or payload.get("type") != "offer" or "sdp" not in payload:
                return web.Response(status=400, text="invalid payload")
            offer_sdp = str(payload["sdp"]) or ""
            LOGGER.debug("offer SDP head: %s", offer_sdp[:32].replace("\n", "\\n"))
            if not offer_sdp.startswith("v=0"):
                LOGGER.error("Offer SDP doesn't start with 'v=0' (head=%r)", offer_sdp[:16])
                return web.Response(status=400, text="invalid sdp")
        except Exception as exc:
            LOGGER.exception("Failed to parse offer: %s", exc)
            return web.Response(status=400, text="bad request")

        pc = RTCPeerConnection()

        @pc.on("connectionstatechange")
        def _on_connstate():
            try:
                LOGGER.info("PC(%s) connectionState=%s", id(pc), pc.connectionState)
            except Exception:
                pass

        @pc.on("iceconnectionstatechange")
        def _on_ice():
            try:
                LOGGER.info("PC(%s) iceConnectionState=%s", id(pc), pc.iceConnectionState)
            except Exception:
                pass

        await pc.setRemoteDescription(RTCSessionDescription(sdp=offer_sdp, type="offer"))

        offer_has_video = "m=video" in offer_sdp
        offer_has_audio = "m=audio" in offer_sdp

        if offer_has_video:
            if self.video_source == "ffmpeg":
                try:
                    video_track = FFmpegVideoTrack(self.width, self.height, self.fps, ffmpeg_bin=self.ffmpeg_bin)
                except Exception as exc:
                    LOGGER.warning("FFmpeg not available (%s), falling back to synthetic video", exc)
                    video_track = SyntheticVideoTrack(self.width, self.height, self.fps)
            else:
                video_track = SyntheticVideoTrack(self.width, self.height, self.fps)
            pc.addTrack(video_track)
        if offer_has_audio and self.audio:
            pc.addTrack(SyntheticAudioTrack())

        sink = MediaBlackhole()

        @pc.on("track")
        async def on_track(track):
            await sink.start()
            sink.addTrack(track)

        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

        answer_sdp = (pc.localDescription.sdp or "") if pc.localDescription else ""
        # Normalize to CRLF for maximum SDP parser compatibility
        if "\r\n" not in answer_sdp:
            answer_sdp = answer_sdp.replace("\n", "\r\n")
        # Basic validation: SDP must start with v=0
        if not answer_sdp.startswith("v=0"):
            LOGGER.error("Generated invalid SDP (head=%r)", answer_sdp[:16])
            return web.Response(status=500, text="invalid sdp")
        LOGGER.debug("answer SDP head: %s", answer_sdp[:32].replace("\n", "\\n"))
        payload = {"type": "answer", "sdp": answer_sdp}
        asyncio.create_task(self._cleanup_pc(pc, sink))
        # Always respond as base64(JSON) for Creality/go2rtc compatibility
        out = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
        return web.Response(status=200, text=out, headers={"Content-Type": "text/plain"})

    async def _cleanup_pc(self, pc: RTCPeerConnection, sink: MediaBlackhole):
        await asyncio.sleep(60)
        try:
            await sink.stop()
        except Exception:
            pass
        try:
            await pc.close()
        except Exception:
            pass

    async def handle_mjpeg(self, request: web.Request):
        if self.cam_mode != "mjpeg":
            return web.Response(status=404, text="MJPEG not enabled for this model")

        boundary = "frame"
        response = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": f"multipart/x-mixed-replace; boundary=--{boundary}",
                "Pragma": "no-cache",
                "Cache-Control": "no-cache, no-store, must-revalidate",
            },
        )
        await response.prepare(request)

        if self.video_source == "ffmpeg":
            # Stream JPEG frames produced by ffmpeg directly; wrap into multipart
            if not shutil.which(self.ffmpeg_bin):
                await response.write(b"FFmpeg not found on PATH.\n")
                await response.write_eof()
                return response

            proc = await asyncio.create_subprocess_exec(
                self.ffmpeg_bin,
                "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", f"testsrc2=size={self.width}x{self.height}:rate={self.fps}",
                "-f", "mjpeg", "-q:v", "5", "pipe:1",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            assert proc.stdout is not None
            buf = bytearray()
            try:
                while True:
                    chunk = await proc.stdout.read(65536)
                    if not chunk:
                        break
                    buf.extend(chunk)
                    # Extract complete JPEGs and stream them
                    while True:
                        # Find SOI and EOI
                        soi = buf.find(b"\xff\xd8")
                        if soi == -1:
                            break
                        eoi = buf.find(b"\xff\xd9", soi + 2)
                        if eoi == -1:
                            break
                        jpg = bytes(buf[soi:eoi + 2])
                        del buf[:eoi + 2]
                        header = (
                            f"--{boundary}\r\n"
                            "Content-Type: image/jpeg\r\n"
                            f"Content-Length: {len(jpg)}\r\n\r\n"
                        ).encode("ascii")
                        await response.write(header + jpg + b"\r\n")
            except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
                pass
            finally:
                with contextlib.suppress(Exception):
                    await response.write_eof()
                # Terminate ffmpeg
                try:
                    if proc.returncode is None:
                        proc.terminate()
                        try:
                            await asyncio.wait_for(proc.wait(), timeout=2.0)
                        except asyncio.TimeoutError:
                            proc.kill()
                except Exception:
                    pass
            return response
        else:
            # Python fallback: Synthetic + Pillow encoder
            # Optional dependency for encoding JPEGs
            try:
                from PIL import Image  # type: ignore
            except Exception:
                await response.write(b"MJPEG requires Pillow (PIL) to be installed.\n")
                await response.write_eof()
                return response

            video = SyntheticVideoTrack(self.width, self.height, self.fps)

            async def write_frame():
                frame = await video.recv()
                rgb = frame.to_ndarray(format="rgb24")
                img = Image.fromarray(rgb)
                from io import BytesIO

                buf2 = BytesIO()
                img.save(buf2, format="JPEG", quality=80)
                jpg = buf2.getvalue()

                header = (
                    f"--{boundary}\r\n"
                    "Content-Type: image/jpeg\r\n"
                    f"Content-Length: {len(jpg)}\r\n\r\n"
                ).encode("ascii")
                await response.write(header + jpg + b"\r\n")

            try:
                while True:
                    await write_frame()
            except asyncio.CancelledError:
                pass
            except (ConnectionResetError, BrokenPipeError):
                pass
            finally:
                with contextlib.suppress(Exception):
                    await response.write_eof()
            return response

    async def run(self):
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        self._runner = runner
        self._site = site
        LOGGER.info(
            "🌐 HTTP server: http://%s:%d (webrtc=%s | mjpeg=%s)",
            self.host,
            self.port,
            self.cam_mode == "webrtc",
            self.cam_mode == "mjpeg",
        )

    async def shutdown(self):
        # Gracefully stop HTTP server
        try:
            if self._runner is not None:
                await self._runner.cleanup()
        except Exception:
            pass


# -----------------------------------------------------------------------------
# Main app wiring
# -----------------------------------------------------------------------------


async def main_async(args: argparse.Namespace):
    model_cfg = MODEL_CONFIGS.get(args.model, MODEL_CONFIGS["k2plus"])
    cam_mode = model_cfg["camera"]

    sim = SimOptions(
        total_print_seconds=args.print_seconds,
        total_layers=args.layers,
        total_objects=args.objects,
        self_test_seconds=args.self_test_seconds,
        max_x=args.max_x,
        max_y=args.max_y,
        max_z=args.max_z,
    )
    state = PrinterState(
        model_key=args.model,
        simulate_print=args.simulate_print,
        sim=sim,
        targets={
            "nozzle": args.target_nozzle or 0,
            "bed": args.target_bed or 0,
            "box": args.target_box or 0,
        },
    )

    # HTTP server (WebRTC + MJPEG endpoints)
    http_srv = HttpServer(
        host=args.host,
        port=args.http_port,
        cam_mode=cam_mode,
        width=args.width,
        height=args.height,
        fps=args.fps,
        audio=not args.no_audio,
        video_source=getattr(args, "video_source", "synthetic"),
        ffmpeg_bin=getattr(args, "ffmpeg_bin", "ffmpeg"),
    )

    # WebSocket server for telemetry
    import websockets
    # Suppress noisy handshake errors from raw TCP/HTTP probes when not debugging
    if not getattr(args, "debug", False):
        try:
            logging.getLogger("websockets.server").setLevel(logging.WARNING)
        except Exception:
            pass

    async def _process_request(path, request_like):
        # Gracefully respond to non-WebSocket HTTP requests on the WS port.
        # websockets may pass either a headers mapping or a Request-like object with .headers
        try:
            headers_map = getattr(request_like, "headers", request_like)
            upgrade_val = (headers_map.get("Upgrade") or "").lower() if hasattr(headers_map, "get") else ""
        except Exception:
            upgrade_val = ""
        if upgrade_val != "websocket":
            headers = [("Content-Type", "text/plain; charset=utf-8")]
            body = b"This endpoint expects a WebSocket upgrade.\n"
            # 426 Upgrade Required would be semantically correct; 405 is fine to mimic device probes
            return (405, headers, body)
        return None

    ws_server = await websockets.serve(
        lambda ws: ws_handle_conn(ws, state),
        args.host,
        args.ws_port,
        ping_interval=None,
        process_request=_process_request,
    )
    LOGGER.info("🔌 WS server: ws://%s:%d", args.host, args.ws_port)

    await http_srv.run()

    LOGGER.info("🚀 Unified Creality Test Server ready")
    LOGGER.info(
        "🖨️ Model: %s | Camera: %s | Box Control: %s | Light: %s",
        model_cfg["name"],
        cam_mode.upper(),
        "Yes" if model_cfg["box_control"] else "No",
        "Yes" if model_cfg["light"] else "No",
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    # Suppress benign aioice Transaction.__retry() InvalidStateError noise when not in debug
    prev_exc_handler = loop.get_exception_handler()

    def _quiet_asyncio_exceptions(loop: asyncio.AbstractEventLoop, context: dict):
        try:
            if not getattr(args, "debug", False):
                msg = context.get("message", "")
                exc = context.get("exception")
                # Detect aioice Transaction retry timeouts that sometimes raise InvalidStateError
                text = f"{msg} {repr(exc)}"
                if (
                    "Transaction.__retry" in text
                    or "aioice.stun.TransactionTimeout" in text
                    or ("InvalidStateError" in text and "Transaction" in text)
                ):
                    LOGGER.debug("Suppressed benign aioice exception: %s", text)
                    return
        except Exception:
            pass
        # Delegate to previous handler/default
        if prev_exc_handler is not None:
            prev_exc_handler(loop, context)
        else:
            loop.default_exception_handler(context)

    loop.set_exception_handler(_quiet_asyncio_exceptions)
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:
                # Fallback in environments without signal support
                pass
        await stop.wait()
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        try:
            ws_server.close()
            await ws_server.wait_closed()
        except Exception:
            pass
        try:
            await http_srv.shutdown()
        except Exception as e:
            LOGGER.exception("Exception during HTTP server shutdown: %s", e)
        # Restore previous exception handler
        with contextlib.suppress(Exception):
            loop.set_exception_handler(prev_exc_handler)


def build_argparser() -> argparse.ArgumentParser:
    epilog = (
        "\nExamples:\n"
        "  # Start K2 Plus with WebRTC camera, 10-min print simulation at 1080p30\n"
        "  %(prog)s --model k2plus --simulate-print --print-seconds 600\n\n"
        "  # K1 with MJPEG camera at 720p25, nozzle/bed targets set\n"
        "  %(prog)s --model k1 --simulate-print --width 1280 --height 720 --fps 25 \\\n+            --target-nozzle 210 --target-bed 60\n\n"
        "  # K2 Pro, set box temp, 8 objects, 160 layers, larger bed area\n"
        "  %(prog)s --model k2pro --simulate-print --target-box 40 --objects 8 --layers 160 \\\n+            --max-x 300 --max-y 300 --max-z 300\n\n"
        "Endpoints:\n"
        "  WebSocket telemetry: ws://<host>:9999\n"
        "  WebRTC signaling (K2 family): POST http://<host>:8000/call/webrtc_local\n"
        "  MJPEG stream (others): GET  http://<host>:8000/stream.mjpeg\n\n"
        "Notes:\n"
        "  - WebRTC requires aiortc + av + numpy; MJPEG requires Pillow.\n"
        "  - The model determines camera mode automatically.\n"
        "  - Temperatures converge toward targets with ±0.1–0.2°C oscillation.\n"
        "  - Default targets: nozzle 250°C, bed 70°C, box 50°C (override with --target-*).\n"
    )
    p = argparse.ArgumentParser(
        description="Unified Creality WS + Video test server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog,
    )
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--ws-port", type=int, default=9999)
    p.add_argument("--http-port", type=int, default=8000)
    p.add_argument("--model", default="k2plus",
                   choices=list(MODEL_CONFIGS.keys()),
                   help="Printer model to emulate")
    p.add_argument("--simulate-print", action="store_true")
    p.add_argument("--print-seconds", type=int, default=600, help="Total simulated print duration in seconds")
    p.add_argument("--layers", type=int, default=120, help="Total layers to simulate")
    p.add_argument("--objects", type=int, default=6, help="Total object count to simulate")
    p.add_argument("--self-test-seconds", type=int, default=5, help="Initial self-test duration")

    # video options
    p.add_argument("--width", type=int, default=1920)
    p.add_argument("--height", type=int, default=1080)
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--low-power", action="store_true", help="Use 640x360 @ 10 fps for low-end hardware")
    p.add_argument("--no-audio", action="store_true")

    # temp targets
    p.add_argument("--target-nozzle", type=float, default=250, help="Initial nozzle target (°C)")
    p.add_argument("--target-bed", type=float, default=70, help="Initial bed target (°C)")
    p.add_argument("--target-box", type=float, default=50, help="Initial box target (°C, if supported)")

    # motion bounds
    p.add_argument("--max-x", type=float, default=235.0)
    p.add_argument("--max-y", type=float, default=235.0)
    p.add_argument("--max-z", type=float, default=250.0)

    # logging
    p.add_argument("--debug", action="store_true")
    # video source selection / ffmpeg integration
    p.add_argument("--video-source", choices=["synthetic", "ffmpeg"], default="synthetic",
                   help="Video generator: Python synthetic or FFmpeg testsrc2")
    p.add_argument("--ffmpeg-bin", default="ffmpeg", help="Path to ffmpeg binary (for --video-source=ffmpeg)")
    return p


def main():
    parser = build_argparser()
    # Show help if no args provided
    if len(sys.argv) == 1:
        parser.print_help()
        return
    args = parser.parse_args()
    # Apply low-power defaults if requested
    if getattr(args, "low_power", False):
        if args.width == 1920 and args.height == 1080 and args.fps == 30:
            args.width, args.height, args.fps = 640, 360, 10

    if args.debug:
        LOGGER.setLevel(logging.DEBUG)
        logging.getLogger("aiohttp.server").setLevel(logging.DEBUG)
        logging.getLogger("aiortc").setLevel(logging.DEBUG)
        logging.getLogger("websockets.server").setLevel(logging.INFO)
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        # Ensure clean exit on Ctrl+C even if signals aren't installed
        pass


if __name__ == "__main__":
    # Lazy import to avoid unconditional dependency if MJPEG not used
    import contextlib  # used in MJPEG cleanup
    main()
