"""Rich notification content: media gating, tap targets and tag identity.

The two media traps this file exists for: the image entity serves a 1x1
transparent PNG whenever it has no real preview, and a direct-signalling WebRTC
camera can never produce a still at all. Attaching either renders as an empty
grey box, so both are gated on an entity attribute rather than fetched
hopefully.
"""

import asyncio
import re
from types import SimpleNamespace

import pytest

from conftest import fake_config_entry

from homeassistant.helpers import entity_registry as er_mod

from custom_components.ha_creality_ws.coordinator import KCoordinator

TAG_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

PREVIEW_KEY = ("image", "1.2.3.4-current_print_preview")
CAMERA_KEY = ("camera", "1.2.3.4-camera")


class HassStub:
    def __init__(self):
        self.loop = asyncio.get_event_loop()
        self._states: dict[str, SimpleNamespace] = {}
        self.states = SimpleNamespace(get=self._states.get)
        self.calls: list[tuple[str, str, dict]] = []
        self.services = SimpleNamespace(async_call=self._async_call)
        self.tasks: list = []
        # A config_entry_id is passed so the tag is entry-derived; returning no
        # entry keeps _load_options on its defaults.
        self.config_entries = SimpleNamespace(async_get_entry=lambda _id: None)
        self.config = SimpleNamespace(language="en")

    async def _async_call(self, domain, service, data, **_kw):
        self.calls.append((domain, service, data))

    def async_create_task(self, coro):
        self.tasks.append(coro)
        return coro

    def set_state(self, entity_id, state="idle", **attrs):
        self._states[entity_id] = SimpleNamespace(
            entity_id=entity_id,
            state=state,
            domain=entity_id.split(".", 1)[0],
            attributes=attrs,
        )


@pytest.fixture(autouse=True)
def _loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield
    loop.close()


@pytest.fixture
def registry(monkeypatch):
    """A settable stand-in for the entity registry."""
    mapping: dict[tuple[str, str], str] = {}

    def _async_get(_hass):
        return SimpleNamespace(
            async_get_entity_id=lambda platform, _domain, unique: mapping.get(
                (platform, unique)
            )
        )

    monkeypatch.setattr(er_mod, "async_get", _async_get)
    return mapping


def _coordinator(entry_id="abc123def456"):
    hass = HassStub()
    coord = KCoordinator(hass, host="1.2.3.4", config_entry=fake_config_entry(entry_id))
    coord._notify_targets = ["notify.mobile_app_pixel"]
    coord.data = {"hostname": "K1C", "printProgress": 42}
    return coord, hass


def _flush(hass):
    """Await everything dispatch queued, then drain the recorded calls.

    Drains rather than reads: a test that emits twice would otherwise keep
    asserting against the first payload.
    """
    pending, hass.tasks = hass.tasks, []
    if pending:
        asyncio.get_event_loop().run_until_complete(asyncio.gather(*pending))
    calls, hass.calls = hass.calls, []
    return calls


def _emit(coord, hass, kind="completed", message="done"):
    asyncio.get_event_loop().run_until_complete(
        coord._notify_event(message, kind=kind)
    )
    calls = _flush(hass)
    assert calls, "expected a dispatched notification"
    return calls[0][2]["data"]


# --------------------------------------------------------------------------- #
# The G-code preview
# --------------------------------------------------------------------------- #


def test_the_preview_is_attached_when_the_entity_says_it_is_real(registry):
    coord, hass = _coordinator()
    registry[PREVIEW_KEY] = "image.k1c_current_print_preview"
    hass.set_state("image.k1c_current_print_preview", preview_reason="ok")
    data = _emit(coord, hass)
    assert data["icon_url"] == "/api/image_proxy/image.k1c_current_print_preview"


@pytest.mark.parametrize("reason", ["not_printing", "fetch_failed"])
def test_a_placeholder_preview_is_never_attached(registry, reason):
    """Sending the 1x1 transparent PNG's URL renders an empty grey square."""
    coord, hass = _coordinator()
    registry[PREVIEW_KEY] = "image.k1c_current_print_preview"
    hass.set_state("image.k1c_current_print_preview", preview_reason=reason)
    assert "icon_url" not in _emit(coord, hass)


@pytest.mark.parametrize("attrs", [{"preview_reason": None}, {}])
def test_an_unfetched_preview_is_still_attached(registry, attrs):
    """`preview_reason` is only set once something has asked the image entity
    for bytes, and Home Assistant never does that on its own. Treating unset as
    "no good" meant the preview was missing from every notification until a
    dashboard happened to render the entity -- the companion app's own fetch is
    what populates it.
    """
    coord, hass = _coordinator()
    registry[PREVIEW_KEY] = "image.k1c_current_print_preview"
    hass.set_state("image.k1c_current_print_preview", **attrs)
    assert _emit(coord, hass)["icon_url"] == (
        "/api/image_proxy/image.k1c_current_print_preview"
    )


def test_a_disabled_preview_entity_is_not_attached(registry):
    """The registry answers for disabled entities too, and the proxy 404s."""
    coord, hass = _coordinator()
    registry[PREVIEW_KEY] = "image.k1c_current_print_preview"
    # Registered, but no state: disabled, or not yet added.
    assert "icon_url" not in _emit(coord, hass)


def test_an_unresolvable_preview_never_becomes_a_none_url(registry):
    coord, hass = _coordinator()
    data = _emit(coord, hass)
    assert "icon_url" not in data
    assert "None" not in str(data)


def test_the_preview_can_be_switched_off(registry):
    coord, hass = _coordinator()
    coord._notify_preview_image = False
    registry[PREVIEW_KEY] = "image.k1c_current_print_preview"
    hass.set_state("image.k1c_current_print_preview", preview_reason="ok")
    assert "icon_url" not in _emit(coord, hass)


# --------------------------------------------------------------------------- #
# The camera snapshot
# --------------------------------------------------------------------------- #


def test_a_snapshot_capable_camera_is_attached(registry):
    coord, hass = _coordinator()
    registry[CAMERA_KEY] = "camera.k1c_printer_camera"
    hass.set_state("camera.k1c_printer_camera", snapshot_supported=True)
    data = _emit(coord, hass)
    assert data["image"] == "/api/camera_proxy/camera.k1c_printer_camera"


def test_a_direct_webrtc_camera_is_never_attached(registry):
    """It has no snapshot endpoint at all: async_camera_image returns the 1x1
    fallback unconditionally, so a still is permanently unavailable."""
    coord, hass = _coordinator()
    registry[CAMERA_KEY] = "camera.k1c_printer_camera"
    hass.set_state("camera.k1c_printer_camera", snapshot_supported=False)
    assert "image" not in _emit(coord, hass)


def test_the_snapshot_can_be_switched_off(registry):
    coord, hass = _coordinator()
    coord._notify_camera_snapshot = False
    registry[CAMERA_KEY] = "camera.k1c_printer_camera"
    hass.set_state("camera.k1c_printer_camera", snapshot_supported=True)
    assert "image" not in _emit(coord, hass)


def test_an_alert_also_shows_what_the_bed_looks_like(registry):
    coord, hass = _coordinator()
    registry[CAMERA_KEY] = "camera.k1c_printer_camera"
    hass.set_state("camera.k1c_printer_camera", snapshot_supported=True)
    data = _emit(coord, hass, kind="error", message="Printer Error 521")
    assert data["image"] == "/api/camera_proxy/camera.k1c_printer_camera"


# --------------------------------------------------------------------------- #
# Tap targets
# --------------------------------------------------------------------------- #


def test_a_tap_opens_the_camera_with_no_configuration_at_all(registry):
    coord, hass = _coordinator()
    registry[CAMERA_KEY] = "camera.k1c_printer_camera"
    hass.set_state("camera.k1c_printer_camera", snapshot_supported=True)
    data = _emit(coord, hass)
    assert data["clickAction"] == "entityId:camera.k1c_printer_camera"
    # entityId: is Android-only, so iOS gets nothing rather than a dead link.
    assert "url" not in data


def test_without_a_camera_a_tap_falls_back_to_the_preview(registry):
    coord, hass = _coordinator()
    registry[PREVIEW_KEY] = "image.k1c_current_print_preview"
    data = _emit(coord, hass)
    assert data["clickAction"] == "entityId:image.k1c_current_print_preview"


def test_with_nothing_resolved_no_tap_target_is_sent(registry):
    data = _emit(*_coordinator())
    assert "clickAction" not in data
    assert "url" not in data


def test_a_configured_dashboard_path_wins_on_both_platforms(registry):
    coord, hass = _coordinator()
    coord._notify_tap_path = "/lovelace/printer"
    registry[CAMERA_KEY] = "camera.k1c_printer_camera"
    data = _emit(coord, hass)
    assert data["clickAction"] == "/lovelace/printer"
    assert data["url"] == "/lovelace/printer"


def test_a_path_that_is_not_a_path_is_ignored(registry):
    """Otherwise a stray word would be sent as a clickAction and do nothing."""
    coord, hass = _coordinator()
    coord._notify_tap_path = "printer"
    registry[CAMERA_KEY] = "camera.k1c_printer_camera"
    assert _emit(coord, hass)["clickAction"] == "entityId:camera.k1c_printer_camera"


# --------------------------------------------------------------------------- #
# Resolution caching
# --------------------------------------------------------------------------- #


def test_a_resolved_entity_is_cached(registry, monkeypatch):
    coord, _hass = _coordinator()
    registry[CAMERA_KEY] = "camera.k1c_printer_camera"
    assert coord._resolve_entity_id("camera", "camera") == "camera.k1c_printer_camera"

    # A second lookup must not consult the registry again.
    def _boom(_hass):
        raise AssertionError("registry consulted twice for a cached entity")

    monkeypatch.setattr(er_mod, "async_get", _boom)
    assert coord._resolve_entity_id("camera", "camera") == "camera.k1c_printer_camera"


def test_a_miss_is_not_cached(registry):
    """Platforms are forwarded after the coordinator exists, so an early lookup
    must not pin None for the lifetime of the entry."""
    coord, _hass = _coordinator()
    assert coord._resolve_entity_id("camera", "camera") is None
    registry[CAMERA_KEY] = "camera.k1c_printer_camera"
    assert coord._resolve_entity_id("camera", "camera") == "camera.k1c_printer_camera"


def test_options_reload_drops_the_cache(registry):
    coord, _hass = _coordinator()
    registry[CAMERA_KEY] = "camera.old_name"
    assert coord._resolve_entity_id("camera", "camera") == "camera.old_name"
    registry[CAMERA_KEY] = "camera.renamed_by_user"
    coord._entity_id_cache.clear()
    assert coord._resolve_entity_id("camera", "camera") == "camera.renamed_by_user"


def test_a_registry_failure_is_survivable(registry, monkeypatch):
    coord, _hass = _coordinator()

    def _explode(_hass):
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(er_mod, "async_get", _explode)
    assert coord._resolve_entity_id("camera", "camera") is None


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #


def test_the_tag_is_legal_and_derived_from_the_entry_not_the_host(registry):
    coord, hass = _coordinator()
    data = _emit(coord, hass)
    assert TAG_RE.match(data["tag"])
    # The host is an IP: dots would be rejected outright.
    assert "1.2.3.4" not in data["tag"]
    assert "abc123def456" in data["tag"]


def test_the_tag_stays_legal_when_there_is_no_entry_id_to_use(registry):
    """Falls back to the host, which must then be sanitised."""
    hass = HassStub()
    coord = KCoordinator(hass, host="1.2.3.4")
    coord._notify_targets = ["notify.mobile_app_pixel"]
    coord.data = {"hostname": "K1C"}
    data = _emit(coord, hass)
    assert TAG_RE.match(data["tag"])


def test_alerts_and_lifecycle_events_do_not_share_a_tag(registry):
    """An error must not be overwritten by a "finishing soon" heads-up."""
    coord, hass = _coordinator()
    event_tag = _emit(coord, hass, kind="completed")["tag"]
    alert_tag = _emit(coord, hass, kind="error")["tag"]
    assert event_tag != alert_tag
    # ...but they group together on the phone.
    assert _emit(coord, hass)["group"] == coord._notify_tag_base()


def test_the_title_is_the_printer_not_the_job(registry):
    """It is required on Android and cannot be changed once a live activity has
    started, so a per-job title would go stale immediately."""
    coord, hass = _coordinator()
    coord.data = {"hostname": "K1C", "printFileName": "/gcodes/3DBenchy.gcode"}
    asyncio.get_event_loop().run_until_complete(
        coord._notify_event("done", kind="completed")
    )
    payload = _flush(hass)[0][2]
    assert payload["title"] == "K1C"
    assert "3DBenchy" not in payload["title"]


def test_the_title_falls_back_to_the_host_not_a_generic_string(registry):
    """The title freezes for the life of a live activity, and telemetry arrives
    incrementally -- so a card can start before `hostname` shows up. A generic
    fallback would then label the whole job "Creality Printer", with no way to
    tell two printers apart."""
    coord, hass = _coordinator()
    coord.data = {}
    asyncio.get_event_loop().run_until_complete(
        coord._notify_event("done", kind="completed")
    )
    assert _flush(hass)[0][2]["title"] == "1.2.3.4"
