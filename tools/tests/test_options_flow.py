"""The camera step of the options flow.

Driven for real rather than grepped: the interesting behaviour is which settings
survive a submit, and that depends on which fields the form rendered, which no
source-text assertion can capture.

Skipped when voluptuous is unavailable -- config_flow builds real vol.Schema
objects, so a stubbed schema would make these assertions meaningless.
"""

import asyncio
import importlib.util
import sys
import types
from unittest.mock import MagicMock

import pytest

requires_voluptuous = pytest.mark.skipif(
    importlib.util.find_spec("voluptuous") is None,
    reason="voluptuous is not installed",
)

_ABSENT = object()
_RESTORE: dict[str, object] = {}


def _stub(name: str, module) -> None:
    _RESTORE.setdefault(name, sys.modules.get(name, _ABSENT))
    sys.modules[name] = module


def teardown_module(_module):
    """Undo the process-wide stubs; they would otherwise leak to later modules."""
    for name, old in _RESTORE.items():
        if old is _ABSENT:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = old


def _install_stubs():
    """The HA surface config_flow.py imports, no more."""
    config_entries = types.ModuleType("homeassistant.config_entries")

    class _Flow:
        # `class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN)` passes a
        # keyword to the metaclass, which object.__init_subclass__ rejects.
        def __init_subclass__(cls, **_kwargs):
            pass

        def __init__(self, *_a, **_k):
            pass

    config_entries.ConfigEntry = object
    config_entries.ConfigFlow = _Flow
    config_entries.OptionsFlow = _Flow
    config_entries.OptionsFlowWithConfigEntry = _Flow
    config_entries.CONN_CLASS_LOCAL_PUSH = "local_push"
    config_entries.HANDLERS = MagicMock()
    _stub("homeassistant.config_entries", config_entries)

    data_entry_flow = types.ModuleType("homeassistant.data_entry_flow")
    data_entry_flow.FlowResult = dict
    _stub("homeassistant.data_entry_flow", data_entry_flow)

    # selector.*Selector are only used to build the schema; identity is enough.
    selector = types.ModuleType("homeassistant.helpers.selector")
    for name in (
        "SelectSelector", "SelectSelectorConfig", "SelectOptionDict",
        "TextSelector", "TextSelectorConfig", "NumberSelector",
        "NumberSelectorConfig", "BooleanSelector", "EntitySelector",
        "EntitySelectorConfig", "DeviceSelector", "DeviceSelectorConfig",
    ):
        setattr(selector, name, MagicMock(return_value=str))
    selector.SelectSelectorMode = MagicMock()
    selector.TextSelectorType = MagicMock()
    selector.NumberSelectorMode = MagicMock()
    _stub("homeassistant.helpers.selector", selector)

    helpers = sys.modules.get("homeassistant.helpers")
    if helpers is not None:
        helpers.selector = selector


_install_stubs()

from custom_components.ha_creality_ws.config_flow import (  # noqa: E402
    OptionsFlowHandler,
)
from custom_components.ha_creality_ws.const import (  # noqa: E402
    CAM_MODE_CUSTOM,
    CAM_MODE_WEBRTC,
    CAM_MODE_WEBRTC_DIRECT,
    CONF_CAMERA_MODE,
    CONF_CUSTOM_CAMERA_URL,
    CONF_GO2RTC_PORT,
    CONF_GO2RTC_RTSP_PORT,
    CONF_GO2RTC_URL,
)

EXTERNAL = {
    CONF_CAMERA_MODE: CAM_MODE_WEBRTC,
    CONF_GO2RTC_URL: "10.0.0.9",
    CONF_GO2RTC_PORT: 1984,
    CONF_GO2RTC_RTSP_PORT: 8555,
}


def _handler(options):
    """An options flow for a printer whose camera step we can submit to."""
    entry = MagicMock()
    entry.options = dict(options)
    entry.data = {"host": "1.2.3.4"}
    entry.entry_id = "entry1"

    handler = OptionsFlowHandler(entry)
    handler.hass = MagicMock()
    handler.hass.data = {}
    # The step returns to the menu on success and re-renders on error; both are
    # recorded rather than executed.
    handler.async_step_init = _record("menu")
    handler.async_show_form = lambda **kw: {"step": "form", **kw}
    return handler


def _record(tag):
    async def _inner(*_a, **_k):
        return {"step": tag}

    return _inner


def _submit(handler, user_input):
    return asyncio.run(handler.async_step_camera(user_input))


def _rendered_fields(result):
    """The option keys the form asked for, as plain strings."""
    schema = result.get("data_schema")
    return {str(getattr(k, "schema", k)) for k in schema.schema} if schema else set()


@requires_voluptuous
def test_an_external_go2rtc_survives_a_submit_that_omits_its_fields():
    """Switching to Custom must not silently reset the go2rtc server.

    The Custom step hides the go2rtc fields, so its submit carries none of them.
    Treating "absent" as "use the default" replaced a configured external server
    with localhost:11984 -- the same defect as dropping the settings entirely,
    reached from the other direction.
    """
    handler = _handler(EXTERNAL)

    result = _submit(handler, {
        CONF_CAMERA_MODE: CAM_MODE_CUSTOM,
        CONF_CUSTOM_CAMERA_URL: "rtsp://cam.local/stream1",
    })

    assert result["step"] == "menu", "a valid rtsp:// URL must be accepted"
    assert handler._working[CONF_GO2RTC_URL] == "10.0.0.9"
    assert handler._working[CONF_GO2RTC_PORT] == 1984
    assert handler._working[CONF_GO2RTC_RTSP_PORT] == 8555


@requires_voluptuous
def test_an_invalid_custom_url_does_not_discard_the_go2rtc_settings():
    """The step re-renders on a bad URL; the settings must still be there."""
    handler = _handler(EXTERNAL)

    result = _submit(handler, {
        CONF_CAMERA_MODE: CAM_MODE_CUSTOM,
        CONF_CUSTOM_CAMERA_URL: "not-a-url",
    })

    assert result["step"] == "form"
    assert result["errors"], "the bad URL must be reported"
    assert handler._working[CONF_GO2RTC_URL] == "10.0.0.9", (
        "correcting the URL must not require re-entering the go2rtc server"
    )


@requires_voluptuous
def test_a_submitted_go2rtc_url_is_still_applied():
    """The fix must not stop the fields from working when they are rendered."""
    handler = _handler(EXTERNAL)

    _submit(handler, {
        CONF_CAMERA_MODE: CAM_MODE_WEBRTC,
        CONF_GO2RTC_URL: "10.0.0.55",
        CONF_GO2RTC_PORT: 1985,
        CONF_GO2RTC_RTSP_PORT: 8556,
    })

    assert handler._working[CONF_GO2RTC_URL] == "10.0.0.55"
    assert handler._working[CONF_GO2RTC_PORT] == 1985
    assert handler._working[CONF_GO2RTC_RTSP_PORT] == 8556


@requires_voluptuous
def test_a_non_go2rtc_mode_still_drops_the_settings():
    """They must not linger on a mode that cannot use them."""
    handler = _handler(EXTERNAL)

    _submit(handler, {CONF_CAMERA_MODE: CAM_MODE_WEBRTC_DIRECT})

    assert CONF_GO2RTC_URL not in handler._working
    assert CONF_GO2RTC_PORT not in handler._working
    assert CONF_GO2RTC_RTSP_PORT not in handler._working


@requires_voluptuous
def test_an_http_custom_url_is_not_treated_as_a_go2rtc_source():
    """http(s) is fetched directly by HA, so go2rtc settings do not apply."""
    handler = _handler(EXTERNAL)

    _submit(handler, {
        CONF_CAMERA_MODE: CAM_MODE_CUSTOM,
        CONF_CUSTOM_CAMERA_URL: "http://cam.local/snapshot.jpg",
    })

    assert CONF_GO2RTC_URL not in handler._working


@requires_voluptuous
def test_the_go2rtc_fields_are_offered_for_a_custom_rtsp_source():
    """Once the URL is staged, the step must expose the server settings."""
    handler = _handler({
        CONF_CAMERA_MODE: CAM_MODE_CUSTOM,
        CONF_CUSTOM_CAMERA_URL: "rtsp://cam.local/stream1",
    })

    fields = _rendered_fields(asyncio.run(handler.async_step_camera(None)))

    assert CONF_GO2RTC_URL in fields, (
        "a Custom rtsp:// source is served by go2rtc, so it needs these fields"
    )
    assert CONF_GO2RTC_RTSP_PORT in fields
