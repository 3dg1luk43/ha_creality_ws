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

from custom_components.ha_creality_ws.coordinator import send_command
from custom_components.ha_creality_ws.number import (
    BedTargetNumber,
    BoxTargetNumber,
    NozzleTargetNumber,
    PrintTuningPercent,
)


@pytest.fixture(autouse=True)
def _loop():
    try:
        previous = asyncio.get_event_loop_policy().get_event_loop()
    except Exception:  # pylint: disable=broad-except
        previous = None
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        yield loop
    finally:
        loop.close()
        asyncio.set_event_loop(previous)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class Client:
    def __init__(self, fail=False):
        self._host = "10.0.0.5"
        self.fail = fail
        self.sent = []

    async def send_set_retry(self, **params):
        if self.fail:
            raise RuntimeError("printer link not available after 6.0s")
        self.sent.append(params)


def _coordinator(fail=False):
    coord = SimpleNamespace(
        client=Client(fail),
        data={"targetNozzleTemp": 0, "targetBedTemp0": 0, "targetBoxTemp": 0},
        config_entry=SimpleNamespace(data={}, options={}),
        async_update_listeners=lambda: None,
        async_add_listener=lambda *a, **k: (lambda: None),
    )
    coord.merge_telemetry = coord.data.update
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


def test_the_speed_control_reports_a_failed_send():
    entity = PrintTuningPercent(_coordinator(fail=True))
    with pytest.raises(HomeAssistantError):
        _run(entity.async_set_native_value(100))
