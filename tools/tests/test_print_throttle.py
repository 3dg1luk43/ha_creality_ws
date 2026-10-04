"""Entity updates while printing follow the polling rate (#53).

Every frame updated every entity, several times a second while printing, which
took 40-50% of a CPU on small hosts. While printing, updates are held to the
polling rate; outside a print every frame still updates at once.
"""

import asyncio
import sys
from types import SimpleNamespace

from conftest import fake_config_entry

from custom_components.ha_creality_ws.const import CONF_POLLING_RATE  # noqa: E402
from custom_components.ha_creality_ws.coordinator import KCoordinator  # noqa: E402

coord_mod = sys.modules[KCoordinator.__module__]

PRINTING = {"state": 1, "printFileName": "/usr/data/printer_data/gcodes/a.gcode", "printProgress": 10}
IDLE = {"state": 0, "printFileName": "", "printProgress": 0}


class _Loop:
    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now


def _coord(monkeypatch, polling_rate=5):
    hass = SimpleNamespace(loop=_Loop(), states=SimpleNamespace(get=lambda _entity_id: None))
    monkeypatch.setattr(coord_mod, "async_dispatcher_send", lambda *_a, **_kw: None)
    c = KCoordinator(hass, host="1.2.3.4", config_entry=fake_config_entry(options={CONF_POLLING_RATE: polling_rate}))
    monkeypatch.setattr(c, "_flush_pending", lambda _s=None: asyncio.sleep(0))
    monkeypatch.setattr(c, "_check_notifications", lambda _p: asyncio.sleep(0))
    monkeypatch.setattr(c, "_maybe_request_gcode_info", lambda: asyncio.sleep(0))
    # The printer is talking: every frame has just arrived.
    monkeypatch.setattr(c.client, "last_rx_monotonic", lambda: hass.loop.now, raising=False)
    c.updates = []
    monkeypatch.setattr(c, "async_update_listeners", lambda: c.updates.append(hass.loop.now))
    return c, hass


def _frames(c, hass, frame, times):
    async def run():
        for t in times:
            hass.loop.now = 1000.0 + t
            await c._handle_message(dict(frame))

    asyncio.run(run())


def test_frames_while_printing_update_at_the_polling_rate(monkeypatch):
    c, hass = _coord(monkeypatch, polling_rate=5)
    _frames(c, hass, PRINTING, [0, 1, 2, 3, 4, 6, 7])
    assert c.updates == [1000.0, 1006.0]
    # Held back, but never unavailable: availability follows the frames, not
    # the updates (the 0.8.0-alpha2 regression of the same fix).
    assert c.available


def test_frames_outside_a_print_update_every_time(monkeypatch):
    c, hass = _coord(monkeypatch, polling_rate=5)
    _frames(c, hass, IDLE, [0, 1, 2])
    assert len(c.updates) == 3


def test_a_polling_rate_of_zero_updates_every_frame(monkeypatch):
    c, hass = _coord(monkeypatch, polling_rate=0)
    _frames(c, hass, PRINTING, [0, 1, 2])
    assert len(c.updates) == 3
