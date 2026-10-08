"""The print preview at the start of a job (R27).

The image entity only moved its timestamp when it fetched bytes, so a
dashboard kept the previous job's picture until the access token rotated; and
the idle-time `preview_reason` of "not_printing" outlived the job start, so the
start notification went out without its preview.
"""

import sys
import types
from types import SimpleNamespace

from conftest import fake_config_entry, install_stub_module, restore_stubs

if "homeassistant.components.image" not in sys.modules:
    image_mod = types.ModuleType("homeassistant.components.image")

    class ImageEntity:
        def __init__(self, hass):
            self.hass = hass

    image_mod.ImageEntity = ImageEntity
    install_stub_module(__name__, "homeassistant.components.image", image_mod)
if "homeassistant.helpers.entity_platform" not in sys.modules:
    ep = types.ModuleType("homeassistant.helpers.entity_platform")
    ep.AddConfigEntryEntitiesCallback = object
    install_stub_module(__name__, "homeassistant.helpers.entity_platform", ep)

from custom_components.ha_creality_ws.coordinator import KCoordinator  # noqa: E402
from custom_components.ha_creality_ws.image import CurrentPrintPreviewImage  # noqa: E402


def teardown_module(_module):
    restore_stubs(__name__)


def _coordinator(data):
    import time as _time

    hass = SimpleNamespace(states=SimpleNamespace(get=lambda _e: None), loop=SimpleNamespace(time=_time.monotonic))
    coord = KCoordinator(hass, host="1.2.3.4", config_entry=fake_config_entry("e1"))
    coord.data = dict(data)
    return coord


PRINTING = {"printFileName": "/usr/data/printer_data/gcodes/benchy.gcode", "state": 1, "printProgress": 3}


def test_an_idle_not_printing_reason_does_not_cost_the_start_notification_its_preview():
    coord = _coordinator(PRINTING)
    coord._notify_preview_image = True
    stale = SimpleNamespace(attributes={"preview_reason": "not_printing"})
    coord._live_entity_state = lambda platform, suffix: ("image.k1c_preview", stale)
    assert coord._notify_media(include_snapshot=False).preview_url == "/api/image_proxy/image.k1c_preview"


def test_a_failed_fetch_still_keeps_the_preview_out():
    coord = _coordinator(PRINTING)
    coord._notify_preview_image = True
    failed = SimpleNamespace(attributes={"preview_reason": "fetch_failed"})
    coord._live_entity_state = lambda platform, suffix: ("image.k1c_preview", failed)
    assert coord._notify_media(include_snapshot=False).preview_url is None


def test_a_new_job_drops_the_old_picture_and_tells_the_frontend():
    coord = _coordinator(PRINTING)
    image = CurrentPrintPreviewImage(coord)
    writes = []
    image.async_write_ha_state = lambda: writes.append(image._attr_image_last_updated)
    image._handle_coordinator_update()  # first sight of the job
    image._last_image = b"old job's preview"
    image._last_reason = "not_printing"
    before = image._attr_image_last_updated

    coord.data["printFileName"] = "/usr/data/printer_data/gcodes/next.gcode"
    image._handle_coordinator_update()

    assert image._last_image is None
    assert image._last_reason is None
    assert image._attr_image_last_updated != before
    assert writes, "the state was not written"
