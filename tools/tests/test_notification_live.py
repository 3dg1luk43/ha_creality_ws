"""The live print card: lifecycle, throttling and the traps around it.

Drives `_check_notifications` frame by frame, the way the WebSocket client does,
with a controllable clock so the interval floor and the milestone latch can be
exercised without sleeping.
"""

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import fake_config_entry

from custom_components.ha_creality_ws.const import (
    CLEAR_NOTIFICATION_MARKER,
    NOTIFY_END_CONFIRM_SECS,
    NOTIFY_LIVE_INTERVAL_SECS,
    NOTIFY_LIVE_MILESTONE_STEP,
    NOTIFY_LIVE_MIN_INTERVAL_SECS,
    NOTIFY_LIVE_STALE_CLEAR_SECS,
    STALE_AFTER_SECS,
)
from custom_components.ha_creality_ws.coordinator import KCoordinator

_STRINGS = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "custom_components/ha_creality_ws/strings.json"
    ).read_text(encoding="utf-8")
)["common"]


class Clock:
    """Stands in for hass.loop, which the rules engine only asks for time()."""

    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class HassStub:
    def __init__(self):
        self.loop = Clock()
        self._states: dict[str, SimpleNamespace] = {}
        self.states = SimpleNamespace(get=self._states.get)
        self.calls: list[tuple[str, str, dict]] = []
        self.services = SimpleNamespace(async_call=self._async_call)
        self.tasks: list = []
        self.config_entries = SimpleNamespace(async_get_entry=lambda _id: None)
        self.config = SimpleNamespace(language="en")
        self.events: list = []
        self.bus = SimpleNamespace(
            async_fire=lambda event, data=None: self.events.append((event, data))
        )

    async def _async_call(self, domain, service, data, **_kw):
        self.calls.append((domain, service, data))

    def async_create_task(self, coro):
        self.tasks.append(coro)
        return coro


@pytest.fixture(autouse=True)
def _loop():
    # Restore the previous loop: closing does not uninstall it, so the policy
    # keeps handing this closed loop to any later module without its own fixture.
    try:
        previous = asyncio.get_event_loop_policy().get_event_loop()
    except Exception:  # pylint: disable=broad-except
        previous = None
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        yield
    finally:
        loop.close()
        asyncio.set_event_loop(previous)


def _build(hass):
    """A coordinator whose freshness is quoted on this module's fake clock.

    `coordinator.available` is `hass.loop.time() - client.last_rx_monotonic()
    < STALE_AFTER_SECS`. In production both sides are the event loop's
    monotonic clock and agree by construction. Here the left side is the
    `Clock` above while the stub client stamps the right side from the real
    `time.monotonic()`, so leaving them mixed made freshness a function of the
    host's uptime -- fine above ~985s of it, stale below, which read as
    `derive_activity_state() == "off"` and failed every card assertion here.

    Fresh by default, on the clock the tests actually advance. A test that
    wants a silent printer pins `client._last` relative to `hass.loop.now`.
    """
    coord = KCoordinator(hass, host="1.2.3.4", config_entry=fake_config_entry("abc123"))
    coord.client._last = None
    coord.client.last_rx_monotonic = lambda: (
        hass.loop.now if coord.client._last is None else coord.client._last
    )
    return coord


def _coordinator(*, targets=("notify.mobile_app_pixel",), live=True):
    hass = HassStub()
    coord = _build(hass)
    coord._notify_targets = list(targets)
    coord._notify_live = live
    coord._notify_completed = True
    coord._notify_error = True
    coord._notify_minutes_to_end = False
    # Skip the priming window; the tests that care about it drive it explicitly.
    coord._notify_primed = True
    coord._notified_completed = False
    return coord, hass


def _frame(coord, hass, **telemetry):
    """Feed one telemetry frame and return the notifications it produced."""
    coord.data = {"hostname": "K1C", **telemetry}
    asyncio.get_event_loop().run_until_complete(coord._check_notifications({}))
    pending, hass.tasks = hass.tasks, []
    if pending:
        asyncio.get_event_loop().run_until_complete(asyncio.gather(*pending))
    calls, hass.calls = hass.calls, []
    return [c[2] for c in calls]


def _frame_calls(coord, hass, **telemetry):
    """Like `_frame`, but keeps the target each payload went to.

    `_frame` discards it -- it returns only the payload dicts -- so a test about
    *which* device received what cannot use it.
    """
    coord.data = {"hostname": "K1C", **telemetry}
    asyncio.get_event_loop().run_until_complete(coord._check_notifications({}))
    pending, hass.tasks = hass.tasks, []
    if pending:
        asyncio.get_event_loop().run_until_complete(asyncio.gather(*pending))
    calls, hass.calls = hass.calls, []
    return calls


def _printing(progress=10, **kw):
    payload = {
        "printFileName": "/usr/data/printer_data/gcodes/3DBenchy.gcode",
        "printProgress": progress,
        "printJobTime": 600 + progress * 10,
        "printLeftTime": 3600,
        "state": 1,
        "layer": progress * 3,
        "TotalLayer": 300,
    }
    payload.update(kw)
    return payload


def _live(payloads):
    """Live-card progress *pushes*.

    Three different things now ride the `_live` tag, because replacing a
    notification in place requires reusing its tag: the card itself, the
    terminal banner that supersedes it, and the dismiss sentinel.

    Selecting on `activity != "end"` rather than on the presence of
    `live_update`, because past Apple's eight-hour ceiling the card deliberately
    drops `live_update` and degrades to a plain tagged notification -- still
    very much the card, and still under test here.
    """
    return [
        p
        for p in payloads
        if p["data"]["tag"].endswith("_live")
        and p["data"].get("activity") != "end"
        and p["message"] != CLEAR_NOTIFICATION_MARKER
    ]


def _clears(payloads):
    return [p for p in payloads if p["message"] == CLEAR_NOTIFICATION_MARKER]


def _events(payloads):
    """Lifecycle banners: the terminal ones, plus the finishing-soon reminder.

    Terminal banners land on the live tag (they replace the card) and are told
    apart by `activity: "end"`; the reminder has its own tag so it can sit
    alongside the card rather than clobbering it.
    """
    return [
        p
        for p in payloads
        if p["message"] != CLEAR_NOTIFICATION_MARKER
        and (
            p["data"].get("activity") == "end"
            or p["data"]["tag"].endswith("_soon")
        )
    ]


def _soon(payloads):
    return [p for p in payloads if p["data"]["tag"].endswith("_soon")]


# --------------------------------------------------------------------------- #
# Starting
# --------------------------------------------------------------------------- #


def test_a_print_starts_exactly_one_card():
    coord, hass = _coordinator()
    first = _live(_frame(coord, hass, **_printing(5)))
    assert len(first) == 1
    data = first[0]["data"]
    # Wire format: the FCM relay rejects a push whose top-level data holds a
    # native int or bool, so these are strings by the time they are sent.
    assert data["live_update"] == "true"
    assert data["progress"] == "5"
    assert data["chronometer"] == "true"
    assert data["progress_max"] == "100"

    # The very next frame changes nothing worth pushing.
    coord.hass.loop.advance(1)
    assert _live(_frame(coord, hass, **_printing(5))) == []


def test_the_card_body_names_the_job_and_the_layer():
    coord, hass = _coordinator()
    message = _live(_frame(coord, hass, **_printing(42)))[0]["message"]
    # Basenamed: the printer reports the whole path.
    assert "3DBenchy.gcode" in message
    assert "/usr/data" not in message
    assert "42%" in message
    assert "Layer 126/300" in message


def test_when_is_wall_clock_not_the_loop_clock():
    """The loop clock is monotonic and roughly uptime. Feeding one of those to
    `when` would put the chronometer in 1970."""
    coord, hass = _coordinator()
    before = time.time()
    data = _live(_frame(coord, hass, **_printing(5, printLeftTime=3600)))[0]["data"]
    assert int(data["when"]) >= int(before + 3600) - 2
    assert int(data["when"]) > 1_700_000_000


def test_live_pushes_never_reach_a_non_mobile_target():
    coord, hass = _coordinator(
        targets=("notify.mobile_app_pixel", "notify.persistent_notification")
    )
    payloads = _frame(coord, hass, **_printing(5))
    assert len(_live(payloads)) == 1


def test_the_card_is_opt_in():
    coord, hass = _coordinator(live=False)
    assert _live(_frame(coord, hass, **_printing(5))) == []


# --------------------------------------------------------------------------- #
# Throttling
# --------------------------------------------------------------------------- #


def test_fifty_frames_in_one_second_produce_one_push():
    """`_check_notifications` runs on every WebSocket frame, several a second."""
    coord, hass = _coordinator()
    pushes = 0
    for i in range(50):
        coord.hass.loop.advance(0.02)
        pushes += len(_live(_frame(coord, hass, **_printing(5 + i))))
    assert pushes == 1


def test_crossing_a_milestone_pushes_once_the_floor_has_passed():
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(10))
    coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)
    crossed = 10 + NOTIFY_LIVE_MILESTONE_STEP
    assert len(_live(_frame(coord, hass, **_printing(crossed)))) == 1


def test_a_pause_is_pushed_immediately():
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(42))
    coord.hass.loop.advance(6)  # well inside the interval floor
    payload = _live(_frame(coord, hass, **_printing(42, state=5)))[0]
    assert "Paused at 42%" in payload["message"]
    data = payload["data"]
    # The timer is stopped explicitly: omitting the key may not clear a
    # previously set one, and a countdown running through a pause is worse.
    assert data["chronometer"] == "false"
    assert "when" not in data
    assert data["critical_text"] == "Paused"


def test_resuming_is_pushed_immediately_too():
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(42))
    coord.hass.loop.advance(6)
    _frame(coord, hass, **_printing(42, state=5))
    coord.hass.loop.advance(6)
    data = _live(_frame(coord, hass, **_printing(42, state=1)))[0]["data"]
    assert data["chronometer"] == "true"


def test_the_title_never_changes_across_a_job():
    """Android requires a title and refuses to update one after the activity has
    started, so a title that varies means the code is lying to itself."""
    coord, hass = _coordinator()
    titles = set()
    for progress, state in ((5, 1), (30, 1), (55, 5), (80, 1)):
        coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)
        for payload in _live(_frame(coord, hass, **_printing(progress, state=state))):
            titles.add(payload["title"])
    coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)
    for payload in _events(_frame(coord, hass, **_printing(100))):
        titles.add(payload["title"])
    assert titles == {"K1C"}


# --------------------------------------------------------------------------- #
# Ending
# --------------------------------------------------------------------------- #


def test_finishing_ends_the_activity_and_then_announces_it():
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(50))
    coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)
    payloads = _frame(coord, hass, **_printing(100, printLeftTime=0))

    # The card is dismissed and the banner posted in its place, in that order
    # and on the same tag. Relying on tag identity alone left an ongoing
    # Android card stuck on its last percentage while its own completion notice
    # was delivered successfully.
    assert len(_clears(payloads)) == 1
    assert _clears(payloads)[0]["data"]["tag"].endswith("_live")
    assert len(_events(payloads)) == 1

    events = _events(payloads)
    assert len(events) == 1
    assert "completed successfully" in events[0]["message"]
    # It must actually alert: it would otherwise replace the card in silence.
    assert "alert_once" not in events[0]["data"]


def test_the_activity_is_only_ended_once():
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(50))
    coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)
    _frame(coord, hass, **_printing(100, printLeftTime=0))
    coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)
    again = _frame(coord, hass, **_printing(100, printLeftTime=0))
    assert _clears(again) == []


def test_a_silent_printer_has_its_card_cleared():
    """Nothing else would: every other transition needs an incoming frame."""
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(42))
    coord.client._last = coord.hass.loop.now - (NOTIFY_LIVE_STALE_CLEAR_SECS + 10)

    coord.notifier_tick()
    pending, hass.tasks = hass.tasks, []
    if pending:
        asyncio.get_event_loop().run_until_complete(asyncio.gather(*pending))
    assert [c[2]["message"] for c in hass.calls] == [CLEAR_NOTIFICATION_MARKER]


def test_a_healthy_printer_keeps_its_card():
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(42))
    coord.client._last = coord.hass.loop.now - 5
    coord.notifier_tick()
    assert hass.tasks == []


def test_the_tick_does_nothing_without_a_card():
    coord, hass = _coordinator()
    coord.client._last = coord.hass.loop.now - 10_000
    coord.notifier_tick()
    assert hass.tasks == []


# --------------------------------------------------------------------------- #
# Job boundaries
# --------------------------------------------------------------------------- #


def test_end_of_print_jitter_produces_one_card_push_and_one_completion():
    """The recorded 99/100/99/99/100 sequence behind b2ac2ea, now with a live
    card layered on top of the same predicate."""
    coord, hass = _coordinator()
    live_pushes = 0
    completions = 0
    for tick, progress in enumerate((99, 100, 99, 99, 100)):
        coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)
        payloads = _frame(
            coord, hass, **_printing(progress, printJobTime=1600 + tick)
        )
        live_pushes += len(_live(payloads))
        completions += len(
            [p for p in _events(payloads) if "completed" in p["message"]]
        )
    assert (live_pushes, completions) == (1, 1)


def test_a_reprint_starts_a_fresh_card():
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(50))
    coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)
    _frame(coord, hass, **_printing(100, printLeftTime=0))

    # Same file, restarted job clock -- the one unambiguous new-cycle signal.
    coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)
    payloads = _frame(
        coord, hass, **_printing(2, printJobTime=5, printLeftTime=3600)
    )
    assert len(_live(payloads)) == 1
    assert _live(payloads)[0]["data"]["progress"] == "2"


def test_a_job_that_finished_before_startup_never_gets_a_card():
    """Issue #112: the printer reports the last job's name and 100% forever."""
    hass = HassStub()
    coord = _build(hass)
    coord._notify_targets = ["notify.mobile_app_pixel"]
    coord._notify_live = True
    coord._notify_completed = True
    # Not primed: the first frame is a baseline, not an event.
    payloads = _frame(coord, hass, **_printing(100, printLeftTime=0))
    # No card and no announcement -- but a dismissal is expected and is the
    # point of the adopt-clear: a card can outlive the process, so startup has
    # to assume one might be on a phone and take it away.
    assert _live(payloads) == []
    assert _events(payloads) == []
    assert [c["message"] for c in _clears(payloads)] == [CLEAR_NOTIFICATION_MARKER]
    assert coord._live_card.card_active is False


def test_a_restart_mid_print_resyncs_the_card_instead_of_leaving_it_frozen():
    hass = HassStub()
    coord = _build(hass)
    coord._notify_targets = ["notify.mobile_app_pixel"]
    coord._notify_live = True
    coord._notify_completed = True

    # First frame primes silently.
    assert _frame(coord, hass, **_printing(42)) == []
    # Second frame adopts the running job.
    coord.hass.loop.advance(1)
    payloads = _live(_frame(coord, hass, **_printing(42)))
    assert len(payloads) == 1
    assert payloads[0]["data"]["progress"] == "42"


# --------------------------------------------------------------------------- #
# The stale-error trap
# --------------------------------------------------------------------------- #


def test_a_stale_error_code_does_not_pin_the_card_to_error():
    """`derive_print_state` reports "error" for any non-zero errcode, including
    one the printer never clears -- which would freeze the card for a whole
    print and stop every later progress push."""
    coord, hass = _coordinator()
    frame = _printing(20, err={"errcode": 521, "key": 1})
    payloads = _frame(coord, hass, **frame)

    # The alert still fires, once, off the code *changing*.
    alerts = [p for p in payloads if p["data"]["tag"].endswith("_alert")]
    assert len(alerts) == 1
    # ...and the card still tracks the print.
    assert len(_live(payloads)) == 1

    coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)
    later = _live(_frame(coord, hass, **_printing(40, err={"errcode": 521, "key": 1})))
    assert len(later) == 1, "a stale error code froze the live card"
    assert later[0]["data"]["progress"] == "40"


# --------------------------------------------------------------------------- #
# Apple's eight-hour ceiling
# --------------------------------------------------------------------------- #


def test_past_the_ios_ceiling_the_card_degrades_instead_of_dying_silently():
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(10))
    # Pretend the activity started nine hours ago.
    coord._live_card.started_epoch = time.time() - 9 * 3600

    coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)
    data = _live(_frame(coord, hass, **_printing(30)))[0]["data"]
    for key in ("live_update", "chronometer", "when"):
        assert key not in data, key
    # Still one card, still replaced in place, still shows progress.
    assert data["tag"].endswith("_live")
    assert data["progress"] == "30"


def test_a_degraded_card_spells_out_the_remaining_time():
    """There is no on-device chronometer any more, so the body has to say it."""
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(10))
    coord._live_card.started_epoch = time.time() - 9 * 3600
    coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)
    message = _live(_frame(coord, hass, **_printing(30, printLeftTime=4500)))[0][
        "message"
    ]
    assert "1h 15m left" in message


def test_the_card_does_not_flap_back_after_a_job_finishes():
    """Isolates the jitter latch: once a print is announced finished, telemetry
    that briefly says "printing" again must not resurrect the card."""
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(50))
    coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)
    _frame(coord, hass, **_printing(100, printJobTime=2000, printLeftTime=0))

    coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)
    back = _frame(coord, hass, **_printing(99, printJobTime=2001))
    assert _live(back) == []
    assert coord._live_card.job_finished is True


def test_a_reprint_re_arms_even_with_completion_notifications_off():
    """`_notified_completed` feeds is_new_job_cycle(), so latching it only when
    completion notifications were enabled retired the live card permanently:
    with the card on and completion off, no later reprint ever showed one."""
    coord, hass = _coordinator()
    coord._notify_completed = False

    _frame(coord, hass, **_printing(50))
    coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)
    _frame(coord, hass, **_printing(100, printJobTime=2000, printLeftTime=0))
    assert coord._live_card.job_finished is True

    # Same file, restarted job clock.
    coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)
    payloads = _frame(coord, hass, **_printing(2, printJobTime=5, printLeftTime=3600))
    assert len(_live(payloads)) == 1, "the card never came back for the reprint"


def test_clearing_the_file_name_dismisses_the_card():
    """The new-job reset zeroes `card_active`, so the dismissal has to happen
    first -- otherwise nothing knows an activity is still up on the phone, and
    `notifier_tick` will not clean it up either."""
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(42))
    assert coord._live_card.card_active is True

    coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)
    payloads = _frame(
        coord, hass, printFileName="", printProgress=0, printJobTime=0, state=0
    )
    assert len(_clears(payloads)) == 1
    assert _clears(payloads)[0]["data"]["tag"].endswith("_live")
    assert coord._live_card.card_active is False


def test_starting_a_different_file_dismisses_the_previous_card():
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(42))
    coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)
    payloads = _frame(
        coord,
        hass,
        printFileName="/usr/data/printer_data/gcodes/bracket.gcode",
        printProgress=1,
        printJobTime=5,
        printLeftTime=7200,
        state=1,
        layer=1,
        TotalLayer=400,
    )
    assert len(_clears(payloads)) == 1
    # ...and the new job gets its own card in the same frame.
    assert len(_live(payloads)) == 1
    assert "bracket.gcode" in _live(payloads)[0]["message"]


def test_stopping_and_reprinting_the_same_file_shows_a_card_again():
    """The card is retired by a stop and comes back for the reprint.

    Retiring it is not optional: after a stop this printer reports state 0 with
    the file still named, which derives as "processing" and counts as busy, so a
    card left un-retired is stood straight back up at 0%.

    What makes retiring it safe is `JobEndWatch`: a job printing again re-arms
    the latches. It used to be `_notified_completed`, which a stop never sets --
    so back then retiring the card here meant the same file could never show one
    again, and the card had to be left alive instead.
    """
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(30))
    assert coord._live_card.card_active is True

    # state 4 is "stopped": job over, but nowhere near 100%.
    coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)
    stopped = _frame(coord, hass, **_printing(30, state=4))
    assert len(_events(stopped)) == 1
    assert len(_clears(stopped)) == 1
    assert coord._live_card.job_finished is True

    coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)
    again = _frame(coord, hass, **_printing(1, printJobTime=5, printLeftTime=3600))
    assert len(_live(again)) == 1


def test_switching_the_card_off_dismisses_it_before_the_reload():
    """An options change reloads the entry and the rebuilt state starts empty,
    so this is the last moment anything knows a card is on a phone."""
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(42))
    hass.calls.clear()

    coord.notify_options_changed(
        {"notify_live": False, "notify_targets": ["notify.mobile_app_pixel"]}
    )
    pending, hass.tasks = hass.tasks, []
    if pending:
        asyncio.get_event_loop().run_until_complete(asyncio.gather(*pending))
    assert [c[2]["message"] for c in hass.calls] == [CLEAR_NOTIFICATION_MARKER]


def test_removing_every_target_also_dismisses_the_card():
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(42))
    hass.calls.clear()
    # Sent to the targets still loaded, which is exactly what we want.
    coord.notify_options_changed({"notify_live": True, "notify_targets": []})
    pending, hass.tasks = hass.tasks, []
    if pending:
        asyncio.get_event_loop().run_until_complete(asyncio.gather(*pending))
    assert [c[2]["message"] for c in hass.calls] == [CLEAR_NOTIFICATION_MARKER]


def test_an_unrelated_options_change_leaves_the_card_alone():
    """Otherwise toggling any setting mid-print would make the card flicker and
    spend an iOS push-to-start slot for nothing."""
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(42))
    hass.calls.clear()
    coord.notify_options_changed(
        {"notify_live": True, "notify_targets": ["notify.mobile_app_pixel"]}
    )
    assert hass.tasks == []


def test_a_pause_clears_the_countdown_deadline():
    """A push with no deadline has stopped the timer, so keeping the old one
    made decide() see it expire and fire a pointless overrun push."""
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(42, printLeftTime=60))
    assert coord._live_card.last_when is not None

    coord.hass.loop.advance(6)
    _frame(coord, hass, **_printing(42, state=5, printLeftTime=60))
    assert coord._live_card.last_when is None

    # Long past the old deadline, and still nothing to say. Also past the
    # refresh cadence, which must stay quiet too: a paused print holds its
    # progress and has no countdown, so a repaint would be identical pixels
    # charged against the relay's daily budget.
    coord.hass.loop.advance(600)
    assert _live(_frame(coord, hass, **_printing(42, state=5, printLeftTime=60))) == []


# --------------------------------------------------------------------------- #
# Stopped / cancelled
# --------------------------------------------------------------------------- #


def test_a_stopped_print_says_so_instead_of_the_card_just_vanishing():
    """The live card is cleared when a job stops, and used to be all that
    happened -- from the user's side a notification silently disappeared with no
    explanation of whether the print finished or died."""
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(30))
    coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)

    payloads = _frame(coord, hass, **_printing(30, state=4))
    assert len(_clears(payloads)) == 1, "the card is dismissed, then replaced"

    events = _events(payloads)
    assert len(events) == 1
    assert "stopped at 30%" in events[0]["message"]
    assert "3DBenchy.gcode" in events[0]["message"]
    assert events[0]["data"]["notification_icon"] == "mdi:stop-circle"
    # It has to alert, like the completion banner.
    assert "alert_once" not in events[0]["data"]


def test_a_stopped_print_is_announced_once():
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(30))
    coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)
    _frame(coord, hass, **_printing(30, state=4))
    coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)
    assert _events(_frame(coord, hass, **_printing(30, state=4))) == []


def test_completion_wins_when_a_finished_job_also_reports_stopped():
    """The printer settles on state 4 after a successful print too, so ranking
    matters: progress >= 100 is "completed", not "stopped"."""
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(50))
    coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)
    payloads = _events(_frame(coord, hass, **_printing(100, state=4, printLeftTime=0)))
    assert len(payloads) == 1
    assert "completed" in payloads[0]["message"]
    assert coord._notified_stopped is False


def test_a_print_already_stopped_at_startup_is_not_announced():
    """Priming adopts whatever the printer is reporting; the printer holds state
    4 indefinitely, so a restart must not announce last week's cancellation."""
    hass = HassStub()
    coord = _build(hass)
    coord._notify_targets = ["notify.mobile_app_pixel"]
    coord._notify_completed = True
    coord._notify_live = True

    payloads = _frame(coord, hass, **_printing(30, state=4))
    assert _live(payloads) == []
    assert _events(payloads) == [], "last week's cancellation must stay quiet"
    assert coord._notified_stopped is True
    coord.hass.loop.advance(1)
    assert _events(_frame(coord, hass, **_printing(30, state=4))) == []


def test_stopping_then_reprinting_announces_the_next_stop_too():
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(30))
    coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)
    _frame(coord, hass, **_printing(30, state=4))

    # New job on the same file: the job clock restarting is the new-cycle signal.
    coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)
    _frame(coord, hass, **_printing(2, printJobTime=5))
    assert coord._notified_stopped is False

    coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)
    events = _events(_frame(coord, hass, **_printing(2, printJobTime=6, state=4)))
    assert len(events) == 1
    assert "stopped at 2%" in events[0]["message"]


# --------------------------------------------------------------------------- #
# Dismissal, cadence and coexistence
# --------------------------------------------------------------------------- #


def test_the_card_does_not_ask_for_things_that_do_not_work():
    """A live card cannot be made unswipeable on Android 16. Tested on a real
    handset with `persistent`, without it, and with the action buttons removed:
    swipeable in every combination, which matches the companion documentation
    ("starting in Android 14 persistent notifications will be dismissable
    except when the device is locked").

    So these three are omitted because they achieve nothing here, and
    `importance: low` actively asked for the minimised presentation a live card
    does not want. Hide is the way out of the card."""
    coord, hass = _coordinator()
    data = _live(_frame(coord, hass, **_printing(5)))[0]["data"]
    assert data["live_update"] == "true"
    assert data["alert_once"] == "true"
    for key in ("persistent", "sticky", "importance"):
        assert key not in data, key


def test_a_refreshing_card_always_carries_a_way_out():
    """Printer controls are opt-in, but the card must always offer a way out.

    A swipe only clears the notification on screen; the next refresh re-posts it
    under the same tag. Without a button the user has a card that keeps coming
    back for the rest of the print.
    """
    for actions in (True, False):
        coord, hass = _coordinator()
        coord._notify_actions = actions
        data = _live(_frame(coord, hass, **_printing(5)))[0]["data"]
        ids = [a["action"] for a in data["actions"]]
        assert any(i.startswith("CREALITY_DISMISS_") for i in ids), actions


def test_the_first_push_alerts_and_later_ones_do_not():
    """iOS alerts per push unless told otherwise, so a refresh cadence would
    buzz the phone for the whole print. `alert_once` is Android-only and does
    nothing there. The push that *starts* the card stays audible: it is the
    "your print is on the Lock Screen" cue."""
    coord, hass = _coordinator()
    start = _live(_frame(coord, hass, **_printing(5)))[0]["data"]
    assert "silent" not in start
    assert "push" not in start
    assert start["activity"] == "start"

    coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)
    refresh = _live(_frame(coord, hass, **_printing(10)))[0]["data"]
    assert refresh["silent"] == "true"
    # Nested, so it is exempt from the FCM string rule and stays a real dict.
    assert refresh["push"] == {"interruption-level": "passive"}
    assert "activity" not in refresh


def test_progress_refreshes_on_the_clock_not_only_on_a_milestone():
    """The card used to move only on a 5% boundary, so on a long print the
    percentage and the remaining estimate sat visibly stale for twenty minutes
    at a time.

    Progress is deliberately *unchanged* across the two frames. The step is 1, so
    advancing it at all crosses a milestone and the push would be explained by
    either rule -- which is what the old version did, under a comment claiming a
    "5% bucket" left over from the previous step. Holding progress still leaves
    the clock as the only thing that can produce a push.
    """
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(10))
    # Well past the refresh cadence, and inside the same milestone.
    coord.hass.loop.advance(NOTIFY_LIVE_INTERVAL_SECS + 1)
    pushes = _live(_frame(coord, hass, **_printing(10)))
    assert len(pushes) == 1, "the wall clock alone must refresh the card"
    assert pushes[0]["data"]["progress"] == "10"


def test_the_finishing_soon_reminder_sits_beside_the_card():
    """It used to share the `_event` tag with the completion banner, so the one
    notification whose entire purpose is "go and look at the printer" was the
    one that got replaced minutes later."""
    coord, hass = _coordinator()
    coord._notify_minutes_to_end = True
    coord._minutes_to_end_value = 5
    _frame(coord, hass, **_printing(80))
    coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)

    payloads = _frame(coord, hass, **_printing(81, printLeftTime=120))
    soon = _soon(payloads)
    assert len(soon) == 1
    # Its own tag, so neither notification can replace the other...
    assert soon[0]["data"]["tag"] != f"{coord._notify_tag_base()}_live"
    # ...and its own channel, so it can make a sound while the card stays quiet.
    assert soon[0]["data"]["channel"] == _STRINGS["channel_soon"]
    assert soon[0]["data"]["channel"] != _STRINGS["channel_live"]


def test_the_terminal_banner_replaces_the_card_and_frees_it():
    """Posted on the card's own tag so it supersedes it in place.

    The print is over, so the refreshing card has to stop being re-posted; the
    banner taking over its tag is what frees it.
    """
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(50))
    coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)

    payloads = _frame(coord, hass, **_printing(100, printLeftTime=0))
    done = _events(payloads)
    assert len(done) == 1
    data = done[0]["data"]
    assert data["tag"] == f"{coord._notify_tag_base()}_live"
    assert data["activity"] == "end"
    # No alert_once, or replacing the card would happen silently and the
    # "finished" ping would never sound.
    assert "alert_once" not in data


def test_a_mac_is_not_sent_a_dismissal_for_a_card_it_never_received():
    """The replace path must use the same live-capability gate as the card.

    `live_only` skips macOS, so it never gets the live card -- but the
    replacement path selected every *mobile* target, so the clear sentinel went
    to it anyway, clearing a tag it does not have. The banner still must arrive,
    exactly as for the finishing-soon reminder.
    """
    coord, hass = _coordinator(
        targets=("notify.mobile_app_s24", "notify.mobile_app_macbookairlukas")
    )
    _with_devices(coord, hass, [("S24", "Android"), ("MacBookAirLukas", "macOS")])

    _frame(coord, hass, **_printing(50))
    coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)
    calls = _frame_calls(coord, hass, **_printing(100, printLeftTime=0))

    def messages_for(service):
        return [c[2]["message"] for c in calls if c[1] == service]

    mac = messages_for("mobile_app_macbookairlukas")
    android = messages_for("mobile_app_s24")

    assert CLEAR_NOTIFICATION_MARKER not in mac, (
        f"the Mac never had the card, so it must not be sent a dismissal: {mac}"
    )
    assert len(mac) == 1, f"but it does still get the banner: {mac}"

    assert CLEAR_NOTIFICATION_MARKER in android, (
        f"the phone did have the card and must have it cleared: {android}"
    )
    assert len(android) == 2, f"clear then banner, in that order: {android}"
    assert android[0] == CLEAR_NOTIFICATION_MARKER


def test_a_card_the_user_hid_does_not_come_straight_back():
    """Hide has to stick. The next telemetry frame arrives within a second, and
    re-creating the card there would make the button look broken."""
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(42))
    ids = coord._notify_action_ids()
    asyncio.get_event_loop().run_until_complete(
        coord.async_handle_notification_action(ids["dismiss"])
    )
    # Drain rather than discard: the dismiss itself queues a send, and dropping
    # the coroutine unawaited would leak it into the next test as a warning.
    pending, hass.tasks = hass.tasks, []
    if pending:
        asyncio.get_event_loop().run_until_complete(asyncio.gather(*pending))
    hass.calls.clear()

    for step in range(1, 6):
        coord.hass.loop.advance(NOTIFY_LIVE_INTERVAL_SECS + 1)
        assert _live(_frame(coord, hass, **_printing(42 + step))) == []


# --------------------------------------------------------------------------- #
# Orphaned cards -- every way a card can be left stranded on a phone
# --------------------------------------------------------------------------- #


def test_a_job_whose_completion_was_already_announced_still_loses_its_card():
    """The reported bug. The printer reports 100% forever, so any restart
    re-latches the completion -- and the card was then retired with neither a
    banner nor a dismissal, leaving a stale percentage on the phone for good.

    The dismissal is a debt settled at the end of the frame rather than a guess
    about who will pay it.
    """
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(50))
    assert coord._live_card.card_active is True

    # Completion already announced for this job.
    coord._notified_completed = True
    coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)
    payloads = _frame(coord, hass, **_printing(100, printLeftTime=0))

    assert _events(payloads) == [], "no second announcement"
    assert len(_clears(payloads)) == 1, "the card must still be taken away"
    assert coord._live_card.card_active is False


def test_a_stop_already_announced_still_loses_its_card():
    """Same hole, reached through the stopped path rather than completion."""
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(30))
    coord._notified_stopped = True
    coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)

    payloads = _frame(coord, hass, **_printing(30, state=4))
    assert _events(payloads) == []
    assert len(_clears(payloads)) == 1


def test_a_card_that_outlived_the_process_is_taken_away_at_startup():
    """`card_active` lives in memory only, so after a restart nothing knows a
    card is still on a phone -- and `notifier_tick` will not tidy it up either,
    because it only acts on a card it believes exists. A print that ended while
    Home Assistant was down left one stranded indefinitely."""
    hass = HassStub()
    coord = _build(hass)
    coord._notify_targets = ["notify.mobile_app_pixel"]
    coord._notify_live = True
    coord._notify_completed = True

    # First frame ever: an idle printer still reporting the finished job.
    payloads = _frame(coord, hass, **_printing(100, printLeftTime=0))
    assert len(_clears(payloads)) == 1
    assert _live(payloads) == []


def test_adopting_a_running_print_replaces_the_card_rather_than_clearing_it():
    """The other half of that: mid-job the resync push supersedes the card in
    place, so dismissing first would flicker and burn an iOS push-to-start
    slot for nothing."""
    hass = HassStub()
    coord = _build(hass)
    coord._notify_targets = ["notify.mobile_app_pixel"]
    coord._notify_live = True
    coord._notify_completed = True

    _frame(coord, hass, **_printing(42))          # baseline, silent
    coord.hass.loop.advance(1)
    payloads = _frame(coord, hass, **_printing(43))
    assert _clears(payloads) == [], "no dismissal while a job is running"
    assert len(_live(payloads)) == 1


def test_the_finishing_soon_reminder_is_taken_away_when_the_job_ends():
    """It has its own tag so it can sit beside the card, which also means
    nothing else will ever remove it. A phone reading "finishing in 30 minutes"
    for a print that already ended is worse than never having warned."""
    coord, hass = _coordinator()
    coord._notify_minutes_to_end = True
    coord._minutes_to_end_value = 30
    _frame(coord, hass, **_printing(80, printLeftTime=600))
    assert coord._notified_minutes_to_end is True
    coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)

    payloads = _frame(coord, hass, **_printing(100, printLeftTime=0))
    cleared_tags = {c["data"]["tag"] for c in _clears(payloads)}
    assert f"{coord._notify_tag_base()}_soon" in cleared_tags


def test_no_dismissal_is_sent_when_there_was_never_a_reminder():
    """The clear is only worth a push if a reminder actually went out."""
    coord, hass = _coordinator()
    coord._notify_minutes_to_end = False
    _frame(coord, hass, **_printing(50))
    coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)

    payloads = _frame(coord, hass, **_printing(100, printLeftTime=0))
    tags = {c["data"]["tag"] for c in _clears(payloads)}
    assert f"{coord._notify_tag_base()}_soon" not in tags


def test_the_reminder_is_cleared_even_with_completion_notifications_off():
    """The soon-clear used to live inside the terminal-banner path, so a user
    who wanted the reminder but not the completion ping kept a stale
    "finishing soon" forever -- the one combination where nothing ran."""
    coord, hass = _coordinator()
    coord._notify_completed = False
    coord._notify_minutes_to_end = True
    coord._minutes_to_end_value = 30
    _frame(coord, hass, **_printing(80, printLeftTime=600))
    assert coord._notified_minutes_to_end is True
    coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)

    payloads = _frame(coord, hass, **_printing(100, printLeftTime=0))
    tags = {c["data"]["tag"] for c in _clears(payloads)}
    assert f"{coord._notify_tag_base()}_soon" in tags
    # ...and the card still goes, since no banner is coming to replace it.
    assert f"{coord._notify_tag_base()}_live" in tags


# --------------------------------------------------------------------------- #
# Platform capability, resolved alerts, payload conformance
# --------------------------------------------------------------------------- #


def _with_devices(coord, hass, devices):
    """Teach the stub which companion platform is behind each notify target."""
    entries = [
        SimpleNamespace(data={"device_name": name, "os_name": os_name})
        for name, os_name in devices
    ]
    hass.config_entries.async_entries = lambda domain=None: entries
    coord._target_os_cache.clear()


def test_a_mac_is_not_sent_a_live_card_it_cannot_render():
    """macOS has neither an iOS Live Activity nor an Android progress
    notification, so every refresh would land as another ordinary banner that
    supersedes nothing -- twelve an hour for the length of the print."""
    coord, hass = _coordinator(
        targets=("notify.mobile_app_s24", "notify.mobile_app_macbookairlukas")
    )
    _with_devices(coord, hass, [("S24", "Android"), ("MacBookAirLukas", "macOS")])

    pushes = _live(_frame(coord, hass, **_printing(5)))
    assert len(pushes) == 1, "only the Android phone gets the card"


def test_phones_of_both_platforms_still_get_the_card():
    coord, hass = _coordinator(
        targets=("notify.mobile_app_s24", "notify.mobile_app_iphone_15_pro")
    )
    _with_devices(coord, hass, [("S24", "Android"), ("iPhone 15 PRO", "iOS")])
    assert len(_live(_frame(coord, hass, **_printing(5)))) == 2


def test_an_unidentified_target_is_given_the_benefit_of_the_doubt():
    """Failing to resolve a platform must not silently mute a real phone."""
    coord, hass = _coordinator(targets=("notify.mobile_app_mystery",))
    _with_devices(coord, hass, [])
    assert len(_live(_frame(coord, hass, **_printing(5)))) == 1


def test_the_finishing_soon_reminder_still_reaches_the_mac():
    """It is an ordinary banner, which macOS renders perfectly well -- the
    exclusion is about the live card specifically, not about the device."""
    coord, hass = _coordinator(
        targets=("notify.mobile_app_s24", "notify.mobile_app_macbookairlukas")
    )
    _with_devices(coord, hass, [("S24", "Android"), ("MacBookAirLukas", "macOS")])
    coord._notify_minutes_to_end = True
    coord._minutes_to_end_value = 30
    assert len(_soon(_frame(coord, hass, **_printing(80, printLeftTime=600)))) == 2


def test_a_recovered_printer_has_its_alert_taken_away():
    """A lock screen still reading "filament runout" after the user reloaded is
    actively misleading, and nothing else shares that tag to supersede it."""
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(40, materialStatus=1))
    assert coord._alert_showing is True

    coord.hass.loop.advance(1)
    payloads = _frame(coord, hass, **_printing(41, materialStatus=0))
    tags = {c["data"]["tag"] for c in _clears(payloads)}
    assert f"{coord._notify_tag_base()}_alert" in tags
    assert coord._alert_showing is False


def test_one_condition_resolving_does_not_dismiss_the_others_alert():
    """Errors and runouts share a tag, so clearing on the first to resolve
    would take away an alert that is still true."""
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(40, materialStatus=1, err={"errcode": 521, "key": 1}))
    coord.hass.loop.advance(1)

    # Runout resolved, error still live.
    payloads = _frame(
        coord, hass, **_printing(41, materialStatus=0, err={"errcode": 521, "key": 1})
    )
    tags = {c["data"]["tag"] for c in _clears(payloads)}
    assert f"{coord._notify_tag_base()}_alert" not in tags
    assert coord._alert_showing is True


def test_nothing_is_dismissed_when_no_alert_was_ever_shown():
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(40))
    coord.hass.loop.advance(1)
    payloads = _frame(coord, hass, **_printing(41))
    tags = {c["data"]["tag"] for c in _clears(payloads)}
    assert f"{coord._notify_tag_base()}_alert" not in tags


def test_the_card_is_dismissed_before_the_banner_not_after():
    """Order is the whole point. Posting the banner alone left an ongoing
    Android card stuck on its last percentage; dismissing *after* it would take
    the banner down too, turning a stuck card into no notification at all.

    Both go out as one task per target so the event loop cannot interleave them.
    """
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(50))
    coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)

    coord.data = {"hostname": "K1C", **_printing(100, printLeftTime=0)}
    asyncio.get_event_loop().run_until_complete(coord._check_notifications({}))
    pending, hass.tasks = hass.tasks, []
    asyncio.get_event_loop().run_until_complete(asyncio.gather(*pending))

    sequence = [c[2]["message"] for c in hass.calls]
    assert sequence[0] == CLEAR_NOTIFICATION_MARKER
    assert sequence[1] != CLEAR_NOTIFICATION_MARKER
    assert len(sequence) == 2


def test_the_chronometer_is_given_an_absolute_future_deadline():
    """`when` is the epoch second the print is expected to end, and the
    companion app counts down to it on-device -- which is what makes the
    remaining time move between pushes rather than only at one."""
    coord, hass = _coordinator()
    data = _live(_frame(coord, hass, **_printing(20, printLeftTime=3600)))[0]["data"]
    assert data["chronometer"] == "true"
    assert int(data["when"]) > 1_700_000_000


def test_the_soon_reminder_has_no_progress_bar():
    """With one it renders as a second live card under the real one: same
    title, same bar, nothing to say which is the one still updating."""
    coord, hass = _coordinator()
    coord._notify_minutes_to_end = True
    coord._minutes_to_end_value = 30
    soon = _soon(_frame(coord, hass, **_printing(80, printLeftTime=600)))[0]["data"]
    assert "progress" not in soon
    assert "progress_max" not in soon
    assert "progress_indeterminate" not in soon


# --------------------------------------------------------------------------- #
# A print that is stopped rather than finished
# --------------------------------------------------------------------------- #
# Stopping a print from the printer's own screen, from the Creality app or from
# Home Assistant all end the same way in telemetry, and none of them announces
# itself: `state` goes back to 0 with the file name still attached and the
# progress reset to 0, which derives as "processing" and counts as busy. Nothing
# fired, and the card's last word on the print was a refresh reading 0%.


def _stopped_as_reported():
    """A frame shaped like the one a cancelled print actually produces.

    Taken from `set_stop` in the test server, which mirrors the real firmware:
    the progress, the job clock and the layer are all reset, `state` goes back
    to 0, and the file name stays exactly where it was.
    """
    return _printing(0, printJobTime=0, printLeftTime=0, state=0, layer=0)


def _confirm(coord, hass, frames=1):
    """Feed the stop, let its confirmation window elapse, feed more frames.

    The window is measured between frames, so the clock has to move *between*
    them: a printer that goes quiet the instant a job ends is not a printer we
    can declare anything about.
    """
    out = list(_frame(coord, hass, **_stopped_as_reported()))
    hass.loop.advance(NOTIFY_END_CONFIRM_SECS + 1)
    for _ in range(frames):
        out.extend(_frame(coord, hass, **_stopped_as_reported()))
    return out


def test_a_stop_the_printer_does_not_announce_is_still_announced():
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(40))

    # The frame the stop arrives on says nothing a state word could catch.
    hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)
    assert _frame(coord, hass, **_stopped_as_reported()) == [], (
        "a job that may have just ended must not repaint the card at 0%"
    )

    payloads = _confirm(coord, hass)
    events = _events(payloads)
    assert len(events) == 1
    # The progress from before the stop, not the 0 the printer now reports.
    assert events[0]["message"] == _STRINGS["stopped"].format(
        filename="3DBenchy.gcode", progress=40
    )
    # Posted on the card's tag, and the card dismissed first so the ongoing
    # Android notification actually goes away.
    assert len(_clears(payloads)) == 1
    assert events[0]["data"]["tag"].endswith("_live")
    assert "progress" not in events[0]["data"]


def test_a_stop_that_clears_the_file_name_is_announced_at_once():
    """This frame is the only chance: `_check_notifications` returns early on
    every later one, because there is no job left to describe."""
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(40))

    hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)
    payloads = _frame(coord, hass, printFileName="", printProgress=0, state=0)

    events = _events(payloads)
    assert len(events) == 1
    assert "3DBenchy.gcode" in events[0]["message"]
    assert len(_clears(payloads)) == 1


def test_the_bus_event_fires_for_a_stop_nothing_else_would_have_caught():
    """The language-neutral escape hatch has to cover the same ends the
    built-in notification does."""
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(40))
    _confirm(coord, hass)

    stopped = [d for name, d in hass.events if name.endswith("_print_stopped")]
    assert len(stopped) == 1
    assert stopped[0]["filename"] == "3DBenchy.gcode"
    # The percentage the print reached, not the 0 the frame that revealed the
    # stop reports: an automation writing its own text needs the same number
    # the built-in notification quotes.
    assert stopped[0]["progress"] == 40


def test_a_stop_is_announced_once_however_many_frames_follow():
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(40))
    _confirm(coord, hass, frames=5)

    for _ in range(5):
        hass.loop.advance(NOTIFY_LIVE_INTERVAL_SECS + 1)
        assert _frame(coord, hass, **_stopped_as_reported()) == []


def test_one_idle_frame_mid_print_costs_no_notification():
    """The printer does emit one, and the card has to come back rather than be
    replaced by "your print was stopped"."""
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(40))

    hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)
    assert _frame(coord, hass, **_stopped_as_reported()) == []

    hass.loop.advance(1)
    back = _frame(coord, hass, **_printing(41))
    assert _events(back) == []
    assert len(_live(back)) == 1
    assert coord._notified_stopped is False


def test_a_dropped_connection_is_not_a_stopped_print():
    """Every entity goes unavailable when telemetry stops, and the derived state
    is "unknown" -- which says nothing about the job. Announcing a stop off it
    would turn a Wi-Fi blip into "your print was stopped"."""
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(40))

    coord.client._last = hass.loop.now - (STALE_AFTER_SECS + 5)
    hass.loop.advance(NOTIFY_END_CONFIRM_SECS + 1)
    payloads = _frame(coord, hass, **_printing(40))

    assert _events(payloads) == []
    assert coord._notified_stopped is False


def test_stopping_the_same_file_twice_is_announced_twice():
    """Nothing used to re-arm the latch: a stop does not change the file name,
    and it resets `printJobTime` to 0 so the job clock never runs backwards
    either. The second stop went unannounced."""
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(40))
    assert len(_events(_confirm(coord, hass))) == 1

    # Printing again is what says a new job has begun.
    hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)
    again = _frame(coord, hass, **_printing(5, printJobTime=50))
    assert len(_live(again)) == 1, "the reprint gets its own card"
    assert coord._notified_stopped is False

    hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)
    _frame(coord, hass, **_stopped_as_reported())
    assert len(_events(_confirm(coord, hass))) == 1


def test_the_card_still_comes_down_when_end_notifications_are_off():
    """No banner is coming to replace it, so the dismissal is the only thing
    that will take it off the phone."""
    coord, hass = _coordinator()
    coord._notify_completed = False
    _frame(coord, hass, **_printing(40))

    payloads = _confirm(coord, hass)

    assert _events(payloads) == []
    assert len(_clears(payloads)) == 1
    assert coord._live_card.card_active is False


def test_a_finished_print_is_not_announced_as_stopped_as_well():
    """The printer resets the progress to 0 a while after a job ends, which is
    the same shape a stop has. Reading it as one would follow every completion
    with "stopped at 99%"."""
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(50))
    hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)
    done = _frame(coord, hass, **_printing(100, printLeftTime=0))
    assert len(_events(done)) == 1

    payloads = _confirm(coord, hass, frames=3)
    assert _events(payloads) == []
