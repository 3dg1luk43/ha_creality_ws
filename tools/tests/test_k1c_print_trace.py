"""A real K1C print, start and end (R81, R83), replayed through the real coordinator.

`fixtures/k1c_print_end.jsonl` is the last 90 s of a 38-minute print on firmware
1.3.5.22, captured on 2026-10-25. The printer reports 100% while it is still
printing with 86 s left, goes back to 99 fifteen seconds later, and only then
ends the job (state 2, no time left). The integration announced the completion
on the first 100 and the status went completed -> printing -> completed.

`fixtures/k1c_print_start.jsonl` is the same print starting: state 9, state 1,
then the start-of-print self-test with no layer count and no estimate yet.
"""

import json
import re
from pathlib import Path

from test_notification_live import CLEAR_NOTIFICATION_MARKER, _coordinator, _frame_calls
# Its autouse event-loop fixture, so these replays do not depend on what ran
# before them: a module that clears the loop on teardown left none to use.
from test_notification_live import _loop  # noqa: F401

from custom_components.ha_creality_ws.utils import coerce_numbers, derive_print_state

FIXTURES = Path(__file__).parent / "fixtures"


def _replay(name, *, templates=None):
    coord, hass = _coordinator(targets=("notify.mobile_app_pixel",))
    coord._notify_primed = False
    if templates:
        coord._notify_templates = dict(templates)
    merged: dict = {}
    statuses: list[tuple[float, str]] = []
    pushes: list[tuple[float, str, dict]] = []
    lines = (FIXTURES / name).read_text(encoding="utf-8").splitlines()[1:]
    for line in lines:
        rec = json.loads(line)
        hass.loop.now = 1000.0 + rec["t"]
        merged.update(coerce_numbers(rec["frame"]))
        for call in _frame_calls(coord, hass, **merged):
            pushes.append((rec["t"], call[2]["message"], call[2].get("data") or {}))
        status = derive_print_state(coord.data)
        if not statuses or statuses[-1][1] != status:
            statuses.append((rec["t"], status))
    return statuses, pushes, hass.events, lines


def _end_time(lines):
    return next(
        json.loads(line)["t"]
        for line in lines
        if json.loads(line)["frame"].get("state") == 2
    )


def test_the_status_does_not_flap_back_to_printing_at_the_end():
    statuses, _pushes, _events, lines = _replay("k1c_print_end.jsonl")
    assert [s for _t, s in statuses] == ["printing", "completed"]
    assert statuses[1][0] == _end_time(lines)


def test_the_completion_is_announced_once_when_the_printer_ends_the_job():
    _statuses, pushes, events, lines = _replay("k1c_print_end.jsonl")
    banners = [
        (t, m) for t, m, _d in pushes
        if m != CLEAR_NOTIFICATION_MARKER and "finished" in m.lower()
    ]
    assert [t for t, _m in banners] == [_end_time(lines)]
    finished = [data for name, data in events if name == "ha_creality_ws_print_finished"]
    assert len(finished) == 1
    # The event describes the end, not the printer's early rounding.
    assert finished[0]["progress"] == 100 and finished[0]["left_seconds"] == 0


def test_a_live_template_gets_no_zero_estimate_or_layer_count_during_self_test():
    """The user's template read "0% - 0/0 - 0s" for the whole self-test (R83)."""
    _statuses, pushes, _events, _lines = _replay(
        "k1c_print_start.jsonl",
        templates={"live": "{progress}%[ - {layer}/{total_layers}][ - {eta}]"},
    )
    cards = [m for _t, m, d in pushes if d.get("tag", "").endswith("_live") and m != CLEAR_NOTIFICATION_MARKER]
    assert cards, "the print start should put a card up"
    assert set(cards) == {"0%"}


def test_the_latched_material_flag_never_raises_a_runout_alert():
    """materialStatus is 1 through this whole start, filament loaded (R82)."""
    _statuses, pushes, _events, lines = _replay("k1c_print_start.jsonl")
    assert all(json.loads(line)["frame"].get("materialStatus", 1) == 1 for line in lines[:1])
    assert not [d for _t, _m, d in pushes if d.get("tag", "").endswith("_alert")]


def test_a_job_start_never_reads_idle_and_starts_one_card():
    """State 9 read "idle" for under a second, so every start went
    stopped -> idle -> printing and fired automations waiting for idle (R89).
    As "processing" it must not stand the card up before "printing" does."""
    statuses, pushes, _events, _lines = _replay("k1c_print_start.jsonl")
    assert [s for _t, s in statuses] == ["stopped", "processing", "printing", "self-testing"]
    starts = [d for _t, _m, d in pushes if d.get("activity") == "start"]
    assert len(starts) == 1
    first_card = next(
        t for t, m, d in pushes
        if d.get("tag", "").endswith("_live") and m != CLEAR_NOTIFICATION_MARKER
    )
    printing_at = next(t for t, s in statuses if s == "printing")
    assert first_card >= printing_at


def test_a_real_runout_alerts_once_and_holds_until_the_filament_is_back():
    """The K1C's runout sensor, `materialDetector1`, went to 1 with the flag and
    back to 0 on reload (`fixtures/k1c_runout.jsonl`, R82). Read through a
    pause alone, the alert and Filament Status cleared when the print was
    cancelled, with the filament still out."""
    coord, hass = _coordinator(targets=("notify.mobile_app_pixel",))
    coord._notify_primed = False
    merged: dict = {}
    alerts, clears, runout = [], [], []
    for line in (FIXTURES / "k1c_runout.jsonl").read_text(encoding="utf-8").splitlines()[1:]:
        rec = json.loads(line)
        hass.loop.now = 1000.0 + rec["t"]
        merged.update(coerce_numbers(rec["frame"]))
        for call in _frame_calls(coord, hass, **merged):
            data = call[2].get("data") or {}
            if data.get("tag", "").endswith("_alert"):
                (clears if call[2]["message"] == CLEAR_NOTIFICATION_MARKER else alerts).append(rec["t"])
        now = coord.filament_runout()
        if not runout or runout[-1][1] != now:
            runout.append((rec["t"], now))
    assert alerts == [27.008]
    assert clears == [122.001]
    assert runout == [(0.0, False), (27.008, True), (122.001, False)]


def test_the_start_trace_clears_the_last_jobs_card_and_starts_on_its_own_tag():
    """The K1C dates its jobs (`printStartTime`); a card gets the job's tag
    (R98). At the baseline the printer is idle on the previous job, so that
    job's card and the shared pre-0.9.10 one are cleared; the new job's card
    then starts on its own tag with nothing in front of it."""
    statuses, pushes, _events, _lines = _replay("k1c_print_start.jsonl")
    base = "ha_creality_ws_"
    live = [(t, d.get("tag", ""), m) for t, m, d in pushes if d.get("tag", "").endswith("_live")]
    cleared = {tag for _t, tag, m in live if m == CLEAR_NOTIFICATION_MARKER}
    assert any(tag.endswith("_1792915251_live") for tag in cleared)
    # The shared tag (the integration's base and `_live`, no job time between),
    # cleared at the baseline itself, before any card.
    first_card = min(t for t, _tag, m in live if m != CLEAR_NOTIFICATION_MARKER)
    assert any(
        t < first_card and tag.startswith(base) and not re.search(r"_\d{9,}_live$", tag)
        for t, tag, m in live if m == CLEAR_NOTIFICATION_MARKER
    )
    cards = [(t, tag) for t, tag, m in live if m != CLEAR_NOTIFICATION_MARKER]
    assert cards and all(tag.endswith("_1792915744_live") for _t, tag in cards)
    assert not any(tag.endswith("_1792915744_live") for tag in cleared)
