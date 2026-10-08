"""Config entry lifecycle against a real Home Assistant (R47)."""
from __future__ import annotations

from typing import Any

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_creality_ws.config_flow import ConfigFlow

from .conftest import DOMAIN, HOST, entry_data


async def _add(hass: HomeAssistant, data: dict[str, Any] | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=ConfigFlow.VERSION,
        data=data or {"host": HOST},
        unique_id=(data or {}).get("host", HOST),
        title="Test K1C",
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _entities(hass: HomeAssistant, entry: MockConfigEntry) -> list[er.RegistryEntry]:
    return er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)


async def test_the_user_flow_creates_an_entry(hass: HomeAssistant, fake_printer) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": HOST, "name": "Test K1C"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"host": HOST}
    assert result["result"].unique_id == HOST


async def test_the_same_printer_cannot_be_added_twice(hass: HomeAssistant, fake_printer) -> None:
    await _add(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": HOST, "name": "x"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_setup_creates_the_device_and_its_entities(hass: HomeAssistant, fake_printer) -> None:
    entry = await _add(hass)
    assert entry.state is ConfigEntryState.LOADED
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert [d.identifiers for d in devices] == [{(DOMAIN, HOST)}]
    entities = _entities(hass, entry)
    assert len(entities) > 20
    nozzle = next(e for e in entities if e.unique_id == f"{HOST}-nozzle_temperature")
    # The string "24.500000" from the wire, as a number.
    assert float(hass.states.get(nozzle.entity_id).state) == 24.5


async def test_a_k2_pro_device_is_named_by_its_model(hass: HomeAssistant, fake_printer) -> None:
    """A K2 Pro reports its board code as the model; the device page showed
    "F012" (R80). The model sensor keeps the reported value."""
    fake_printer.overrides = {
        "model": "F012",
        "modelVersion": "printer hw ver:;printer sw ver:;DWIN hw ver:CR0CN200400C10;DWIN sw ver:1.1.6.7;",
        "hostname": "K2Pro-0000",
        "webrtcSupport": 1,
    }
    entry = await _add(hass)
    [device] = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert (device.model, device.model_id) == ("K2 Pro", "F012")
    model_sensor = next(e for e in _entities(hass, entry) if e.unique_id == f"{HOST}-model_info")
    assert hass.states.get(model_sensor.entity_id).state == "F012"


async def test_unload_stops_the_client_and_reload_starts_it_again(hass: HomeAssistant, fake_printer) -> None:
    entry = await _add(hass)
    client = fake_printer.instances[-1]
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert client.stopped >= 1
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert fake_printer.instances[-1] is not client


async def test_a_light_command_reaches_the_printer(hass: HomeAssistant, fake_printer) -> None:
    entry = await _add(hass)
    light = next(e.entity_id for e in _entities(hass, entry) if e.domain == "light")
    await hass.services.async_call("light", "turn_off", {"entity_id": light}, blocking=True)
    assert {"lightSw": 0} in fake_printer.instances[-1].sent


async def test_a_switched_off_printer_does_not_hold_up_setup(hass: HomeAssistant, fake_printer) -> None:
    """With the capabilities cached there is nothing to wait for: the
    entities come from the cache. Setup used to wait 15 s for the first frame
    per printer, holding up Home Assistant's start by that much (R31)."""
    fake_printer.online = False
    entry = await _add(hass, entry_data())
    assert entry.state is ConfigEntryState.LOADED
    assert fake_printer.instances[-1].waits == []
    assert any(e.unique_id == f"{HOST}-nozzle_temperature" for e in _entities(hass, entry))


async def test_a_start_that_fails_retries_without_leaving_a_client_running(
    hass: HomeAssistant, fake_printer
) -> None:
    fake_printer.start_error = OSError("no route to host")
    entry = await _add(hass)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert fake_printer.instances[-1].stopped >= 1


async def test_home_assistant_stopping_stops_the_client(hass: HomeAssistant, fake_printer) -> None:
    """Entries are not unloaded on shutdown; the client used to keep
    reconnecting until the loop was torn down under it (R39)."""
    await _add(hass)
    client = fake_printer.instances[-1]
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert client.stopped >= 1


async def test_the_actions_exist_before_any_printer_loads(hass: HomeAssistant, fake_printer) -> None:
    """Registered with the first entry, they were missing while it failed to
    load, so an automation got "action not found" (R34)."""
    assert await async_setup_component(hass, DOMAIN, {})
    for action in ("diagnostic_dump", "request_cfs_info", "set_cfs_material"):
        assert hass.services.has_service(DOMAIN, action), action
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "set_cfs_material",
            {"device_id": "no-such-device", "box_id": 1, "slot_id": 0, "type": "PLA"},
            blocking=True,
        )


async def test_only_a_device_the_printer_no_longer_uses_can_be_removed(
    hass: HomeAssistant, fake_printer, hass_ws_client
) -> None:
    """Removing the current device wiped the cached model and MAC and came
    straight back without its customisations (R35)."""
    entry = await _add(hass)
    dev_reg = dr.async_get(hass)
    [current] = dr.async_entries_for_config_entry(dev_reg, entry.entry_id)
    stale = dev_reg.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, "192.0.2.99")})
    ws = await hass_ws_client(hass)

    async def remove(device_id: str) -> dict[str, Any]:
        await ws.send_json_auto_id({
            "type": "config/device_registry/remove_config_entry",
            "config_entry_id": entry.entry_id,
            "device_id": device_id,
        })
        return await ws.receive_json()

    assert (await remove(current.id))["success"] is False
    assert dev_reg.async_get(current.id) is not None
    assert (await remove(stale.id))["success"] is True
    assert dev_reg.async_get(stale.id) is None
