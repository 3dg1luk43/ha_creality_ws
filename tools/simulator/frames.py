"""What goes on the wire: number formatting, deltas, and the session's view.

A real printer sends one full frame when a client connects and from then on
only the keys that changed, every second or so: `{"nozzleTemp":"31.120000"}`.
The integration merges them into one cumulative state, which is exactly where
#121 came from (a blank sent once stays until that key changes again), so the
simulator has to do the same to reproduce it.

`full` mode, the old simulator's behaviour, re-sends everything every couple of
seconds and stays available for comparison.
"""
from __future__ import annotations

import json
from typing import Any

from .profiles import STRING_NUMBER_FIELDS


def format_frame(values: dict[str, Any], *, string_numbers: bool) -> dict[str, Any]:
    """Write numbers the way the firmware does.

    The K1 family sends temperatures, flow and a few tuning values as
    six-decimal strings ("31.030000"); everything else stays numeric.
    """
    if not string_numbers:
        return dict(values)
    out = dict(values)
    for key in STRING_NUMBER_FIELDS:
        value = out.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out[key] = f"{float(value):.6f}"
    return out


def changed(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """The keys whose value differs, compared by their JSON form."""
    out = {}
    for key, value in current.items():
        if key not in previous or not _same(previous[key], value):
            out[key] = value
    return out


def _same(a: Any, b: Any) -> bool:
    if type(a) is not type(b):
        return False
    if isinstance(a, (dict, list)):
        return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    return a == b


class FrameStream:
    """One client's view of the telemetry: what it has been sent so far."""

    def __init__(self, mode: str = "delta"):
        self.mode = mode
        self.sent: dict[str, Any] = {}
        self.frames_sent = 0

    def first(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        self.sent = dict(snapshot)
        self.frames_sent += 1
        return snapshot

    def next(self, snapshot: dict[str, Any]) -> dict[str, Any] | None:
        """The frame to send now, or None when nothing changed."""
        if self.mode == "full":
            self.sent = dict(snapshot)
            self.frames_sent += 1
            return snapshot
        delta = changed(self.sent, snapshot)
        if not delta:
            return None
        self.sent.update(delta)
        self.frames_sent += 1
        return delta

    def resend(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        """A full frame on request (ReqPrinterPara), resetting the baseline."""
        return self.first(snapshot)
