"""Setup and unload of a config entry do not hold up or strand the client (R31).

Setup waited 15 s for the first frame of every printer without a power switch,
so each one that was switched off delayed Home Assistant's startup by that
much. A setup that failed after the client started left it running, and unload
stopped the client before the platforms, so a refused unload left a loaded
entry with a dead connection. The same behaviour is timed against a real Home
Assistant on the test box (setup 0.27 s with the printer off).
"""

import asyncio
from types import SimpleNamespace

import pytest

from test_entry_removal import _load_package_init

from custom_components.ha_creality_ws.const import DOMAIN

integration = _load_package_init()


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


class Stop(Exception):
    """Ends setup at the first step after the client starts."""


class FakeCoordinator:
    instances: list["FakeCoordinator"] = []

    def __init__(self, hass, **kw):
        self.events: list[str] = []
        FakeCoordinator.instances.append(self)

    async def async_start(self):
        self.events.append("start")

    async def async_stop(self):
        self.events.append("stop")

    async def wait_first_connect(self, timeout=5.0):
        self.events.append(f"wait {timeout}")
        return False


def _entry():
    entry = SimpleNamespace(
        entry_id="e1",
        unique_id="10.0.0.5",
        data={"host": "10.0.0.5", "_device_info_cached": True},
        options={},
        on_unload=[],
    )
    entry.async_on_unload = entry.on_unload.append
    return entry


def _setup_until_after_start(monkeypatch):
    """Run async_setup_entry up to the first call after the client started."""
    FakeCoordinator.instances.clear()
    monkeypatch.setattr(integration, "KCoordinator", FakeCoordinator)
    monkeypatch.setattr(integration, "_core_version", lambda: (2099, 1))
    monkeypatch.setattr(integration, "_migrate_go2rtc_settings", lambda hass, entry: None)
    monkeypatch.setattr(integration, "_async_follow_host", lambda hass, entry, host: None)

    async def _stop_here(hass):
        raise Stop

    monkeypatch.setattr(integration, "_get_integration_version", _stop_here)
    entry = _entry()
    with pytest.raises(Stop):
        asyncio.get_event_loop().run_until_complete(
            integration.async_setup_entry(SimpleNamespace(), entry)
        )
    return FakeCoordinator.instances[0], entry


def test_setup_does_not_wait_for_the_first_frame(monkeypatch):
    coord, _ = _setup_until_after_start(monkeypatch)
    assert coord.events == ["start"]


def test_a_setup_that_fails_after_the_start_stops_the_client(monkeypatch):
    # Home Assistant runs an entry's on_unload callbacks when setup raises.
    coord, entry = _setup_until_after_start(monkeypatch)
    for fn in entry.on_unload:
        asyncio.get_event_loop().run_until_complete(fn())
    assert coord.events == ["start", "stop"]


def _unload(platforms_ok):
    order: list[str] = []
    coord = FakeCoordinator(None)
    coord.events = order

    async def _unload_platforms(entry, platforms):
        order.append("platforms")
        return platforms_ok

    hass = SimpleNamespace(
        data={DOMAIN: {"e1": coord}},
        config_entries=SimpleNamespace(async_unload_platforms=_unload_platforms),
    )
    ok = asyncio.get_event_loop().run_until_complete(
        integration.async_unload_entry(hass, SimpleNamespace(entry_id="e1"))
    )
    return ok, order, hass


def test_unload_removes_the_entities_before_stopping_the_client():
    ok, order, hass = _unload(True)
    assert ok is True
    assert order == ["platforms", "stop"]
    assert "e1" not in hass.data[DOMAIN]


def test_a_refused_unload_keeps_the_client_running():
    ok, order, hass = _unload(False)
    assert ok is False
    assert order == ["platforms"]
    assert "e1" in hass.data[DOMAIN]
