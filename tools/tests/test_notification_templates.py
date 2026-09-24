"""Custom notification text: the placeholders, and what falls back to ours.

The rendering itself is covered in test_notification_rules; this module is
about the wiring -- that a template configured in the options flow reaches the
notification it names, that the values fed to it come from the telemetry frame
that triggered the push, and that anything unusable quietly leaves the shipped
sentence in place.
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import fake_config_entry

from custom_components.ha_creality_ws.const import (
    CLEAR_NOTIFICATION_MARKER,
    NOTIFY_END_CONFIRM_SECS,
    CONF_NOTIFY_LIVE,
    CONF_NOTIFY_TARGETS,
    CONF_NOTIFY_TEMPLATE_COMPLETED,
    CONF_NOTIFY_TEMPLATE_LIVE,
    CONF_NOTIFY_TEMPLATE_STOPPED,
    NOTIFY_BODY_SEPARATOR,
    NOTIFY_TEMPLATE_OPTIONS,
)
from custom_components.ha_creality_ws.coordinator import KCoordinator

_STRINGS = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "custom_components/ha_creality_ws/strings.json"
    ).read_text(encoding="utf-8")
)["common"]


class Clock:
    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class HassStub:
    def __init__(self):
        self.loop = Clock()
        self.states = SimpleNamespace(get=lambda _id: None)
        self.calls: list[tuple[str, str, dict]] = []
        self.services = SimpleNamespace(async_call=self._async_call)
        self.tasks: list = []
        self.config_entries = SimpleNamespace(async_get_entry=lambda _id: None)
        self.config = SimpleNamespace(language="en")
        self.bus = SimpleNamespace(async_fire=lambda *_a, **_k: None)

    async def _async_call(self, domain, service, data, **_kw):
        self.calls.append((domain, service, data))

    def async_create_task(self, coro):
        self.tasks.append(coro)
        return coro


@pytest.fixture(autouse=True)
def _loop():
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


def _coordinator(**templates):
    """A printer whose notifications are configured through the options.

    Built from a real options dict and `_load_options`, not by assigning the
    private attributes: the option keys are half of what is under test here.
    """
    hass = HassStub()
    options = {
        CONF_NOTIFY_TARGETS: ["notify.mobile_app_pixel"],
        CONF_NOTIFY_LIVE: True,
        **templates,
    }
    coord = KCoordinator(
        hass,
        host="1.2.3.4",
        config_entry=fake_config_entry("abc123", options=options),
    )
    coord.client._last = None
    coord.client.last_rx_monotonic = lambda: hass.loop.now
    coord._load_options()
    coord._notify_completed = True
    coord._notify_error = True
    coord._notify_primed = True
    coord._notified_completed = False
    return coord, hass


def _printing(progress=10, **kw):
    payload = {
        "printFileName": "/usr/data/printer_data/gcodes/3DBenchy.gcode",
        "printProgress": progress,
        "printJobTime": 3600,
        "printLeftTime": 1800,
        "state": 1,
        "layer": progress * 3,
        "TotalLayer": 300,
        "usedMaterialLength": 4200,
        "nozzleTemp": 219.7,
        "bedTemp0": 60.2,
    }
    payload.update(kw)
    return payload


def _frame(coord, hass, **telemetry):
    coord.data = {"hostname": "K1C", **telemetry}
    asyncio.get_event_loop().run_until_complete(coord._check_notifications({}))
    pending, hass.tasks = hass.tasks, []
    if pending:
        asyncio.get_event_loop().run_until_complete(asyncio.gather(*pending))
    calls, hass.calls = hass.calls, []
    return [c[2] for c in calls]


def _cards(payloads):
    return [
        p
        for p in payloads
        if p["data"]["tag"].endswith("_live")
        and p["data"].get("activity") != "end"
        and p["message"] != CLEAR_NOTIFICATION_MARKER
    ]


def _terminal(payloads):
    return [p for p in payloads if p["data"].get("activity") == "end"]


def _finish(coord, hass):
    """Drive a print to completion and return the banner it produced."""
    _frame(coord, hass, **_printing(50))
    hass.loop.advance(60)
    return _terminal(_frame(coord, hass, **_printing(100, printLeftTime=0)))


# --------------------------------------------------------------------------- #
# The options reach the notifications they name
# --------------------------------------------------------------------------- #


def test_every_template_option_is_loaded_under_the_name_the_code_uses():
    """`NOTIFY_TEMPLATE_OPTIONS` is the only thing tying an option key to the
    notification it customises, and a typo on either side is silent: the field
    saves, and nothing ever reads it."""
    coord, _hass = _coordinator(
        **{key: f"text for {name}" for name, key in NOTIFY_TEMPLATE_OPTIONS.items()}
    )
    assert coord._notify_templates == {
        name: f"text for {name}" for name in NOTIFY_TEMPLATE_OPTIONS
    }


def test_a_live_template_replaces_the_card_body():
    coord, hass = _coordinator(
        **{CONF_NOTIFY_TEMPLATE_LIVE: "{filename} is {progress}% done on {device}"}
    )
    card = _cards(_frame(coord, hass, **_printing(42)))[0]
    assert card["message"] == "3DBenchy.gcode is 42% done on K1C"
    # The title is still the printer, because Android cannot change one after
    # the activity has started.
    assert card["title"] == "K1C"


def test_a_completion_template_replaces_the_finished_text():
    coord, hass = _coordinator(
        **{
            CONF_NOTIFY_TEMPLATE_COMPLETED:
                "{device} finished {filename} in {elapsed} using {filament}"
        }
    )
    banner = _finish(coord, hass)[0]
    assert banner["message"] == (
        "K1C finished 3DBenchy.gcode in 1h 00m using 4.2 m"
    )


def test_the_values_come_from_the_frame_that_triggered_the_push():
    """Every placeholder is read from one telemetry frame, so a card cannot mix
    this second's percentage with the last one's estimate."""
    coord, hass = _coordinator(
        **{
            CONF_NOTIFY_TEMPLATE_LIVE:
                "{progress} {layer}/{total_layers} {eta} {nozzle} {bed} {state}"
        }
    )
    card = _cards(_frame(coord, hass, **_printing(20)))[0]
    # Temperatures are whole degrees: 219.7 and 60.2 as reported.
    assert card["message"] == "20 60/300 30m 220 60 printing"


def test_a_template_for_a_stop_gets_the_progress_from_before_it():
    """The frame that reveals a stop has already reset the progress to 0 and may
    have cleared the file name, so both come from the watch rather than from
    `self.data` -- and a user template has to be filled from the same place, or
    their own wording would be the one reading "stopped at 0%"."""
    coord, hass = _coordinator(
        **{CONF_NOTIFY_TEMPLATE_STOPPED: "{filename} stopped at {progress}%"}
    )
    _frame(coord, hass, **_printing(40))
    # What a cancelled print actually reports: state 0, progress and job clock
    # reset, file name untouched.
    stopped = dict(_printing(0), printJobTime=0, printLeftTime=0, state=0, layer=0)
    _frame(coord, hass, **stopped)
    hass.loop.advance(NOTIFY_END_CONFIRM_SECS + 1)

    banner = _terminal(_frame(coord, hass, **stopped))[0]
    assert banner["message"] == "3DBenchy.gcode stopped at 40%"


def test_a_stopped_print_gets_its_own_template():
    coord, hass = _coordinator(
        **{CONF_NOTIFY_TEMPLATE_STOPPED: "{filename} stopped at {progress}%"}
    )
    _frame(coord, hass, **_printing(30))
    hass.loop.advance(60)
    # state 4 is what the printer reports for a cancelled job.
    banner = _terminal(_frame(coord, hass, **_printing(30, state=4)))[0]
    assert banner["message"] == "3DBenchy.gcode stopped at 30%"


# --------------------------------------------------------------------------- #
# Falling back
# --------------------------------------------------------------------------- #


def test_no_template_leaves_the_shipped_text_alone():
    coord, hass = _coordinator()
    banner = _finish(coord, hass)[0]
    assert banner["message"] == _STRINGS["completed_detailed"].format(
        filename="3DBenchy.gcode", duration="1h 00m", filament="4.2 m"
    )


@pytest.mark.parametrize(
    "template",
    [
        # A value this printer does not stream at all.
        "used {filament_grams} of filament",
        # A real placeholder, but not one a completion can fill: the print has
        # finished, so there is no time remaining to report.
        "done, {eta} left",
    ],
)
def test_a_template_naming_something_that_does_not_exist_falls_back(template):
    """The options flow refuses these, so reaching here means a hand-edited
    .storage -- and the notification still has to arrive."""
    coord, hass = _coordinator(**{CONF_NOTIFY_TEMPLATE_COMPLETED: template})
    banner = _finish(coord, hass)[0]
    assert "{" not in banner["message"]
    assert banner["message"] == _STRINGS["completed_detailed"].format(
        filename="3DBenchy.gcode", duration="1h 00m", filament="4.2 m"
    )


def test_a_template_that_renders_to_nothing_falls_back():
    """A blank notification is worse than a generic one: the user would see an
    empty banner and have nothing to act on. `{eta}` is a real placeholder that
    this frame simply cannot fill -- the printer reports no estimate in the
    first minutes of a print -- so the template's one segment drops out."""
    coord, hass = _coordinator(**{CONF_NOTIFY_TEMPLATE_LIVE: "[{eta} to go]"})
    no_estimate = _printing(42)
    del no_estimate["printLeftTime"]
    card = _cards(_frame(coord, hass, **no_estimate))[0]
    assert card["message"] == NOTIFY_BODY_SEPARATOR.join(
        (
            "3DBenchy.gcode",
            _STRINGS["body_progress"].format(progress=42),
            _STRINGS["body_layer"].format(layer=126, total_layers=300),
        )
    )


def test_an_emptied_field_restores_the_built_in_text():
    """Clearing the box is how a user gets the default back, so whitespace has
    to count as empty rather than as a template that renders one space."""
    coord, _hass = _coordinator(**{CONF_NOTIFY_TEMPLATE_LIVE: "   "})
    assert coord._notify_templates == {}


# --------------------------------------------------------------------------- #
# The finished banner is a confirmation
# --------------------------------------------------------------------------- #


def test_the_finished_banner_carries_no_progress_bar():
    """End to end, not just at the payload builder: the coordinator used to
    pass the printer's 100 into it, so the notification announcing that a print
    had finished still rendered the live card's bar, full."""
    coord, hass = _coordinator()
    banner = _finish(coord, hass)[0]
    for key in ("progress", "progress_max", "progress_indeterminate",
                "chronometer", "when", "live_update", "actions"):
        assert key not in banner["data"], key


# --------------------------------------------------------------------------- #
# Costing nothing when nobody uses it
# --------------------------------------------------------------------------- #


def test_the_card_path_computes_no_values_without_a_template():
    """`_check_notifications` runs on every WebSocket frame, and ws_client
    awaits it inline in its receive loop. Formatting six numbers per frame to
    throw them away is exactly the kind of work that does not belong there."""
    coord, hass = _coordinator()
    coord._template_values = lambda **_kw: pytest.fail(
        "no template is configured, so nothing should have been formatted"
    )
    assert _cards(_frame(coord, hass, **_printing(42)))


def test_an_unrenderable_template_is_complained_about_once(caplog):
    """A card is pushed up to 600 times a job, and a template that renders to
    nothing usually does so for the whole print."""
    coord, hass = _coordinator(**{CONF_NOTIFY_TEMPLATE_LIVE: "[{minutes} to go]"})
    with caplog.at_level("WARNING"):
        for progress in (10, 20, 30):
            hass.loop.advance(60)
            assert _cards(_frame(coord, hass, **_printing(progress)))
    complaints = [r for r in caplog.records if "could not be rendered" in r.message]
    assert len(complaints) == 1
