"""The printer's side of the WebSocket conversation.

Kept apart from the transport so a test can feed it messages and read the
replies without a socket. Every message in and out is also written to a log
the control UI shows, which is what makes "did the integration send SET_PIN?"
an assertion rather than a guess.

What the printer does, as seen from a real K1C:
* the client sends `{"ModeCode":"heart_beat","msg":<ISO time>}` every few
  seconds and the printer answers with the bare text `ok`;
* `get` requests are answered with just what was asked for: `boxsInfo`, the
  G-code listing, `{current_object, excluded_objects, objects}`, the probe
  matrix, or for `ReqPrinterPara` a full frame;
* `set` requests change the printer; the change shows in the next frames.
"""
from __future__ import annotations

import collections
import json
import logging
import time
from typing import Any

from .frames import FrameStream
from .printer import PrinterState

LOGGER = logging.getLogger("simulator.protocol")


class MessageLog:
    """The last messages in both directions, for the control UI and tests."""

    def __init__(self, size: int = 400):
        self.items: collections.deque[dict[str, Any]] = collections.deque(maxlen=size)
        self.seq = 0

    def add(self, direction: str, peer: str, data: Any, note: str = "") -> None:
        self.seq += 1
        text = data if isinstance(data, str) else json.dumps(data, separators=(",", ":"))
        self.items.append({
            "seq": self.seq,
            "t": time.time(),
            "dir": direction,
            "peer": peer,
            "size": len(text),
            # Long frames are cut for display; the size says how long they were.
            "data": text if len(text) <= 4000 else text[:4000] + "...",
            "note": note,
        })

    def since(self, seq: int) -> list[dict[str, Any]]:
        return [item for item in self.items if item["seq"] > seq]

    def commands(self) -> list[dict[str, Any]]:
        return [item for item in self.items if item["dir"] == "in"]


def _num(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    return float(value)


class Protocol:
    """Handles one client's messages against the shared printer."""

    def __init__(self, state: PrinterState, log: MessageLog, *, reject_sets: bool = False):
        self.state = state
        self.log = log
        self.reject_sets = reject_sets
        self._peer = "?"

    def handle(self, raw: Any, stream: FrameStream, peer: str = "?") -> list[Any]:
        """Replies to one incoming message, in order. Never raises on bad input:
        a real printer ignores what it does not understand, and the old
        simulator dropped the connection instead."""
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", "ignore")
        if raw == "ok":
            return []
        try:
            msg = json.loads(raw)
        except (TypeError, ValueError):
            self.log.add("in", peer, str(raw)[:500], "not JSON, ignored")
            return []
        self.log.add("in", peer, msg)
        self._peer = peer
        if not isinstance(msg, dict):
            return []

        if msg.get("ModeCode") == "heart_beat":
            return ["ok"]

        params = msg.get("params")
        if not isinstance(params, dict):
            return []
        method = msg.get("method")
        if method == "get":
            return self._get(params, stream)
        if method == "set":
            return self._set(params, stream)
        return []

    # ------------------------------------------------------------- get
    def _get(self, params: dict[str, Any], stream: FrameStream) -> list[Any]:
        state = self.state
        out: list[Any] = []
        for key in params:
            if key == "boxsInfo":
                if state.profile.cfs_capable:
                    out.append(state.get_cfs_info())
            elif key == "reqGcodeFile":
                listing = state.get_gcode_file_info()
                if listing is not None:
                    out.append(listing)
            elif key == "reqPrintObjects":
                out.append(state.print_objects())
            elif key == "reqProbedMatrix":
                out.append(state.probed_matrix())
            else:
                # ReqPrinterPara and anything unknown: the whole state.
                out.append(stream.resend(state.snapshot()))
        return out

    # ------------------------------------------------------------- set
    def _set(self, params: dict[str, Any], stream: FrameStream) -> list[Any]:
        state = self.state
        if self.reject_sets:
            self.log.add("note", self._peer, {"rejected": list(params)}, "set ignored (fault)")
            return []
        out: list[Any] = []
        for key, value in params.items():
            try:
                reply = self._apply(key, value)
            except (TypeError, ValueError) as exc:
                LOGGER.warning("set %s=%r rejected: %s", key, value, exc)
                self.log.add("note", self._peer, {key: value}, f"rejected: {exc}")
                continue
            if reply is not None:
                out.append(reply)
        delta = stream.next(state.snapshot())
        if delta:
            out.append(delta)
        return out

    def _apply(self, key: str, value: Any) -> dict[str, Any] | None:
        state = self.state
        if key == "pause":
            state.set_pause(bool(int(_num(value))))
        elif key == "stop":
            state.stop()
        elif key == "nozzleTempControl":
            state.set_nozzle_temp(_num(value))
        elif key == "bedTempControl":
            temp = value.get("val", 0) if isinstance(value, dict) else value
            state.set_bed_temp(_num(temp))
        elif key in ("boxTempControl", "targetBoxTemp"):
            state.set_box_temp(_num(value))
        elif key in ("lightSw", "light"):
            state.set_light(bool(int(_num(value))))
        elif key == "autohome":
            state.set_autohome(str(value or "X Y Z"))
        elif key == "setFeedratePct":
            state.set_feedrate(_num(value, 100))
        elif key == "setFlowratePct":
            state.set_flowrate(_num(value, 100))
        elif key == "gcodeCmd":
            if not state.handle_gcode(str(value or "")):
                self.log.add("note", self._peer, {key: value}, "accepted, no effect simulated")
        elif key == "materialStatus":
            # Not a real command; the old simulator's shortcut, kept for scripts.
            state.set_material_status(int(_num(value)))
        elif key == "modifyMaterial":
            # The CFS write path. Mutating the stored slot is the point: the
            # reply reflects the write, so a test can assert the round trip.
            payload = value if value is not None else {}
            LOGGER.info("modifyMaterial received: %s", payload)
            try:
                updated = state.modify_material(payload)
            except ValueError as exc:
                # A real printer would not invent a slot, so neither do we.
                LOGGER.warning("modifyMaterial rejected: %s", exc)
                self.log.add("note", self._peer, payload if isinstance(payload, dict) else str(payload),
                             f"modifyMaterial rejected: {exc}")
                return None
            LOGGER.info("modifyMaterial applied, slot is now: %s", updated)
            return state.get_cfs_info()
        else:
            self.log.add("note", self._peer, {key: value}, "unknown set key, ignored")
        return None
