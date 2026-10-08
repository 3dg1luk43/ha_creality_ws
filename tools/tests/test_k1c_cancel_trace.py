"""A real K1C print, cancelled during its start-of-print self-test (R29, #124).

Replays `fixtures/k1c_cancel_during_self_test.jsonl` -- what a K1C on firmware
1.3.5.22 streamed, frame by frame -- through the real coordinator, merged the
way the client merges frames and on the recorded clock. Captured to settle what
#124 left open: whether a cancel during self-test is still announced (it is,
once the printer reports the stop), whether self-test can stick at 1-99 after
a cancel (no: it went to 100 about 30 s later) and whether self-test shows up
as state 2 (not on this printer: state 1).
"""

import json
from pathlib import Path

from test_notification_live import CLEAR_NOTIFICATION_MARKER, _coordinator, _frame_calls

from custom_components.ha_creality_ws.utils import coerce_numbers, derive_print_state

TRACE = Path(__file__).parent / "fixtures" / "k1c_cancel_during_self_test.jsonl"


def _replay():
    coord, hass = _coordinator(targets=("notify.mobile_app_pixel",))
    coord._notify_primed = False
    merged: dict = {}
    statuses: list[str] = []
    pushes: list[tuple[float, str, dict]] = []
    raw_states: list[tuple[float, int]] = []
    lines = TRACE.read_text(encoding="utf-8").splitlines()[1:]
    for line in lines:
        rec = json.loads(line)
        hass.loop.now = 1000.0 + rec["t"]
        merged.update(coerce_numbers(rec["frame"]))
        if "state" in rec["frame"]:
            raw_states.append((rec["t"], merged["state"]))
        for call in _frame_calls(coord, hass, **merged):
            pushes.append((rec["t"], call[2]["message"], call[2].get("data") or {}))
        status = derive_print_state(coord.data)
        if not statuses or statuses[-1] != status:
            statuses.append(status)
    return statuses, pushes, raw_states, hass.events


def test_the_status_follows_the_job_through_the_cancel():
    """State 7, while the cancel finishes the move under way, read "idle"."""
    statuses, _pushes, _raw, _events = _replay()
    assert statuses == ["idle", "printing", "self-testing", "processing", "stopped"]


def test_the_cancel_is_announced_once_when_the_printer_reports_the_stop():
    _statuses, pushes, raw_states, events = _replay()
    stopped_at = next(t for t, state in raw_states if state == 4)
    banners = [(t, m) for t, m, d in pushes if m != CLEAR_NOTIFICATION_MARKER and "stopped" in m]
    assert [t for t, _m in banners] == [stopped_at]
    assert "0%" in banners[0][1]
    assert [name for name, _data in events] == [
        "ha_creality_ws_print_started", "ha_creality_ws_print_stopped",
    ]


def test_this_firmware_reports_self_test_as_state_1_and_ends_it_at_100():
    """The two #124 questions, answered for this printer."""
    lines = TRACE.read_text(encoding="utf-8").splitlines()[1:]
    merged: dict = {}
    states_during_self_test = set()
    self_test_values = []
    for line in lines:
        frame = json.loads(line)["frame"]
        merged.update(coerce_numbers(frame))
        if "withSelfTest" in frame:
            self_test_values.append(merged["withSelfTest"])
        if 1 <= (merged.get("withSelfTest") or 0) <= 99:
            states_during_self_test.add(merged.get("state"))
    assert self_test_values[-1] == 100
    assert 2 not in states_during_self_test and 1 in states_during_self_test
