"""Cameras: WebRTC signalling on :8000 and an mjpg-streamer on :8080.

The only module that needs aiortc, av, numpy and Pillow; everything is imported
lazily by the servers, so the rest of the simulator runs without them.

The media tracks and the signalling handler are the old simulator's, moved
here unchanged in behaviour: they are what made the WebRTC and HLS paths work
against Home Assistant (pre-encoded H.264 with a 1 s GOP, H.264 answered
first).
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import json
import logging
import math
import os
import shutil
import tempfile
import time
from fractions import Fraction
from typing import Any

import av
import numpy as np
from aiohttp import web
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaBlackhole

from . import media
from .h264_timing import assign_clip_timestamps

LOGGER = logging.getLogger("simulator.cameras")

CALL_PATH = "/call/webrtc_local"

# An offer that never reaches `connected` would otherwise pin its peer
# connection forever, so the cleanup task gives up after this long.
PC_CONNECT_TIMEOUT = 60.0


# -----------------------------------------------------------------------------
# Media tracks
# -----------------------------------------------------------------------------

class SyntheticVideoTrack(MediaStreamTrack):
    kind = "video"

    def __init__(self, width: int = 1920, height: int = 1080, fps: int = 30):
        super().__init__()
        self.width = width
        self.height = height
        self.fps = fps
        self._frame_dur = 1 / fps
        self._t0 = time.monotonic()
        self._video_pts = 0
        self._video_time_base = Fraction(1, fps)

    async def recv(self):
        # Maintain nominal frame pacing without blocking the event loop
        await asyncio.sleep(self._frame_dur)
        t = time.monotonic() - self._t0
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


_CLIP_CACHE: dict[tuple, tuple[list[Any], int]] = {}
_CLIP_LOCK = asyncio.Lock()


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
        self._t0: float | None = None

    @staticmethod
    def available(ffmpeg_bin: str = "ffmpeg") -> bool:
        return media.H264_CLIP.exists() or shutil.which(ffmpeg_bin) is not None

    async def prepare(self) -> None:
        """Encode the clip now, so a failure is visible before the answer.

        `available()` only proves the ffmpeg *binary* exists. The encode needs
        libx264, which builds like Fedora's `ffmpeg-free` omit -- and without
        this the first failure happened inside `recv()`, in aiortc's sender
        task, long after the SDP answer had gone out. The session connected and
        carried no video, and the synthetic fallback the README promises never
        ran because the track had already been chosen.
        """
        await self._ensure_clip()

    async def _ensure_clip(self) -> None:
        """The clip, encoded once per size and rate and shared by every session.

        Encoding is the expensive part, and it used to run again for every
        viewer; a loop of a few seconds is all a test needs.
        """
        if self._packets:
            return
        key = (self.ffmpeg_bin, self.width, self.height, self.fps, self.seconds)
        async with _CLIP_LOCK:
            cached = _CLIP_CACHE.get(key)
            if cached is None:
                cached = await self._encode_clip()
                _CLIP_CACHE[key] = cached
        self._packets, self._clip_duration_pts = cached

    async def _encode_clip(self) -> tuple[list[Any], int]:
        """The clip stored with the simulator (see media.py), read and looped
        with no encoding at all; ffmpeg only when it is missing."""
        if media.H264_CLIP.exists():
            return await asyncio.to_thread(self._demux, str(media.H264_CLIP), media.FPS)
        if not shutil.which(self.ffmpeg_bin):
            raise RuntimeError("no stored clip and no ffmpeg binary")
        tmp = tempfile.NamedTemporaryFile(suffix=".h264", delete=False)
        tmp.close()
        try:
            proc = await asyncio.create_subprocess_exec(
                *media.h264_command(self.ffmpeg_bin, tmp.name, width=self.width, height=self.height,
                                    fps=self.fps, seconds=self.seconds),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, err = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(f"ffmpeg failed: {err.decode(errors='replace')[:200]}")
            return await asyncio.to_thread(self._demux, tmp.name, self.fps)
        finally:
            with contextlib.suppress(OSError):
                os.unlink(tmp.name)

    def _demux(self, path: str, fps: int) -> tuple[list[Any], int]:
        container = av.open(path)
        try:
            stream = container.streams.video[0]
            # Annex-B raw H.264 has no container timestamps; synthesise them.
            # Timestamping lives in h264_timing so it can be tested without
            # aiortc/av; see the note there on why kept-vs-demuxed matters.
            packets, duration_pts = assign_clip_timestamps(container.demux(stream), fps)
            for packet in packets:
                packet.time_base = self._time_base
        finally:
            container.close()
        if not packets:
            raise RuntimeError("H.264 clip contained no packets")
        LOGGER.info("H.264 loop ready: %d packets, keyframe every second (%s)", len(packets), path)
        return packets, duration_pts

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
        self._proc: asyncio.subprocess.Process | None = None
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
# WebRTC signalling (POST /call/webrtc_local)
# -----------------------------------------------------------------------------


class WebRtcSignalling:
    """Answers the printer's single-shot WebRTC offer."""

    def __init__(self, *, width: int, height: int, fps: int, audio: bool,
                 video_source: str = "auto", ffmpeg_bin: str = "ffmpeg",
                 prefer_codec: str = "h264") -> None:
        self.width = width
        self.height = height
        self.fps = fps
        self.audio = audio
        self.video_source = video_source
        self.ffmpeg_bin = ffmpeg_bin
        # Real K-series printers stream H.264. aiortc would otherwise answer with
        # VP8 first, which Home Assistant's HLS pipeline cannot package.
        self.prefer_codec = prefer_codec
        self._cleanup_tasks: set[asyncio.Task] = set()
        self.sessions = 0

    async def handle_call(self, request: web.Request):
        self.sessions += 1
        # Accept multiple payload formats and always answer as base64 JSON (Creality style)
        # Supported inputs:
        #  - base64(JSON{"type":"offer","sdp":"v=0..."})   [go2rtc creality client]
        #  - JSON {"type":"offer","sdp":"v=0..."}
        #  - base64("v=0...") or plain "v=0..." (raw SDP)
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
            video_track = await self._make_video_track(offer_sdp)
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

    async def _make_video_track(self, offer_sdp: str) -> MediaStreamTrack:
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
                track = H264PassthroughTrack(
                    self.width, self.height, self.fps, ffmpeg_bin=self.ffmpeg_bin
                )
                try:
                    # Before the answer goes out, not on the first recv(): see
                    # `prepare`. An ffmpeg without libx264 fails here, where
                    # there is still somewhere to fall back to.
                    await track.prepare()
                except Exception as exc:  # pragma: no cover - build-specific
                    LOGGER.warning(
                        "H.264 clip could not be encoded (%s); falling back to "
                        "synthetic video. An ffmpeg without libx264 does this.",
                        exc,
                    )
                else:
                    return track
            else:
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


# -----------------------------------------------------------------------------
# MJPEG: an mjpg-streamer on :8080
# -----------------------------------------------------------------------------

BOUNDARY = "boundarydonotcross"


class MjpegCamera:
    """What a K1's mjpg-streamer serves: `/?action=stream` and `?action=snapshot`.

    The headers are the real ones (`Server: MJPG-Streamer/0.2`, boundary
    `boundarydonotcross`); the old simulator served `:8000/stream.mjpeg` with a
    boundary its header and body disagreed on, so the integration's MJPEG
    camera never worked against it (R75).

    The picture is the loop stored with the simulator (media.py), replayed to
    every client; ffmpeg or Pillow render one only when it is missing. Drawing
    and encoding each frame per viewer was the heaviest thing the simulator
    did, and low-end hosts could not keep up.
    """

    LOOP_SECONDS = 4

    def __init__(self, *, width: int, height: int, fps: int, video_source: str = "auto",
                 ffmpeg_bin: str = "ffmpeg") -> None:
        self.width = width
        self.height = height
        self.fps = max(1, fps)
        self.video_source = video_source
        self.ffmpeg_bin = ffmpeg_bin
        self.clients = 0
        self._frames: list[bytes] = []
        self._lock = asyncio.Lock()
        self._t0 = time.monotonic()

    async def prepare(self) -> None:
        async with self._lock:
            if self._frames:
                return
            frames: list[bytes] = []
            if self.video_source in ("auto", "h264"):
                frames = await asyncio.to_thread(media.mjpeg_frames)
                if frames:
                    self.fps = media.MJPEG_FPS
            if not frames and self.video_source != "synthetic" and shutil.which(self.ffmpeg_bin):
                try:
                    frames = await self._render_ffmpeg()
                except Exception as exc:  # pylint: disable=broad-except
                    LOGGER.warning("ffmpeg could not render the MJPEG loop (%s); using Pillow", exc)
            if not frames:
                frames = await asyncio.to_thread(self._render_pillow)
            self._frames = frames
            LOGGER.info("MJPEG loop ready: %d frames, %d s at %dx%d", len(frames), self.LOOP_SECONDS,
                        self.width, self.height)

    async def _render_ffmpeg(self) -> list[bytes]:
        proc = await asyncio.create_subprocess_exec(
            self.ffmpeg_bin, "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc2=size={self.width}x{self.height}:rate={self.fps}",
            "-t", str(self.LOOP_SECONDS), "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "5", "pipe:1",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        data, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(err.decode(errors="replace")[:200])
        return media.split_jpegs(data)

    def _render_pillow(self) -> list[bytes]:
        from PIL import Image  # optional dependency, only for MJPEG

        track = SyntheticVideoTrack(self.width, self.height, self.fps)
        frames = []
        for i in range(self.fps * self.LOOP_SECONDS):
            buf = io.BytesIO()
            Image.fromarray(track._bars(self.width, self.height, i / self.fps)).save(buf, format="JPEG", quality=75)
            frames.append(buf.getvalue())
        return frames

    async def frame(self) -> bytes:
        """The loop's current frame, the same one for every client."""
        await self.prepare()
        index = int((time.monotonic() - self._t0) * self.fps) % len(self._frames)
        return self._frames[index]

    async def handle(self, request: web.Request) -> web.StreamResponse:
        action = request.query.get("action", "stream")
        if action == "snapshot":
            jpg = await self.frame()
            return web.Response(body=jpg, headers={
                "Server": "MJPG-Streamer/0.2", "Content-Type": "image/jpeg",
                "Cache-Control": "no-store, no-cache, must-revalidate",
            })
        if action != "stream":
            return web.Response(status=404, text="unknown action", headers={"Server": "MJPG-Streamer/0.2"})
        return await self.stream(request, BOUNDARY, server="MJPG-Streamer/0.2")

    async def stream(self, request: web.Request, boundary: str, server: str | None = None) -> web.StreamResponse:
        headers = {
            "Content-Type": f"multipart/x-mixed-replace;boundary={boundary}",
            "Cache-Control": "no-store, no-cache, must-revalidate, pre-check=0, post-check=0, max-age=0",
            "Pragma": "no-cache",
        }
        if server:
            headers["Server"] = server
        response = web.StreamResponse(status=200, headers=headers)
        await response.prepare(request)
        self.clients += 1
        try:
            while True:
                started = time.monotonic()
                jpg = await self.frame()
                await response.write(
                    f"--{boundary}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpg)}\r\n"
                    f"X-Timestamp: {time.time():.6f}\r\n\r\n".encode("ascii") + jpg + b"\r\n"
                )
                await asyncio.sleep(max(0.0, 1.0 / self.fps - (time.monotonic() - started)))
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            self.clients -= 1
            with contextlib.suppress(Exception):
                await response.write_eof()
        return response

    async def handle_legacy(self, request: web.Request) -> web.StreamResponse:
        """The old simulator's `:8000/stream.mjpeg`, for anything still using it."""
        return await self.stream(request, "frame")


async def warm(camera: str, *, width: int, height: int, fps: int, ffmpeg_bin: str, video_source: str,
               mjpeg: "MjpegCamera | None" = None) -> None:
    """Render the camera's loop in the background at power on, so the first
    viewer does not wait for it."""
    try:
        if camera == "mjpeg" and mjpeg is not None:
            await mjpeg.prepare()
        elif camera == "webrtc" and video_source in ("auto", "h264") and H264PassthroughTrack.available(ffmpeg_bin):
            await H264PassthroughTrack(width, height, fps, ffmpeg_bin=ffmpeg_bin).prepare()
    except Exception as exc:  # pylint: disable=broad-except
        LOGGER.warning("camera loop not pre-rendered: %s", exc)
