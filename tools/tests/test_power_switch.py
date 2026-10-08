"""Power-switch edges: the coordinator starts and stops the client on them.

The conftest `KClient` stub reports `is_connected` as always False, which is
exactly the state in which the 0.9.1 regression (#45) cannot happen, so these
tests swap in a client whose connection state and start/stop calls they
control.
"""

import asyncio
from types import SimpleNamespace

from custom_components.ha_creality_ws.coordinator import KCoordinator

SWITCH = "switch.printer_plug"


class FakeClient:
    def __init__(self):
        self.is_connected = False
        self.calls: list[str] = []
        self._task = None
        self._host = "1.2.3.4"
        self.stop_gate: asyncio.Event | None = None

    async def start(self):
        self.calls.append("start")

    async def stop(self):
        self.calls.append("stop")
        if self.stop_gate is not None:
            # A real stop awaits the socket close, which can take seconds.
            await self.stop_gate.wait()
        self.is_connected = False


class HassStub:
    def __init__(self):
        self._states: dict[str, SimpleNamespace] = {}
        self.states = SimpleNamespace(get=self._states.get)
        self.loop = None

    def set_switch(self, state: str) -> None:
        self._states[SWITCH] = SimpleNamespace(entity_id=SWITCH, state=state)


def _coordinator(initial: str):
    hass = HassStub()
    hass.set_switch(initial)
    coord = KCoordinator(hass, host="1.2.3.4", power_switch=SWITCH)
    client = FakeClient()
    coord.client = client
    coord.async_update_listeners = lambda: None
    return coord, hass, client


def test_cutting_power_under_a_live_socket_stops_the_client():
    """#45. The plug cuts a running printer while the socket still looks
    connected for another 30 s. Deciding the edge from `power_is_off`, which
    trusts a connected socket, saw no edge at all."""

    async def run():
        coord, hass, client = _coordinator("on")
        client.is_connected = True
        hass.set_switch("off")
        await coord.async_handle_power_change()
        assert client.calls == ["stop"]
        assert coord._last_power_off is True

    asyncio.run(run())


def test_power_returning_restarts_the_client_without_waiting_out_backoff():
    """The half the user saw: with the off edge missed, the on edge found
    nothing to do, and the old loop sat in its backoff for up to 300 s."""

    async def run():
        coord, hass, client = _coordinator("on")
        client.is_connected = True
        hass.set_switch("off")
        await coord.async_handle_power_change()
        hass.set_switch("on")
        await coord.async_handle_power_change()
        assert client.calls == ["stop", "start"]
        assert coord._last_power_off is False

    asyncio.run(run())


def test_a_quick_off_then_on_ends_with_the_client_running():
    """Stopping waits on the socket close. Without serializing, the on edge
    ran during it, found nothing to do, and the off edge then finished and
    left the client stopped with the power on."""

    async def run():
        coord, hass, client = _coordinator("on")
        client.is_connected = True
        client.stop_gate = asyncio.Event()

        hass.set_switch("off")
        off = asyncio.create_task(coord.async_handle_power_change())
        await asyncio.sleep(0)
        hass.set_switch("on")
        on = asyncio.create_task(coord.async_handle_power_change())
        await asyncio.sleep(0)
        client.stop_gate.set()
        await asyncio.gather(off, on)

        assert client.calls[-1] == "start"
        assert coord._last_power_off is False

    asyncio.run(run())


def test_a_connected_printer_stays_available_whatever_the_switch_says():
    """Availability still trusts the socket, so a lagging or wrong switch does
    not blank a printer that is visibly streaming."""
    coord, hass, client = _coordinator("on")
    client.is_connected = True
    hass.set_switch("off")
    assert coord.power_is_off() is False
    client.is_connected = False
    assert coord.power_is_off() is True


def test_a_plug_blinking_unavailable_does_not_cut_a_streaming_printer():
    """Zigbee and Wi-Fi plugs drop to unavailable for a moment routinely. That
    is no power cut while the printer is visibly streaming, and stopping the
    client for it would blank every entity until the plug came back."""

    async def run():
        coord, hass, client = _coordinator("on")
        client.is_connected = True
        for blip in ("unavailable", "unknown"):
            hass.set_switch(blip)
            await coord.async_handle_power_change()
        assert client.calls == []
        assert coord._last_power_off is False
        # A real off still stops it.
        hass.set_switch("off")
        await coord.async_handle_power_change()
        assert client.calls == ["stop"]

    asyncio.run(run())


def test_an_unavailable_plug_with_no_connection_counts_as_off():
    """Nothing is streaming, so there is nothing to protect: treat it as off
    and let the loop poll the switch rather than dial the printer."""

    async def run():
        coord, hass, client = _coordinator("on")
        hass.set_switch("unavailable")
        await coord.async_handle_power_change()
        assert client.calls == ["stop"]
        assert coord._last_power_off is True

    asyncio.run(run())


def test_a_repeated_state_report_is_not_an_edge():
    async def run():
        coord, hass, client = _coordinator("off")
        assert coord._last_power_off is True
        await coord.async_handle_power_change()
        assert client.calls == []

    asyncio.run(run())


# --- a switch that has gone missing (R24) ----------------------------------- #

import homeassistant.helpers.issue_registry as ir  # noqa: E402  (conftest stub)

import custom_components.ha_creality_ws.coordinator as coord_mod  # noqa: E402


def _missing(monkeypatch):
    coord, hass, client = _coordinator("on")
    hass._states.pop(SWITCH)
    clock = [1000.0]
    monkeypatch.setattr(coord_mod.time, "monotonic", lambda: clock[0])
    ir.created.clear()
    ir.deleted.clear()
    return coord, hass, client, clock


def test_a_missing_switch_counts_as_off_while_integrations_load(monkeypatch):
    coord, _, _, clock = _missing(monkeypatch)
    assert coord.power_is_off() is True
    clock[0] += 60
    assert coord.power_is_off() is True
    assert ir.created == []


def test_a_switch_that_stays_missing_stops_blocking_the_printer(monkeypatch):
    """R24. A renamed or deleted switch counted as off forever: the printer
    never connected again, with only a debug line to say why."""
    coord, _, _, clock = _missing(monkeypatch)
    coord.power_is_off()
    clock[0] += 121
    assert coord.power_is_off() is False
    assert coord.power_is_off() is False
    assert len(ir.created) == 1, "the repair is raised once"
    domain, issue_id, kw = ir.created[0]
    assert kw["translation_key"] == "missing_power_switch"
    assert kw["translation_placeholders"]["entity_id"] == SWITCH


def test_the_repair_is_withdrawn_when_the_switch_comes_back(monkeypatch):
    coord, hass, _, clock = _missing(monkeypatch)
    coord.power_is_off()
    clock[0] += 121
    coord.power_is_off()
    hass.set_switch("on")
    assert coord.power_is_off() is False
    assert len(ir.deleted) == 1


def test_the_end_of_the_grace_period_starts_a_client_deferred_at_setup(monkeypatch):
    """No state change ever arrives for an entity that does not exist, so the
    interval check has to notice the grace period ending."""

    async def run():
        coord, _, client, clock = _missing(monkeypatch)
        coord._last_power_off = coord._switch_reports_off()  # deferred at setup
        assert coord._last_power_off is True
        clock[0] += 121
        await coord.async_recheck_missing_switch()
        assert client.calls == ["start"]

    asyncio.run(run())
