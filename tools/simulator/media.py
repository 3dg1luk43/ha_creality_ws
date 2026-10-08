"""The camera picture: a short loop, rendered once and stored with the simulator.

Encoding video was the heaviest thing the simulator did, per viewer and per
frame, and low-end hosts could not keep up. So the picture is generated ahead
of time, committed under `media/`, and only read at runtime:

* `loop.h264`: Annex-B H.264, baseline, a keyframe every second with SPS/PPS
  before each one (real Creality cameras send one about every second; Home
  Assistant's HLS pipeline and go2rtc need them that often), for WebRTC.
* `loop.mjpeg`: the same loop as concatenated JPEG frames, for the
  mjpg-streamer on :8080.

Regenerate with `python -m simulator.media` from tools/ (needs ffmpeg with
libx264). The runtime needs neither ffmpeg nor Pillow for these.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

MEDIA_DIR = Path(__file__).resolve().parent / "media"
H264_CLIP = MEDIA_DIR / "loop.h264"
MJPEG_LOOP = MEDIA_DIR / "loop.mjpeg"

WIDTH, HEIGHT, FPS, SECONDS = 640, 360, 15, 4
# MJPEG is a frame per JPEG with no compression between frames, so its loop is
# kept lighter: fewer frames, stronger quantisation.
MJPEG_FPS, MJPEG_QUALITY = 8, 12


def h264_command(ffmpeg_bin: str, out: str, *, width: int = WIDTH, height: int = HEIGHT,
                 fps: int = FPS, seconds: int = SECONDS) -> list[str]:
    return [
        ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size={width}x{height}:rate={fps}",
        "-t", str(seconds),
        "-c:v", "libx264", "-preset", "veryslow", "-tune", "zerolatency",
        "-profile:v", "baseline", "-pix_fmt", "yuv420p", "-crf", "30",
        # keyframe every second, no B-frames, SPS/PPS before every IDR so a
        # consumer joining mid-clip can start decoding immediately
        "-x264-params", f"keyint={fps}:min-keyint={fps}:scenecut=0:repeat-headers=1",
        "-bsf:v", "dump_extra",
        "-f", "h264", out,
    ]


def mjpeg_command(ffmpeg_bin: str, out: str) -> list[str]:
    return [
        ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size={WIDTH}x{HEIGHT}:rate={MJPEG_FPS}",
        "-t", str(SECONDS), "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", str(MJPEG_QUALITY), out,
    ]


def split_jpegs(data: bytes) -> list[bytes]:
    """Cut a concatenation of JPEG files into frames."""
    frames, pos = [], 0
    while True:
        soi = data.find(b"\xff\xd8", pos)
        eoi = data.find(b"\xff\xd9", soi + 2) if soi != -1 else -1
        if eoi == -1:
            return frames
        frames.append(bytes(data[soi:eoi + 2]))
        pos = eoi + 2


def mjpeg_frames() -> list[bytes]:
    """The stored MJPEG loop, or [] when it is missing."""
    return split_jpegs(MJPEG_LOOP.read_bytes()) if MJPEG_LOOP.exists() else []


def generate(ffmpeg_bin: str = "ffmpeg") -> None:
    if not shutil.which(ffmpeg_bin):
        raise SystemExit(f"{ffmpeg_bin} not found; it needs libx264")
    MEDIA_DIR.mkdir(exist_ok=True)
    subprocess.run(h264_command(ffmpeg_bin, str(H264_CLIP)), check=True)
    subprocess.run(mjpeg_command(ffmpeg_bin, str(MJPEG_LOOP)), check=True)
    for path in (H264_CLIP, MJPEG_LOOP):
        print(f"{path.name}: {path.stat().st_size} bytes")
    print(f"{len(mjpeg_frames())} JPEG frames")


if __name__ == "__main__":
    generate(*(sys.argv[1:2] or ["ffmpeg"]))
