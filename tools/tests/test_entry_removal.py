"""Deleting the integration must take its notifications off every phone.

Unload deliberately leaves them alone: `options_update_listener` reloads the
entry on any options change, so dismissing there would make the card flicker
whenever an unrelated setting is toggled. Removal is the one teardown that is
not a reload, and it is final -- nothing will ever push to these tags again, so
anything left behind sits on a phone forever advertising a printer Home
Assistant no longer has.
"""

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import fake_config_entry


def _load_package_init():
    """Execute the real `__init__.py`.

    conftest registers `custom_components.ha_creality_ws` as a stub package so
    submodules import from the filesystem without running the package body, so
    the entry-point functions are not otherwise reachable from these tests.
    """
    path = (
        Path(__file__).resolve().parents[2]
        / "custom_components/ha_creality_ws/__init__.py"
    )
    name = "custom_components.ha_creality_ws._entry_under_test"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    # `__init__.py` uses relative imports, which resolve against __package__
    # rather than the module's own name -- so it has to be told it belongs to
    # the real package, whose __path__ conftest already points at the source.
    module.__package__ = "custom_components.ha_creality_ws"
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


@pytest.fixture(autouse=True)
def _loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield
    loop.close()


class HassStub:
    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.fail_on: set[str] = set()
        self.services = SimpleNamespace(async_call=self._async_call)

    async def _async_call(self, domain, service, data, **_kw):
        if f"{domain}.{service}" in self.fail_on:
            raise RuntimeError("device is gone")
        self.calls.append((domain, service, data))


def _remove(entry, hass=None):
    hass = hass or HassStub()
    module = _load_package_init()
    asyncio.get_event_loop().run_until_complete(module.async_remove_entry(hass, entry))
    return hass


def test_every_tag_this_printer_owns_is_dismissed():
    hass = _remove(
        fake_config_entry(
            "abc123", options={"notify_targets": ["notify.mobile_app_s24"]}
        )
    )
    tags = {c[2]["data"]["tag"] for c in hass.calls}
    assert tags == {
        "ha_creality_ws_abc123_live",
        "ha_creality_ws_abc123_soon",
        "ha_creality_ws_abc123_alert",
    }
    assert {c[2]["message"] for c in hass.calls} == {"clear_notification"}


def test_every_phone_is_cleaned_up_not_just_the_first():
    hass = _remove(
        fake_config_entry(
            "abc123",
            options={
                "notify_targets": [
                    "notify.mobile_app_s24",
                    "notify.mobile_app_iphone_15_pro",
                ]
            },
        )
    )
    assert {c[1] for c in hass.calls} == {"mobile_app_s24", "mobile_app_iphone_15_pro"}


def test_the_dismiss_marker_never_reaches_a_non_mobile_target():
    """It renders as literal body text anywhere but the companion app, so a
    removal would otherwise post "clear_notification" to someone's Signal."""
    hass = _remove(
        fake_config_entry(
            "abc123",
            options={
                "notify_targets": ["notify.signal_messenger", "notify.mobile_app_s24"]
            },
        )
    )
    assert {c[1] for c in hass.calls} == {"mobile_app_s24"}


def test_a_dead_phone_does_not_stop_the_others():
    """This is the last chance to tidy up, so one unreachable device must not
    take the rest of the cleanup with it."""
    hass = HassStub()
    hass.fail_on = {"notify.mobile_app_s24"}
    _remove(
        fake_config_entry(
            "abc123",
            options={
                "notify_targets": [
                    "notify.mobile_app_s24",
                    "notify.mobile_app_iphone_15_pro",
                ]
            },
        ),
        hass,
    )
    assert {c[1] for c in hass.calls} == {"mobile_app_iphone_15_pro"}


def test_the_legacy_single_target_is_honoured():
    """`notify_device` predates the list and is never deleted, so an entry that
    was never re-configured still has a phone to clean up."""
    hass = _remove(
        fake_config_entry("abc123", options={"notify_device": "notify.mobile_app_s24"})
    )
    assert {c[1] for c in hass.calls} == {"mobile_app_s24"}


def test_nothing_is_sent_when_no_target_was_ever_configured():
    hass = _remove(fake_config_entry("abc123", options={}))
    assert hass.calls == []
