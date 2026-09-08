import asyncio
from unittest.mock import MagicMock
from types import SimpleNamespace

from conftest import install_stub_module, restore_stubs

# Mock homeassistant.helpers.update_coordinator
# The coordinator stub comes from conftest. This module used to install a
# narrower one of its own, and because pytest imports every test module during
# collection it was still active while *other* modules were imported -- so a
# constructor argument missing here broke them, and teardown_module ran far too
# late to help.

# Mock homeassistant.helpers.aiohttp_client
mock_aiohttp_client = MagicMock()
install_stub_module(__name__, "homeassistant.helpers.aiohttp_client", mock_aiohttp_client)

# homeassistant.helpers.dispatcher comes from conftest; installing another stub
# here would clobber it for every module collected afterwards.

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


PRINTING = {
    "printFileName": "demo.gcode",
    "printProgress": 10,
    "state": 1,
    "deviceState": 0,
}


def _coord_with_send(monkeypatch, hass=None):
    coord = KCoordinator(hass or HassStub(), host="dummy")
    coord._paused_flag = False
    sent: list[dict] = []

    async def fake_send_set_retry(**params):  # noqa: ANN001
        sent.append(params)

    monkeypatch.setattr(coord.client, "send_set_retry", fake_send_set_retry)
    return coord, sent


def test_pause_resume_queue_logic(monkeypatch):
    async def run():
        coord, sent = _coord_with_send(monkeypatch)
        # A genuine printing frame: `state: 1` is what makes it printing. The
        # old version of this test omitted it and still expected an immediate
        # pause, because the coordinator used to treat "has a file name and a
        # progress value" as printing.
        coord.data = dict(PRINTING)

        await coord.request_pause()
        assert sent and sent[-1] == {"pause": 1}

        coord.mark_paused(True)
        await coord.request_resume()
        assert sent and sent[-1] == {"pause": 0}

    asyncio.run(run())


def test_pause_is_not_sent_to_a_finished_print(monkeypatch):
    """The printer has stopped; there is nothing to pause.

    This used to fire `pause=1` at a printer that had already finished, because
    a job at 100% still counted as printing.
    """
    async def run():
        coord, sent = _coord_with_send(monkeypatch)
        coord.data = dict(PRINTING, printProgress=100)
        await coord.request_pause()
        assert sent == []
        assert coord.pending_pause() is False, "and it must not linger for the next print"

    asyncio.run(run())


def test_pause_is_not_sent_to_a_stopped_print(monkeypatch):
    async def run():
        coord, sent = _coord_with_send(monkeypatch)
        coord.data = dict(PRINTING, state=4)
        await coord.request_pause()
        assert sent == []
        assert coord.pending_pause() is False

    asyncio.run(run())


def test_pause_is_queued_while_the_printer_is_still_warming_up(monkeypatch):
    """state 0 is processing: busy, but not yet printing, so the pause waits."""
    async def run():
        coord, sent = _coord_with_send(monkeypatch)
        coord.data = dict(PRINTING, state=0)
        await coord.request_pause()
        assert sent == []
        assert coord.pending_pause() is True

        # ...and lands on the frame that starts printing.
        coord.data = dict(PRINTING)
        await coord._flush_pending()
        assert sent == [{"pause": 1}]
        assert coord.pending_pause() is False

    asyncio.run(run())


def test_a_queued_action_is_dropped_when_the_job_ends(monkeypatch):
    """Otherwise it stays armed and pauses the *next* print as it starts."""
    async def run():
        coord, sent = _coord_with_send(monkeypatch)
        coord.data = dict(PRINTING, state=0)
        await coord.request_pause()
        assert coord.pending_pause() is True

        coord.data = dict(PRINTING, printProgress=100)
        await coord._flush_pending()
        assert coord.pending_pause() is False
        assert sent == []

        # A brand new print must start unpaused.
        coord.data = {"printFileName": "next.gcode", "printProgress": 1, "state": 1}
        await coord._flush_pending()
        assert sent == []

    asyncio.run(run())


def test_pausing_an_already_paused_print_does_nothing(monkeypatch):
    async def run():
        coord, sent = _coord_with_send(monkeypatch)
        coord.data = dict(PRINTING, state=5)
        coord.mark_paused(True)
        await coord.request_pause()
        assert sent == []
        assert coord.pending_pause() is False

    asyncio.run(run())


def test_resume_is_not_queued_for_an_idle_printer(monkeypatch):
    async def run():
        coord, sent = _coord_with_send(monkeypatch)
        coord.data = {}
        await coord.request_resume()
        assert sent == []
        assert coord.pending_resume() is False

    asyncio.run(run())


def test_a_stale_error_code_does_not_block_pausing(monkeypatch):
    """derive_print_state calls any non-zero errcode "error"; a code the printer
    never clears must not make the printer unpausable for the rest of the job."""
    async def run():
        coord, sent = _coord_with_send(monkeypatch)
        coord.data = dict(PRINTING, err={"errcode": 521, "key": 1})
        await coord.request_pause()
        # The job is still printing underneath the stale code, so the pause goes
        # straight out rather than being queued or discarded.
        assert sent == [{"pause": 1}]

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


def teardown_module(_module):
    restore_stubs(__name__)
