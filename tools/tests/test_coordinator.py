import asyncio
from types import SimpleNamespace

from custom_components.ha_creality_ws.coordinator import KCoordinator


class HassStub:
    def __init__(self, loop=None):
        if loop is None:
            loop = asyncio.get_running_loop()
        self.loop = loop
        self.states = SimpleNamespace(get=self._get_state)
        self._state_map = {}

    def set_state(self, entity_id: str, state: str):
        self._state_map[entity_id] = SimpleNamespace(state=state)

    def _get_state(self, entity_id: str):
        return self._state_map.get(entity_id)


def test_wait_for_fields_success():
    async def run():
        hass = HassStub()
        coord = KCoordinator(hass, host="dummy")
        # simulate incoming telemetry
        coord.data = {"model": "K1C"}
        assert await coord.wait_for_fields(["model"], timeout=0.2) is True
        # missing field scenario
        assert await coord.wait_for_fields(["nonexistent"], timeout=0.3) is False

    asyncio.run(run())


def test_pause_resume_queue_logic(monkeypatch):
    async def run():
        hass = HassStub()
        coord = KCoordinator(hass, host="dummy")

        # Fake printing state so pause sends immediately
        coord.data = {"printFileName": "demo.gcode", "printProgress": 10, "deviceState": 0}
        coord._paused_flag = False

        sent = []

        async def fake_send_set_retry(**params):  # noqa: ANN001
            sent.append(params)

        monkeypatch.setattr(coord.client, "send_set_retry", fake_send_set_retry)

        await coord.request_pause()
        assert sent and sent[-1] == {"pause": 1}
        # Now mark paused and request resume
        coord.mark_paused(True)
        await coord.request_resume()
        assert sent and sent[-1] == {"pause": 0}

    asyncio.run(run())


def test_power_switch_logic():
    # we can create a loop for hass stub explicitly for consistency
    loop = asyncio.new_event_loop()
    try:
        hass = HassStub(loop=loop)
        coord = KCoordinator(hass, host="dummy", power_switch="switch.printer")
        # Switch not set yet -> power_is_off True (fail-safe)
        assert coord.power_is_off() is True
        hass.set_state("switch.printer", "off")
        assert coord.power_is_off() is True
        hass.set_state("switch.printer", "on")
        assert coord.power_is_off() is False
        hass.set_state("switch.printer", "unavailable")
        assert coord.power_is_off() is True
    finally:
        loop.close()
