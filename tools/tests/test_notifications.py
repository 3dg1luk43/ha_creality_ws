"""Notification gating.

Issue #112: the printer keeps reporting the finished job's file name and 100%
progress, so a freshly started coordinator used to send a "print completed"
notification on every Home Assistant restart.
"""

import asyncio
from types import SimpleNamespace

import pytest

from custom_components.ha_creality_ws.const import NOTIFY_PRIME_GRACE_SECS
from custom_components.ha_creality_ws.coordinator import KCoordinator


class HassStub:
    def __init__(self):
        self.loop = asyncio.get_event_loop()
        self.states = SimpleNamespace(get=lambda _eid: None)


def _coordinator(monkeypatch, notify_completed=True):
    """A coordinator with notifications enabled and sends captured."""
    coord = KCoordinator(HassStub(), host="1.2.3.4")
    coord._notify_device = "notify.mobile_app_test"
    coord._notify_completed = notify_completed
    coord._notify_error = True
    coord._notify_minutes_to_end = True
    coord._minutes_to_end_value = 5

    sent: list[str] = []

    async def _capture(message):
        sent.append(message)

    monkeypatch.setattr(coord, "_send_notification", _capture)
    return coord, sent


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _event_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield
    loop.close()


def test_finished_job_at_startup_does_not_notify(monkeypatch):
    """The first frame only establishes a baseline; nothing is sent."""
    coord, sent = _coordinator(monkeypatch)
    coord.data = {"printFileName": "old_job.gcode", "printProgress": 100}

    _run(coord._check_notifications({}))
    assert sent == []
    assert coord._notify_primed is True
    assert coord._notified_completed is True

    # Repeated frames of the same stale state stay silent.
    _run(coord._check_notifications({}))
    assert sent == []


def test_completion_after_startup_still_notifies(monkeypatch):
    """A real 0 -> 100 transition after the baseline must notify exactly once."""
    coord, sent = _coordinator(monkeypatch)

    coord.data = {"printFileName": "job.gcode", "printProgress": 12}
    _run(coord._check_notifications({}))
    assert sent == []
    assert coord._notified_completed is False

    coord.data = {"printFileName": "job.gcode", "printProgress": 100}
    _run(coord._check_notifications({}))
    assert sent == ["Print 'job.gcode' completed successfully!"]

    _run(coord._check_notifications({}))
    assert len(sent) == 1


def test_new_job_after_a_stale_completion_notifies(monkeypatch):
    """Baselining a finished job must not mute the next print."""
    coord, sent = _coordinator(monkeypatch)

    coord.data = {"printFileName": "old_job.gcode", "printProgress": 100}
    _run(coord._check_notifications({}))
    assert sent == []

    coord.data = {"printFileName": "new_job.gcode", "printProgress": 5}
    _run(coord._check_notifications({}))
    assert sent == []

    coord.data = {"printFileName": "new_job.gcode", "printProgress": 100}
    _run(coord._check_notifications({}))
    assert sent == ["Print 'new_job.gcode' completed successfully!"]


def test_priming_waits_for_a_frame_with_progress(monkeypatch):
    """Telemetry is incremental; don't baseline off a half-populated frame."""
    coord, sent = _coordinator(monkeypatch)

    coord.data = {"printFileName": "job.gcode"}  # no progress yet
    _run(coord._check_notifications({}))
    assert coord._notify_primed is False

    coord.data = {"printFileName": "job.gcode", "printProgress": 100}
    _run(coord._check_notifications({}))
    assert coord._notify_primed is True
    assert sent == []


def test_priming_gives_up_after_the_grace_window(monkeypatch):
    """An idle printer reports neither field; baseline anyway and move on."""
    coord, sent = _coordinator(monkeypatch)
    coord.data = {}

    _run(coord._check_notifications({}))
    assert coord._notify_primed is False

    # Pretend the grace window elapsed.
    coord._notify_prime_deadline = coord.hass.loop.time() - 1
    _run(coord._check_notifications({}))
    assert coord._notify_primed is True
    assert sent == []
    assert NOTIFY_PRIME_GRACE_SECS > 0


def test_stale_error_at_startup_does_not_notify(monkeypatch):
    coord, sent = _coordinator(monkeypatch)
    coord.data = {
        "printFileName": "job.gcode",
        "printProgress": 100,
        "err": {"errcode": 521, "key": 1},
    }

    _run(coord._check_notifications({}))
    assert sent == []
    assert coord._last_error_code == 521


def test_minutes_to_end_reads_the_real_telemetry_field(monkeypatch):
    """The printer sends `printLeftTime`; `printTimeLeft` never existed."""
    coord, sent = _coordinator(monkeypatch)

    coord.data = {"printFileName": "job.gcode", "printProgress": 10, "printLeftTime": 3600}
    _run(coord._check_notifications({}))
    assert sent == []

    coord.data = {"printFileName": "job.gcode", "printProgress": 96, "printLeftTime": 120}
    _run(coord._check_notifications({}))
    assert sent == ["Print 'job.gcode' finishing in 2 minutes."]


def test_minutes_to_end_already_inside_window_at_startup_is_silent(monkeypatch):
    coord, sent = _coordinator(monkeypatch)
    coord.data = {"printFileName": "job.gcode", "printProgress": 97, "printLeftTime": 120}

    _run(coord._check_notifications({}))
    assert sent == []
    assert coord._notified_minutes_to_end is True


def test_reprinting_the_same_file_notifies_again(monkeypatch):
    """The file name never changes on a reprint, so the reset must key on progress."""
    coord, sent = _coordinator(monkeypatch)

    coord.data = {"printFileName": "job.gcode", "printProgress": 50}
    _run(coord._check_notifications({}))  # baseline

    coord.data = {"printFileName": "job.gcode", "printProgress": 100}
    _run(coord._check_notifications({}))
    assert len(sent) == 1

    # Same file printed again: progress drops, then climbs back to 100.
    coord.data = {"printFileName": "job.gcode", "printProgress": 3}
    _run(coord._check_notifications({}))
    assert coord._notified_completed is False

    coord.data = {"printFileName": "job.gcode", "printProgress": 100}
    _run(coord._check_notifications({}))
    assert len(sent) == 2


def test_a_genuine_zero_percent_frame_re_arms_completion(monkeypatch):
    """`printProgress or dProgress` treated a real 0 as missing.

    The printer keeps reporting the finished job's `dProgress`, so falling back
    to it on the first frame of a reprint held prog_val at 100, the completion
    flag never re-armed, and the next finish went unnotified.
    """
    coord, sent = _coordinator(monkeypatch)

    coord.data = {"printFileName": "job.gcode", "printProgress": 100, "dProgress": 100}
    _run(coord._check_notifications({}))  # baseline: already complete
    assert coord._notified_completed is True

    # Reprint starts: printProgress resets, dProgress still holds the old 100.
    coord.data = {"printFileName": "job.gcode", "printProgress": 0, "dProgress": 100}
    _run(coord._check_notifications({}))
    assert coord._notified_completed is False, "a real 0% must re-arm completion"

    coord.data = {"printFileName": "job.gcode", "printProgress": 100, "dProgress": 100}
    _run(coord._check_notifications({}))
    assert sent == ["Print 'job.gcode' completed successfully!"]


def test_priming_reads_progress_the_same_way_as_the_check(monkeypatch):
    """Both paths must agree: `or` baselined a real 0% off a stale dProgress."""
    coord, sent = _coordinator(monkeypatch)
    coord.data = {"printFileName": "job.gcode", "printProgress": 0, "dProgress": 100}

    _run(coord._check_notifications({}))  # this frame is the baseline
    assert coord._notify_primed is True
    assert coord._notified_completed is False, (
        "a 0% frame must not baseline as already complete"
    )

    coord.data = {"printFileName": "job.gcode", "printProgress": 100, "dProgress": 100}
    _run(coord._check_notifications({}))
    assert sent == ["Print 'job.gcode' completed successfully!"]


def test_dprogress_is_still_used_when_printprogress_is_absent(monkeypatch):
    """The fallback itself must survive: only a missing value triggers it."""
    coord, sent = _coordinator(monkeypatch)

    coord.data = {"printFileName": "job.gcode", "dProgress": 40}
    _run(coord._check_notifications({}))  # baseline
    assert coord._notified_completed is False

    coord.data = {"printFileName": "job.gcode", "dProgress": 100}
    _run(coord._check_notifications({}))
    assert sent == ["Print 'job.gcode' completed successfully!"]


def test_reprint_after_a_stale_startup_completion_notifies(monkeypatch):
    """The exact live scenario: baseline at 100%, then reprint the same file."""
    coord, sent = _coordinator(monkeypatch)

    coord.data = {"printFileName": "demo.gcode", "printProgress": 100}
    _run(coord._check_notifications({}))
    assert sent == []
    assert coord._notified_completed is True

    coord.data = {"printFileName": "demo.gcode", "printProgress": 7}
    _run(coord._check_notifications({}))
    assert coord._notified_completed is False

    coord.data = {"printFileName": "demo.gcode", "printProgress": 100}
    _run(coord._check_notifications({}))
    assert sent == ["Print 'demo.gcode' completed successfully!"]
