"""Sliced-G-code metadata: the listing reply, and the sensors built on it.

Issue #122. The printer knows what the slicer estimated for the job it is
running but never streams it; asking for it (`reqGcodeFile`) returns metadata
for *every* file on the printer in one reply. Verified on a K1C, firmware
1.3.5.22 -- the shapes asserted here are that printer's, including the fields
it leaves empty.

Two properties carry most of the weight. The listing must never reach
coordinator data, because at a couple of hundred files it is ~150 KiB that
templates and the dashboard card would carry around. And the cached entry must
belong to the running job, never to the one before it.
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import fake_config_entry

from custom_components.ha_creality_ws.const import (  # noqa: E402
    GCODE_FILE_RESPONSE,
    GCODE_INFO_KEY,
    GCODE_INFO_MAX_ATTEMPTS,
    GCODE_INFO_RETRY_SECS,
    LATE_DISCOVERY_FIELDS,
)
from custom_components.ha_creality_ws.coordinator import KCoordinator  # noqa: E402

coord_mod = sys.modules[KCoordinator.__module__]

RUNNING = "/usr/data/printer_data/gcodes/Body14_PLA_25m52s.gcode"

# Trimmed to the fields the integration reads, but valued as the K1C sent them:
# `consumables` in mm, `filamentWeight` in grams as a string, `match` with the
# trailing whitespace the printer really includes.
ENTRY = {
    "name": "Body14_PLA_25m52s.gcode",
    "path": RUNNING,
    "timeCost": 1552,
    "consumables": 1536,
    "material": "PLA",
    "materialColors": "#26A69A",
    "filamentWeight": "4.62",
    "match": "T1A=  ",
    "software": "OrcaSlicer",
}

# Files the printer did not slice itself arrive with a length but no weight.
UNSLICED_ENTRY = {
    "name": "3DBenchy.gcode",
    "path": "/usr/data/printer_data/gcodes/3DBenchy.gcode",
    "timeCost": 983,
    "consumables": 3723,
    "material": "PLA",
    "materialColors": "",
    "filamentWeight": "",
    "match": "",
    "software": "",
}

OTHER_ENTRY = {
    "name": "older_job.gcode",
    "path": "/usr/data/printer_data/gcodes/older_job.gcode",
    "timeCost": 8300,
    "consumables": 7422,
    "filamentWeight": "22.32",
}

LISTING = [OTHER_ENTRY, ENTRY, UNSLICED_ENTRY]


class _Loop:
    """A clock the tests can wind forward, standing in for the event loop's."""

    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now


class HassStub:
    def __init__(self):
        self.loop = _Loop()
        self.states = SimpleNamespace(get=lambda _entity_id: None)


@pytest.fixture
def coord(monkeypatch):
    """Coordinator with the frame handler narrowed to the metadata path."""
    monkeypatch.setattr(coord_mod, "async_dispatcher_send", lambda *_a, **_kw: None)
    c = KCoordinator(HassStub(), host="1.2.3.4", config_entry=fake_config_entry("entry1"))
    monkeypatch.setattr(c, "_flush_pending", lambda _s=None: asyncio.sleep(0))
    monkeypatch.setattr(c, "_check_notifications", lambda _p: asyncio.sleep(0))
    monkeypatch.setattr(c, "async_update_listeners", lambda: None)

    requests: list[int] = []

    async def _request():
        requests.append(1)

    monkeypatch.setattr(c.client, "request_gcode_file_info", _request)
    c.requests = requests
    return c


_LOOP = None


def _loop():
    """This module's event loop, owned rather than borrowed.

    `asyncio.get_event_loop()` raises once anything in the suite has called
    `asyncio.run`, and these tests pass in isolation either way -- so borrowing
    one shows up only as a failure that depends on pytest's collection order.
    It is left installed because the sensor-platform harness reached for below
    does call `get_event_loop()`.
    """
    global _LOOP
    if _LOOP is None or _LOOP.is_closed():
        _LOOP = asyncio.new_event_loop()
    asyncio.set_event_loop(_LOOP)
    return _LOOP


def _feed(c, payload):
    _loop().run_until_complete(c._handle_message(payload))


# --- the listing reply ------------------------------------------------------ #


def test_listing_is_reduced_to_the_running_job(coord):
    """The array is the whole point of this test: it must not be stored."""
    _feed(coord, {"printFileName": RUNNING})
    _feed(coord, {GCODE_FILE_RESPONSE: LISTING})

    assert GCODE_FILE_RESPONSE not in coord.data
    assert coord.data[GCODE_INFO_KEY] == ENTRY


def test_a_bare_file_name_still_matches(coord):
    """Firmware reporting the job by base name can never match on `path`."""
    _feed(coord, {"printFileName": "Body14_PLA_25m52s.gcode"})
    _feed(coord, {GCODE_FILE_RESPONSE: LISTING})

    assert coord.data[GCODE_INFO_KEY] == ENTRY


def test_path_wins_over_a_same_named_file_elsewhere(coord):
    """Two files can share a base name; only one is the job being printed."""
    decoy = {**ENTRY, "path": "/usr/data/media/Body14_PLA_25m52s.gcode", "consumables": 1}
    _feed(coord, {"printFileName": RUNNING})
    _feed(coord, {GCODE_FILE_RESPONSE: [decoy, ENTRY]})

    assert coord.data[GCODE_INFO_KEY]["consumables"] == 1536


def test_an_unlisted_file_is_a_settled_answer(coord):
    """No metadata is a result, not a reason to keep asking for the listing."""
    _feed(coord, {"printFileName": RUNNING})
    assert coord.requests == [1]

    _feed(coord, {GCODE_FILE_RESPONSE: [OTHER_ENTRY]})
    assert coord.data.get(GCODE_INFO_KEY) is None

    coord.hass.loop.now += GCODE_INFO_RETRY_SECS * 10
    _feed(coord, {"printFileName": RUNNING})
    assert coord.requests == [1]


def test_a_malformed_reply_is_ignored(coord):
    _feed(coord, {"printFileName": RUNNING})
    _feed(coord, {GCODE_FILE_RESPONSE: {"name": "not a list"}})

    assert GCODE_FILE_RESPONSE not in coord.data
    assert coord.data.get(GCODE_INFO_KEY) is None


# --- when the listing is asked for ------------------------------------------ #


def test_the_listing_is_requested_once_per_file(coord):
    _feed(coord, {"printFileName": RUNNING})
    _feed(coord, {GCODE_FILE_RESPONSE: LISTING})

    for _ in range(5):
        coord.hass.loop.now += GCODE_INFO_RETRY_SECS * 2
        _feed(coord, {"printFileName": RUNNING, "printProgress": 20})

    assert coord.requests == [1]


def test_a_new_job_drops_the_previous_estimate_before_asking(coord):
    """The stale entry must go at once, not when the reply happens to land.

    Between the two it would otherwise be published as the new job's estimate,
    and a print started right after a much larger one would read as instantly
    over-consuming.
    """
    _feed(coord, {"printFileName": RUNNING})
    _feed(coord, {GCODE_FILE_RESPONSE: LISTING})
    assert coord.data[GCODE_INFO_KEY] == ENTRY

    _feed(coord, {"printFileName": "/usr/data/printer_data/gcodes/next.gcode"})
    assert coord.data.get(GCODE_INFO_KEY) is None
    assert coord.requests == [1, 1]


def test_silence_is_not_retried_forever(coord):
    """Firmware without the request answers nothing at all, so nothing arrives."""
    for _ in range(GCODE_INFO_MAX_ATTEMPTS + 5):
        coord.hass.loop.now += GCODE_INFO_RETRY_SECS * 2
        _feed(coord, {"printFileName": RUNNING})

    assert len(coord.requests) == GCODE_INFO_MAX_ATTEMPTS


def test_retries_wait_out_the_cooldown(coord):
    """Every frame carries the file name; every frame must not ask again."""
    for _ in range(10):
        _feed(coord, {"printFileName": RUNNING})

    assert coord.requests == [1]


def test_nothing_is_requested_without_a_file_name(coord):
    _feed(coord, {"printFileName": "", "state": 0})
    assert coord.requests == []


def test_the_metadata_key_gates_entity_creation():
    """The sensors are created on its first appearance, so it has to be listed."""
    assert GCODE_INFO_KEY in LATE_DISCOVERY_FIELDS


def test_invalidation_keeps_the_key_present(coord):
    """Clearing must not re-arm the one-shot the entities are gated on.

    Deleting the key would make it "newly seen" on the next job and fire the
    discovery pass again; seeding it with None before any metadata exists would
    spend the one-shot on a printer that never answered.
    """
    _feed(coord, {"printFileName": RUNNING})
    assert GCODE_INFO_KEY not in coord.data

    _feed(coord, {GCODE_FILE_RESPONSE: LISTING})
    _feed(coord, {"printFileName": "/usr/data/printer_data/gcodes/next.gcode"})
    assert GCODE_INFO_KEY in coord.data


# --- the sensors ------------------------------------------------------------ #

from custom_components.ha_creality_ws.sensor import (  # noqa: E402
    ExpectedMaterialLengthSensor,
    ExpectedMaterialWeightSensor,
    FilamentConsumptionSensor,
)


def _sensor(cls, info=ENTRY, used_mm=None):
    data = {"printFileName": RUNNING}
    if info is not None:
        data[GCODE_INFO_KEY] = info
    if used_mm is not None:
        data["usedMaterialLength"] = used_mm
    return cls(
        SimpleNamespace(
            client=SimpleNamespace(_host="1.2.3.4"),
            data=data,
            available=True,
            power_is_off=lambda: False,
        )
    )


def test_expected_length_is_reported_in_cm():
    """Matching UsedMaterialLengthSensor's unit is what makes the pair usable."""
    assert _sensor(ExpectedMaterialLengthSensor).native_value == 153.6


def test_expected_weight_is_grams():
    assert _sensor(ExpectedMaterialWeightSensor).native_value == 4.62


def test_expected_weight_is_unknown_when_the_printer_left_it_blank():
    """Creality-Print output and the stock models carry a length but no weight."""
    assert _sensor(ExpectedMaterialWeightSensor, UNSLICED_ENTRY).native_value is None
    assert _sensor(ExpectedMaterialLengthSensor, UNSLICED_ENTRY).native_value == 372.3


def test_a_multi_valued_weight_is_declined_rather_than_guessed():
    """A CFS job packs several weights into the field; none of them is the total.

    Unverifiable without the hardware (issue #122), so the sensor goes unknown
    instead of publishing one filament's weight as the whole job's.
    """
    multi = {**ENTRY, "filamentWeight": "189.72,34.47"}
    assert _sensor(ExpectedMaterialWeightSensor, multi).native_value is None


def test_consumption_is_used_over_expected():
    """The ratio the issue asked for: independent of printProgress."""
    assert _sensor(FilamentConsumptionSensor, used_mm=768).native_value == 50.0


def test_consumption_is_not_capped_at_full():
    """Running past the estimate is the case worth seeing, not worth hiding."""
    assert _sensor(FilamentConsumptionSensor, used_mm=1690).native_value == 110.0


def test_consumption_needs_both_halves():
    assert _sensor(FilamentConsumptionSensor, used_mm=None).native_value is None
    assert _sensor(FilamentConsumptionSensor, info=None, used_mm=768).native_value is None


def test_a_zero_estimate_is_no_estimate():
    """Guards the division, and no real job consumes nothing."""
    empty = {**ENTRY, "consumables": 0}
    assert _sensor(ExpectedMaterialLengthSensor, empty).native_value is None
    assert _sensor(FilamentConsumptionSensor, empty, used_mm=768).native_value is None


def test_estimates_survive_the_printer_going_away():
    """Unlike the live measurements, these describe a file, not a reading."""
    sensor = _sensor(ExpectedMaterialLengthSensor)
    sensor.coordinator.available = False
    sensor.coordinator.power_is_off = lambda: True
    assert sensor.native_value == 153.6


def test_length_attributes_pass_the_printer_s_own_wording_through():
    attrs = _sensor(ExpectedMaterialLengthSensor).extra_state_attributes
    assert attrs["material"] == "PLA"
    assert attrs["color"] == "#26A69A"
    assert attrs["slicer"] == "OrcaSlicer"
    assert attrs["estimated_time_s"] == 1552
    assert attrs["gcode_file"] == "Body14_PLA_25m52s.gcode"


def test_blank_attributes_are_omitted_rather_than_published_empty():
    attrs = _sensor(ExpectedMaterialLengthSensor, UNSLICED_ENTRY).extra_state_attributes
    assert "color" not in attrs
    assert "slicer" not in attrs
    assert attrs["material"] == "PLA"


# --- entity gating ---------------------------------------------------------- #


def _run_sensor_setup(coord_data):
    """Drive the real sensor platform, as test_late_discovery does."""
    from test_late_discovery import _run_sensor_setup as _run

    _loop()
    c = SimpleNamespace(
        data=dict(coord_data),
        client=SimpleNamespace(_host="1.2.3.4"),
        available=True,
        power_is_off=lambda: False,
        config_entry=SimpleNamespace(data={}),
    )
    return c, _run(c, {})


def _estimate_names(added):
    return {
        e._attr_translation_key
        for e in added
        if getattr(e, "_attr_translation_key", "").startswith(
            ("expected_material", "filament_consumption")
        )
    }


ESTIMATE_KEYS = {
    "expected_material_length",
    "expected_material_weight",
    "filament_consumption",
}


def test_no_estimate_sensors_before_the_printer_answers():
    """Firmware without the request never replies, so nothing should appear."""
    _coord, run = _run_sensor_setup({"printFileName": RUNNING})
    assert _estimate_names(run.added) == set()


def test_the_estimate_sensors_appear_with_the_metadata():
    _coord, run = _run_sensor_setup({"printFileName": RUNNING, GCODE_INFO_KEY: ENTRY})
    assert _estimate_names(run.added) == ESTIMATE_KEYS


def test_late_metadata_still_creates_them_exactly_once():
    """The printer may only answer long after the platform was set up."""
    coord, run = _run_sensor_setup({"printFileName": RUNNING})
    assert _estimate_names(run.added) == set()

    coord.data[GCODE_INFO_KEY] = ENTRY
    # Twice before flushing: the discovery signal fires on every gating field's
    # first appearance, so the second pass must queue nothing rather than a
    # duplicate set. Flushing between the two would only replay the harness's
    # own queue and prove nothing.
    run.fire_discovery()
    run.fire_discovery()
    run.flush()

    keys = [
        e._attr_translation_key
        for e in run.added
        if getattr(e, "_attr_translation_key", "") in ESTIMATE_KEYS
    ]
    assert sorted(keys) == sorted(ESTIMATE_KEYS)


# --------------------------------------------------------------------------- #
# Non-finite values from the printer
# --------------------------------------------------------------------------- #
#
# `json.loads` accepts the bare `NaN` and `Infinity` tokens and `float()`
# accepts the strings, so the payload can carry either. These sensors have a
# unit and a state class, so a non-finite value is not merely displayed: it is
# written into history and long-term statistics.


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan"), "inf", "nan"])
def test_a_non_finite_expected_length_is_declined(bad):
    """`mm and mm > 0` rejected NaN but let infinity straight through."""
    assert _sensor(ExpectedMaterialLengthSensor, {**ENTRY, "consumables": bad}).native_value is None


@pytest.mark.parametrize("bad", [float("inf"), float("nan"), "nan"])
def test_a_non_finite_expected_weight_is_declined(bad):
    """NaN compares false against everything, so `grams <= 0` never caught it."""
    assert _sensor(ExpectedMaterialWeightSensor, {**ENTRY, "filamentWeight": bad}).native_value is None


@pytest.mark.parametrize("bad", [float("inf"), float("nan")])
def test_a_non_finite_used_length_does_not_reach_the_percentage(bad):
    """The used length is divided by the estimate, so it propagates unchecked."""
    assert _sensor(FilamentConsumptionSensor, used_mm=bad).native_value is None


# --------------------------------------------------------------------------- #
# The listing is a one-shot reply, not telemetry
# --------------------------------------------------------------------------- #


def test_the_listing_does_not_ride_along_on_later_frames():
    """`KClient` accumulates frames into `_state` and hands out a copy of it.

    Left in there, the ~150 KiB listing would be on every subsequent frame and
    the coordinator would rescan the whole thing once per frame, on the receive
    path the integration is otherwise careful to keep cheap.

    A source contract because `conftest` stubs `KClient` for the whole suite,
    so the real receive loop cannot be driven from here.
    """
    source = (
        Path(__file__).resolve().parents[2]
        / "custom_components" / "ha_creality_ws" / "ws_client.py"
    ).read_text(encoding="utf-8")

    assert "self._state.pop(GCODE_FILE_RESPONSE, None)" in source, (
        "the one-shot listing is no longer removed from the cumulative state"
    )
    # The snapshot has to be taken before the pop and delivered by name. Going
    # back to `_on_message(dict(self._state))` would either resurrect the bug
    # or, after the pop, deliver a frame the listing had already been taken out
    # of -- so the coordinator would never see it at all.
    assert "await self._on_message(frame)" in source
    assert "await self._on_message(dict(self._state))" not in source


# --------------------------------------------------------------------------- #
# Recovery when static registration fails
# --------------------------------------------------------------------------- #


def test_a_failed_static_add_lets_a_later_pass_retry_the_estimates():
    """The uids are marked before the entities are handed over.

    So if `async_add_entities` raises, the three sensors are already recorded
    as added and every later discovery pass returns [] for them. The chamber
    set was cleared in that handler for exactly this reason; the estimate set
    was added later and missed it.
    """
    from test_late_discovery import _EntryStub, _sensor_platform

    _loop()
    coord = SimpleNamespace(
        data={"printFileName": RUNNING, GCODE_INFO_KEY: ENTRY},
        client=SimpleNamespace(_host="1.2.3.4"),
        available=True,
        power_is_off=lambda: False,
        config_entry=SimpleNamespace(data={}),
    )
    added, connected, calls = [], [], []

    def _add(ents):
        calls.append(ents)
        if len(calls) == 1:
            raise RuntimeError("entity registry unavailable")
        added.extend(ents)

    hass = SimpleNamespace(
        data={"ha_creality_ws": {}},
        loop=SimpleNamespace(call_soon=lambda fn, *a: fn(*a)),
        config_entries=SimpleNamespace(async_get_entry=lambda _e: SimpleNamespace(data={})),
    )
    entry = _EntryStub({})
    hass.data["ha_creality_ws"][entry.entry_id] = coord

    import custom_components.ha_creality_ws.sensor as sensor_mod

    original = sensor_mod.async_dispatcher_connect
    sensor_mod.async_dispatcher_connect = (
        lambda _hass, _signal, target: connected.append(target) or (lambda: None)
    )
    try:
        asyncio.get_event_loop().run_until_complete(
            sensor_mod.async_setup_entry(hass, entry, _add)
        )
    finally:
        sensor_mod.async_dispatcher_connect = original

    assert calls, "the static add was never attempted"
    assert _estimate_names(added) == set(), "the failing add must not have registered anything"

    # The printer is still reporting metadata, so the next discovery pass has
    # to offer the three sensors again rather than treating them as done.
    for cb in connected:
        cb()
    assert _estimate_names(added) == ESTIMATE_KEYS
