"""The camera step of the options flow.

Driven for real rather than grepped: the interesting behaviour is which settings
survive a submit, and that depends on which fields the form rendered, which no
source-text assertion can capture.

Skipped when voluptuous is unavailable -- config_flow builds real vol.Schema
objects, so a stubbed schema would make these assertions meaningless.
"""

import asyncio
import importlib.util
import json
import re
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# A `skipif` marker is evaluated *after* collection, so it cannot protect the
# module-level `config_flow` import below -- and config_flow does `import
# voluptuous as vol`, so without it this module raised a collection error
# instead of skipping, which is the opposite of what the docstring promises.
pytest.importorskip("voluptuous", reason="voluptuous is not installed")

requires_voluptuous = pytest.mark.skipif(
    importlib.util.find_spec("voluptuous") is None,
    reason="voluptuous is not installed",
)

_ABSENT = object()
_RESTORE: dict[str, object] = {}
_RESTORE_ATTRS: list[tuple[object, str, object]] = []


def _stub(name: str, module) -> None:
    _RESTORE.setdefault(name, sys.modules.get(name, _ABSENT))
    sys.modules[name] = module


def teardown_module(_module):
    """Undo the process-wide stubs; they would otherwise leak to later modules."""
    for obj, attr, previous in reversed(_RESTORE_ATTRS):
        if previous is _ABSENT:
            try:
                delattr(obj, attr)
            except AttributeError:
                pass
        else:
            setattr(obj, attr, previous)
    _RESTORE_ATTRS.clear()
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

    class _Section:
        """Home Assistant's `section`, close enough to introspect.

        The real one keeps the inner schema on `.schema` and validates through
        it, which is exactly what these tests read to find the fields a group
        renders.
        """

        def __init__(self, schema, options=None):
            self.schema = schema
            self.options = options or {}

        def __call__(self, value):
            return self.schema(value)

    data_entry_flow.section = _Section
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

    # The attribute on the shared parent module, recorded so teardown can undo it.
    # `_RESTORE` covers `sys.modules` only, so without this a later
    # `from homeassistant.helpers import selector` kept receiving this stub.
    helpers = sys.modules.get("homeassistant.helpers")
    if helpers is not None:
        _RESTORE_ATTRS.append((helpers, "selector", getattr(helpers, "selector", _ABSENT)))
        helpers.selector = selector


_install_stubs()

from custom_components.ha_creality_ws.config_flow import (  # noqa: E402
    OptionsFlowHandler,
)
from custom_components.ha_creality_ws.const import (  # noqa: E402
    CAM_MODE_CUSTOM,
    CONF_POLLING_RATE,
    CONF_NOTIFY_TEMPLATE_COMPLETED,
    CONF_NOTIFY_TEMPLATE_LIVE,
    CONF_HOST,
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
    # The real base class writes the options and closes the dialog; recording
    # the call is enough to assert what would have been written.
    handler.async_create_entry = lambda **kw: {"step": "created", **kw}
    return handler


def _record(tag):
    async def _inner(*_a, **_k):
        return {"step": tag}

    return _inner


def _submit(handler, user_input):
    return asyncio.run(handler.async_step_camera(user_input))


def _rendered_fields(result):
    """Every option key the form asked for, sections included.

    Flat on purpose: which group a field is rendered in is a property of the
    form, and a test that pinned it would fail every time one moved. The tests
    that care about the grouping ask `_section_fields` instead.
    """
    schema = result.get("data_schema")
    if not schema:
        return set()
    found = set()
    for marker, value in schema.schema.items():
        inner = getattr(value, "schema", None)
        if inner is not None and hasattr(inner, "schema"):
            found.update(str(getattr(k, "schema", k)) for k in inner.schema)
        else:
            found.add(str(getattr(marker, "schema", marker)))
    return found


def _section_fields(result, name):
    """The option keys one collapsible group renders."""
    for marker, value in result["data_schema"].schema.items():
        if str(getattr(marker, "schema", marker)) != name:
            continue
        return {str(getattr(k, "schema", k)) for k in value.schema.schema}
    raise AssertionError(f"no section {name!r} in {_rendered_fields(result)}")


def _section(result, name):
    """The section object itself, for its collapsed state."""
    for marker, value in result["data_schema"].schema.items():
        if str(getattr(marker, "schema", marker)) == name:
            return value
    raise AssertionError(f"no section {name!r}")


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
@pytest.mark.parametrize("url", [
    # Non-empty netloc made entirely of userinfo: `urlparse` reports
    # netloc="user@" but hostname=None, so a netloc check accepted it.
    "rtsp://user@",
    "rtsp://user:pw@",
    "rtsp://@",
    "http://",
    "rtsp://",
    "not-a-url",
])
def test_a_custom_url_without_a_host_is_refused(url):
    handler = _handler(EXTERNAL)

    _submit(handler, {CONF_CAMERA_MODE: CAM_MODE_CUSTOM, CONF_CUSTOM_CAMERA_URL: url})

    assert CONF_CUSTOM_CAMERA_URL not in handler._working, (
        f"{url!r} has no host and must not be saved as a camera source"
    )


@requires_voluptuous
@pytest.mark.parametrize("url", [
    "rtsp://10.0.0.5:554/stream1",
    "rtsp://user:pw@10.0.0.5/stream1",
    "https://cam.local/snapshot.jpg",
])
def test_a_custom_url_with_a_host_is_accepted(url):
    """Credentials in the URL are fine as long as a host follows them."""
    handler = _handler(EXTERNAL)

    _submit(handler, {CONF_CAMERA_MODE: CAM_MODE_CUSTOM, CONF_CUSTOM_CAMERA_URL: url})

    assert handler._working[CONF_CUSTOM_CAMERA_URL] == url


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
    """Submit the notifications page the way the frontend does: in sections.

    Tests name their fields flat, because which group a field is rendered in is
    a property of the form and not of the setting. The nesting is put back here
    from the same map the form builds itself out of, so a submit in a test has
    the shape a real one has.
    """
    from custom_components.ha_creality_ws.config_flow import _NOTIFY_SECTIONS

    payload: dict = {}
    for key, value in user_input.items():
        for name, fields in _NOTIFY_SECTIONS.items():
            if key in fields:
                payload.setdefault(name, {})[key] = value
                break
        else:
            payload[key] = value
    return asyncio.run(handler.async_step_notifications(payload))


def _render_notifications(handler):
    return asyncio.run(handler.async_step_notifications(None))


def _defaults(result):
    """The default each rendered field was offered with, sections included."""
    schema = result.get("data_schema")
    out = {}

    def _collect(mapping):
        for marker, value in mapping.items():
            inner = getattr(value, "schema", None)
            if inner is not None and hasattr(inner, "schema"):
                _collect(inner.schema)
                continue
            default = getattr(marker, "default", None)
            out[str(getattr(marker, "schema", marker))] = (
                default() if callable(default) else default
            )

    _collect(schema.schema)
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
    must not be handed a self-refreshing lock-screen card by an update."""
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
def test_the_generic_send_message_action_is_not_offered_as_a_target():
    """`notify.send_message` takes an `entity_id`, so it can only ever fail.

    It appears in the notify service registry alongside the per-device legacy
    services, and `_async_deliver_one` would call it with no entity target.
    """
    from homeassistant.helpers import selector as stubbed_selector

    handler = _handler({})
    handler.hass.services.async_services = lambda: {
        "notify": {
            "send_message": None,
            "mobile_app_pixel": None,
            "persistent_notification": None,
        }
    }
    stubbed_selector.SelectOptionDict.reset_mock()
    handler._notify_target_options([])
    offered = [
        call.kwargs["value"]
        for call in stubbed_selector.SelectOptionDict.call_args_list
    ]
    assert "notify.send_message" not in offered
    assert offered == ["notify.mobile_app_pixel", "notify.persistent_notification"]


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


# --------------------------------------------------------------------------- #
# Saving
# --------------------------------------------------------------------------- #
# Every section used to stage into `_working` and only "Save and apply"
# persisted it, which made the one menu item you could not skip look like the
# one you could -- closing the dialog, the way a settings dialog normally ends,
# threw the lot away.


def _updates(handler):
    """The kwargs of the single entry update a submit should have made."""
    calls = handler.hass.config_entries.async_update_entry.call_args_list
    assert len(calls) == 1, f"expected exactly one entry update, got {len(calls)}"
    return calls[0].kwargs


@requires_voluptuous
def test_submitting_a_section_saves_it_there_and_then():
    handler = _handler({})

    result = _submit(handler, {CONF_CAMERA_MODE: CAM_MODE_WEBRTC_DIRECT})

    assert result["step"] == "menu"
    assert _updates(handler)["options"][CONF_CAMERA_MODE] == CAM_MODE_WEBRTC_DIRECT


@requires_voluptuous
def test_a_refused_submit_saves_nothing():
    """An invalid camera URL re-renders the form. Writing the half-applied
    working copy would persist a mode whose URL the user has not fixed yet."""
    handler = _handler({})

    result = _submit(handler, {
        CONF_CAMERA_MODE: CAM_MODE_CUSTOM,
        CONF_CUSTOM_CAMERA_URL: "not-a-url",
    })

    assert result["step"] == "form"
    assert handler.hass.config_entries.async_update_entry.call_args_list == []


@requires_voluptuous
def test_a_refused_submit_does_not_leak_into_the_next_section():
    """The refusal itself writes nothing, but `_persist` writes the *whole*
    working copy -- so a camera mode applied before its URL had been validated
    was saved by whichever section the user submitted next, leaving Custom
    configured with no URL to go with it."""
    handler = _handler({CONF_CAMERA_MODE: CAM_MODE_WEBRTC_DIRECT})

    _submit(handler, {
        CONF_CAMERA_MODE: CAM_MODE_CUSTOM,
        CONF_CUSTOM_CAMERA_URL: "not-a-url",
    })
    asyncio.run(handler.async_step_connection({CONF_POLLING_RATE: 3}))

    options = _updates(handler)["options"]
    assert options[CONF_CAMERA_MODE] == CAM_MODE_WEBRTC_DIRECT
    assert CONF_CUSTOM_CAMERA_URL not in options


@requires_voluptuous
def test_a_new_host_and_the_options_go_out_in_one_update():
    """The host lives in `data` and everything else in `options`. Updating them
    separately fired the update listener twice and reloaded the entry twice for
    one submit."""
    handler = _handler({})

    asyncio.run(handler.async_step_connection({
        CONF_HOST: " 5.6.7.8 ",
        CONF_POLLING_RATE: 3,
    }))

    updates = _updates(handler)
    assert updates["data"][CONF_HOST] == "5.6.7.8"
    assert updates["options"][CONF_POLLING_RATE] == 3


@requires_voluptuous
def test_an_unchanged_host_is_not_written_back():
    """Home Assistant skips an update that changes nothing, but only if we do
    not hand it a `data` dict to compare in the first place."""
    handler = _handler({})

    asyncio.run(handler.async_step_connection({CONF_HOST: "1.2.3.4"}))

    assert "data" not in _updates(handler)


@requires_voluptuous
def test_the_menu_holds_nothing_that_has_to_be_pressed_to_save():
    """The point of saving on submit is that closing the dialog cannot lose
    anything -- which a "Save and apply" item at the bottom of the menu flatly
    contradicts, by looking like one more optional page."""
    handler = _handler({})
    handler.async_step_init = OptionsFlowHandler.async_step_init.__get__(handler)
    handler.async_show_menu = lambda **kw: {"step": "menu", **kw}

    options = asyncio.run(handler.async_step_init(None))["menu_options"]

    assert "notifications" in options
    assert not {"save", "done", "finish", "apply"}.intersection(options)
    # And no separate page for the notification text: it is a group on the
    # notifications page.
    assert "notification_text" not in options


# --------------------------------------------------------------------------- #
# The notification-text step
# --------------------------------------------------------------------------- #


# The custom-text fields live on the notifications page too, in their own
# collapsible group -- there is no separate page for them any more.
_submit_text = _submit_notifications


@requires_voluptuous
def test_a_template_is_saved_and_offered_back():
    handler = _handler({})
    template = "{filename} is {progress}% done"

    result = _submit_text(handler, {CONF_NOTIFY_TEMPLATE_LIVE: template})

    assert result["step"] == "menu"
    assert _updates(handler)["options"][CONF_NOTIFY_TEMPLATE_LIVE] == template
    reopened = _defaults(_render_notifications(handler))
    assert reopened[CONF_NOTIFY_TEMPLATE_LIVE] == template


@requires_voluptuous
def test_an_unknown_placeholder_is_refused():
    """The only moment the typo can be shown to the person who made it: a
    notification is composed on a WebSocket frame, where the best available
    recourse is a line in the log."""
    handler = _handler({})

    result = _submit_text(
        handler, {CONF_NOTIFY_TEMPLATE_COMPLETED: "used {filament_grams}"}
    )

    assert result["step"] == "form"
    assert result["errors"] == {
        CONF_NOTIFY_TEMPLATE_COMPLETED: "unknown_placeholder"
    }
    assert handler.hass.config_entries.async_update_entry.call_args_list == []


@requires_voluptuous
def test_a_refused_template_is_offered_back_as_the_user_typed_it():
    """Re-rendering the stored value instead would take the typo away along
    with any chance of correcting it."""
    handler = _handler({})
    typed = "used {filament_grams}"

    result = _submit_text(handler, {CONF_NOTIFY_TEMPLATE_COMPLETED: typed})

    assert _defaults(result)[CONF_NOTIFY_TEMPLATE_COMPLETED] == typed


@requires_voluptuous
def test_one_bad_field_does_not_take_a_good_one_with_it():
    """The valid field is still re-offered with what the user typed, so a
    corrected submit keeps both -- but nothing is stored until the whole page
    is valid, or "Done" would later save the good half of a page the user was
    still being shown an error for."""
    handler = _handler({})

    result = _submit_text(handler, {
        CONF_NOTIFY_TEMPLATE_LIVE: "{filename} {progress}%",
        CONF_NOTIFY_TEMPLATE_COMPLETED: "{nope}",
    })

    assert set(result["errors"]) == {CONF_NOTIFY_TEMPLATE_COMPLETED}
    assert _defaults(result)[CONF_NOTIFY_TEMPLATE_LIVE] == "{filename} {progress}%"
    assert CONF_NOTIFY_TEMPLATE_LIVE not in handler._working


@requires_voluptuous
def test_an_emptied_template_is_stored_as_empty_rather_than_none():
    """Clearing the box is how the built-in text comes back, and a stored None
    is what `options.get(key, default)` returns instead of the default."""
    handler = _handler({CONF_NOTIFY_TEMPLATE_LIVE: "{filename}"})

    _submit_text(handler, {CONF_NOTIFY_TEMPLATE_LIVE: None})

    assert _updates(handler)["options"][CONF_NOTIFY_TEMPLATE_LIVE] == ""


@requires_voluptuous
def test_the_text_group_offers_every_template_option():
    """A key in NOTIFY_TEMPLATE_OPTIONS with no field is text the coordinator
    reads and nobody can set."""
    from custom_components.ha_creality_ws.const import NOTIFY_TEMPLATE_OPTIONS

    rendered = _render_notifications(_handler({}))
    assert _section_fields(rendered, "text") == set(NOTIFY_TEMPLATE_OPTIONS.values())


@requires_voluptuous
def test_every_notification_setting_is_rendered_exactly_once():
    """The grouping is the form's business, but a field that fell out of every
    group -- or landed in two -- is a setting the user can no longer reach, or
    one that submits twice with different values."""
    from custom_components.ha_creality_ws.config_flow import _NOTIFY_SECTIONS

    rendered = _render_notifications(_handler({}))
    grouped = [key for fields in _NOTIFY_SECTIONS.values() for key in fields]

    assert len(grouped) == len(set(grouped)), "a setting is in two groups"
    assert _rendered_fields(rendered) == set(grouped) | {CONF_NOTIFY_TARGETS}
    for name, fields in _NOTIFY_SECTIONS.items():
        assert _section_fields(rendered, name) == set(fields), name


@requires_voluptuous
def test_an_untouched_group_opens_folded_away():
    """The point of the grouping: a page that is short until you need it."""
    rendered = _render_notifications(_handler({}))

    # What you came for, always open.
    assert _section(rendered, "events").options["collapsed"] is False
    assert _section(rendered, "text").options["collapsed"] is True


@requires_voluptuous
def test_a_group_the_user_has_changed_opens_by_itself():
    """Custom text hidden behind a disclosure that looks untouched is text the
    user cannot find again."""
    rendered = _render_notifications(
        _handler({CONF_NOTIFY_TEMPLATE_LIVE: "{filename}"})
    )
    assert _section(rendered, "text").options["collapsed"] is False

    # And so does one holding an error, whatever its stored values look like.
    handler = _handler({})
    refused = _submit_text(handler, {CONF_NOTIFY_TEMPLATE_LIVE: "{nope}"})
    assert _section(refused, "text").options["collapsed"] is False


@requires_voluptuous
def test_every_placeholder_the_strings_use_is_supplied():
    """The frontend renders a step's text through ICU MessageFormat, so a
    `{name}` it was given no value for does not render as itself: it replaces
    the whole string with "Translation error". Six per-notification placeholder
    lists are a lot of names to keep in step by hand, and the field help is the
    one thing on this page nobody would think to re-read after a rename.
    """
    strings = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "custom_components/ha_creality_ws/strings.json"
        ).read_text(encoding="utf-8")
    )["options"]["step"]["notifications"]

    used = set()

    def _scan(value):
        if isinstance(value, dict):
            for item in value.values():
                _scan(item)
        elif isinstance(value, str):
            used.update(re.findall(r"\{([a-z_]+)\}", value))

    _scan(strings)
    assert used, "no placeholders found -- this guard would be vacuous"

    supplied = set(_render_notifications(_handler({}))["description_placeholders"])
    assert used <= supplied, f"nothing supplies {sorted(used - supplied)}"


@requires_voluptuous
def test_a_submit_missing_a_whole_group_leaves_it_alone():
    """The groups are optional in the schema, so a submit that arrives without
    one must keep what is stored rather than reset it to the form's defaults --
    the same rule the camera step learned the hard way about go2rtc."""
    handler = _handler({
        CONF_NOTIFY_TEMPLATE_LIVE: "{filename}",
        CONF_NOTIFY_TARGETS: ["notify.mobile_app_pixel"],
    })

    asyncio.run(handler.async_step_notifications({
        "events": {CONF_NOTIFY_LIVE: True},
    }))

    stored = _updates(handler)["options"]
    assert stored[CONF_NOTIFY_LIVE] is True
    assert stored[CONF_NOTIFY_TEMPLATE_LIVE] == "{filename}"


@requires_voluptuous
def test_the_settings_are_stored_flat_whatever_group_they_are_rendered_in():
    """The grouping is a property of the form. Writing it into the options would
    move a user's settings the next time a field moved between groups."""
    handler = _handler({})

    _submit_notifications(handler, {
        CONF_NOTIFY_LIVE: True,
        CONF_NOTIFY_TAP_PATH: "/lovelace/printer",
        CONF_NOTIFY_TEMPLATE_LIVE: "{filename}",
    })

    stored = _updates(handler)["options"]
    assert not {"events", "extras", "text"}.intersection(stored)
    assert stored[CONF_NOTIFY_LIVE] is True
    assert stored[CONF_NOTIFY_TAP_PATH] == "/lovelace/printer"
    assert stored[CONF_NOTIFY_TEMPLATE_LIVE] == "{filename}"


@requires_voluptuous
def test_a_placeholder_is_only_accepted_where_it_can_be_filled():
    """`{minutes}` is the point of the finishing-soon reminder and is nothing at
    all on a live card, where it would have rendered as empty text."""
    from custom_components.ha_creality_ws.const import CONF_NOTIFY_TEMPLATE_SOON

    handler = _handler({})
    refused = _submit_text(handler, {CONF_NOTIFY_TEMPLATE_LIVE: "{minutes} left"})
    assert refused["errors"] == {CONF_NOTIFY_TEMPLATE_LIVE: "unknown_placeholder"}

    handler = _handler({})
    accepted = _submit_text(handler, {CONF_NOTIFY_TEMPLATE_SOON: "{minutes} left"})
    assert accepted["step"] == "menu"
