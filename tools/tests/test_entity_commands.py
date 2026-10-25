"""A command the printer cannot take tells the user so (R32).

The client raises a bare RuntimeError when the link does not come back, which
Home Assistant shows as "Unknown error" with a traceback in the log, and the
home button returned silently when the printer was not connected. Commands now
raise a translated HomeAssistantError, and a temperature target is only shown
once the printer was sent it.
"""

import asyncio
from types import SimpleNamespace

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.ha_creality_ws.coordinator import KCoordinator, send_command
from custom_components.ha_creality_ws.number import (
    BedTargetNumber,
    BoxTargetNumber,
    NozzleTargetNumber,
    FlowRatePercent,
    PrintTuningPercent,
)


@pytest.fixture(autouse=True)
def _loop():
    # Cleared afterwards: closing a loop does not uninstall it, and a closed
    # loop left installed broke whatever later called asyncio.get_event_loop().
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        yield loop
    finally:
        loop.close()
        asyncio.set_event_loop(None)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class Client:
    def __init__(self, fail=False, echo=None):
        self._host = "10.0.0.5"
        self.fail = fail
        self.sent = []
        # The client's cumulative state; `echo` is the printer reporting a
        # value while the command is still in flight.
        self.state = {"targetNozzleTemp": 0, "targetBedTemp0": 0, "targetBoxTemp": 0}
        self.echo = echo or {}

    async def send_set_retry(self, **params):
        if self.fail:
            raise RuntimeError("printer link not available after 6.0s")
        self.sent.append(params)
        self.state.update(self.echo)

    def reported(self, key):
        return self.state.get(key)

    def assume(self, values):
        self.state.update(values)


def _coordinator(fail=False, echo=None):
    coord = SimpleNamespace(
        client=Client(fail, echo),
        data={"targetNozzleTemp": 0, "targetBedTemp0": 0, "targetBoxTemp": 0},
        config_entry=SimpleNamespace(data={}, options={}),
        async_update_listeners=lambda: None,
        async_add_listener=lambda *a, **k: (lambda: None),
    )
    coord.merge_telemetry = coord.data.update
    coord.assume_commanded = KCoordinator.assume_commanded.__get__(coord)
    return coord


def test_a_failed_send_is_a_translated_error():
    with pytest.raises(HomeAssistantError) as err:
        _run(send_command(Client(fail=True), lightSw=1))
    assert err.value.translation_key == "printer_not_connected"
    assert isinstance(err.value.__cause__, RuntimeError)


def test_a_send_that_works_passes_the_command_through():
    client = Client()
    _run(send_command(client, lightSw=1))
    assert client.sent == [{"lightSw": 1}]


@pytest.mark.parametrize(
    ("cls", "key"),
    [
        (NozzleTargetNumber, "targetNozzleTemp"),
        (BedTargetNumber, "targetBedTemp0"),
        (BoxTargetNumber, "targetBoxTemp"),
    ],
)
def test_a_target_that_was_not_sent_is_not_shown(cls, key):
    coord = _coordinator(fail=True)
    entity = cls(coord)
    entity._attr_native_min_value, entity._attr_native_max_value = 0, 300
    with pytest.raises(HomeAssistantError):
        _run(entity.async_set_native_value(60))
    assert coord.data[key] == 0


def test_a_target_that_was_sent_is_shown_straight_away():
    coord = _coordinator()
    entity = NozzleTargetNumber(coord)
    entity._attr_native_min_value, entity._attr_native_max_value = 0, 300
    _run(entity.async_set_native_value(210))
    assert coord.client.sent == [{"nozzleTempControl": 210}]
    assert coord.data["targetNozzleTemp"] == 210
    # Into the state frames are built from too, or the next frame reverts it (#132).
    assert coord.client.state["targetNozzleTemp"] == 210


@pytest.mark.parametrize(
    ("cls", "key"),
    [
        (NozzleTargetNumber, "targetNozzleTemp"),
        (BedTargetNumber, "targetBedTemp0"),
        (BoxTargetNumber, "targetBoxTemp"),
    ],
)
def test_a_value_the_printer_reported_during_the_send_is_kept(cls, key):
    """It may have clamped the command; showing ours instead would lie."""
    coord = _coordinator(echo={key: 55})
    entity = cls(coord)
    entity._attr_native_min_value, entity._attr_native_max_value = 0, 300
    _run(entity.async_set_native_value(60))
    assert coord.client.state[key] == 55
    assert coord.data[key] == 0  # the echo reaches .data with its frame


def test_the_speed_control_reports_a_failed_send():
    entity = PrintTuningPercent(_coordinator(fail=True))
    with pytest.raises(HomeAssistantError):
        _run(entity.async_set_native_value(100))


def test_speed_and_flow_are_set_separately():
    """R26: one control wrote both, so 150% speed was also 150% extrusion."""
    coord = _coordinator()
    _run(PrintTuningPercent(coord).async_set_native_value(150))
    _run(FlowRatePercent(coord).async_set_native_value(95))
    assert coord.client.sent == [{"setFeedratePct": 150}, {"setFlowratePct": 95}]


def test_speed_and_flow_each_read_their_own_field():
    coord = _coordinator()
    coord.available = True
    coord.power_is_off = lambda: False
    coord.data.update({"curFeedratePct": 150, "curFlowratePct": 95})
    assert PrintTuningPercent(coord).native_value == 150
    assert FlowRatePercent(coord).native_value == 95
    assert FlowRatePercent(coord)._attr_unique_id.endswith("-flow_rate_pct")
