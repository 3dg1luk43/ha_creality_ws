"""The power switch starts and stops the client (R2, #45)."""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_creality_ws.config_flow import ConfigFlow
from custom_components.ha_creality_ws.const import CONF_POWER_SWITCH, CONF_POWER_SWITCH_ENABLED

from .conftest import DOMAIN, HOST, entry_data

PLUG = "switch.printer_plug"


async def _add(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=ConfigFlow.VERSION,
        data=entry_data(),
        options={CONF_POWER_SWITCH_ENABLED: True, CONF_POWER_SWITCH: PLUG},
        unique_id=HOST,
        title="Test K1C",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _plug(hass: HomeAssistant, state: str) -> None:
    hass.states.async_set(PLUG, state)
    await hass.async_block_till_done()


async def test_a_plug_cutting_a_running_printer_stops_and_restarts_the_client(
    hass: HomeAssistant, fake_printer
) -> None:
    """The socket still looks connected when the plug goes off. The edge was
    decided from the socket, so it was never seen, and the client was not
    restarted when the plug came back on (R2)."""
    await _plug(hass, "on")
    await _add(hass)
    client = fake_printer.instances[-1]
    assert client.started == 1 and client.is_connected

    await _plug(hass, "off")
    assert client.stopped == 1

    await _plug(hass, "on")
    assert client.started == 2
    assert client.is_connected


async def test_a_plug_blinking_unavailable_keeps_a_live_connection(hass: HomeAssistant, fake_printer) -> None:
    await _plug(hass, "on")
    await _add(hass)
    client = fake_printer.instances[-1]
    await _plug(hass, "unavailable")
    await _plug(hass, "on")
    assert client.stopped == 0
    assert client.started == 1


async def test_a_printer_switched_off_at_setup_connects_when_switched_on(hass: HomeAssistant, fake_printer) -> None:
    await _plug(hass, "off")
    await _add(hass)
    client = fake_printer.instances[-1]
    assert client.started == 0
    await _plug(hass, "on")
    assert client.started == 1
