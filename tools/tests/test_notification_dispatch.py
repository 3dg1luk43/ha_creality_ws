"""Notification delivery: fan-out, dual dispatch and the sentinel guards.

Separate from test_notifications.py on purpose. That file drives the *gating*
rules through a deliberately thin HassStub, and the narrowness of that stub is
itself a guard: it fails loudly if the rules engine ever starts touching
`hass.services` or the entity registry from the WebSocket path. These tests need
the opposite -- a stub fat enough to observe real service calls.
"""

import asyncio
from types import SimpleNamespace

import pytest

from conftest import fake_config_entry

from custom_components.ha_creality_ws.const import CLEAR_NOTIFICATION_MARKER
from custom_components.ha_creality_ws.coordinator import KCoordinator


class HassStub:
    def __init__(self):
        self.loop = asyncio.get_event_loop()
        self._states: dict[str, SimpleNamespace] = {}
        self.states = SimpleNamespace(
            get=self._states.get,
            async_entity_ids=lambda domain=None: [
                e for e in self._states if domain is None or e.startswith(f"{domain}.")
            ],
        )
        self.calls: list[tuple[str, str, dict]] = []
        self.fail_on: set[str] = set()
        self.services = SimpleNamespace(
            async_call=self._async_call,
            async_services=lambda: {"notify": {"mobile_app_test": None}},
        )
        self.tasks: list = []
        self.entries: dict[str, SimpleNamespace] = {}
        self.config = SimpleNamespace(language="en")
        self.config_entries = SimpleNamespace(async_get_entry=self.entries.get)

    async def _async_call(self, domain, service, data, **_kw):
        target = data.get("entity_id") or f"{domain}.{service}"
        if target in self.fail_on:
            raise RuntimeError(f"{target} is unreachable")
        self.calls.append((domain, service, data))

    def async_create_task(self, coro):
        # Collected, never closed: closing it here would make every dispatch
        # test pass vacuously.
        self.tasks.append(coro)
        return coro

    def set_state(self, entity_id, state="on", *, domain=None, **attrs):
        self._states[entity_id] = SimpleNamespace(
            entity_id=entity_id,
            state=state,
            domain=domain or entity_id.split(".", 1)[0],
            attributes=attrs,
        )


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _coordinator(targets):
    hass = HassStub()
    coord = KCoordinator(hass, host="1.2.3.4")
    coord._notify_targets = list(targets)
    return coord, hass


def _flush(hass):
    """Await everything dispatch queued, then drain the recorded calls.

    Drains rather than reads, so a test that dispatches twice cannot silently
    keep asserting against the first payload.
    """
    pending, hass.tasks = hass.tasks, []
    if pending:
        _run(asyncio.gather(*pending))
    calls, hass.calls = hass.calls, []
    return calls


@pytest.fixture(autouse=True)
def _loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield
    loop.close()


RICH = {
    "title": "K1C",
    "message": "42%",
    "data": {"tag": "ha_creality_ws_abc_job", "live_update": True, "progress": 42},
}


# --------------------------------------------------------------------------- #
# Dual dispatch
# --------------------------------------------------------------------------- #


def test_a_legacy_mobile_service_receives_the_full_payload():
    coord, hass = _coordinator(["notify.mobile_app_pixel"])
    coord._notify_dispatch(RICH)
    assert _flush(hass) == [
        (
            "notify",
            "mobile_app_pixel",
            {"message": "42%", "title": "K1C", "data": RICH["data"]},
        )
    ]


def test_a_notify_entity_routes_to_send_message_without_data():
    """`notify.send_message` is the only service for entity targets and its
    schema has no `data` field, so extras have to be dropped rather than sent."""
    coord, hass = _coordinator(["notify.my_notifier"])
    hass.set_state("notify.my_notifier", domain="notify")
    coord._notify_dispatch(RICH)
    assert _flush(hass) == [
        (
            "notify",
            "send_message",
            {"entity_id": "notify.my_notifier", "message": "42%", "title": "K1C"},
        )
    ]


def test_a_non_mobile_service_gets_no_data_at_all():
    """Strict-schema platforms reject unknown keys and fail the whole call, so
    this is a blanket rule rather than a per-key allowlist."""
    coord, hass = _coordinator(["notify.signal_messenger"])
    coord._notify_dispatch(RICH)
    assert _flush(hass) == [
        ("notify", "signal_messenger", {"message": "42%", "title": "K1C"})
    ]


def test_a_target_outside_the_notify_domain_still_works():
    coord, hass = _coordinator(["custom_notifier.send"])
    coord._notify_dispatch({"title": "K1C", "message": "hi"})
    assert _flush(hass) == [
        ("custom_notifier", "send", {"message": "hi", "title": "K1C"})
    ]


def test_an_unroutable_target_is_logged_rather_than_silently_dropped(caplog):
    """The options flow accepts free text; this used to no-op with no trace."""
    coord, hass = _coordinator(["mobile_app_pixel_typo"])
    coord._notify_dispatch(RICH)
    with caplog.at_level("WARNING"):
        assert _flush(hass) == []
    assert "expected the form domain.service" in caplog.text


# --------------------------------------------------------------------------- #
# The dismiss sentinel
# --------------------------------------------------------------------------- #


CLEAR = {"message": CLEAR_NOTIFICATION_MARKER, "data": {"tag": "t"}}


def test_the_sentinel_reaches_a_mobile_target():
    coord, hass = _coordinator(["notify.mobile_app_pixel"])
    coord._notify_dispatch(CLEAR)
    assert _flush(hass) == [
        ("notify", "mobile_app_pixel", {"message": CLEAR_NOTIFICATION_MARKER, "data": {"tag": "t"}})
    ]


def test_the_sentinel_never_reaches_a_notify_entity():
    """It can't carry a tag there, so it would just show up as body text."""
    coord, hass = _coordinator(["notify.my_notifier"])
    hass.set_state("notify.my_notifier", domain="notify")
    coord._notify_dispatch(CLEAR)
    assert _flush(hass) == []


def test_the_sentinel_never_reaches_a_non_mobile_service():
    coord, hass = _coordinator(["notify.signal_messenger"])
    coord._notify_dispatch(CLEAR)
    assert _flush(hass) == []


# --------------------------------------------------------------------------- #
# Fan-out
# --------------------------------------------------------------------------- #


def test_every_target_gets_its_own_call():
    coord, hass = _coordinator(
        ["notify.mobile_app_pixel", "notify.mobile_app_ipad", "notify.signal_messenger"]
    )
    coord._notify_dispatch(RICH)
    calls = _flush(hass)
    assert [c[1] for c in calls] == [
        "mobile_app_pixel",
        "mobile_app_ipad",
        "signal_messenger",
    ]
    # ...and only the mobile ones carry the rich payload.
    assert "data" in calls[0][2] and "data" in calls[1][2]
    assert "data" not in calls[2][2]


def test_one_unreachable_target_does_not_starve_the_others():
    coord, hass = _coordinator(["notify.mobile_app_dead", "notify.mobile_app_alive"])
    hass.fail_on.add("notify.mobile_app_dead")
    coord._notify_dispatch(RICH)
    calls = _flush(hass)
    assert [c[1] for c in calls] == ["mobile_app_alive"]


def test_live_pushes_skip_non_mobile_targets():
    coord, hass = _coordinator(["notify.mobile_app_pixel", "notify.signal_messenger"])
    coord._notify_dispatch(RICH, mobile_only=True)
    calls = _flush(hass)
    assert [c[1] for c in calls] == ["mobile_app_pixel"]


def test_no_targets_means_no_work():
    coord, hass = _coordinator([])
    coord._notify_dispatch(RICH)
    assert hass.tasks == []
    assert hass.calls == []


def test_a_mobile_only_push_with_only_non_mobile_targets_logs_nothing(caplog):
    coord, hass = _coordinator(["notify.signal_messenger"])
    with caplog.at_level("INFO"):
        coord._notify_dispatch(RICH, mobile_only=True)
    assert hass.tasks == []
    assert "Notification dispatched" not in caplog.text


def test_the_info_log_leaks_neither_targets_nor_the_body(caplog):
    """Both end up in user-submitted bug reports, and both are theirs."""
    coord, hass = _coordinator(["notify.mobile_app_seans_iphone"])
    with caplog.at_level("INFO"):
        coord._notify_dispatch(
            {"title": "K1C", "message": "Print 'secret_project.gcode' completed"},
            kind="completed",
        )
    records = [r for r in caplog.records if r.levelname == "INFO"]
    assert records, "expected an INFO line"
    text = "\n".join(r.getMessage() for r in records)
    assert "completed" in text
    assert "seans_iphone" not in text
    assert "secret_project" not in text
    _flush(hass)


# --------------------------------------------------------------------------- #
# The WebSocket hot path
# --------------------------------------------------------------------------- #


class ExplodingHass(HassStub):
    """Fails the test if the no-target path reaches for a service or a state.

    The bus is exempt and recorded instead: firing events without any notify
    target configured is the whole point of them.
    """

    def __init__(self):
        super().__init__()
        self.events: list[tuple[str, dict]] = []
        self.bus = SimpleNamespace(async_fire=self._async_fire)

    def _async_fire(self, event_type, data=None):
        self.events.append((event_type, data or {}))

    @property
    def states(self):
        raise AssertionError("states must not be touched on the no-target path")

    @states.setter
    def states(self, _value):
        pass

    @property
    def services(self):
        raise AssertionError("services must not be touched on the no-target path")

    @services.setter
    def services(self, _value):
        pass


def _drive_frames(coord):
    """A whole print, with a fault appearing partway through.

    The fault has to start clean: `_prime_notification_state` baselines whatever
    the printer is already reporting, so an error present on the very first
    frame is correctly treated as pre-existing and never announced (issue #112).
    """
    for progress in (0, 42, 99, 100):
        coord.data = {
            "printFileName": "/usr/data/printer_data/gcodes/x.gcode",
            "printProgress": progress,
            "printJobTime": 600 + progress,
            "printLeftTime": 120,
            "err": {"errcode": 7, "key": 1} if progress >= 99 else {"errcode": 0},
            "materialStatus": 1,
        }
        _run(coord._check_notifications({}))


def _no_target_coordinator(hass):
    coord = KCoordinator(hass, host="1.2.3.4")
    coord._notify_targets = []
    coord._notify_live = True
    coord._notify_completed = True
    coord._notify_error = True
    coord._notify_minutes_to_end = True
    return coord


def test_the_no_target_path_builds_and_sends_nothing():
    """`_check_notifications` runs on every WebSocket frame, and ws_client awaits
    it inline in its receive loop. With nothing configured it must do no payload
    work and touch no service -- the cheap, durable guard against a stall
    creeping back in."""
    hass = ExplodingHass()
    _drive_frames(_no_target_coordinator(hass))
    assert hass.tasks == []


def test_bus_events_fire_even_with_no_notify_target():
    """They are the supported escape hatch for building your own notification
    text -- in your own language -- so they cannot depend on delivery."""
    hass = ExplodingHass()
    _drive_frames(_no_target_coordinator(hass))
    fired = [name for name, _data in hass.events]
    assert "ha_creality_ws_print_finished" in fired
    assert "ha_creality_ws_print_error" in fired


def test_a_bus_event_carries_the_job_detail_an_automation_needs():
    hass = ExplodingHass()
    _drive_frames(_no_target_coordinator(hass))
    finished = next(d for name, d in hass.events if name.endswith("_print_finished"))
    # Basenamed, not the raw K1C path.
    assert finished["filename"] == "x.gcode"
    assert finished["progress"] == 100
    assert finished["host"] == "1.2.3.4"
    assert finished["err_code"] == 7


def test_state_still_advances_without_a_target():
    """It used to stop: `_last_job_time` only advanced past the old early
    return, so enabling notifications mid-print saw a phantom job restart and
    fired a spurious completion."""
    hass = ExplodingHass()
    coord = _no_target_coordinator(hass)
    _drive_frames(coord)
    assert coord._last_job_time == 700
    assert coord._notified_completed is True


# --------------------------------------------------------------------------- #
# Option migration
# --------------------------------------------------------------------------- #


def _load(options):
    """A coordinator whose options have been read from its config entry."""
    return KCoordinator(
        HassStub(), host="1.2.3.4", config_entry=fake_config_entry("e1", options)
    )


def test_a_legacy_single_device_still_notifies_after_upgrade():
    coord = _load({"notify_device": "notify.mobile_app_pixel", "notify_completed": True})
    assert coord._notify_targets == ["notify.mobile_app_pixel"]


def test_the_new_key_wins_and_an_emptied_list_stays_empty():
    coord = _load({"notify_device": "notify.old", "notify_targets": ["notify.new"]})
    assert coord._notify_targets == ["notify.new"]

    coord = _load({"notify_device": "notify.old", "notify_targets": []})
    assert coord._notify_targets == []


def test_the_filename_in_a_message_is_basenamed():
    """K1C reports a full path and the old messages interpolated it raw."""
    coord, hass = _coordinator(["notify.mobile_app_pixel"])
    coord._notify_primed = True
    coord._notify_completed = True
    coord._notified_completed = False
    coord._last_filename = "/usr/data/printer_data/gcodes/3DBenchy.gcode"
    coord.data = {
        "printFileName": "/usr/data/printer_data/gcodes/3DBenchy.gcode",
        "printProgress": 100,
    }
    _run(coord._check_notifications({}))
    calls = _flush(hass)
    assert calls[0][2]["message"] == "Print 3DBenchy.gcode completed successfully!"


def test_a_new_file_fires_the_started_event():
    hass = ExplodingHass()
    coord = _no_target_coordinator(hass)
    _drive_frames(coord)  # primes on job "x.gcode", then finishes it

    coord.data = {
        "printFileName": "/usr/data/printer_data/gcodes/bracket.gcode",
        "printProgress": 1,
        "printJobTime": 10,
        "printLeftTime": 7200,
        "layer": 1,
        "TotalLayer": 400,
        "err": {"errcode": 0},
    }
    _run(coord._check_notifications({}))

    started = [d for name, d in hass.events if name.endswith("_print_started")]
    assert len(started) == 1
    assert started[0]["filename"] == "bracket.gcode"
    assert started[0]["total_layers"] == 400


def test_a_job_already_running_at_startup_does_not_fire_started():
    """Priming baselines the file name, so adopting a print in progress is not
    the same thing as a new job beginning."""
    hass = ExplodingHass()
    coord = _no_target_coordinator(hass)
    for _ in range(3):
        coord.data = {
            "printFileName": "/usr/data/printer_data/gcodes/x.gcode",
            "printProgress": 42,
            "printJobTime": 600,
            "err": {"errcode": 0},
        }
        _run(coord._check_notifications({}))
    assert [n for n, _d in hass.events if n.endswith("_print_started")] == []


def test_a_new_name_arriving_with_the_old_jobs_progress_is_not_a_start():
    """Telemetry arrives incrementally, so the frame that first carries a new
    file name usually still carries the *previous* job's 100%. Announcing a
    start there would report a print beginning at 100%."""
    hass = ExplodingHass()
    coord = _no_target_coordinator(hass)
    coord.data = {
        "printFileName": "/usr/data/printer_data/gcodes/x.gcode",
        "printProgress": 100,
        "printJobTime": 600,
        "err": {"errcode": 0},
    }
    _run(coord._check_notifications({}))  # primes

    coord.data = {
        "printFileName": "/usr/data/printer_data/gcodes/next.gcode",
        "printProgress": 100,  # still the old job's value
        "printJobTime": 601,
        "err": {"errcode": 0},
    }
    _run(coord._check_notifications({}))
    assert [n for n, _d in hass.events if n.endswith("_print_started")] == []

    # The next frame carries the new job's real progress, and that is the start.
    coord.data = {
        "printFileName": "/usr/data/printer_data/gcodes/next.gcode",
        "printProgress": 0,
        "printJobTime": 5,
        "err": {"errcode": 0},
    }
    _run(coord._check_notifications({}))
    started = [d for n, d in hass.events if n.endswith("_print_started")]
    assert len(started) == 1
    assert started[0]["filename"] == "next.gcode"


def test_clearing_the_file_name_is_not_a_start():
    hass = ExplodingHass()
    coord = _no_target_coordinator(hass)
    coord.data = {
        "printFileName": "/usr/data/printer_data/gcodes/x.gcode",
        "printProgress": 42,
        "err": {"errcode": 0},
    }
    _run(coord._check_notifications({}))  # primes
    coord.data = {"printFileName": "", "printProgress": 0, "err": {"errcode": 0}}
    _run(coord._check_notifications({}))
    assert [n for n, _d in hass.events if n.endswith("_print_started")] == []


def test_live_pushes_do_not_spam_the_info_log(caplog):
    """There are roughly twenty per print; at INFO they would bury the handful
    of lines that describe something actually happening."""
    coord, hass = _coordinator(["notify.mobile_app_pixel"])
    with caplog.at_level("INFO"):
        coord._notify_dispatch(RICH, kind="live:milestone", mobile_only=True)
    assert [r for r in caplog.records if r.levelname == "INFO"] == []
    _flush(hass)


def test_lifecycle_notifications_are_still_logged_at_info(caplog):
    coord, hass = _coordinator(["notify.mobile_app_pixel"])
    with caplog.at_level("INFO"):
        coord._notify_dispatch(RICH, kind="completed")
    assert [r for r in caplog.records if r.levelname == "INFO"]
    _flush(hass)


def test_bus_events_ignore_the_per_event_notification_toggles():
    """They are documented as firing regardless of notification settings. Nesting
    them inside the toggles meant the one user they exist for -- someone writing
    their own text because ours can only follow the server language -- got
    nothing but `print_started`."""
    hass = ExplodingHass()
    coord = _no_target_coordinator(hass)
    coord._notify_completed = False
    coord._notify_error = False
    coord._notify_minutes_to_end = False
    _drive_frames(coord)

    fired = {name for name, _d in hass.events}
    assert "ha_creality_ws_print_finished" in fired
    assert "ha_creality_ws_print_error" in fired


def test_the_error_code_baseline_advances_with_notifications_off():
    """It used to only advance inside the option check, so switching error
    notifications on mid-print immediately replayed an error already showing."""
    hass = ExplodingHass()
    coord = _no_target_coordinator(hass)
    coord._notify_error = False
    _drive_frames(coord)
    assert coord._last_error_code == 7
    assert coord._notified_filament_runout is True
