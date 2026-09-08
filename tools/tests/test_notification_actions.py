"""Live-card buttons: which appear, and what a tap actually does.

The safety property under test is that two printers cannot cross-fire. Action
ids are namespaced per config entry and the handler matches them exactly, so a
tap on one card must never touch another machine's print.
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from custom_components.ha_creality_ws.coordinator import KCoordinator
from custom_components.ha_creality_ws.notification_rules import (
    ACTION_PAUSE,
    ACTION_RESUME,
    ACTION_STOP,
    LiveSnapshot,
    action_ids,
    build_actions,
)


_STRINGS = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "custom_components/ha_creality_ws/strings.json"
    ).read_text(encoding="utf-8")
)["common"]  # notification strings; see test_translations.py for why "common"

# Button titles come from the shipped strings, so a renamed key fails here.
LABELS = {
    ACTION_PAUSE: _STRINGS["action_pause"],
    ACTION_RESUME: _STRINGS["action_resume"],
    ACTION_STOP: _STRINGS["action_stop"],
}


class HassStub:
    def __init__(self):
        self.loop = SimpleNamespace(time=lambda: 1000.0)
        self._states: dict = {}
        self.states = SimpleNamespace(get=self._states.get)
        self.services = SimpleNamespace(async_call=self._async_call)
        self.calls: list = []
        self.tasks: list = []
        self.config_entries = SimpleNamespace(async_get_entry=lambda _id: None)
        self.bus = SimpleNamespace(async_fire=lambda *_a, **_k: None)
        self.config = SimpleNamespace(language="en")

    async def _async_call(self, domain, service, data, **_kw):
        self.calls.append((domain, service, data))

    def async_create_task(self, coro):
        self.tasks.append(coro)
        return coro


@pytest.fixture(autouse=True)
def _loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield
    loop.close()


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _coordinator(entry_id="abc123def456", actions=True):
    coord = KCoordinator(HassStub(), host="1.2.3.4", config_entry_id=entry_id)
    coord._notify_actions = actions
    # Button titles come from strings.json; the conftest stub serves the real file.
    _run(coord._async_load_notify_strings())
    return coord


def _snap(state="printing"):
    return LiveSnapshot(activity_state=state, job_active=True, progress=42)


# --------------------------------------------------------------------------- #
# Which buttons appear
# --------------------------------------------------------------------------- #


def test_buttons_are_off_by_default():
    coord = _coordinator(actions=False)
    assert coord._notify_card_actions(_snap()) is None


def test_a_printing_card_offers_pause_and_stop():
    coord = _coordinator()
    titles = [a["title"] for a in coord._notify_card_actions(_snap())]
    assert titles == ["Pause", "Stop"]


def test_a_paused_card_offers_resume_instead_of_pause():
    coord = _coordinator()
    titles = [a["title"] for a in coord._notify_card_actions(_snap("paused"))]
    assert titles == ["Resume", "Stop"]


def test_stop_is_guarded():
    """A mis-tap on a lock screen must not be able to end a 14-hour print."""
    stop = build_actions(paused=False, ids=action_ids("abc123"), labels=LABELS)[-1]
    assert stop["destructive"] is True
    assert stop["authenticationRequired"] is True


def test_pause_is_not_guarded():
    """Pausing is recoverable, so an extra confirmation would just be friction."""
    pause = build_actions(paused=False, ids=action_ids("abc123"), labels=LABELS)[0]
    assert "authenticationRequired" not in pause
    assert "destructive" not in pause


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #


def test_two_printers_get_different_action_ids():
    a = action_ids("aaaaaaaaaaaa")
    b = action_ids("bbbbbbbbbbbb")
    assert set(a.values()).isdisjoint(b.values())


def test_action_ids_survive_a_dotted_host_as_the_fallback_key():
    """Without a config entry id the host is used, and that is an IP."""
    ids = action_ids("192.168.1.50")
    for value in ids.values():
        assert "." not in value


def test_action_ids_are_stable_across_calls():
    """They are baked into notifications that outlive the process."""
    assert action_ids("abc123def456") == action_ids("abc123def456")


# --------------------------------------------------------------------------- #
# What a tap does
# --------------------------------------------------------------------------- #


def _handler_result(coord, action):
    return _run(coord.async_handle_notification_action(action))


def test_pause_reaches_the_existing_pause_path(monkeypatch):
    coord = _coordinator()
    seen = []
    monkeypatch.setattr(
        coord, "request_pause", lambda: asyncio.sleep(0, result=seen.append("pause"))
    )
    assert _handler_result(coord, coord._notify_action_ids()[ACTION_PAUSE]) is True
    assert seen == ["pause"]


def test_resume_reaches_the_existing_resume_path(monkeypatch):
    coord = _coordinator()
    seen = []
    monkeypatch.setattr(
        coord, "request_resume", lambda: asyncio.sleep(0, result=seen.append("resume"))
    )
    assert _handler_result(coord, coord._notify_action_ids()[ACTION_RESUME]) is True
    assert seen == ["resume"]


def test_stop_goes_through_the_same_path_as_the_stop_button(monkeypatch):
    coord = _coordinator()
    seen = []
    monkeypatch.setattr(
        coord, "async_stop_print", lambda: asyncio.sleep(0, result=seen.append("stop"))
    )
    assert _handler_result(coord, coord._notify_action_ids()[ACTION_STOP]) is True
    assert seen == ["stop"]


def test_another_printers_action_is_ignored(monkeypatch):
    """The exact-id match is the entire cross-fire guard."""
    coord = _coordinator(entry_id="aaaaaaaaaaaa")
    touched = []
    for name in ("request_pause", "request_resume", "async_stop_print"):
        monkeypatch.setattr(
            coord, name, lambda n=name: asyncio.sleep(0, result=touched.append(n))
        )
    other = action_ids("bbbbbbbbbbbb")[ACTION_STOP]
    assert _handler_result(coord, other) is False
    assert touched == []


def test_an_unrelated_mobile_action_is_ignored(monkeypatch):
    """The bus event is global; other integrations use it too."""
    coord = _coordinator()
    touched = []
    for name in ("request_pause", "request_resume", "async_stop_print"):
        monkeypatch.setattr(
            coord, name, lambda n=name: asyncio.sleep(0, result=touched.append(n))
        )
    assert _handler_result(coord, "SILENCE_ALARM") is False
    assert touched == []


# --------------------------------------------------------------------------- #
# The shared stop path
# --------------------------------------------------------------------------- #


def test_stop_refuses_to_fire_blind_when_disconnected(monkeypatch):
    coord = _coordinator()
    monkeypatch.setattr(coord, "ensure_connected", lambda: asyncio.sleep(0, result=False))
    sent = []
    monkeypatch.setattr(
        coord.client, "send_set_retry", lambda **kw: asyncio.sleep(0, result=sent.append(kw))
    )
    _run(coord.async_stop_print())
    assert sent == []


def test_stop_sends_the_printer_command_when_connected(monkeypatch):
    coord = _coordinator()
    monkeypatch.setattr(coord, "ensure_connected", lambda: asyncio.sleep(0, result=True))
    sent = []
    monkeypatch.setattr(
        coord.client, "send_set_retry", lambda **kw: asyncio.sleep(0, result=sent.append(kw))
    )
    _run(coord.async_stop_print())
    assert sent == [{"stop": 1}]


def test_the_stop_button_entity_uses_the_same_path(monkeypatch):
    """Otherwise the dashboard and the notification could drift apart."""
    import sys
    import types

    button_mod = types.ModuleType("homeassistant.components.button")

    class ButtonEntity:
        pass

    button_mod.ButtonEntity = ButtonEntity
    old = sys.modules.get("homeassistant.components.button")
    sys.modules["homeassistant.components.button"] = button_mod
    try:
        from custom_components.ha_creality_ws.button import KPrintStopButton
    finally:
        if old is None:
            sys.modules.pop("homeassistant.components.button", None)
        else:
            sys.modules["homeassistant.components.button"] = old

    coord = _coordinator()
    button = KPrintStopButton.__new__(KPrintStopButton)
    button.coordinator = coord
    called = []
    monkeypatch.setattr(
        coord, "async_stop_print", lambda: asyncio.sleep(0, result=called.append(True))
    )
    _run(button.async_press())
    assert called == [True]


def test_printers_added_in_the_same_second_do_not_share_action_ids():
    """Home Assistant entry ids are ULIDs whose leading characters encode the
    creation time and carry no randomness. Truncating meant two printers added
    seconds apart could share ids -- and a Stop tap would hit whichever matched
    first."""
    a = action_ids("01JABCDEAAAAAAAAAAAAAAAAAA")
    b = action_ids("01JABCDEBBBBBBBBBBBBBBBBBB")
    assert set(a.values()).isdisjoint(b.values())


def test_a_near_identical_entry_id_is_still_distinguished():
    a = action_ids("01JABCDEFGHJKMNPQRSTVWXYZ0")
    b = action_ids("01JABCDEFGHJKMNPQRSTVWXYZ1")
    assert set(a.values()).isdisjoint(b.values())
