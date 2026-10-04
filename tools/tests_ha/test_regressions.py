"""Closed bugs that had no test, inside a real Home Assistant (R48)."""
from __future__ import annotations

import logging

import pytest
from homeassistant.components.light import ATTR_SUPPORTED_COLOR_MODES, ColorMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_creality_ws.config_flow import ConfigFlow

from .conftest import DOMAIN, HOST, entry_data

K2_PLUS = {
    "model": "F008",
    "modelVersion": "printer hw ver:;printer sw ver:;DWIN hw ver:CR0CN240110C10;DWIN sw ver:1.1.3.13;",
    "hostname": "K2Plus-0000",
    "webrtcSupport": 1,
    "maxBoxTemp": 60,
    "targetBoxTemp": 0,
}


async def _add(hass: HomeAssistant, data: dict | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, version=ConfigFlow.VERSION, data=data or {"host": HOST}, unique_id=HOST, title="Printer"
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _light(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    return next(
        e.entity_id for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id) if e.domain == "light"
    )


async def test_a_k2_plus_light_dims(hass: HomeAssistant, fake_printer) -> None:
    """#102: the K2 Plus/Pro chamber LED was on/off only."""
    fake_printer.overrides = K2_PLUS
    entry = await _add(hass)
    light = _light(hass, entry)
    assert hass.states.get(light).attributes[ATTR_SUPPORTED_COLOR_MODES] == [ColorMode.BRIGHTNESS]

    await hass.services.async_call("light", "turn_on", {"entity_id": light, "brightness": 128}, blocking=True)
    assert fake_printer.instances[-1].sent[-2:] == [
        {"lightSw": 1},
        {"gcodeCmd": "SET_PIN PIN=LED VALUE=0.502"},
    ]
    assert hass.states.get(light).attributes["brightness"] == 128


async def test_an_offline_k2_plus_from_an_older_version_gets_its_dimmer(hass: HomeAssistant, fake_printer) -> None:
    """#102: an entry cached before dimming existed learns the LED pin from
    the cached model, without waiting for the printer to come online."""
    fake_printer.online = False
    data = entry_data(_cached_model="F008", _cached_model_version=K2_PLUS["modelVersion"])
    del data["_cached_has_brightness_control"], data["_cached_led_pin"]
    entry = await _add(hass, data)
    assert entry.data["_cached_led_pin"] == "LED"
    assert hass.states.get(_light(hass, entry)).attributes[ATTR_SUPPORTED_COLOR_MODES] == [ColorMode.BRIGHTNESS]


async def test_the_diagnostic_action_answers(hass: HomeAssistant, fake_printer, hass_ws_client) -> None:
    """#31: the action raised a TypeError instead of collecting anything."""
    await _add(hass)
    response = await hass.services.async_call(DOMAIN, "diagnostic_dump", {}, blocking=True, return_response=True)
    assert len(response["printers"]) == 1
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "persistent_notification/get"})
    ids = [n["notification_id"] for n in (await ws.receive_json())["result"]]
    assert "creality_diagnostic_data" in ids


# What Home Assistant says about any custom integration, or about this test
# environment; nothing to do with the entities.
_HARMLESS = ("We found a custom integration", "libturbojpeg")


@pytest.mark.parametrize("printer", [{}, K2_PLUS], ids=["k1c", "k2plus"])
async def test_home_assistant_has_no_complaint_about_the_entities(
    hass: HomeAssistant, fake_printer, caplog, printer
) -> None:
    """#28 (a unit volume_flow_rate does not accept) and #29 (a light without
    colour modes) were warnings from Home Assistant at setup."""
    fake_printer.overrides = printer
    caplog.set_level(logging.WARNING)
    await _add(hass)
    # Home Assistant's own loggers only. The integration's go2rtc warnings are
    # right here, where no go2rtc runs, and asyncio's slow-step warnings are
    # about the machine running the test.
    complaints = [
        f"{r.levelname} {r.name}: {r.getMessage()}" for r in caplog.records
        if r.levelno >= logging.WARNING
        and r.name.startswith("homeassistant")
        and not any(h in r.getMessage() for h in _HARMLESS)
    ]
    assert complaints == [], "\n".join(complaints)


async def test_both_cards_are_registered_once(hass: HomeAssistant, fake_printer) -> None:
    """#11: the card was not found after install. Each card is a Lovelace
    resource, with its version in the URL, and a reload does not add another."""
    from custom_components.ha_creality_ws.frontend import CARDS, INTEGRATION_URL_BASE, card_version

    entry = await _add(hass)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    urls = sorted(item["url"] for item in hass.data["lovelace"].resources.async_items())
    assert urls == sorted(f"{INTEGRATION_URL_BASE}{card}?v={card_version(card)}" for card in CARDS)


async def test_a_numeric_current_object_is_shown(hass: HomeAssistant, fake_printer) -> None:
    """#106: firmware sending the object as a number raised AttributeError
    every few seconds and left the sensor stale."""
    entry = await _add(hass)
    await fake_printer.instances[-1].feed({"current_object": 3, "printFileName": "/usr/data/printer_data/gcodes/a.gcode"})
    await hass.async_block_till_done()
    sensor = next(
        e.entity_id for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if e.unique_id == f"{HOST}-current_object"
    )
    assert hass.states.get(sensor).state == "3"


def _button(hass: HomeAssistant, entry: MockConfigEntry, uid: str) -> str:
    return next(
        e.entity_id for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if e.unique_id == f"{HOST}-{uid}"
    )


async def test_a_command_between_attempts_reconnects_first(hass: HomeAssistant, fake_printer) -> None:
    """R66: a client waiting out its backoff counted as connected, so Stop
    waited for the next attempt, up to five minutes away."""
    entry = await _add(hass)
    client = fake_printer.instances[-1]
    client.link_up = False
    await hass.services.async_call("button", "press", {"entity_id": _button(hass, entry, "stop_print")}, blocking=True)
    assert client.reconnect_count == 1
    assert {"stop": 1} in client.sent


async def test_a_command_the_printer_cannot_take_says_so(hass: HomeAssistant, fake_printer) -> None:
    from homeassistant.exceptions import HomeAssistantError

    entry = await _add(hass)
    client = fake_printer.instances[-1]
    client.link_up = False
    fake_printer.reconnect_succeeds = False
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "button", "press", {"entity_id": _button(hass, entry, "stop_print")}, blocking=True
        )
    assert {"stop": 1} not in client.sent
