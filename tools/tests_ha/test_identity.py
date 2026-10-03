"""A printer at a new address keeps its device and entities (R4, #39)."""
from __future__ import annotations

from ipaddress import ip_address

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_creality_ws.config_flow import ConfigFlow

from .conftest import DOMAIN, HOST, entry_data

NEW_HOST = "192.0.2.20"
MAC = "AA:BB:CC:00:11:22"


async def _add(hass: HomeAssistant, host: str = HOST, **data) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=ConfigFlow.VERSION,
        data=entry_data(host=host, _last_ip=host, **data),
        unique_id=host,
        title=f"Printer {host}",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _ids(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, str]:
    return {e.entity_id: e.unique_id for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)}


def _discovery(host: str, mac: str | None = MAC) -> ZeroconfServiceInfo:
    return ZeroconfServiceInfo(
        ip_address=ip_address(host),
        ip_addresses=[ip_address(host)],
        port=80,
        hostname="K1C-TEST.local.",
        type="_http._tcp.local.",
        name="K1C-TEST._http._tcp.local.",
        properties={"mac": mac} if mac else {},
    )


async def _connection_page(hass: HomeAssistant, entry: MockConfigEntry, host: str) -> dict:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "connection"})
    assert result["type"] is FlowResultType.FORM
    return await hass.config_entries.options.async_configure(result["flow_id"], {"host": host})


async def test_a_new_address_from_the_options_keeps_the_entities(hass: HomeAssistant, fake_printer) -> None:
    entry = await _add(hass)
    before = _ids(hass, entry)
    assert before

    await _connection_page(hass, entry, NEW_HOST)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.data["host"] == NEW_HOST
    assert entry.unique_id == NEW_HOST
    assert fake_printer.instances[-1].host == NEW_HOST
    after = _ids(hass, entry)
    # Same entity ids, nothing doubled with a `_2`, unique ids on the new host.
    assert after.keys() == before.keys()
    assert all(uid.startswith(f"{NEW_HOST}-") for uid in after.values())
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert [d.identifiers for d in devices] == [{(DOMAIN, NEW_HOST)}]


async def test_an_address_another_printer_has_is_refused(hass: HomeAssistant, fake_printer) -> None:
    entry = await _add(hass)
    await _add(hass, host=NEW_HOST)
    result = await _connection_page(hass, entry, NEW_HOST)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"host": "host_in_use"}
    assert entry.data["host"] == HOST


async def test_a_discovered_printer_is_added_only_once_confirmed(hass: HomeAssistant, fake_printer) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=_discovery(HOST)
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "zeroconf_confirm"
    assert not hass.config_entries.async_entries(DOMAIN)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"host": HOST, "_cached_mac": MAC}


async def test_rediscovery_at_a_new_address_moves_the_entry(hass: HomeAssistant, fake_printer) -> None:
    """A new DHCP lease: the same MAC at another address updates the entry
    instead of offering the printer as a second one."""
    entry = await _add(hass, _cached_mac=MAC)
    before = _ids(hass, entry)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_ZEROCONF}, data=_discovery(NEW_HOST)
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    assert entry.data["host"] == NEW_HOST
    assert _ids(hass, entry).keys() == before.keys()
