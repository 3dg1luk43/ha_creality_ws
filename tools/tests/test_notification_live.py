"""The live print card: lifecycle, throttling and the traps around it.

Drives `_check_notifications` frame by frame, the way the WebSocket client does,
with a controllable clock so the interval floor and the milestone latch can be
exercised without sleeping.
"""

import asyncio
import time
from types import SimpleNamespace

import pytest

from conftest import fake_config_entry

from custom_components.ha_creality_ws.const import (
    CLEAR_NOTIFICATION_MARKER,
    NOTIFY_LIVE_MILESTONE_STEP,
    NOTIFY_LIVE_MIN_INTERVAL_SECS,
    NOTIFY_LIVE_STALE_CLEAR_SECS,
)
from custom_components.ha_creality_ws.coordinator import KCoordinator


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
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield
    loop.close()


def _coordinator(*, targets=("notify.mobile_app_pixel",), live=True):
    hass = HassStub()
    coord = KCoordinator(hass, host="1.2.3.4", config_entry=fake_config_entry("abc123"))
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
    """Live-card *pushes*. The dismiss sentinel shares the tag, so exclude it."""
    return [
        p
        for p in payloads
        if p["data"]["tag"].endswith("_live")
        and p["message"] != CLEAR_NOTIFICATION_MARKER
    ]


def _clears(payloads):
    return [p for p in payloads if p["message"] == CLEAR_NOTIFICATION_MARKER]


def _events(payloads):
    return [p for p in payloads if p["data"]["tag"].endswith("_event")]


# --------------------------------------------------------------------------- #
# Starting
# --------------------------------------------------------------------------- #


def test_a_print_starts_exactly_one_card():
    coord, hass = _coordinator()
    first = _live(_frame(coord, hass, **_printing(5)))
    assert len(first) == 1
    data = first[0]["data"]
    assert data["live_update"] is True
    assert data["progress"] == 5
    assert data["chronometer"] is True
    assert data["progress_max"] == 100

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
    assert data["when"] >= int(before + 3600) - 2
    assert data["when"] > 1_700_000_000


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
    assert data["chronometer"] is False
    assert "when" not in data
    assert data["critical_text"] == "Paused"


def test_resuming_is_pushed_immediately_too():
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(42))
    coord.hass.loop.advance(6)
    _frame(coord, hass, **_printing(42, state=5))
    coord.hass.loop.advance(6)
    data = _live(_frame(coord, hass, **_printing(42, state=1)))[0]["data"]
    assert data["chronometer"] is True


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

    # The activity has to be ended with the sentinel; a same-tag banner does not
    # end one. And the banner lands on its own tag, so it survives.
    clears = _clears(payloads)
    assert len(clears) == 1
    assert clears[0]["data"]["tag"].endswith("_live")

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
    assert _live(payloads)[0]["data"]["progress"] == 2


def test_a_job_that_finished_before_startup_never_gets_a_card():
    """Issue #112: the printer reports the last job's name and 100% forever."""
    hass = HassStub()
    coord = KCoordinator(hass, host="1.2.3.4", config_entry=fake_config_entry("abc123"))
    coord._notify_targets = ["notify.mobile_app_pixel"]
    coord._notify_live = True
    coord._notify_completed = True
    # Not primed: the first frame is a baseline, not an event.
    payloads = _frame(coord, hass, **_printing(100, printLeftTime=0))
    assert payloads == []
    assert coord._live_card.card_active is False


def test_a_restart_mid_print_resyncs_the_card_instead_of_leaving_it_frozen():
    hass = HassStub()
    coord = KCoordinator(hass, host="1.2.3.4", config_entry=fake_config_entry("abc123"))
    coord._notify_targets = ["notify.mobile_app_pixel"]
    coord._notify_live = True
    coord._notify_completed = True

    # First frame primes silently.
    assert _frame(coord, hass, **_printing(42)) == []
    # Second frame adopts the running job.
    coord.hass.loop.advance(1)
    payloads = _live(_frame(coord, hass, **_printing(42)))
    assert len(payloads) == 1
    assert payloads[0]["data"]["progress"] == 42


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
    assert later[0]["data"]["progress"] == 40


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
    assert data["progress"] == 30


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
    """A stopped print never sets `_notified_completed`, which is the only thing
    that re-arms the jitter latch -- so retiring the card on any non-busy frame
    meant the same file could never show a card again."""
    coord, hass = _coordinator()
    _frame(coord, hass, **_printing(30))
    assert coord._live_card.card_active is True

    # state 4 is "stopped": job over, but nowhere near 100%.
    coord.hass.loop.advance(NOTIFY_LIVE_MIN_INTERVAL_SECS + 1)
    stopped = _frame(coord, hass, **_printing(30, state=4))
    assert len(_clears(stopped)) == 1
    assert coord._live_card.job_finished is False, "a stop must not retire the card"

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

    # Long past the old deadline, and still nothing to say.
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
    assert len(_clears(payloads)) == 1, "the card is still dismissed"

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
    coord = KCoordinator(hass, host="1.2.3.4", config_entry=fake_config_entry("abc123"))
    coord._notify_targets = ["notify.mobile_app_pixel"]
    coord._notify_completed = True
    coord._notify_live = True

    assert _frame(coord, hass, **_printing(30, state=4)) == []
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
