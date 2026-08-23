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
import os
import random
import re
import signal
import tempfile
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
    datefmt="%H:%M:%S",
)
LOGGER = logging.getLogger("creality_printer_test_server")

# Fan control the integration sends over `gcodeCmd`: M106 P<channel> S<0-255>
_M106_RE = re.compile(r"^M106(?:\s+P(?P<p>\d+))?(?:\s+S(?P<s>\d+(?:\.\d+)?))?", re.IGNORECASE)


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


class H264PassthroughTrack(MediaStreamTrack):
    """Send pre-encoded H.264 packets through untouched, with a 1 s GOP.

    aiortc's built-in H.264 encoder inherits libx264's default 250-frame
    keyframe interval, so at these frame rates keyframes are 8-25 s apart. Home
    Assistant's stream worker gives up long before that -- "Error demuxing
    stream while finding first packet" -- and go2rtc's RTSP output cannot start
    without a keyframe either, so the HLS/recording path looked broken against
    the simulator even though real printers work.

    Real Creality cameras emit a keyframe roughly every second, so a short clip
    is pre-encoded with `keyint=fps` and its packets are looped. aiortc detects
    an `av.Packet` (rather than a `VideoFrame`) and packetises it directly,
    skipping its own encoder entirely.
    """

    kind = "video"

    def __init__(self, width: int, height: int, fps: int, ffmpeg_bin: str = "ffmpeg",
                 seconds: int = 4):
        super().__init__()
        self.width = width
        self.height = height
        self.fps = max(1, int(fps))
        self.ffmpeg_bin = ffmpeg_bin
        self.seconds = max(2, int(seconds))
        self._packets: list[Any] = []
        self._time_base = Fraction(1, 90000)
        self._idx = 0
        self._pts_offset = 0
        self._clip_duration_pts = 0
        self._t0: Optional[float] = None

    @staticmethod
    def available(ffmpeg_bin: str = "ffmpeg") -> bool:
        return shutil.which(ffmpeg_bin) is not None

    async def _ensure_clip(self) -> None:
        if self._packets:
            return
        if not shutil.which(self.ffmpeg_bin):
            raise RuntimeError("ffmpeg binary not found")

        tmp = tempfile.NamedTemporaryFile(suffix=".h264", delete=False)
        tmp.close()
        path = tmp.name
        try:
            proc = await asyncio.create_subprocess_exec(
                self.ffmpeg_bin,
                "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi",
                "-i", f"testsrc2=size={self.width}x{self.height}:rate={self.fps}",
                "-t", str(self.seconds),
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-tune", "zerolatency",
                "-profile:v", "baseline",
                "-pix_fmt", "yuv420p",
                # keyframe every second, no B-frames, SPS/PPS before every IDR so a
                # consumer joining mid-clip can start decoding immediately
                "-x264-params",
                f"keyint={self.fps}:min-keyint={self.fps}:scenecut=0:repeat-headers=1",
                "-bsf:v", "dump_extra",
                "-f", "h264", path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, err = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(f"ffmpeg failed: {err.decode(errors='replace')[:200]}")

            container = av.open(path)
            try:
                stream = container.streams.video[0]
                # Annex-B raw H.264 has no container timestamps; synthesise them.
                step = int(90000 / self.fps)
                # Counts kept packets, not demuxed ones: a skipped zero-size
                # packet would leave the last pts at (demuxed-1)*step while
                # _clip_duration_pts below is len(kept)*step. The wrap in recv()
                # would then advance _pts_offset by less than the real clip span,
                # so the second loop's timestamps overlap the first instead of
                # increasing monotonically.
                kept = 0
                for packet in container.demux(stream):
                    if packet.size == 0:
                        continue
                    packet.pts = kept * step
                    packet.dts = packet.pts
                    packet.time_base = self._time_base
                    self._packets.append(packet)
                    kept += 1
            finally:
                container.close()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

        if not self._packets:
            raise RuntimeError("pre-encoded clip contained no packets")
        step = int(90000 / self.fps)
        self._clip_duration_pts = len(self._packets) * step
        LOGGER.info(
            "H.264 passthrough clip ready: %d packets, %.1fs, keyframe every %ds",
            len(self._packets), self.seconds, 1,
        )

    async def recv(self):
        await self._ensure_clip()

        packet = self._packets[self._idx]
        pts = self._pts_offset + packet.pts

        # Pace to wall clock so the consumer sees a real-time stream.
        if self._t0 is None:
            self._t0 = time.monotonic()
        target = self._t0 + (pts / 90000.0)
        delay = target - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)

        out = av.Packet(bytes(packet))
        out.pts = pts
        out.dts = pts
        out.time_base = self._time_base

        self._idx += 1
        if self._idx >= len(self._packets):
            self._idx = 0
            self._pts_offset += self._clip_duration_pts
        return out


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
        deterministic: bool = False,
        cfs_variant: str = "default",
    ) -> None:
        self._cfg = MODEL_CONFIGS.get(model_key, MODEL_CONFIGS["k2plus"])  # default
        self.model_key = model_key
        self.simulate_print = simulate_print
        self.sim = sim
        # Deterministic mode strips every source of randomness (temperature
        # oscillation, fan jitter, XYZ drift) so two runs produce byte-identical
        # telemetry. That is what makes an entity-state diff between two versions
        # of the integration meaningful as a regression check.
        self.deterministic = deterministic
        self.cfs_variant = cfs_variant
        # Fields forced by POST /test/set, applied last in snapshot().
        self._overrides: Dict[str, Any] = {}
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
        self._material_status = 0


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
        # M106 channels the client has taken manual control of
        self._manual_fans: set[int] = set()

        # CFS state
        self._cfs_boxes = self._build_cfs_boxes()

        # errors
        self._error_code = 0

        if self.simulate_print:
            self._state_code = 2 if time.monotonic() < self._self_test_end else 1
            if self._state_code == 1:
                self._print_start_ts = time.monotonic()

    # ----------------------- control mutations -----------------------
    def set_material_status(self, status: int) -> None:
        self._material_status = int(status)

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

    def set_fan_pct(self, channel: int, pct: float) -> None:
        """Apply an M106 fan command (P0 model, P1 case, P2 auxiliary/side)."""
        value = int(round(max(0.0, min(100.0, float(pct)))))
        if channel == 0:
            self._model_fan = value
        elif channel == 1:
            self._case_fan = value
        elif channel == 2:
            self._side_fan = value
        else:
            return
        self._manual_fans.add(channel)

    def handle_gcode(self, cmd: str) -> bool:
        """Handle the G-code commands the integration actually sends."""
        m = _M106_RE.match((cmd or "").strip())
        if not m:
            return False
        channel = int(m.group("p") or 0)
        s_val = float(m.group("s") or 0)
        self.set_fan_pct(channel, s_val / 255.0 * 100.0)
        return True

    def set_autohome(self, axes: str) -> None:
        self._device_state = 7
        # simulate quick homing pulse
        self._pos_x = 0.0 if "X" in axes or "x" in axes else self._pos_x
        self._pos_y = 0.0 if "Y" in axes or "y" in axes else self._pos_y
        self._pos_z = 0.0 if "Z" in axes or "z" in axes else self._pos_z
        self._device_state = 0

    @property
    def cfs_enabled(self) -> bool:
        """True when a real CFS unit is attached (a type==0 box), not just an
        external spool holder (type==1). This is what `cfsConnect` reports."""
        return any(box.get("type") == 0 for box in self._cfs_boxes)

    def get_cfs_info(self) -> dict[str, Any]:
        """Generate a realistic CFS status payload."""
        # Update dynamic fields in CFS boxes
        for box in self._cfs_boxes:
            if box.get("type") == 0:  # Box with sensors
                if self.deterministic:
                    box["temp"] = 28.0
                    box["humidity"] = 40.0
                else:
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

    # Keys the integration sends in a modifyMaterial payload, minus the two that
    # address the slot. `rfid` is included because the integration can pass an
    # existing tag id straight back through.
    MATERIAL_WRITABLE_KEYS = (
        "type",
        "name",
        "vendor",
        "color",
        "minTemp",
        "maxTemp",
        "pressure",
        "rfid",
    )

    def modify_material(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Apply a ``modifyMaterial`` write to the stored CFS slot.

        Merges rather than replaces, which is what makes the "absent key keeps the
        printer's value" contract observable -- in particular that a write which
        omits `rfid` leaves the tag association intact.

        Raises ValueError for a box or slot that does not exist, so a wrong
        `boxId` (for example an off-by-one from the card's card-position guess)
        fails loudly in testing instead of silently doing nothing.
        """
        try:
            box_id = int(payload.get("boxId"))
            slot_id = int(payload.get("id"))
        except (TypeError, ValueError):
            raise ValueError(
                f"modifyMaterial needs integer boxId and id, got {payload!r}"
            ) from None

        box = next((b for b in self._cfs_boxes if b.get("id") == box_id), None)
        if box is None:
            known = [b.get("id") for b in self._cfs_boxes]
            raise ValueError(f"no such box {box_id} (have {known})")

        materials = box.get("materials", [])
        slot = next((m for m in materials if m.get("id") == slot_id), None)
        if slot is None:
            known = [m.get("id") for m in materials]
            raise ValueError(f"no such slot {slot_id} in box {box_id} (have {known})")

        for key in self.MATERIAL_WRITABLE_KEYS:
            if key in payload:
                slot[key] = payload[key]

        return slot

    # ----------------------- tick/update loops -----------------------
    def _build_cfs_boxes(self) -> list:
        """CFS payload for the simulated printer.

        `default` mirrors what a real K2 + CFS reports, including Creality's
        seven-character colour strings (a pad character followed by RRGGBB).
        `edge` adds the awkward shapes that broke parsing in the past so they
        stay covered: an already-correct six-character colour, a slot with no
        vendor at all, a comma-separated multi-colour spool, an rfid-bearing
        slot, and an empty slot.
        """
        default_boxes = [
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
                        "minTemp": 190,
                        "maxTemp": 240,
                        "pressure": 0.04,
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
                        "minTemp": 190,
                        "maxTemp": 240,
                        "pressure": 0.04,
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
                        "minTemp": 190,
                        "maxTemp": 240,
                        "pressure": 0.04,
                        "percent": 100,
                        "state": 1,
                        "selected": 0,
                    },
                    {
                        # No temps or pressure: real slots often omit them, and the
                        # edit dialog has to cope with a partial prefill.
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

        if self.cfs_variant != "edge":
            return default_boxes

        # NOTE: `rfid` is what the integration reads for spool identity, but no
        # real-hardware dump confirming the field name has been seen yet -- it is
        # exercised here only so the code path is covered.
        default_boxes[1]["materials"] = [
            {
                "id": 0,
                "vendor": "Creality",
                "type": "PLA",
                "name": "Hyper PLA",
                "color": "#0000000",
                "rfid": "001001",
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
                "rfid": "001001",
                "percent": 80,
                "state": 1,
                "selected": 0,
            },
            {
                # already-correct six-character colour, and no vendor reported
                "id": 2,
                "type": "PETG",
                "name": "PETG",
                "color": "#1b04ae",
                "percent": 50,
                "state": 1,
                "selected": 0,
            },
            {
                # multi-colour spool
                "id": 3,
                "vendor": "Generic",
                "type": "PLA",
                "name": "Generic PLA Silk",
                "color": "#0ffa800,#0ff97e1",
                "percent": 30,
                "state": 1,
                "selected": 0,
            },
        ]
        # empty external slot
        default_boxes[0]["materials"] = [
            {"id": 0, "type": "", "name": "", "color": "", "percent": 0, "state": 0, "selected": 0}
        ]
        return default_boxes

    def _tick_temps(self):
        if self.deterministic:
            # Sit exactly on target: no convergence ramp, no oscillation.
            self._nozzle_temp = self._nozzle_temp_target
            self._bed_temp = self._bed_temp_target
            if self._cfg.get("box_sensor"):
                self._box_temp = self._box_temp_target or 26.0
            return

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

            if self.deterministic:
                # Assigned directly rather than via set_fan_pct, which would mark
                # the channel manual and then need un-marking again.
                if 0 not in self._manual_fans:
                    self._model_fan = 70
                if 1 not in self._manual_fans:
                    self._case_fan = 60
                if 2 not in self._manual_fans:
                    self._side_fan = 50
                self._pos_x, self._pos_y, self._pos_z = 100.0, 100.0, 10.0
                return

            # fans jitter; make side/model fans spike occasionally (bridges).
            # A fan driven by an explicit M106 holds its value so fan controls
            # stay observable while a simulated print runs.
            bridge_boost = 20 if random.random() < 0.1 else 0
            if 1 not in self._manual_fans:
                self._case_fan = int(min(100, max(0, random.gauss(60, 10))))
            if 0 not in self._manual_fans:
                self._model_fan = int(min(100, max(0, random.gauss(70 + bridge_boost, 15))))
            if 2 not in self._manual_fans:
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

            # fans -- names must match the printer's real telemetry, which the
            # integration reads for both the fan and number platforms
            "modelFanPct": self._model_fan,
            "caseFanPct": self._case_fan,
            "auxiliaryFanPct": self._side_fan,
            
            # extra
            "materialStatus": self._material_status,
            # The integration gates CFS discovery on this (__init__.py caches
            # _cached_cfs_detected from it, ws_client only polls boxsInfo blindly
            # while it is unknown). Real printers stream it, so stream it here too
            # or that whole fast path never gets exercised.
            "cfsConnect": 1 if self.cfs_enabled else 0,
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

        # Test overrides win, so a scenario can pin any field exactly.
        d.update(self._overrides)
        return d

    # ----------------------- test control -----------------------
    def apply_overrides(self, values: Dict[str, Any]) -> None:
        """Force telemetry fields (POST /test/set). None removes an override."""
        for key, value in (values or {}).items():
            if value is None:
                self._overrides.pop(key, None)
            else:
                self._overrides[key] = value

    def clear_overrides(self) -> None:
        self._overrides.clear()

    def set_cfs_materials(self, box_id: int, materials: list) -> bool:
        """Replace a CFS box's slot list (POST /test/cfs)."""
        for box in self._cfs_boxes:
            if box.get("id") == box_id:
                box["materials"] = materials
                return True
        return False


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
                    # M106 drives the fans; anything else is a no-op placeholder
                    state.handle_gcode(str(params.get("gcodeCmd") or ""))
                    handled = True
                elif "materialStatus" in params:
                    state.set_material_status(int(params.get("materialStatus") or 0))
                    handled = True
                elif "modifyMaterial" in params:
                    # The CFS write path. Mutating the stored slot is the point:
                    # it makes the next boxsInfo request reflect the write, which
                    # is what lets a test assert the whole round trip rather than
                    # just "the service did not raise".
                    payload = params.get("modifyMaterial") or {}
                    LOGGER.info("modifyMaterial received: %s", payload)
                    try:
                        updated = state.modify_material(payload)
                    except ValueError as exc:
                        # A real printer would not invent a slot, so neither do we.
                        LOGGER.warning("modifyMaterial rejected: %s", exc)
                    else:
                        LOGGER.info("modifyMaterial applied, slot is now: %s", updated)
                        await ws_safe_send(ws, state.get_cfs_info())
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


# An offer that never reaches `connected` would otherwise pin its peer
# connection forever, so the cleanup task gives up after this long.
PC_CONNECT_TIMEOUT = 60.0


class HttpServer:
    def __init__(self, host: str, port: int, cam_mode: str, width: int, height: int, fps: int, audio: bool,
                 video_source: str = "synthetic", ffmpeg_bin: str = "ffmpeg",
                 prefer_codec: str = "h264", state: "Optional[PrinterState]" = None) -> None:
        self.host = host
        self.port = port
        self.cam_mode = cam_mode  # "webrtc" or "mjpeg"
        self.width = width
        self.height = height
        self.fps = fps
        self.audio = audio
        self.video_source = video_source
        self.ffmpeg_bin = ffmpeg_bin
        # Real K-series printers stream H.264. aiortc would otherwise answer with
        # VP8 first, which Home Assistant's HLS pipeline cannot package -- the
        # playlist then blocks forever and the camera looks broken for reasons
        # that have nothing to do with the integration.
        self.prefer_codec = prefer_codec
        self.state = state
        self.app = web.Application()
        self.app.add_routes([
            web.get("/", self.handle_root),
            web.post(CALL_PATH, self.handle_call),
            web.get(CALL_PATH, self.handle_probe),
            web.get("/stream.mjpeg", self.handle_mjpeg),
            # Test-only control surface (not present on real printers)
            web.post("/test/set", self.handle_test_set),
            web.post("/test/reset", self.handle_test_reset),
            web.post("/test/cfs", self.handle_test_cfs),
            web.get("/test/state", self.handle_test_state),
        ])
        self._cleanup_tasks: set[asyncio.Task] = set()
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

    # ---------------- test-only control endpoints ----------------
    # These let a test pin telemetry exactly (progress, error codes, filament
    # runout, remaining time) instead of waiting for a simulated print to get
    # there. Real printers have no such endpoints.

    def _require_state(self) -> "PrinterState":
        if self.state is None:
            raise web.HTTPServiceUnavailable(text="no printer state bound")
        return self.state

    async def handle_test_set(self, request: web.Request):
        state = self._require_state()
        try:
            payload = await request.json()
        except Exception:
            return web.Response(status=400, text="expected a JSON object")
        if not isinstance(payload, dict):
            return web.Response(status=400, text="expected a JSON object")
        state.apply_overrides(payload)
        LOGGER.info("test/set applied: %s", json.dumps(payload))
        return web.json_response({"ok": True, "overrides": state._overrides})

    async def handle_test_reset(self, request: web.Request):
        state = self._require_state()
        state.clear_overrides()
        LOGGER.info("test/reset: overrides cleared")
        return web.json_response({"ok": True})

    async def handle_test_cfs(self, request: web.Request):
        state = self._require_state()
        try:
            payload = await request.json()
        except Exception:
            return web.Response(status=400, text="expected a JSON object")
        # A JSON array or bare string parses fine but has no .get, and a
        # non-integer box_id raises -- both surfaced as a 500 rather than telling
        # the caller their payload was wrong.
        if not isinstance(payload, dict):
            return web.Response(status=400, text="expected a JSON object")
        try:
            box_id = int(payload.get("box_id", 1))
        except (TypeError, ValueError):
            return web.Response(status=400, text="box_id must be an integer")
        materials = payload.get("materials")
        if not isinstance(materials, list):
            return web.Response(status=400, text="materials must be a list")
        if not state.set_cfs_materials(box_id, materials):
            return web.Response(status=404, text=f"no CFS box with id {box_id}")
        LOGGER.info("test/cfs: box %s -> %d slots", box_id, len(materials))
        return web.json_response({"ok": True})

    async def handle_test_state(self, request: web.Request):
        state = self._require_state()
        return web.json_response(state.snapshot())

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
            if LOGGER.isEnabledFor(logging.DEBUG):
                for line in offer_sdp.splitlines():
                    if line.startswith(("m=", "a=rtpmap", "a=fmtp")):
                        LOGGER.debug("offer   %s", line.strip())
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
            video_track = self._make_video_track(offer_sdp)
            pc.addTrack(video_track)
        if offer_has_audio and self.audio:
            pc.addTrack(SyntheticAudioTrack())

        sink = MediaBlackhole()

        @pc.on("track")
        async def on_track(track):
            await sink.start()
            sink.addTrack(track)

        self._apply_codec_preference(pc)

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
        if LOGGER.isEnabledFor(logging.DEBUG):
            for line in answer_sdp.splitlines():
                if line.startswith(("m=", "a=rtpmap", "a=fmtp", "a=sendrecv", "a=recvonly", "a=sendonly")):
                    LOGGER.debug("answer  %s", line.strip())
        payload = {"type": "answer", "sdp": answer_sdp}
        # Keep a strong reference: the loop only holds a weak one, so an
        # unreferenced task can be collected mid-flight.
        cleanup = asyncio.create_task(self._cleanup_pc(pc, sink))
        self._cleanup_tasks.add(cleanup)
        cleanup.add_done_callback(self._cleanup_tasks.discard)
        # Always respond as base64(JSON) for Creality/go2rtc compatibility
        out = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
        return web.Response(status=200, text=out, headers={"Content-Type": "text/plain"})

    def _make_video_track(self, offer_sdp: str) -> MediaStreamTrack:
        """Pick the video track that suits the negotiated codec.

        `auto` sends pre-encoded H.264 whenever the peer offers it (what real
        printers do, and the only thing Home Assistant can package into HLS) and
        falls back to synthetic frames otherwise.
        """
        source = (self.video_source or "auto").lower()
        peer_wants_h264 = "H264/90000" in offer_sdp or "h264/90000" in offer_sdp
        want_h264 = (self.prefer_codec or "").lower() == "h264"

        if source in ("auto", "h264") and peer_wants_h264 and want_h264:
            if H264PassthroughTrack.available(self.ffmpeg_bin):
                return H264PassthroughTrack(
                    self.width, self.height, self.fps, ffmpeg_bin=self.ffmpeg_bin
                )
            LOGGER.warning(
                "ffmpeg not found; falling back to aiortc's H.264 encoder, whose "
                "keyframe interval is too long for Home Assistant's HLS pipeline"
            )
        elif source == "h264":
            # An explicit --video-source h264 that silently produced synthetic
            # frames looked like the passthrough was broken. Say which condition
            # actually failed.
            LOGGER.warning(
                "--video-source h264 not honoured: peer_offers_h264=%s "
                "prefer_codec=%s (needs h264); using synthetic video",
                peer_wants_h264, self.prefer_codec,
            )

        if source == "ffmpeg":
            # Probe the binary here: FFmpegVideoTrack.__init__ does not spawn it,
            # so a missing ffmpeg only failed later inside recv() -- the WebRTC
            # request succeeded and the track then died instead of falling back.
            if not shutil.which(self.ffmpeg_bin):
                LOGGER.warning(
                    "ffmpeg (%s) not found on PATH; using synthetic video",
                    self.ffmpeg_bin,
                )
            else:
                try:
                    return FFmpegVideoTrack(
                        self.width, self.height, self.fps, ffmpeg_bin=self.ffmpeg_bin
                    )
                except Exception as exc:
                    LOGGER.warning("FFmpeg not available (%s), using synthetic video", exc)

        return SyntheticVideoTrack(self.width, self.height, self.fps)

    def _apply_codec_preference(self, pc: RTCPeerConnection) -> None:
        """Answer with the preferred video codec first.

        Real Creality hardware sends H.264. aiortc's default order puts VP8
        first, and a VP8 stream cannot be packaged into HLS by Home Assistant's
        `stream` component, so the HLS playlist blocks forever and the camera
        looks broken for reasons unrelated to the integration.

        This reorders `transceiver._codecs` rather than calling the public
        `setCodecPreferences()`: aiortc consumes preferences inside
        `setRemoteDescription()`, but an answerer's transceivers are *created* by
        that same call, so there is no point at which the public API can be used.
        The list order drives both the answer SDP and the codec the sender picks
        (`codecs[0]`).
        """
        want = (self.prefer_codec or "").lower()
        if want in ("", "auto"):
            return
        target = f"video/{want}"

        for transceiver in pc.getTransceivers():
            if transceiver.kind != "video":
                continue
            codecs = list(getattr(transceiver, "_codecs", None) or [])
            if not codecs:
                continue

            def is_rtx(codec) -> bool:
                return codec.mimeType.lower() == "video/rtx"

            wanted = [c for c in codecs if c.mimeType.lower() == target]
            if not wanted:
                LOGGER.warning(
                    "Preferred codec %s not offered by the peer; keeping %s",
                    want, codecs[0].mimeType,
                )
                continue

            # Keep each codec's retransmission entry next to it.
            wanted_pts = {c.payloadType for c in wanted}
            wanted_rtx = [c for c in codecs if is_rtx(c)
                          and c.parameters.get("apt") in wanted_pts]
            keep = {id(c) for c in wanted + wanted_rtx}
            rest = [c for c in codecs if id(c) not in keep]

            transceiver._codecs = wanted + wanted_rtx + rest
            LOGGER.debug(
                "video codecs reordered -> %s",
                ", ".join(f"{c.mimeType}/{c.payloadType}" for c in transceiver._codecs[:4]),
            )

    async def _cleanup_pc(self, pc: RTCPeerConnection, sink: MediaBlackhole):
        """Tear the session down when it actually ends, not on a fixed timer.

        The previous unconditional 60 s sleep-then-close killed healthy sessions,
        which made every consumer (go2rtc included) reconnect in a loop.
        """
        closed = asyncio.Event()
        dead_states = ("closed", "failed", "disconnected")

        @pc.on("connectionstatechange")
        def _watch():
            if pc.connectionState in dead_states:
                closed.set()

        # The handler is registered after the connection was created, so a
        # connection that already died never fires it -- check the state once
        # here or this task waits forever and leaks pc and sink.
        if pc.connectionState in dead_states:
            closed.set()

        try:
            if pc.connectionState in ("new", "connecting"):
                # An offer that never completes would otherwise pin the peer
                # connection for the lifetime of the process.
                try:
                    await asyncio.wait_for(closed.wait(), timeout=PC_CONNECT_TIMEOUT)
                except asyncio.TimeoutError:
                    if pc.connectionState in ("new", "connecting"):
                        LOGGER.info(
                            "PC(%s) never established within %.0fs; tearing it down",
                            id(pc), PC_CONNECT_TIMEOUT,
                        )
                    else:
                        await closed.wait()
            else:
                await closed.wait()
        except asyncio.CancelledError:
            pass
        finally:
            try:
                await sink.stop()
            except Exception:
                pass
            try:
                await pc.close()
            except Exception:
                pass
            LOGGER.info("PC(%s) session cleaned up", id(pc))

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
        deterministic=getattr(args, "deterministic", False),
        cfs_variant=getattr(args, "cfs_variant", "default"),
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
        video_source=getattr(args, "video_source", "auto"),
        ffmpeg_bin=getattr(args, "ffmpeg_bin", "ffmpeg"),
        prefer_codec=getattr(args, "prefer_codec", "h264"),
        state=state,
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
    p.add_argument("--video-source", choices=["auto", "h264", "synthetic", "ffmpeg"],
                   default="auto",
                   help="Video generator. 'auto' (default) sends pre-encoded H.264 "
                        "with a 1s GOP when the peer offers H.264 -- required for "
                        "Home Assistant's HLS/recording pipeline -- and falls back "
                        "to synthetic frames otherwise.")
    p.add_argument("--ffmpeg-bin", default="ffmpeg", help="Path to ffmpeg binary (for --video-source=ffmpeg)")
    p.add_argument("--deterministic", action="store_true",
                   help="Remove all randomness (temp oscillation, fan jitter, XYZ "
                        "drift) so telemetry is reproducible between runs. Use this "
                        "when diffing entity states across integration versions. "
                        "Print-progress fields stay derived from elapsed wall-clock "
                        "time and so still depend on when you sample them.")
    p.add_argument("--cfs-variant", choices=["default", "edge"], default="default",
                   help="'edge' adds awkward CFS payloads: an already-correct 6-char "
                        "colour, a slot with no vendor, a multi-colour spool, rfid "
                        "values and an empty external slot.")
    p.add_argument("--prefer-codec", choices=["h264", "vp8", "auto"], default="h264",
                   help="Video codec to answer with first. H.264 matches real "
                        "K-series printers and is required for Home Assistant HLS; "
                        "'auto' keeps aiortc's default order (VP8 first).")
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
