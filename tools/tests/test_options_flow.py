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
    config_entries.ConfigFlowResult = dict
    # Carried over from the conftest stub this one replaces. pytest imports every
    # test module during collection, so this stub is still installed while later
    # modules are being imported -- dropping an attribute here breaks them, and
    # teardown_module runs far too late to help.
    config_entries.OperationNotAllowed = Exception
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
    CONF_MINUTES_TO_END_VALUE,
    CONF_NOTIFY_COMPLETED,
    CONF_NOTIFY_DEVICE,
    CONF_NOTIFY_LIVE,
    CONF_NOTIFY_TAP_PATH,
    CONF_NOTIFY_TARGETS,
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

    handler = OptionsFlowHandler()
    # Home Assistant resolves OptionsFlow.config_entry from a private handler id
    # via hass; the stubbed base class has no such property, so the test assigns
    # it directly.
    handler.config_entry = entry
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


# --------------------------------------------------------------------------- #
# The notifications step
# --------------------------------------------------------------------------- #


def _submit_notifications(handler, user_input):
    return asyncio.run(handler.async_step_notifications(user_input))


def _render_notifications(handler):
    return asyncio.run(handler.async_step_notifications(None))


def _defaults(result):
    """The default each rendered field was offered with."""
    schema = result.get("data_schema")
    out = {}
    for marker in schema.schema:
        default = getattr(marker, "default", None)
        out[str(getattr(marker, "schema", marker))] = (
            default() if callable(default) else default
        )
    return out


@requires_voluptuous
def test_a_legacy_single_device_is_preselected_as_a_target():
    """Upgrading users must see their old device already chosen, so the first
    save persists the new multi-target shape."""
    handler = _handler({CONF_NOTIFY_DEVICE: "notify.mobile_app_pixel"})
    defaults = _defaults(_render_notifications(handler))
    assert defaults[CONF_NOTIFY_TARGETS] == ["notify.mobile_app_pixel"]


@requires_voluptuous
def test_the_live_card_is_opt_in_for_upgrading_users():
    """Someone who only ticked "notify when completed" in an earlier release
    must not be handed a persistent lock-screen card by an update."""
    handler = _handler(
        {CONF_NOTIFY_DEVICE: "notify.mobile_app_pixel", CONF_NOTIFY_COMPLETED: True}
    )
    assert _defaults(_render_notifications(handler))[CONF_NOTIFY_LIVE] is False


@requires_voluptuous
def test_a_cleared_field_is_never_persisted_as_none():
    """`options.get(key, DEFAULT)` returns a stored None rather than the default,
    and the int() cast at setup then fails permanently -- a cleared field would
    brick the entry."""
    handler = _handler({CONF_MINUTES_TO_END_VALUE: 5})
    _submit_notifications(
        handler,
        {
            CONF_NOTIFY_TARGETS: ["notify.mobile_app_pixel"],
            CONF_MINUTES_TO_END_VALUE: None,
            CONF_NOTIFY_TAP_PATH: None,
        },
    )
    assert None not in handler._working.values()
    assert handler._working[CONF_MINUTES_TO_END_VALUE] == 5


@requires_voluptuous
def test_an_emptied_target_list_is_stored_as_a_list_not_none():
    handler = _handler({CONF_NOTIFY_DEVICE: "notify.mobile_app_pixel"})
    _submit_notifications(handler, {CONF_NOTIFY_TARGETS: None})
    assert handler._working[CONF_NOTIFY_TARGETS] == []
    # The legacy key survives, so rolling back keeps the user's target.
    assert handler._working[CONF_NOTIFY_DEVICE] == "notify.mobile_app_pixel"


@requires_voluptuous
def test_blank_and_duplicate_targets_are_cleaned_on_submit():
    handler = _handler({})
    _submit_notifications(
        handler,
        {CONF_NOTIFY_TARGETS: ["  notify.mobile_app_pixel  ", "", "   "]},
    )
    assert handler._working[CONF_NOTIFY_TARGETS] == ["notify.mobile_app_pixel"]


@requires_voluptuous
def test_the_step_offers_the_new_fields_and_retires_the_single_device():
    fields = _rendered_fields(_render_notifications(_handler({})))
    assert CONF_NOTIFY_TARGETS in fields
    assert CONF_NOTIFY_LIVE in fields
    assert CONF_NOTIFY_TAP_PATH in fields
    # Still read for migration, but no longer something the user edits.
    assert CONF_NOTIFY_DEVICE not in fields


@requires_voluptuous
def test_an_already_stored_target_stays_offered_when_its_integration_is_down():
    """Otherwise opening the step while a phone's integration is unloaded would
    quietly drop that target on save.

    SelectOptionDict is stubbed, so the built list carries no values -- the
    assertion goes through the recorded calls instead.
    """
    from homeassistant.helpers import selector as stubbed_selector

    handler = _handler({CONF_NOTIFY_TARGETS: ["notify.mobile_app_unloaded"]})
    stubbed_selector.SelectOptionDict.reset_mock()
    handler._notify_target_options(["notify.mobile_app_unloaded"])
    offered = [
        call.kwargs["value"]
        for call in stubbed_selector.SelectOptionDict.call_args_list
    ]
    assert offered == ["notify.mobile_app_unloaded"]


@requires_voluptuous
def test_the_offered_targets_are_deduplicated_and_sorted():
    from homeassistant.helpers import selector as stubbed_selector

    handler = _handler({})
    handler.hass.services.async_services = lambda: {
        "notify": {"mobile_app_pixel": None, "signal_messenger": None}
    }
    stubbed_selector.SelectOptionDict.reset_mock()
    handler._notify_target_options(["notify.mobile_app_pixel", "notify.my_entity"])
    offered = [
        call.kwargs["value"]
        for call in stubbed_selector.SelectOptionDict.call_args_list
    ]
    assert offered == [
        "notify.mobile_app_pixel",
        "notify.my_entity",
        "notify.signal_messenger",
    ]
