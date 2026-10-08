"""A firmware update shows on the device without an integration upgrade (R40).

The device's firmware version came only from the cache setup fills on the
first run, an integration upgrade or a new address, so a printer updated since
(1.3.3.46 to 1.3.5.22 on a K1C, say) kept showing the old version. The
coordinator now follows `modelVersion` and writes the cache itself, and that
write must not reload the entry: a reload mid-print drops the connection.
"""

import asyncio
import sys
from types import SimpleNamespace

import pytest

from test_entry_removal import _load_package_init

from custom_components.ha_creality_ws.const import DOMAIN
from custom_components.ha_creality_ws.coordinator import KCoordinator

coord_mod = sys.modules[KCoordinator.__module__]
integration = _load_package_init()

OLD = "printer hw ver:;printer sw ver:;DWIN hw ver:CR4CU220812S11;DWIN sw ver:1.3.3.46;"
NEW = "printer hw ver:;printer sw ver:;DWIN hw ver:CR4CU220812S11;DWIN sw ver:1.3.5.22;"


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


class Devices:
    def __init__(self):
        self.device = SimpleNamespace(id="dev1", identifiers={(DOMAIN, "1.2.3.4")})
        self.updates = []

    def async_update_device(self, device_id, **kw):
        self.updates.append((device_id, kw))


def _setup(monkeypatch, data):
    devices = Devices()
    monkeypatch.setattr(
        coord_mod,
        "dr",
        SimpleNamespace(
            async_get=lambda hass: devices,
            async_entries_for_config_entry=lambda reg, entry_id: [devices.device],
        ),
    )
    entry = SimpleNamespace(entry_id="e1", data=dict(data), options={})
    writes = []

    def _update_entry(e, **kw):
        writes.append(kw)
        e.data = kw.get("data", e.data)

    hass = SimpleNamespace(
        loop=asyncio.get_event_loop(),
        config_entries=SimpleNamespace(async_update_entry=_update_entry),
    )
    coord = KCoordinator(hass, host="1.2.3.4", config_entry=entry)
    coord._loaded_options = {}
    return coord, entry, writes, devices


CACHED = {"host": "1.2.3.4", "_device_info_cached": True, "_cached_model_version": OLD}


def test_a_new_firmware_reaches_the_device_and_the_cache(monkeypatch):
    coord, entry, writes, devices = _setup(monkeypatch, CACHED)
    coord._follow_firmware({"modelVersion": NEW})
    assert entry.data["_cached_model_version"] == NEW
    assert devices.updates == [("dev1", {"hw_version": "DWIN CR4CU220812S11", "sw_version": "DWIN 1.3.5.22"})]


def test_the_same_firmware_writes_nothing(monkeypatch):
    coord, _entry, writes, devices = _setup(monkeypatch, CACHED)
    coord._follow_firmware({"modelVersion": OLD})
    coord._follow_firmware({"model": "K1C"})
    assert writes == [] and devices.updates == []


def test_the_first_value_is_left_to_setup(monkeypatch):
    """Before setup's cache pass there is nothing to compare with."""
    coord, _entry, writes, _devices = _setup(monkeypatch, {"host": "1.2.3.4"})
    coord._follow_firmware({"modelVersion": NEW})
    assert writes == []


def _listener(coord, entry):
    reloads = []

    async def _reload(entry_id):
        reloads.append(entry_id)

    hass = SimpleNamespace(
        data={DOMAIN: {entry.entry_id: coord}},
        config_entries=SimpleNamespace(async_reload=_reload),
    )
    asyncio.get_event_loop().run_until_complete(integration.options_update_listener(hass, entry))
    return reloads


def test_the_coordinators_own_cache_write_does_not_reload(monkeypatch):
    coord, entry, _writes, _devices = _setup(monkeypatch, CACHED)
    coord._follow_firmware({"modelVersion": NEW})
    assert _listener(coord, entry) == []


def test_a_real_change_after_it_still_reloads(monkeypatch):
    coord, entry, _writes, _devices = _setup(monkeypatch, CACHED)
    coord._follow_firmware({"modelVersion": NEW})
    assert _listener(coord, entry) == []
    entry.data = {**entry.data, "host": "1.2.3.9"}
    assert _listener(coord, entry) == ["e1"]
