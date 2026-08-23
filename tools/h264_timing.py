"""Timestamp arithmetic for the simulator's pre-encoded H.264 clip.

Split out of ``creality_printer_test_server`` so it can be imported and tested
without aiortc/av, which CI does not install. The clip loops forever, so the
only property that matters is that timestamps keep increasing across the seam.
"""

from __future__ import annotations

from typing import Iterable, Iterator, TypeVar

T = TypeVar("T")


def pts_step(fps: int) -> int:
    """Ticks between frames on the 90 kHz RTP clock."""
    return int(90000 / fps)


def assign_clip_timestamps(packets: Iterable[T], fps: int) -> tuple[list[T], int]:
    """Stamp the packets worth keeping and return them with the clip length.

    Zero-size packets are dropped (ffmpeg emits them on a flush), and the
    timestamps count *kept* packets. Deriving them from the demuxed position
    instead left the last pts short of the clip length, so the wrap advanced the
    offset by less than the real span and the next loop overlapped the previous
    one rather than continuing past it.

    Returns ``(kept, clip_duration_pts)`` where ``clip_duration_pts`` is exactly
    one step past the last packet's pts, which is what makes the seam seamless.
    """
    step = pts_step(fps)
    kept: list[T] = []
    for packet in packets:
        if getattr(packet, "size", 0) == 0:
            continue
        packet.pts = len(kept) * step
        packet.dts = packet.pts
        kept.append(packet)
    return kept, len(kept) * step
